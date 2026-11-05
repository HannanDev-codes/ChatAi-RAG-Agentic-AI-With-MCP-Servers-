"""Run the real agent loop with fake model/bridge, without RAG downloads."""
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
from backend.mcp_client_bridge import McpBridge, McpTool
from backend.memory_recall import recall_query, recall_events, name_note, save_name_events, save_confirmation


def load_agent(create, bridge):
    source = ast.parse((Path(__file__).parents[1] / 'backend/agent.py').read_text())
    fn = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'agent_stream')
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), fn], type_ignores=[])
    scope = dict(DEFAULT_MODEL='test', MAX_AGENT_STEPS=6, SYSTEM_PROMPT='', os=os,
                 json=json, recall_query=recall_query, recall_events=recall_events, name_note=name_note, save_name_events=save_name_events, save_confirmation=save_confirmation, R=NS(LAST_FILE=None), _tools_schema=lambda: [],
                 AsyncGroq=lambda **kwargs: NS(chat=NS(completions=NS(create=create))),
                 get_mcp_bridge=AsyncMock(return_value=bridge))
    exec(compile(ast.fix_missing_locations(module), 'agent.py', 'exec'), scope)
    return scope['agent_stream']


def save_call(call_id='call1', content='My name is Hannan'):
    return NS(id=call_id, function=NS(name='mcp_memory_dev_save_note', arguments=json.dumps({'content': content})))


class SaveLoopTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, result, calls=None):
        bridge = NS(as_openai_tools=lambda: [], call=AsyncMock(return_value=result))
        create = AsyncMock(return_value=NS(choices=[NS(message=NS(content='', tool_calls=calls or [save_call()]))]))
        agent = load_agent(create, bridge)
        events = [event async for event in agent('Remember a fact about me.', api_key='test-only')]
        self.assertFalse([e for e in events if e['type'] == 'error'], events)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['steps'], 1)
        self.assertEqual(create.await_count, 1)
        return events, bridge

    async def test_success_finishes_without_second_model_request(self):
        events, bridge = await self.run_turn(json.dumps({'ok': True, 'data': {'id': 1, 'content': 'My name is Hannan'}}))
        self.assertIn("I'll remember your name is Hannan.", str(events))
        self.assertEqual(bridge.call.await_count, 1)

    async def test_duplicate_calls_in_batch_save_once(self):
        events, bridge = await self.run_turn(json.dumps({'ok': True, 'data': {'id': 1}}), [save_call('one'), save_call('two')])
        self.assertEqual(bridge.call.await_count, 1)
        self.assertEqual(sum(e['type'] == 'tool' for e in events), 1)

    async def test_failure_is_reported_once_without_false_success(self):
        events, bridge = await self.run_turn(json.dumps({'ok': False, 'error': 'database is locked'}))
        self.assertIn('database is locked', str(events))
        self.assertNotIn("I'll remember", str(events))
        self.assertEqual(bridge.call.await_count, 1)

    async def test_unrecognized_result_does_not_claim_saved(self):
        events, _ = await self.run_turn('old unstructured result')
        self.assertIn("couldn't confirm", str(events))
        self.assertNotIn("I'll remember", str(events))

    async def test_distinct_notes_are_both_saved(self):
        _, bridge = await self.run_turn(json.dumps({'ok': True, 'data': {'id': 1}}), [save_call('one'), save_call('two', 'I prefer Python')])
        self.assertEqual(bridge.call.await_count, 2)

    async def test_bridge_preserves_success_and_error_status(self):
        bridge = McpBridge(tools=[McpTool(server='memory-dev', name='save_note', description='')])
        session = NS(call_tool=AsyncMock(return_value=NS(isError=True, content=[NS(text='Validation failed')], structuredContent=None)))
        bridge._sessions['memory-dev'] = session
        result = json.loads(await bridge.call('mcp_memory_dev_save_note', {}))
        self.assertEqual(result, {'ok': False, 'error': 'Validation failed'})
        session.call_tool.return_value = NS(isError=False, content=[], structuredContent={'id': 2, 'content': 'Hannan'})
        result = json.loads(await bridge.call('mcp_memory_dev_save_note', {'content': 'Hannan'}))
        self.assertTrue(result['ok'])
        self.assertEqual(result['data']['id'], 2)
        session.call_tool.return_value = NS(isError=False, content=[NS(text=json.dumps({'id': 3, 'content': 'Python'}))], structuredContent=None)
        result = json.loads(await bridge.call('mcp_memory_dev_save_note', {}))
        self.assertEqual(result['data']['content'], 'Python')
        session.call_tool.side_effect = RuntimeError('Connection closed')
        result = json.loads(await bridge.call('mcp_memory_dev_save_note', {}))
        self.assertFalse(result['ok'])
        self.assertIn('Connection closed', result['error'])


if __name__ == '__main__':
    unittest.main()
