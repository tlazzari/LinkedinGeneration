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

import json
from linkedin_generation.social import registry_media as rm, model_policy as mp

class Resp:
    def __init__(self, status=200, js=None, content=b''):
        self.status_code=status; self.content=content or json.dumps(js or {}).encode()
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(f'HTTP {self.status_code}: {self.content.decode()}')
    def iter_content(self, chunk_size): yield self.content
    def close(self): pass

calls=[]; BODIES=[]; POST_RESP=[None]; GET_RESP=[]; ROWS=[]; KEYS={}
def fake_post(url, **kw):
    assert kw.get('allow_redirects') is False and kw.get('stream') is True
    calls.append(('POST',url,kw.get('headers',{}))); BODIES.append(kw.get('json') or {})
    return POST_RESP[0]
def fake_get(url, **kw):
    assert kw.get('allow_redirects') is False and kw.get('stream') is True
    calls.append(('GET',url,kw.get('headers',{}))); return GET_RESP.pop(0)
def chain(function, *, account_scope='company'):
    assert function == 'social_video_seta' and account_scope == 'company'
    return ROWS

def selected_row(base, cred):
    return dict(owner_protocol='alibaba_video', model='happyhorse-1.1-t2v',
                base_url=base.rstrip('/'), cred=cred, timeout_s=60, options={'ratio':'16:9'})
pillar=types.SimpleNamespace(name='Cross-Border M&A',video_prompt='16:9 boardroom',angle='deals')
post=types.SimpleNamespace(video_prompt='16:9 engineers inspecting a production line')
campaign=types.SimpleNamespace(image_provider=ip.ImageProviderConfig())
_tmp=tempfile.TemporaryDirectory(); tmp=Path(_tmp.name)
sequence=[0]
def run_gen(target=None):
    sequence[0]+=1
    return sched.generate_video_for_post(campaign=campaign, post=post, pillar=pillar,
                                        target_dir=target or tmp/str(sequence[0]))

with mock.patch('requests.post',fake_post), mock.patch('requests.get',fake_get), \
     mock.patch('time.sleep',lambda s:None), mock.patch.object(rm,'owner_chain',side_effect=chain), \
     mock.patch.object(rm,'owner_key',side_effect=lambda r:KEYS.get(r['cred'])), \
     mock.patch.object(mp,'route_for',return_value=('social_video_seta','company',None)):
    TP='https://token-plan.ap-southeast-1.maas.aliyuncs.com/'
    PG='https://dashscope-intl.aliyuncs.com/'
    ROWS[:]=[selected_row(TP,'ALIBABA_TOKEN_PLAN_KEY')]
    calls.clear()
    try:run_gen();check('key missing -> raises',False)
    except RuntimeError as e:check('key missing -> refused without HTTP',not calls and 'credential' in str(e))
    # Account choice comes from the table; another available account never substitutes.
    KEYS['ALIBABA_TOKEN_PLAN_KEY']='tp-key'; ROWS[:]=[selected_row(PG,'DASHSCOPE_API_KEY')]
    calls.clear()
    try:run_gen();check('payg missing key -> raises',False)
    except RuntimeError:check('payg without its key -> no Token Plan substitution',not calls)
    ROWS[:]=[dict(selected_row(TP,'ALIBABA_TOKEN_PLAN_KEY'),owner_protocol='typo')]
    calls.clear()
    try:run_gen();check('unknown protocol -> raises',False)
    except ValueError:check('unknown protocol -> refused before HTTP',not calls)
    KEYS['DASHSCOPE_API_KEY']='payg-key'
    for code,msg in ((403,'AccessDenied.Unpurchased'),(429,'insufficient_quota')):
        ROWS[:]=[selected_row(TP,'ALIBABA_TOKEN_PLAN_KEY'),selected_row(PG,'DASHSCOPE_API_KEY')]
        calls.clear();POST_RESP[0]=Resp(code,{'code':msg});target=tmp/f'error-{code}'
        try:run_gen(target);check(f'{code} -> raises',False)
        except RuntimeError as e:check(f'{code} -> status retained',str(code) in str(e) and msg in str(e))
        try:run_gen(target);check(f'{code} -> uncertain retry refused',False)
        except RuntimeError:check(f'{code} -> no duplicate or account fallback',len(calls)==1)
    for name,base,cred,key in [('token_plan',TP,'ALIBABA_TOKEN_PLAN_KEY','tp-key'),('payg',PG,'DASHSCOPE_API_KEY','payg-key')]:
        ROWS[:]=[selected_row(base,cred)];calls.clear()
        POST_RESP[0]=Resp(200,{'output':{'task_id':'t1'}})
        GET_RESP[:]=[Resp(js={'output':{'task_status':'RUNNING'}}),
                    Resp(js={'output':{'task_status':'SUCCEEDED','video_url':'https://dashscope-result-sgp.oss.aliyuncs.com/x.mp4'}}),Resp(content=b'MP4DATA')]
        target=tmp/name;p=run_gen(target)
        check(f'{name}: success -> mp4 saved',p.suffix=='.mp4' and p.read_bytes()==b'MP4DATA')
        api=[c for c in calls if 'oss.aliyuncs.com' not in c[1]]
        check(f'{name}: selected account endpoints only',bool(api) and all(c[1].startswith(base) for c in api))
        check(f'{name}: never Google Veo or Replicate',not any('googleapis' in c[1] or 'replicate' in c[1] for c in calls))
        check(f'{name}: selected credential only',all(c[2].get('Authorization')=='Bearer '+key for c in api))
        check(f'{name}: post subject precedes pillar fallback',BODIES[-1]['input']['prompt']==post.video_prompt)
        check(f'{name}: watermark off,16:9,HappyHorse',BODIES[-1]['model']=='happyhorse-1.1-t2v' and BODIES[-1]['parameters']=={'watermark':False,'ratio':'16:9'})
        count=len(calls);check(f'{name}: completed job reused without HTTP',run_gen(target)==p and len(calls)==count)
    ROWS[:]=[selected_row(TP,'ALIBABA_TOKEN_PLAN_KEY')];calls.clear()
    POST_RESP[0]=Resp(js={'output':{'task_id':'failed-task'}})
    GET_RESP[:]=[Resp(js={'output':{'task_status':'FAILED','message':'content policy'}})]
    target=tmp/'failed'
    try:run_gen(target);check('task FAILED -> raises',False)
    except RuntimeError as e:check('task FAILED -> explicit failure', 'failed' in str(e).lower())
    count=len(calls)
    try:run_gen(target);check('failed job -> replay refused',False)
    except RuntimeError:check('failed job -> no resubmission',len(calls)==count)
    calls.clear()
    try:
        rm.RegistryVideoProvider(types.SimpleNamespace(account_scope='tenant',tenant_id=None))
        check('tenant missing identity -> raises',False)
    except ValueError:check('tenant missing identity -> refused before HTTP',not calls)
_tmp.cleanup()

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
sys.exit(0 if all(ok for _,ok in results) else 1)
