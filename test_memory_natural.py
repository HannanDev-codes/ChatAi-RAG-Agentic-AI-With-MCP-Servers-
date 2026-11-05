import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
from backend.memory_recall import natural_reply, recall_query, recall_events, save_confirmation
from test_memory_save_loop import load_agent, save_call


class NaturalMemoryTests(unittest.IsolatedAsyncioTestCase):
    def test_duplicate_name_facts_collapse_to_one_answer(self):
        notes = [{'content': 'My name is Hannan'}, {'content': "User's name is Hannan."}]
        self.assertEqual(natural_reply(notes, 'name'), 'Your name is Hannan.')

    def test_conflicting_names_are_not_silently_overwritten(self):
        notes = [{'content': 'My name is Hannan'}, {'content': 'Call me Ali'}]
        self.assertIn('Which should I use?', natural_reply(notes, 'name'))

    def test_language_question_variations(self):
        for text in ['which language do i like', 'What language do I prefer?', 'What is my favorite language?', 'Tell me which language I love']:
            self.assertEqual(recall_query(text), 'language')

    def test_likes_and_comparative_preferences(self):
        for content in ['User likes Python', 'I like python', 'I prefer Python over JavaScript', 'My favorite programming language is Python']:
            self.assertEqual(natural_reply([{'content': content}], 'language'), 'You like Python.')
        self.assertEqual(save_confirmation('User likes Python'), "I'll remember that you like Python.")

    def test_no_preference_is_inferred_from_mentions_or_negation(self):
        for content in ["I don't like Python", 'User dislikes Python', 'I like not Python', 'Python is installed', 'My name is Python']:
            self.assertNotEqual(natural_reply([{'content': content}], 'language'), 'You like Python.')

    async def test_likes_fact_found_when_language_word_absent(self):
        response = lambda data: json.dumps({'ok': True, 'data': data})
        bridge = NS(call=AsyncMock(side_effect=[response([]), response([{'content': 'User likes Python'}])]))
        events = [e async for e in recall_events(bridge, 'language')]
        self.assertEqual([e['content'] for e in events if e['type'] == 'delta'], ['You like Python.'])

    async def test_language_question_answers_without_model(self):
        response = lambda data: json.dumps({'ok': True, 'data': data})
        bridge = NS(as_openai_tools=lambda: [], call=AsyncMock(side_effect=[response([]), response([{'content': 'User likes Python'}])]))
        model = AsyncMock(side_effect=AssertionError('Model must not invent a preference'))
        events = [e async for e in load_agent(model, bridge)('which language do i like', api_key='test')]
        self.assertIn({'type': 'delta', 'content': 'You like Python.'}, events)
        model.assert_not_awaited()

    async def test_model_save_confirmation_is_natural(self):
        bridge = NS(as_openai_tools=lambda: [], call=AsyncMock(return_value=json.dumps({'ok': True, 'data': {'id': 1, 'content': 'User likes Python'}})))
        tc = save_call(content='User likes Python')
        create = AsyncMock(return_value=NS(choices=[NS(message=NS(content='', tool_calls=[tc]))]))
        events = [e async for e in load_agent(create, bridge)('i like python', api_key='test')]
        self.assertIn({'type': 'delta', 'content': "I'll remember that you like Python."}, events)
