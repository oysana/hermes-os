"""Current T3 wait binding and native task controls shared by the hook and return sweep."""
import re

T3_META_FIELD = re.compile(r'(?m)^(T3-(?:Thread|Run|State)):\s*(\S+)\s*$')
T3_THREAD_ID = re.compile(r'\b((?:mcp|thread|node):[0-9A-Za-z%:_-]{8,})\b')
T3_RUN_ID = re.compile(r'\b(run:thread:[0-9A-Za-z%:_-]{8,})\b')
T3_WAIT_INTENT = re.compile(r'\bwait\w*\b|ожида\w*|жд(?:ёт|ет|ем|ём|ут|ите)\w*', re.I)
_ORDINARY_THREAD = re.compile(r'(?:(?:mcp|thread):)?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.I)
_HOLD = re.compile(r'(?:стоп|stop|пауза|pause|не продолжать(?: работу)?)[.!\s]*(?:не продолжать(?: работу)?[.!\s]*)?', re.I)
_RESUME = re.compile(r'(?:продолжай|продолжить|возобнови(?: работу)?|resume|continue)[.!\s]*', re.I)
_CONTROL = re.compile(r'T3-Control:\s*(hold|resume)', re.I)


def extract_t3_binding(body):
    fields = {match[1]: match[2] for match in T3_META_FIELD.finditer(body or '')}
    if fields.get('T3-Thread'):
        return {'thread': fields['T3-Thread'], 'run': fields.get('T3-Run', ''),
                'state': (fields.get('T3-State') or 'waiting').casefold()}
    if T3_WAIT_INTENT.search(body or ''):
        thread = T3_THREAD_ID.search(body)
        if thread:
            run = T3_RUN_ID.search(body)
            return {'thread': thread[1], 'run': run[1] if run else '', 'state': 'waiting'}
    return None


def resumable_t3_wait(card):
    if (card.get('status') != 'blocked' or card.get('assignee', 'default') not in ('default', 'hqecea')
            or card.get('block_kind') not in (None, 'transient') or card.get('current_run_id')):
        return None
    binding = extract_t3_binding(card.get('body') or '')
    if binding and binding['state'] in ('waiting', 'pending'):
        return binding
    return None


def ordinary_thread(thread):
    return isinstance(thread, str) and _ORDINARY_THREAD.fullmatch(thread) is not None


def owner_control(comments):
    """Fold explicit card controls in ID order; author labels are not authentication."""
    control = None
    for comment in comments:
        text = (comment.get('body') or '').strip()
        marker = _CONTROL.fullmatch(text)
        if marker:
            control = marker[1].casefold()
        elif _HOLD.fullmatch(text):
            control = 'hold'
        elif comment.get('author') == 'user' and _RESUME.fullmatch(text):
            control = 'resume'
    return control
