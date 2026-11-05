import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

from backend.memory_recall import name_note
from backend.mcp_client_bridge import McpBridge, McpTool
from test_memory_save_loop import load_agent


class MemoryDeclarationTests(unittest.IsolatedAsyncioTestCase):
    def test_self_introductions(self):
        for text in ['My name is Hannan', 'could you remember my name is Hannan.', 'Remember that my name is Hannan.']:
            self.assertEqual(name_note(text), 'My name is Hannan')
        for text in ['what is my name?', 'My name is not Hannan?', 'The document says my name is Hannan']:
            self.assertIsNone(name_note(text))

    async def test_name_statement_saves_without_model(self):
        bridge = NS(as_openai_tools=lambda: [], call=AsyncMock(return_value=json.dumps({'ok': True, 'data': {'id': 1, 'content': 'My name is Hannan'}})))
        create = AsyncMock(side_effect=AssertionError('Model should not handle explicit name'))
        agent = load_agent(create, bridge)
        events = [e async for e in agent('My name is Hannan', api_key='test')]
        self.assertIn("I'll remember your name is Hannan.", str(events))
        bridge.call.assert_awaited_once_with('mcp_memory_dev_save_note', {'content': 'My name is Hannan', 'tags': ['name', 'profile']})
        create.assert_not_awaited()

    async def test_blank_timeout_and_failed_save_are_explained(self):
        bridge = McpBridge(tools=[McpTool(server='memory-dev', name='save_note', description='')])
        bridge._sessions['memory-dev'] = NS(call_tool=AsyncMock(side_effect=TimeoutError()))
        outcome = json.loads(await bridge.call('mcp_memory_dev_save_note', {}))
        self.assertIn('TimeoutError', outcome['error'])
        agent = load_agent(AsyncMock(), bridge)
        events = [e async for e in agent('My name is Hannan', api_key='test')]
        self.assertIn('TimeoutError', str(events))
        self.assertNotIn("I'll remember", str(events))

    async def test_greeting_does_not_save_or_call_model(self):
        create = AsyncMock()
        bridge = NS(call=AsyncMock())
        agent = load_agent(create, bridge)
        events = [e async for e in agent('hi', api_key='test')]
        self.assertEqual(events[0]['content'], 'Hello!')
        bridge.call.assert_not_awaited()
        create.assert_not_awaited()


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Install requirements for real MCP lifecycle tests')
class RealLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_request_tasks_and_server_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            code = ('from pathlib import Path; from backend import memory_dev_tools as t; '
                    f't.ROOT=Path({temp!r}); from backend.mcp_memory_dev_server import mcp; mcp.run(transport="stdio")')
            good = {'name': 'memory-dev', 'command': sys.executable, 'args': ['-c', code], 'cwd': str(Path.cwd())}
            bad = {'name': 'broken-test-server', 'command': sys.executable, 'args': ['-c', 'raise SystemExit(1)'], 'cwd': str(Path.cwd())}
            bridge = McpBridge()
            try:
                # Simulate a status request that returns before the next chat request.
                await asyncio.create_task(bridge._start_servers([good, bad]))
                self.assertIn('memory-dev', bridge._sessions)
                self.assertIn('broken-test-server', bridge.errors)
                create = AsyncMock(side_effect=AssertionError('No model for personal memory'))
                agent = load_agent(create, bridge)
                async def request(message):
                    return [e async for e in agent(message, history=[], api_key='test')]
                saved = await asyncio.create_task(request('My name is Hannan'))
                self.assertIn("I'll remember your name is Hannan.", str(saved))
                recalled = await asyncio.create_task(request('what is my name'))
                self.assertIn('Your name is Hannan.', str(recalled))
            finally:
                # Shutdown from another request/task must clean up transports safely.
                await asyncio.create_task(bridge.close())
            self.assertFalse(bridge._tasks)
            bridge = McpBridge()
            try:
                await asyncio.create_task(bridge._start_servers([good]))
                agent = load_agent(AsyncMock(side_effect=AssertionError('No model needed')), bridge)
                async def refreshed_request():
                    return [e async for e in agent('what is my name', history=[], api_key='test')]
                events = await asyncio.create_task(refreshed_request())
                self.assertIn('Your name is Hannan.', str(events))
            finally:
                await asyncio.create_task(bridge.close())

    async def test_http_status_then_two_streamed_chat_requests(self):
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse, StreamingResponse
        from starlette.routing import Route
        import httpx
        with tempfile.TemporaryDirectory() as temp:
            code = ('from pathlib import Path; from backend import memory_dev_tools as t; '
                    f't.ROOT=Path({temp!r}); from backend.mcp_memory_dev_server import mcp; mcp.run(transport="stdio")')
            spec = {'name': 'memory-dev', 'command': sys.executable, 'args': ['-c', code], 'cwd': str(Path.cwd())}
            bridge = McpBridge()
            agent = load_agent(AsyncMock(side_effect=AssertionError('No model for name memory')), bridge)
            async def status(request):
                await bridge._start_servers([spec])
                return JSONResponse({'servers': list(bridge._sessions), 'errors': bridge.errors})
            async def chat(request):
                payload = await request.json()
                async def stream():
                    async for event in agent(payload['message'], history=[], api_key='test'):
                        yield 'data: ' + json.dumps(event) + '\n\n'
                return StreamingResponse(stream(), media_type='text/event-stream')
            app = Starlette(routes=[Route('/status', status), Route('/chat', chat, methods=['POST'])])
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    response = await client.get('/status')
                    self.assertIn('memory-dev', response.json()['servers'])
                    response = await client.post('/chat', json={'message': 'My name is Hannan'})
                    self.assertIn("I'll remember your name is Hannan.", response.text)
                    response = await client.post('/chat', json={'message': 'what is my name'})
                    self.assertIn('Your name is Hannan.', response.text)
                    self.assertNotIn('could not read', response.text)
            finally:
                await bridge.close()
