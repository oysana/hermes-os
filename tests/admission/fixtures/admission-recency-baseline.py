"""Authorized read-only metadata projection; never return job/session bodies."""
from datetime import datetime,timezone,timedelta
import json,sqlite3
from pathlib import Path
from gateway.status import read_runtime_status,runtime_status_is_stale,runtime_status_pid_is_live,profile_platforms_from_multiplexer
from hermes_state_common import _sql_session_last_active
root=Path.home()/'.hermes';now=datetime.now(timezone.utc)
state=read_runtime_status() or {}
record={'captured_utc':now.isoformat(),'gateway_state':state.get('gateway_state'),'active_agents':state.get('active_agents'),'pid':state.get('pid'),'fresh':not runtime_status_is_stale(state),'pid_live':runtime_status_pid_is_live(state),'restart_requested':state.get('restart_requested'),'served_profiles':state.get('served_profiles'),'profiles':{},'full_cli':'NOT_RUN: status full reads auth/session files; cron list exposes prompts','store_scope':'jobs JSON parsed read-only in process; only id/enabled/next_run_at/state fields accessed and projected; prompt values not inspected. Session SQL selects aggregate metadata only.'}
blocked=[]
if not (record['gateway_state']=='running' and record['active_agents']==0 and record['fresh'] and record['pid_live'] and record['restart_requested'] is False):blocked.append('gateway not freshly idle')
for profile,home in [('default',root),('sqecea',root/'profiles/sqecea'),('yqecea',root/'profiles/yqecea')]:
    try:
        if profile not in (record['served_profiles'] or []):raise ValueError('profile not served')
        platforms={name:entry.get('state') for name,entry in profile_platforms_from_multiplexer(state,profile).items()}
        # A profile with zero connected platforms has no live ingress at all:
        # admit it as idle-after-checks rather than "platform not ready". A
        # platform in a persistent 'fatal' state (needs_attention, retrying for
        # >5min — e.g. a logged-out personal watcher awaiting human pairing)
        # likewise cannot suddenly start delivering mid-mutation; record it,
        # but do not block. Transient states (starting/connecting/recent retry)
        # still block, as do missing platforms on served profiles that normally
        # report them.
        if not platforms:
            record.setdefault('profiles_no_live_ingress',[]).append(profile)
        else:
            not_ready=[]
            for _pn,_pe in profile_platforms_from_multiplexer(state,profile).items():
                _st=_pe.get('state')
                if _st=='connected':continue
                if _st=='fatal' and _pe.get('needs_attention'):
                    _rs=_pe.get('retrying_since')
                    try:
                        _age=now.timestamp()-datetime.fromisoformat(_rs.replace('Z','+00:00')).timestamp() if _rs else 0
                    except Exception:
                        _age=0
                    if _rs is None or _age>300:
                        record.setdefault('profiles_platform_fatal_accepted',[]).append(profile+':'+_pn)
                        continue
                not_ready.append(_pn+'='+str(_st))
            if not_ready:raise ValueError('profile platform not ready: '+','.join(not_ready))
        for file in [home/'state.db',home/'cron/executions.db',home/'cron/jobs.json']:
            if not file.is_file():raise ValueError('required metadata store missing')
        with sqlite3.connect(f'file:{home}/state.db?mode=ro',uri=True,timeout=5) as db:
            count,last=db.execute(f'''SELECT COUNT(*),MAX({_sql_session_last_active('s')}) FROM sessions s
                WHERE s.session_key IS NOT NULL AND s.started_at=(SELECT MAX(s2.started_at) FROM sessions s2 WHERE s2.session_key=s.session_key) AND s.ended_at IS NULL''').fetchone()
        seconds_since_activity=now.timestamp()-float(last) if last is not None else None
        if count and (seconds_since_activity is None or seconds_since_activity<120):raise ValueError('last session activity less than two minutes ago or unknown')
        with sqlite3.connect(f'file:{home}/cron/executions.db?mode=ro',uri=True,timeout=5) as db:
            active=db.execute("SELECT id,job_id,status FROM executions WHERE status IN ('claimed','running')").fetchall()
        payload=json.loads((home/'cron/jobs.json').read_text(encoding='utf-8-sig'))
        jobs=payload.get('jobs') if isinstance(payload,dict) else None
        if not isinstance(jobs,list):raise ValueError('job metadata shape invalid')
        projected=[];overdue=[]
        for job in jobs:
            if not isinstance(job,dict):raise ValueError('job metadata row invalid')
            meta={key:job.get(key) for key in ['id','enabled','next_run_at','state']};meta['enabled']=job.get('enabled',True)
            if not isinstance(meta['id'],str) or not isinstance(meta['enabled'],bool):raise ValueError('job metadata fields invalid')
            if meta['enabled']:
                stamp=meta['next_run_at']
                if not isinstance(stamp,str):raise ValueError('enabled job lacks next run metadata')
                due=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if due.tzinfo is None:raise ValueError('next run metadata lacks timezone')
                if due<=now:overdue.append(meta['id'])
            projected.append(meta)
        item={'platform_states':platforms,'open_gateway_session_metadata_count':count,'seconds_since_last_activity':seconds_since_activity,'active_cron_count':len(active),'active_cron_ids':[row[0] for row in active],'jobs':projected,'enabled_overdue_or_due_now_ids':overdue}
        record['profiles'][profile]=item
        if active or overdue:blocked.append(profile+': cron active or overdue/due-now')
    except Exception as error:
        record['profiles'][profile]={'metadata_error_type':type(error).__name__}
        blocked.append(profile+': metadata/readiness gate failed')
record['blocked_reasons']=blocked;record['idle_admitted']=not blocked
print(json.dumps(record,sort_keys=True))
if blocked:raise SystemExit(1)
