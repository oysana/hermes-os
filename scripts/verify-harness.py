#!/usr/bin/env python3
"""Verify installed persona composition and per-profile hook delivery without an LLM or messages.

Run with: python3 ~/.hermes/shared/proactive/run-selected.py ~/.hermes/shared/proactive/verify-harness.py
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path.home() / '.hermes'


def alias_checks(contexts):
    """Check each supplied harness context; this proves context coverage, not model obedience."""
    patterns={
        'H':r'H/h\s*=\s*default/hqecea\s*\(work and T3\)',
        'S':r'S/s\s*=\s*sqecea\s*\(all education\)',
        'Y':r'Y/y\s*=\s*yqecea\s*\(personal life\)',
    }
    result={}
    for name,text in contexts.items():
        checks={alias:bool(re.search(pattern,text)) for alias,pattern in patterns.items()}
        result[name]={'ok':all(checks.values()),'aliases':checks,'scope':'context only; model behavior unmeasured'}
    return result


def nonsecret_manifest(home=Path.home()):
    """Allowlisted settings keys, SKILL digests and history stat; no values or history bodies."""
    manifest={'schema':1,'settings_keys':{},'skill_hashes':{},'history_metadata':{}}
    claude=home/'.claude'
    def keys(value,prefix=''):
        result=[]
        if isinstance(value,dict):
            for key,child in sorted(value.items()):
                path=prefix+'.'+key if prefix else key
                result.append(path);result.extend(keys(child,path))
        elif isinstance(value,list):
            for child in value:result.extend(keys(child,prefix+'[]'))
        return sorted(set(result))
    for name in ('settings.json','settings.local.json'):
        path=claude/name
        if path.is_file():manifest['settings_keys'][str(path.relative_to(home))]=keys(json.loads(path.read_text()))
    for path in sorted((claude/'skills').glob('*/SKILL.md')):
        if path.is_file():manifest['skill_hashes'][str(path.relative_to(home))]=hashlib.sha256(path.read_bytes()).hexdigest()
    histories=[claude/'history.jsonl',*sorted((claude/'projects').glob('**/*.jsonl'))]
    for path in histories:
        if path.is_file():
            stat=path.stat()
            manifest['history_metadata'][str(path.relative_to(home))]={'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}
    return manifest


def compare_manifests(before,after):
    if before is None:return {'ok':False,'status':'NO_BASELINE','scope':'No historical preservation claim'}
    if before.get('schema')!=1 or after.get('schema')!=1:raise ValueError('Expected nonsecret manifest schema 1')
    changed={};missing={};added={}
    for section in ('settings_keys','skill_hashes','history_metadata'):
        old,new=before[section],after[section]
        changed[section]=sorted(k for k in old.keys() & new.keys() if old[k]!=new[k])
        missing[section]=sorted(old.keys()-new.keys());added[section]=sorted(new.keys()-old.keys())
    return {'ok':not any(changed.values()) and not any(missing.values()),'status':'COMPARED',
            'changed':changed,'missing':missing,'added':added,
            'scope':'Settings key sets, SKILL SHA256 and history size/mtime only; not settings values, history content or auth preservation'}


def named_harness_contexts(home=Path.home()):
    paths={'Codex':home/'.codex/AGENTS.md','Claude Code':home/'.claude/CLAUDE.md','Gemini':home/'.gemini/GEMINI.md'}
    return {name:path.read_text() if path.is_file() else '' for name,path in paths.items()}


def verify_runtime(persona_root=None):
    import yaml  # runtime-only dependency; offline modes stay standard-library
    from agent.prompt_builder import load_soul_md
    contexts={}

    persona_root = Path(persona_root) if persona_root else ROOT
    print('Persona source:', persona_root, '(staged)' if persona_root != ROOT else '(installed)')
    character = (persona_root / 'shared/CHARACTER.md').read_text().rstrip()
    hook = ROOT / 'shared/proactive/turn-brief.py'
    for profile, home in [('default', ROOT), ('yqecea', ROOT / 'profiles/yqecea'),
                          ('sqecea', ROOT / 'profiles/sqecea')]:
        persona_home = persona_root if profile == 'default' else persona_root / 'profiles' / profile
        own = persona_home / 'CHARACTER.md'  # build-soul.sh: a profile's own CHARACTER.md replaces the shared one
        own_character = own.read_text().rstrip() if own.exists() else character
        expected = own_character + '\n\n' + (persona_home / 'SOUL.profile.md').read_text().rstrip() + '\n'
        assert (persona_home / 'SOUL.md').read_text() == expected, f'{profile}: stale generated SOUL'
        loaded = load_soul_md(home_override=persona_home)
        assert loaded and expected.strip() in loaded, f'{profile}: runtime persona missing or truncated'
        config = yaml.safe_load((home / 'config.yaml').read_text())
        hooks = config.get('hooks') or {}
        if profile != 'default':
            # 16.11.2026 ledger switch: Y and S are pure Hermes, no shell hooks at all.
            assert not any(hooks.values()), f'{profile}: Y/S must run no shell hooks, found {sorted(k for k, v in hooks.items() if v)}'
            print(f'{profile}: exact composed SOUL, native identity loader, no shell hooks PASS')
            continue
        # 16.11.2026 H ledger switch: turn-brief is no longer wired in H either; H keeps only
        # its no-self-verify guard (retire-old-path). Its brief must not come back.
        commands = [entry['command'] for entry in hooks.get('pre_llm_call') or []]
        assert 'python3 ' + str(hook) not in commands, f'{profile}: turn-brief is wired again'
        print(f'{profile}: exact composed SOUL, native identity loader, turn-brief not wired PASS')
    contexts.update(named_harness_contexts())
    checks=alias_checks(contexts)
    print(json.dumps({'alias_checks':checks},ensure_ascii=False,indent=2))
    print('No model call or outbound message. These checks prove configuration and hook execution, not model compliance.')
    return all(c['ok'] for c in checks.values())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--persona-root', type=Path, default=os.environ.get('HERMES_VERIFY_PERSONA_ROOT'),
                        help='Explicit staged persona source; hook wiring remains installed')
    parser.add_argument('--manifest-out',type=Path)
    parser.add_argument('--baseline',type=Path,help='Pre-change nonsecret manifest; absent baseline stays unverified')
    parser.add_argument('--manifest-only',action='store_true',help='No Hermes bootstrap, hook, model or message execution')
    parser.add_argument('--aliases-only',action='store_true',help='Check named harness instruction files without runtime execution')
    args=parser.parse_args()
    if args.manifest_only or args.manifest_out or args.baseline:
        manifest=nonsecret_manifest()
        if args.manifest_out:args.manifest_out.write_text(json.dumps(manifest,indent=2)+'\n')
        before=json.loads(args.baseline.read_text()) if args.baseline else None
        comparison=compare_manifests(before,manifest)
        print(json.dumps({'manifest_comparison':comparison},indent=2))
        if args.manifest_only:return 0 if comparison['ok'] else 1
    if args.aliases_only:
        checks=alias_checks(named_harness_contexts())
        print(json.dumps({'alias_checks':checks},indent=2))
        return 0 if all(c['ok'] for c in checks.values()) else 1
    return 0 if verify_runtime(args.persona_root) else 1


if __name__ == '__main__':
    raise SystemExit(main())
