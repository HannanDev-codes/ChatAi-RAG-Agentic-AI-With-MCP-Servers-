import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from backend import memory_dev_tools as t


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(t, 'ROOT', self.root)
        self.root_patch.start()
        self.env_patch = patch.dict('os.environ', {'MCP_SAFE_ROOT': str(self.root)})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def test_memory_survives_connections_and_search_is_literal(self):
        note = t.save_note('I prefer Python; 100% of the time', ['language'])
        self.assertEqual(t.search_notes('python')[0]['id'], note['id'])
        self.assertEqual(t.search_notes('language')[0]['tags'], ['language'])
        self.assertEqual(len(t.search_notes('%')), 1)
        self.assertEqual(t.search_notes("' OR 1=1 --"), [])
        self.assertEqual(t.get_all_notes()[0]['content'], note['content'])
        t.save_note('second')
        self.assertEqual(t.get_all_notes(limit=1, offset=1)[0]['id'], note['id'])

    def test_memory_validation(self):
        for call in (lambda: t.save_note(' '), lambda: t.get_all_notes(limit=0),
                     lambda: t.search_notes(''), lambda: t.save_note('x', ['x'*101])):
            with self.assertRaises(ValueError):
                call()

    def test_files_and_symlink_escape(self):
        (self.root / 'code.py').write_text('print(1)')
        (self.root / '.env').write_text('secret')
        self.assertEqual(t.read_file('code.py'), 'print(1)')
        self.assertEqual([e['name'] for e in t.list_directory()], ['code.py'])
        with self.assertRaises(ValueError):
            t.read_file('.env')
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'private.txt'
            target.write_text('outside')
            (self.root / 'escape.txt').symlink_to(target)
            with self.assertRaises(ValueError):
                t.read_file('escape.txt')
            with self.assertRaises(ValueError):
                t.read_file(str(target))

    def test_command_injection_and_arbitrary_commands_rejected(self):
        for cmd in ['git status; echo hacked', 'pytest', 'python -c "print(1)"',
                    'git -c core.fsmonitor=evil status', 'git status --porcelain']:
            with self.assertRaises(ValueError):
                t.run_safe_command(cmd)
        (self.root / '.git').mkdir()
        with patch.object(t.subprocess, 'run') as run:
            run.return_value.returncode = 0
            t.run_safe_command('git status')
            self.assertFalse(run.call_args.kwargs['shell'])
            self.assertEqual(run.call_args.kwargs['cwd'], self.root)

    def test_private_addresses_and_mixed_dns_blocked(self):
        for ip in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '192.168.1.2']:
            with patch.object(t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', (ip, 80))]):
                with self.assertRaises(ValueError):
                    t._target('http://example.com')
        with self.assertRaises(ValueError):
            t._target('file:///etc/passwd')

    def test_redirect_revalidated_and_html_cleaned(self):
        response = Mock(status=302)
        response.getheader.return_value = 'http://127.0.0.1/secret'
        conn = Mock()
        conn.getresponse.return_value = response
        public = t.urlsplit('https://example.com')
        with patch.object(t, '_target', side_effect=[(public, 443, '93.184.216.34'), ValueError('blocked')]), patch.object(t.http.client, 'HTTPSConnection', return_value=conn):
            with self.assertRaisesRegex(ValueError, 'blocked'):
                t.fetch_url_content('https://example.com')
        conn.close.assert_called_once()
        parser = t._Text()
        parser.feed('<p>Hello &amp; world</p><script>secret()</script>')
        self.assertEqual(''.join(parser.parts), 'Hello & world')

    def test_response_limit_and_dns_pin(self):
        response = Mock(status=200)
        response.getheader.return_value = 'text/plain'
        response.read.return_value = b'x' * (t.MAX_BYTES + 1)
        conn = Mock()
        conn.getresponse.return_value = response
        public = t.urlsplit('https://example.com')
        with patch.object(t, '_target', return_value=(public, 443, '93.184.216.34')), patch.object(t.http.client, 'HTTPSConnection', return_value=conn):
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                t.fetch_url_content('https://example.com')
            with patch.object(t.socket, 'create_connection') as create:
                conn._create_connection(('example.com', 443), 10)
                self.assertEqual(create.call_args.args[0], ('93.184.216.34', 443))


if __name__ == '__main__':
    unittest.main()
