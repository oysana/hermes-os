"""Use the gateway's own startup and turn hooks in the H profile."""
import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from agent.estop import is_engaged
from gateway import run_api
from gateway.config import ChannelOverride, Platform
from gateway.config_loader import merge_platform_sections, platform_section
from gateway.run import _load_gateway_config
from gateway.run_active_work_verb import _profile_units
from gateway.session_authorities import active_authority, owner_scope, served_profile_name
from gateway.session_automation import route_owner_lock
from gateway.slash_commands_model import _ModelSwitchContext
from gateway.slash_commands_session import discard_lost_turns
from hermes_constants import get_hermes_home

OWNER = 'OWNER_CHAT_ID'
NOTIFY_TOPIC = '9267'
STATUS_AFTER = 300
STATUS_POLL = 60
REVISION_KEY = 'owner_continuity_config_route'


def owner_route(runner, entry, home):
    source = entry.origin
    return (
        source is not None
        and source.platform.value == 'telegram'
        and source.chat_type == 'dm'
        and str(source.chat_id) == str(source.user_id) == OWNER
        and not source.is_bot
        and runner._resolve_profile_home_for_source(source).resolve() == home
        and runner._is_user_authorized_for_source(source)
    )


def refresh_topic_route(runner, source, config):
    """Refresh the owner's exact topic in the loaded adapter policy."""
    platform = runner.config.platforms.get(Platform.TELEGRAM)
    if platform is None or not source.thread_id:
        return
    gateway = config.get('gateway')
    merged = merge_platform_sections(config, gateway, {}).get('telegram', {})
    section, _ = platform_section(config, 'telegram', gateway.get('platforms') if isinstance(gateway, dict) else None)
    overrides = merged.get('channel_overrides')
    if isinstance(section, dict) and isinstance(section.get('channel_overrides'), dict):
        overrides = section['channel_overrides']
    if not isinstance(overrides, dict):
        return
    topic = str(source.thread_id)
    authored = {str(key): value for key, value in overrides.items()}.get(topic)
    current = platform.channel_overrides.get(topic)
    if authored is not None and not isinstance(authored, dict):
        return
    if authored is None and current is None:
        return
    route = ChannelOverride.from_dict(authored or {})
    entry = replace(current or ChannelOverride(), model=route.model, provider=route.provider)
    if entry.to_dict():
        platform.channel_overrides[topic] = entry
    else:
        platform.channel_overrides.pop(topic, None)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


async def follow_config(runner, entry, home, *, reset=True):
    store = runner.async_session_store
    key = entry.session_key
    path = home / 'config.yaml'
    async with route_owner_lock(runner, entry.session_id), runner._model_switch_lock():
        if await store.peek_session_id(key) != entry.session_id:
            return
        config = await asyncio.to_thread(_load_gateway_config, path)
        if not config.get('model'):
            return
        refresh_topic_route(runner, entry.origin, config)
        route = await asyncio.to_thread(runner._channel_default_route, entry.origin, path)
        if not route[0]:
            return
        revision = fingerprint(route)
        state = runner._peek_session_state(key)
        if state is not None and state.conversation.one_turn_restore is not None:
            return
        previous = await store.get_session_metadata(key, REVISION_KEY, {})
        override = await store.get_model_override(key)
        current = {'config': revision, 'override': fingerprint(override)}
        if previous == current:
            return
        if override and previous.get('config') != revision and previous.get('override') == current['override']:
            if not reset:
                return
            ctx = _ModelSwitchContext(session_key=key, source=entry.origin, config_path=path, persist_global=False)
            await runner._handle_model_reset(None, ctx)
            if await store.get_model_override(key):
                return
            current['override'] = fingerprint(None)
        await store.set_session_metadata(key, REVISION_KEY, current)


async def restore_owner_routes(runner, authority, home):
    entries = await runner.async_session_store.list_sessions()
    pending = await asyncio.to_thread(
        authority.db._read_all,
        "SELECT DISTINCT target_session_id FROM session_admissions WHERE status='unknown'",
    )
    lost = {row['target_session_id'] for row in pending}
    bindings = []
    for entry in entries:
        if not owner_route(runner, entry, home):
            continue
        await follow_config(runner, entry, home, reset=False)
        sid = authority.logical_owner(entry.session_id)
        if sid in lost:
            adapter = runner._adapter_for_source(entry.origin)
            if adapter is not None:
                bindings.append((sid, entry.origin, adapter))
    for sid, verdict in (await authority.recover_native_sessions(bindings)).items():
        if verdict == 'unknown_execution' and not is_engaged():
            await discard_lost_turns(authority, sid)


def goal_progress(now):
    from hermes_cli.kanban_db import kanban_db_path

    uri = kanban_db_path(board='goals').resolve().as_uri() + '?mode=ro'
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as conn:
            row = conn.execute(
                "SELECT MIN(started_at) FROM tasks WHERE status = 'running'"
            ).fetchone()
    except sqlite3.Error:
        return None
    return None if row[0] is None else now - row[0]


async def report_progress(runner, profile, reported):
    goal_age = await asyncio.to_thread(goal_progress, time.time())
    units = [
        unit for unit in _profile_units(runner, profile, runner._describe_active_work())
        if unit['kind'] in {'chat', 'managed', 'side_agent'}
    ]
    if not units and goal_age is None:
        return False
    long_goal = goal_age is not None and goal_age >= STATUS_AFTER
    if reported or not (long_goal or any(unit.get('elapsed_s', 0) >= STATUS_AFTER for unit in units)):
        return reported
    adapter = runner.adapters.get(Platform.TELEGRAM)
    if adapter is None or is_engaged():
        return False
    source = adapter.build_source(chat_id=OWNER, chat_type='dm', user_id=OWNER, thread_id=NOTIFY_TOPIC)
    if not runner._is_user_authorized_for_source(source):
        return False
    status = ('Задача в T3 ещё в работе. Результат пока не готов.' if long_goal else
              'H ещё работает. Результат пока не готов.')
    sent = await adapter.send(OWNER, status, metadata={'thread_id': NOTIFY_TOPIC})
    return sent.success


async def watch_progress(runner, authority, restore_after_pause):
    reported = False
    while runner._running and not authority.retiring:
        await asyncio.sleep(STATUS_POLL)
        if runner._running and not runner._draining and not authority.retiring:
            with owner_scope(authority):
                if is_engaged():
                    continue
                if restore_after_pause:
                    await restore_owner_routes(runner, authority, Path(authority.profile_id))
                    restore_after_pause = is_engaged()
                reported = await report_progress(runner, served_profile_name(authority.profile_id), reported)


async def handle(event_type, context):
    runner = run_api._ACTIVE_RUNNER
    authority = active_authority(runner) if runner is not None else None
    home = get_hermes_home().resolve()
    if authority is None or Path(authority.profile_id).resolve() != home:
        return
    if event_type == 'gateway:startup':
        paused = is_engaged()
        if not paused:
            await restore_owner_routes(runner, authority, home)
            paused = is_engaged()
        if runner._running:
            runner._spawn_supervised(lambda: watch_progress(runner, authority, paused), 'owner-progress')
    elif (not is_engaged() and context.get('platform') == 'telegram'
          and str(context.get('user_id')) == str(context.get('chat_id')) == OWNER):
        entry = await runner.async_session_store.lookup_by_session_id(context.get('session_id'))
        if entry is not None and owner_route(runner, entry, home):
            await follow_config(runner, entry, home)
