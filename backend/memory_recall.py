"""Ground personal-memory answers in tool results instead of model recall."""
from __future__ import annotations

import json
import re


def recall_query(message: str) -> str | None:
    """Recognize personal recall, leaving unrelated knowledge-base queries alone."""
    text = message.strip().lower()
    explicit = 'mcp_memory_dev_search_notes' in text or 'mcp_memory_dev_get_all_notes' in text
    # A save instruction must still use the normal save tool flow.
    if not explicit and (re.search(r'\b(remember that|save|store|forget|delete|update)\b', text)
                         or ('remember my' in text and not re.search(r'\bdo you remember\b', text))):
        return None
    if not explicit and re.search(r'\b(document|knowledge base|resume|uploaded|pdf|report)\b', text):
        return None
    if not explicit and re.search(r'\b(list|show)\b.*\b(saved notes|memories|memory notes)\b', text):
        return ''
    if re.search(r'\b(my name|who am i)\b', text) and (explicit or re.search(r'\b(what|who|tell|recall|do you)\b', text)):
        return 'name'
    if explicit:
        quoted = re.search(r'(?:search|query|for)[^\n]*?["“\']([^"”\']+)["”\']', message, re.I)
        return quoted.group(1).strip() if quoted else ''
    if re.search(r'\b(what|which|do you|can you|tell me|recall)\b', text):
        if re.search(r'\b(language|languages)\b', text) and re.search(r'\b(like|love|prefer|favourite|favorite|enjoy)\b', text):
            return 'language'
        if re.search(r'\b(i prefer|do i prefer|my preferences|language.*prefer|prefer.*language)\b', text):
            return 'prefer'
        if re.search(r'\b(remember|know|saved)\b.*\b(about me|my preferences)\b', text):
            return ''
    return None


def _decode(raw: str) -> list[dict]:
    outcome = json.loads(raw)
    if not isinstance(outcome, dict) or outcome.get('ok') is not True:
        error = (outcome.get('error') or 'MCP failed without an error message') if isinstance(outcome, dict) else 'Invalid MCP result'
        raise ValueError(str(error))
    data = outcome.get('data')
    if isinstance(data, dict) and 'result' in data:
        data = data['result']
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict) and 'content' in data:
        data = [data]
    if not isinstance(data, list) or any(not isinstance(n, dict) or not isinstance(n.get('content'), str) for n in data):
        raise ValueError('Unexpected memory response; no saved facts could be verified')
    return data


def _matches(note: dict, query: str) -> bool:
    text = (note['content'] + ' ' + str(note.get('tags', []))).lower()
    if query == 'language':
        return bool(_language_facts(note['content']))
    if query.lower() in text:
        return True
    if query == 'name':
        return bool(re.search(r'\b(call me|i am called|i go by)\b', text))
    if query == 'prefer':
        return bool(re.search(r'\b(favou?rite|like|likes)\b', text))
    return False


async def recall_events(bridge, query: str):
    """Yield UI events with facts or an explicit empty/error outcome. Never invent."""
    if bridge is None:
        yield {'type': 'delta', 'content': 'Saved memory is unavailable. Enable MCP and check /api/mcp/status for memory-dev.'}
        yield {'type': 'done', 'sources': [], 'files': [], 'steps': 0}
        return
    search_name = 'mcp_memory_dev_search_notes' if query else 'mcp_memory_dev_get_all_notes'
    yield {'type': 'tool', 'query': '[mcp] ' + search_name}
    try:
        arguments = {'query': query, 'limit': 200} if query else {'limit': 200, 'offset': 0}
        notes = _decode(await bridge.call(search_name, arguments))
        if query == 'language':
            notes = [note for note in notes if _language_facts(note['content'])]
        # A fact may be phrased "Call me Hannan" rather than include "name".
        # Search persisted notes once as a fallback, bounded to 200 recent notes.
        if not notes and query:
            yield {'type': 'tool', 'query': '[mcp] mcp_memory_dev_get_all_notes'}
            recent = _decode(await bridge.call('mcp_memory_dev_get_all_notes', {'limit': 200, 'offset': 0}))
            notes = [note for note in recent if _matches(note, query)]
            empty_db = not recent
        else:
            empty_db = not notes
        if notes:
            reply = natural_reply(notes, query)
        elif empty_db:
            reply = ('The memory tools returned no saved notes. I cannot recover your name from this database. '
                     'Please save it again, confirm the “Saved to memory” reply, then ask in a new chat. '
                     'Keep using the same project folder and data/memory.db.')
        else:
            reply = ('No saved note matching “' + query[:100] + '” was found in the search or the 200 most recent notes. '
                     'Other notes exist, so the database is not empty. You can ask to list saved notes to inspect them.')
        yield {'type': 'delta', 'content': reply}
    except Exception as exc:
        from backend.mcp_client_bridge import format_error
        yield {'type': 'delta', 'content': 'I could not read saved memory: ' + format_error(exc)[:500] + '. Check /api/mcp/status; this does not mean your notes were deleted.'}
    yield {'type': 'done', 'sources': [], 'files': [], 'steps': 1}


def name_note(message: str) -> str | None:
    """An unambiguous self-introduction is a fact to persist, not just acknowledge."""
    match = re.fullmatch(
        r"\s*(?:(?:please[, ]*)?(?:(?:could|can|would) you\s+)?"
        r"(?:remember(?: that)?|save(?: that)?|note(?: that)?)\s+)?"
        r"my name is\s+([^\n.!?]{1,100}?)[.!]?\s*",
        message, re.I,
    )
    if not match:
        return None
    name = match.group(1).strip()
    if not name or not re.fullmatch(r"[^\W\d_][\w '\-’]*", name, re.UNICODE):
        return None
    return 'My name is ' + name


async def save_name_events(bridge, content: str):
    """Confirm a self-introduction only after MCP confirms a stored note."""
    yield {'type': 'tool', 'query': '[mcp] mcp_memory_dev_save_note'}
    try:
        if bridge is None:
            raise ValueError('MCP is unavailable. Set ENABLE_MCP=1 and restart the app')
        raw = await bridge.call('mcp_memory_dev_save_note', {'content': content, 'tags': ['name', 'profile']})
        outcome = json.loads(raw)
        if not isinstance(outcome, dict) or outcome.get('ok') is not True:
            error = outcome.get('error') if isinstance(outcome, dict) else None
            raise ValueError(error or 'MCP save failed without an error message')
        data = outcome.get('data')
        if not isinstance(data, dict) or not data.get('id') or data.get('content') != content:
            raise ValueError('The memory server did not confirm the saved name')
        reply = save_confirmation(data['content'])
    except Exception as exc:
        from backend.mcp_client_bridge import format_error
        reply = 'I could not confirm your name was saved: ' + format_error(exc)[:500]
    yield {'type': 'delta', 'content': reply}
    yield {'type': 'done', 'sources': [], 'files': [], 'steps': 1}


# These formatters only transform verified tool data; they do not ask a model
# to infer personal facts, and never interpret note content as instructions.
def _names(content: str) -> list[str]:
    match = re.fullmatch(
        r"(?:my name is|your name is|user['’]s name is|the user['’]s name is|call me|i go by)\s+(.+?)[.!]?",
        content.strip(), re.I,
    )
    if match and re.search(r'\b(not|unknown|anonymous)\b', match.group(1), re.I):
        return []
    if match and re.fullmatch(r"[^\W\d_][\w '\-’]{0,99}", match.group(1)):
        return [match.group(1).strip()]
    return []


_LANGUAGES = ('Python', 'JavaScript', 'TypeScript', 'Java', 'C++', 'C#', 'Rust',
              'Go', 'Ruby', 'PHP', 'Swift', 'Kotlin', 'SQL', 'R', 'Scala', 'Dart',
              'English', 'Urdu', 'Arabic', 'French', 'Spanish', 'German', 'Hindi',
              'Punjabi', 'Chinese', 'Japanese', 'Korean', 'Portuguese')


def _language_facts(content: str) -> list[str]:
    # Parse simple positive first-person/user facts only. A random mention of
    # Python, or a negated preference, is not evidence of a language preference.
    match = re.fullmatch(
        r"(?:(?:i|you|user|the user)\s+(?:like|likes|love|loves|prefer|prefers|enjoy|enjoys)|"
        r"(?:my|your|user['’]s|the user['’]s)\s+(?:(?:favou?rite|preferred)\s+)?"
        r"(?:programming\s+)?language\s+is)\s+(.+?)[.!]?",
        content.strip(), re.I,
    )
    if not match:
        return []
    text = match.group(1)
    if re.search(r"\b(not|don['’]t|dislike|hate|avoid|never|no longer)\b", text, re.I):
        return []
    text = re.split(r'\b(?:over|rather than|instead of)\b', text, flags=re.I)[0]
    return [language for language in _LANGUAGES
            if re.search(r'(?<![\w+#])' + re.escape(language) + r'(?![\w+#])', text, re.I)]


def _unique(values):
    out = []
    seen = set()
    for value in values:
        if value.casefold() not in seen:
            seen.add(value.casefold())
            out.append(value)
    return out


def _join(values: list[str]) -> str:
    if len(values) < 2:
        return values[0]
    return ', '.join(values[:-1]) + ' and ' + values[-1]


def natural_reply(notes: list[dict], query: str) -> str:
    if query == 'name':
        names = _unique([name for note in notes for name in _names(note['content'])])
        if len(names) == 1:
            return 'Your name is ' + names[0] + '.'
        if len(names) > 1:
            return 'I have different names saved: ' + _join(names[:5]) + '. Which should I use?'
    if query in {'language', 'prefer'}:
        languages = _unique([language for note in notes for language in _language_facts(note['content'])])
        if languages:
            return 'You like ' + _join(languages[:10]) + '.'
    unique = _unique([note['content'].strip() for note in notes])[:10]
    if not query:
        names = _unique([name for note in notes for name in _names(note['content'])])
        languages = _unique([language for note in notes for language in _language_facts(note['content'])])
        recognized = [note for note in notes if _names(note['content']) or _language_facts(note['content'])]
        if len(recognized) == len(notes) and len(names) <= 1:
            parts = (['Your name is ' + names[0] + '.'] if names else [])
            if languages:
                parts.append('You like ' + _join(languages[:10]) + '.')
            return ' '.join(parts)
    return 'You told me:\n\n' + '\n'.join('- ' + content[:1000] for content in unique)


def save_confirmation(content: str) -> str:
    names = _names(content)
    if names:
        return "I'll remember your name is " + names[0] + '.'
    languages = _language_facts(content)
    if languages:
        return "I'll remember that you like " + _join(languages[:10]) + '.'
    return "I'll remember this: " + content[:500]
