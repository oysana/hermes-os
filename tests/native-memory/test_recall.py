"""Public recall contracts against the installed native Hermes memory engine."""
import json
from datetime import date, timedelta

import pytest
from hermes_state import SessionDB
from tools.session_search_tool import session_search

@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(tmp_path / "state.db")
    yield db
    db.close()

# Known upstream defects, dated. Each mark expires 30 days after `since`, so the test turns red again
# instead of hiding the bug for good; strict=True turns it red as soon as upstream fixes it.
KNOWN_UPSTREAM_DAYS = 30
CRON_FLOOD = dict(since=date(2026, 11, 16), reason=(
    'upstream tools/session_search_tool.py _DISCOVER_SCAN_LIMIT=300: 301 newer cron rows push the '
    "owner's sessions out of the scan window; issue draft .artifacts/wave2-20261116/checks-repo/UPSTREAM-ISSUE.md"))


def known_upstream(since, reason, today=None):
    if (today or date.today()) > since + timedelta(days=KNOWN_UPSTREAM_DAYS):
        return pytest.mark.usefixtures()
    return pytest.mark.xfail(strict=True, reason=f'known upstream since {since}: {reason}')


def test_known_upstream_mark_expires_after_thirty_days():
    since = date(2026, 11, 16)
    assert known_upstream(since, 'x', today=since + timedelta(days=30)).name == 'xfail'
    assert known_upstream(since, 'x', today=since + timedelta(days=31)).name == 'usefixtures'


def seed(db, sid, source, text, timestamp=100):
    db.create_session(sid, source=source)
    return db.append_message(sid, role='assistant', content=text, timestamp=timestamp)

@pytest.mark.parametrize('query,partial,exact', [
    ('alpha beta', 'alpha gamma', 'alpha beta'),
    ('cat', 'concatenate fixture', 'cat fixture'),
])
def test_global_exact_match_prevents_class_local_relaxation(history, query, partial, exact):
    seed(history, 'public-partial', 'telegram', partial)
    seed(history, 'public-exact', 'cron', exact)
    after = json.loads(session_search(query=query, db=history))
    assert [r['session_id'] for r in after['results']] == ['public-exact']

@known_upstream(**CRON_FLOOD)
@pytest.mark.parametrize('archived', [False, True])
def test_prior_revisions_survive_cron_flood(history, archived):
    seed(history, 'public-v1', 'telegram', 'letter revision .artifacts/public-letter-v1.md')
    seed(history, 'public-v2', 'telegram', 'letter revision .artifacts/public-letter-v2.md')
    if archived:
        history.archive_and_compact('public-v1', [{'role':'assistant','content':'compact summary'}])
    history.create_session('public-cron', source='cron')
    for i in range(301):
        history.append_message('public-cron', role='assistant', content='letter revision automation', timestamp=1000+i)
    after = json.loads(session_search(query='letter revision', sort='newest', db=history))
    assert {'public-v1','public-v2'} <= {r['session_id'] for r in after['results']}
    assert '.artifacts/public-letter-v1.md' in json.dumps(after)
    assert '.artifacts/public-letter-v2.md' in json.dumps(after)

@pytest.mark.parametrize('source', ['cron','subagent','tool'])
def test_source_visibility_is_preserved(history, source):
    seed(history, 'public-source', source, 'exclusive fixture')
    result = json.loads(session_search(query='exclusive fixture', db=history))
    assert result['success']
    assert result['count'] == (1 if source=='cron' else 0)

def test_native_bm25_order_inside_source_class_is_preserved(history):
    seed(history, 'public-a', 'telegram', 'alpha beta alpha beta gamma')
    seed(history, 'public-b', 'telegram', 'alpha beta gamma delta epsilon')
    seed(history, 'public-automation', 'cron', 'alpha beta')
    native_order = [r['session_id'] for r in history.search_messages(query='alpha beta', exclude_sources=['cron'], fields=['session_id'])]
    after = json.loads(session_search(query='alpha beta', db=history))
    assert [r['session_id'] for r in after['results'] if r['source']=='telegram'] == native_order

@pytest.mark.parametrize('query,text', [('alpha beta','alpha gamma'), ('cat','concatenate fixture')])
def test_native_relaxation_remains_available_on_global_miss(history, query, text):
    seed(history, 'public-fallback', 'telegram', text)
    result = json.loads(session_search(query=query, db=history))
    assert [r['session_id'] for r in result['results']] == ['public-fallback']

@known_upstream(**CRON_FLOOD)
def test_stale_index_fallback_prioritizes_before_limit(history):
    seed(history, 'public-v1', 'telegram', 'letter revision .artifacts/public-letter-v1.md')
    history.create_session('public-cron', source='cron')
    for i in range(301):
        history.append_message('public-cron', role='assistant', content='letter revision automation', timestamp=1000+i)
    history._fts_stale = True
    result = json.loads(session_search(query='letter revision', sort='newest', db=history))
    assert result['results'][0]['session_id']=='public-v1'
