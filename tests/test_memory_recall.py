import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
from backend.memory_recall import recall_query, recall_events
from backend.mcp_client_bridge import McpBridge, McpTool
from test_memory_save_loop import load_agent


def result(notes):
    return json.dumps({'ok': True, 'data': notes})


class RecallTests(unittest.IsolatedAsyncioTestCase):
    def test_recognizes_recall_without_hijacking_save_or_documents(self):
        for message in ['what is my name', 'Do you remember my name?', 'tell me my name',
                        'Use mcp_memory_dev_search_notes to search for “name”, then tell me my name.']:
            self.assertEqual(recall_query(message), 'name')
        self.assertEqual(recall_query('What language do I prefer?'), 'language')
        self.assertEqual(recall_query('List saved notes'), '')
        for message in ['could you remember my name is Hannan.', 'Remember that I prefer Python.',
                        'What is this knowledge base about?', 'What is my name in the uploaded resume?']:
            self.assertIsNone(recall_query(message), message)

    async def test_name_recall_bypasses_model_with_empty_history(self):
        bridge = NS(as_openai_tools=lambda: [], call=AsyncMock(return_value=result([{'id': 1, 'content': 'My name is Hannan', 'tags': []}])))
        model = AsyncMock(side_effect=AssertionError('Model should not handle persisted name recall'))
        agent = load_agent(model, bridge)
        events = [e async for e in agent('what is my name', history=[], api_key='test-only')]
        self.assertIn('Your name is Hannan.', str(events))
        model.assert_not_awaited()
        bridge.call.assert_awaited_once_with('mcp_memory_dev_search_notes', {'query': 'name', 'limit': 200})

    async def test_empty_search_falls_back_to_saved_call_me_fact(self):
        bridge = NS(call=AsyncMock(side_effect=[result([]), result([{'content': 'Call me Hannan', 'tags': []}])]))
        events = [e async for e in recall_events(bridge, 'name')]
        self.assertIn('Your name is Hannan.', str(events))
        self.assertEqual(bridge.call.await_count, 2)

    async def test_empty_database_is_reported_as_empty(self):
        bridge = NS(call=AsyncMock(return_value=result([])))
        events = [e async for e in recall_events(bridge, 'name')]
        self.assertIn('no saved notes', str(events))
        self.assertNotIn('Here is what is saved', str(events))

    async def test_unrelated_notes_do_not_become_name(self):
        bridge = NS(call=AsyncMock(side_effect=[result([]), result([{'content': 'I prefer Python', 'tags': ['preferences']}])]))
        events = [e async for e in recall_events(bridge, 'name')]
        self.assertIn('database is not empty', str(events))
        self.assertNotIn('Here is what is saved', str(events))

    async def test_errors_are_distinct_from_empty_memory(self):
        bridge = NS(call=AsyncMock(return_value=json.dumps({'ok': False, 'error': 'Server not connected'})))
        events = [e async for e in recall_events(bridge, 'name')]
        self.assertIn('Server not connected', str(events))
        self.assertNotIn('no saved notes', str(events))

    async def test_bridge_normalizes_multiple_note_blocks_and_empty(self):
        b = McpBridge(tools=[McpTool(server='memory-dev', name='search_notes', description='search')])
        notes = [{'id': 2, 'content': 'My name is Hannan'}, {'id': 1, 'content': 'Call me Hannan'}]
        session = NS(call_tool=AsyncMock(return_value=NS(isError=False, structuredContent=None,
                                                       content=[NS(text=json.dumps(n)) for n in notes])))
        b._sessions['memory-dev'] = session
        data = json.loads(await b.call('mcp_memory_dev_search_notes', {'query': 'name'}))
        self.assertEqual(data['data'], notes)
        session.call_tool.return_value.content = []
        data = json.loads(await b.call('mcp_memory_dev_search_notes', {'query': 'name'}))
        self.assertEqual(data['data'], [])


if __name__ == '__main__':
    unittest.main()
