#!/usr/bin/env python3
"""Offline checks for the HappyHorse-only Seta video chain (2026-11-10, Tom: "set up happy horse").

Run by test_seta_linkedin.py with the LinkedIn venv. Every HTTP call is mocked - no paid generation ever runs here.
Prints one line per case: PASS|FAIL <name> [detail]. Expected results were fixed before the first run.
"""
import os, sys, tempfile, types
from pathlib import Path
from unittest import mock

import requests  # noqa: F401  (patched below)
from linkedin_generation.social import image_providers as ip
import linkedin_generation.seta_post_scheduler as sched

results = []
def check(name, ok, detail=''):
    results.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f' [{detail}]' if detail and not ok else ''))

class Resp:
    def __init__(self, status=200, js=None, content=b''):
        self.status_code = status; self._js = js or {}; self.content = content; self.text = str(js)
    @property
    def ok(self): return 200 <= self.status_code < 300
    def json(self): return self._js
    def raise_for_status(self):
        if not self.ok: raise RuntimeError(f'HTTP {self.status_code}')

calls = []
BODIES = []
def fake_post(url, **kw): calls.append(('POST', url, kw.get('headers', {}))); BODIES.append(kw.get('json') or {}); return POST_RESP[0]
def fake_get(url, **kw): calls.append(('GET', url, kw.get('headers', {}))); return GET_RESP.pop(0)
POST_RESP = [None]; GET_RESP = []

pillar = types.SimpleNamespace(name='Cross-Border M&A', video_prompt='16:9 boardroom', angle='deals')
post = types.SimpleNamespace(video_prompt='16:9 engineers inspecting a production line')
tmp = Path(tempfile.mkdtemp())

def run_gen():
    return sched.generate_video_for_post(campaign=None, post=post, pillar=pillar, target_dir=tmp)

with mock.patch('requests.post', fake_post), mock.patch('requests.get', fake_get), mock.patch('time.sleep', lambda s: None):
    env = {k: v for k, v in os.environ.items() if k not in ('DASHSCOPE_API_KEY', 'ALIBABA_TOKEN_PLAN_KEY', 'SETA_VIDEO_ACCOUNT')}
    TP = 'https://token-plan.ap-southeast-1.maas.aliyuncs.com/'
    PG = 'https://dashscope-intl.aliyuncs.com/'
    # 1. no key at all -> VideoKeyMissing naming the Token Plan key (default account, Tom 2026-11-10), no HTTP call
    with mock.patch.dict(os.environ, env, clear=True):
        calls.clear()
        try: run_gen(); check('key missing -> raises', False, 'returned a path')
        except ip.VideoKeyMissing as e: check('key missing -> raises VideoKeyMissing naming ALIBABA_TOKEN_PLAN_KEY', 'ALIBABA_TOKEN_PLAN_KEY' in str(e), str(e))
        check('key missing -> no HTTP call made', not calls, calls)
    # 2. payg chosen but only the Token Plan key -> refused, no HTTP call (never silently switches account)
    with mock.patch.dict(os.environ, {**env, 'ALIBABA_TOKEN_PLAN_KEY': 'tp-key', 'SETA_VIDEO_ACCOUNT': 'payg'}, clear=True):
        calls.clear()
        try: run_gen(); check('payg without DASHSCOPE key -> refused', False)
        except ip.VideoKeyMissing as e: check('payg without DASHSCOPE key -> refused, no call', not calls and 'DASHSCOPE_API_KEY' in str(e), str(e))
    # 2b. unknown account name -> error, no call
    with mock.patch.dict(os.environ, {**env, 'ALIBABA_TOKEN_PLAN_KEY': 'tp-key', 'SETA_VIDEO_ACCOUNT': 'typo'}, clear=True):
        calls.clear()
        try: run_gen(); check('unknown account -> raises', False)
        except ValueError as e: check('unknown account -> raises ValueError, no call', not calls and 'typo' in str(e), str(e))
    keyed = {**env, 'DASHSCOPE_API_KEY': 'payg-key', 'ALIBABA_TOKEN_PLAN_KEY': 'tp-key'}
    for code, msg in ((403, 'AccessDenied.Unpurchased'), (429, 'insufficient_quota')):
        with mock.patch.dict(os.environ, keyed, clear=True):
            calls.clear(); POST_RESP[0] = Resp(code, {'code': msg})
            try: run_gen(); check(f'{code} -> raises', False)
            except Exception as e: check(f'{code} -> raises with the status in the reason', str(code) in str(e) and msg in str(e), str(e))
    # 3. success on each account -> mp4 saved, only that account's endpoint and key
    for acc, base, key in (('', TP, 'tp-key'), ('payg', PG, 'payg-key')):
        name = acc or 'default(token_plan)'
        with mock.patch.dict(os.environ, {**keyed, **({'SETA_VIDEO_ACCOUNT': acc} if acc else {})}, clear=True):
            calls.clear()
            POST_RESP[0] = Resp(200, {'output': {'task_id': 't1'}})
            GET_RESP[:] = [Resp(200, {'output': {'task_status': 'RUNNING'}}),
                           Resp(200, {'output': {'task_status': 'SUCCEEDED', 'video_url': 'https://dashscope-result-sgp.oss.aliyuncs.com/x.mp4'}}),
                           Resp(200, content=b'MP4DATA')]
            p = run_gen()
            check(f'{name}: success -> mp4 saved', p.suffix == '.mp4' and p.read_bytes() == b'MP4DATA', p)
            api = [c for c in calls if 'oss.aliyuncs.com' not in c[1]]
            check(f'{name}: only {base} endpoints', api and all(c[1].startswith(base) for c in api), [c[1] for c in calls])
            check(f'{name}: never Google Veo or Replicate', not any(('googleapis' in c[1] or 'replicate' in c[1]) for c in calls))
            check(f'{name}: uses {key}', all(c[2].get('Authorization') == f'Bearer {key}' for c in api), [c[2].get('Authorization') for c in api])
            check(f'{name}: watermark off, 16:9, happyhorse-1.1-t2v', BODIES[-1].get('model') == 'happyhorse-1.1-t2v' and BODIES[-1]['parameters'] == {'watermark': False, 'ratio': '16:9'}, BODIES[-1])
    # 4. task FAILED -> raises with the message
    with mock.patch.dict(os.environ, keyed, clear=True):
        POST_RESP[0] = Resp(200, {'output': {'task_id': 't2'}})
        GET_RESP[:] = [Resp(200, {'output': {'task_status': 'FAILED', 'message': 'content policy'}})]
        try: run_gen(); check('task FAILED -> raises', False)
        except Exception as e: check('task FAILED -> raises with the message', 'content policy' in str(e), str(e))

with mock.patch.dict(os.environ, {'SETA_VIDEO_ACCOUNT': 'payg'}):
    check('payg cost line: 5 s x CNY 1.6 = CNY 8.00', 'CNY 8.00' in ip.AlibabaVideoProvider.cost_estimate(), ip.AlibabaVideoProvider.cost_estimate())
with mock.patch.dict(os.environ, {'SETA_VIDEO_ACCOUNT': 'token_plan'}):
    check('token plan cost line says credits, no CNY', 'credits' in ip.AlibabaVideoProvider.cost_estimate() and 'CNY' not in ip.AlibabaVideoProvider.cost_estimate())

# 5. Tom is told, through the shared queue, never raising
seen = {}
def fake_run(cmd, **kw):
    seen['cmd'] = cmd; return types.SimpleNamespace(returncode=0, stderr=b'')
with mock.patch('subprocess.run', fake_run):
    ok = sched.notify_tom_video_fallback('Cross-Border M&A', 'HappyHorse submit failed (403): AccessDenied')
check('fallback note -> queued via tnt_mailer.queue_mail', ok and 'queue_mail' in seen['cmd'][2], seen.get('cmd'))
check('fallback note -> names pillar and reason in one line', 'Cross-Border M&A' in seen['cmd'][3] and '403' in seen['cmd'][3] and '\n' not in seen['cmd'][3], seen['cmd'][3])
with mock.patch('subprocess.run', side_effect=OSError('boom')):
    check('fallback note -> never raises when the queue is down', sched.notify_tom_video_fallback('X', 'y') is False)

# 6. the fallback block calls the note BEFORE the image fallback
src = Path(sched.__file__).read_text()
blk = src[src.index('    elif pillar.use_veo:'):src.index('# Non-Veo, non-chart pillar')]
check('fallback block -> Tom told, then animated image', 0 < blk.index('notify_tom_video_fallback') < blk.index('generate_image_for_post'))
check('scheduler has no Veo/Replicate video step', 'ReplicateVideoProvider' not in src and 'veo-3.1' not in src)

print(f'HHVIDEO {sum(ok for _, ok in results)}/{len(results)}')
