import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
from test_memory_save_loop import load_agent


class DeveloperReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_developer_result_is_summarized_without_repeating_tools(self):
        call = NS(id='read1', function=NS(name='mcp_memory_dev_read_file', arguments=json.dumps({'path': 'README.md'})))
        bridge = NS(as_openai_tools=lambda: [], call=AsyncMock(return_value=json.dumps({'ok': True, 'data': '# RAG chatbot'})))
        async def chunks():
            yield NS(choices=[NS(delta=NS(content='The README describes your RAG chatbot.'))])
        create = AsyncMock(side_effect=[NS(choices=[NS(message=NS(content='', tool_calls=[call]))]), chunks()])
        events = [e async for e in load_agent(create, bridge)('Read README.md using mcp_memory_dev_read_file', api_key='test')]
        bridge.call.assert_awaited_once()
        self.assertEqual(create.await_count, 2)
        final = create.await_args_list[-1].kwargs
        self.assertEqual(final['tool_choice'], 'none')
        self.assertTrue(final['stream'])
        self.assertTrue(any(m['role'] == 'tool' and 'RAG chatbot' in m['content'] for m in final['messages']))
        self.assertIn('The README describes your RAG chatbot.', str(events))
        self.assertEqual(events[-1]['type'], 'done')

    async def test_multiple_directory_blocks_become_one_list(self):
        from backend.mcp_client_bridge import McpBridge, McpTool
        bridge = McpBridge(tools=[McpTool(server='memory-dev', name='list_directory', description='list')])
        entries = [{'name': 'backend', 'type': 'directory'}, {'name': 'README.md', 'type': 'file'}]
        bridge._sessions['memory-dev'] = NS(call_tool=AsyncMock(return_value=NS(
            isError=False, structuredContent=None, content=[NS(text=json.dumps(e)) for e in entries])))
        outcome = json.loads(await bridge.call('mcp_memory_dev_list_directory', {'path': '.'}))
        self.assertEqual(outcome, {'ok': True, 'data': entries})

    async def directory_turn(self, message, outcome):
        call = NS(id='list1', function=NS(name='mcp_memory_dev_list_directory', arguments=json.dumps({'path': 'backend'})))
        bridge = NS(as_openai_tools=lambda: [], call=AsyncMock(return_value=json.dumps(outcome)))
        create = AsyncMock(return_value=NS(choices=[NS(message=NS(content='', tool_calls=[call]))]))
        events = [e async for e in load_agent(create, bridge)(message, api_key='test')]
        create.assert_awaited_once()
        bridge.call.assert_awaited_once_with('mcp_memory_dev_list_directory', {'path': 'backend'})
        self.assertEqual(events[-1]['type'], 'done')
        return '\n'.join(e['content'] for e in events if e['type'] == 'delta')

    async def test_directory_listing_cannot_be_replaced_by_generic_ack(self):
        for message in ['mcp_memory_dev_list_directory', 'Use mcp_memory_dev_list_directory to list the backend folder.']:
            text = await self.directory_turn(message, {'ok': True, 'data': [
                {'name': 'agent.py', 'type': 'file'}, {'name': 'tools', 'type': 'directory'}]})
            self.assertIn('Files in backend:', text)
            self.assertIn('agent.py', text)
            self.assertIn('tools/', text)
            self.assertNotIn('Got it', text)

    async def test_directory_empty_error_and_invalid_results_are_distinct(self):
        text = await self.directory_turn('list backend', {'ok': True, 'data': []})
        self.assertIn('No permitted entries', text)
        text = await self.directory_turn('list backend', {'ok': False, 'error': 'Permission denied'})
        self.assertIn('Permission denied', text)
        self.assertNotIn('Files in backend:', text)
        text = await self.directory_turn('list backend', {'ok': True, 'data': 'unexpected string'})
        self.assertIn('no file names could be verified', text)

    async def test_final_prompt_keeps_real_user_request_as_last_user_message(self):
        call = NS(id='read1', function=NS(name='mcp_memory_dev_read_file', arguments=json.dumps({'path': 'README.md'})))
        bridge = NS(as_openai_tools=lambda: [], call=AsyncMock(return_value=json.dumps({'ok': True, 'data': 'Project docs'})))
        async def chunks():
            yield NS(choices=[NS(delta=NS(content='Project docs'))])
        create = AsyncMock(side_effect=[NS(choices=[NS(message=NS(content='', tool_calls=[call]))]), chunks()])
        request = 'Read README.md using mcp_memory_dev_read_file'
        events = [e async for e in load_agent(create, bridge)(request, api_key='test')]
        messages = create.await_args_list[-1].kwargs['messages']
        self.assertEqual([m['content'] for m in messages if m['role'] == 'user'], [request])
        self.assertIn('provide its result or error', messages[0]['content'])
        self.assertEqual(messages[-1]['role'], 'tool')
