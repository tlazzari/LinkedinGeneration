"""Registry-selected transports. Media generation stays with its existing owner.

Video intent is durably recorded before submission; an uncertain submission is never replayed.
"""
from __future__ import annotations
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
import requests
from .model_policy import owner_chain, owner_key

IMAGE_PROTOCOLS={'google_image','openrouter_image','alibaba_image','openai_image'}
VIDEO_PROTOCOLS={'alibaba_video','replicate_video'}


def _validate_scope(config):
    if config.account_scope not in {'company','tenant'}:
        raise ValueError('Invalid media account scope')
    if config.account_scope=='tenant':
        value=config.tenant_id
        if isinstance(value,bool) or not isinstance(value,int) or value<=0:
            raise ValueError('Tenant media requires explicit positive tenant_id')
    elif config.tenant_id is not None:
        raise ValueError('Company media cannot carry a tenant identity')


def _available_key(row):
    try:return owner_key(row)
    except ValueError:return None


def _endpoint(row, suffix):
    base=row['base_url'].rstrip('/')
    return base+suffix


MAX_JSON_BYTES=8*1024*1024
MAX_MEDIA_BYTES=128*1024*1024


def _read_response(response, limit):
    try:
        if 300 <= response.status_code < 400:
            raise RuntimeError('Provider redirect refused')
        response.raise_for_status()
        chunks=[];length=0
        for chunk in response.iter_content(chunk_size=65536):
            length+=len(chunk)
            if length>limit:raise RuntimeError('Provider response exceeds size limit')
            chunks.append(chunk)
        return b''.join(chunks)
    finally:
        response.close()


def _post_json(url, **kwargs):
    response=requests.post(url,allow_redirects=False,stream=True,**kwargs)
    return json.loads(_read_response(response,MAX_JSON_BYTES))


def _get_json(url, **kwargs):
    response=requests.get(url,allow_redirects=False,stream=True,**kwargs)
    return json.loads(_read_response(response,MAX_JSON_BYTES))


def _download(url, timeout=120):
    response=requests.get(url,timeout=timeout,allow_redirects=False,stream=True)
    return _read_response(response,MAX_MEDIA_BYTES)


def _image(row, prompt, config):
    key=owner_key(row)
    if not key:raise RuntimeError('Selected image credential is unavailable')
    protocol=row['owner_protocol'];model=row['model'];timeout=row.get('timeout_s',180)
    headers={'Authorization':'Bearer '+key};options=row.get('options') or {}
    if protocol=='google_image' and model.startswith('imagen'):
        parameters={'sampleCount':1}
        if config.aspect_ratio:parameters['aspectRatio']=config.aspect_ratio
        data=_post_json(_endpoint(row,'/v1beta/models/'+model+':predict'),headers={'x-goog-api-key':key},
                        json={'instances':[{'prompt':prompt}],'parameters':parameters},timeout=timeout)
        for image in data.get('predictions',[]):
            if image.get('bytesBase64Encoded'):return base64.b64decode(image['bytesBase64Encoded'])
        raise RuntimeError('Selected Imagen model returned no image')
    if protocol=='google_image':
        body={'contents':[{'role':'user','parts':[{'text':prompt}]}],
              'generationConfig':{'responseModalities':['TEXT','IMAGE']}}
        if config.aspect_ratio:body['generationConfig']['imageConfig']={'aspectRatio':config.aspect_ratio}
        data=_post_json(_endpoint(row,'/v1beta/models/'+model+':generateContent'),headers={'x-goog-api-key':key},json=body,timeout=timeout)
        for c in data.get('candidates',[]):
            for p in c.get('content',{}).get('parts',[]):
                inline=p.get('inlineData') or p.get('inline_data') or {}
                if inline.get('data'):return base64.b64decode(inline['data'])
    elif protocol=='openrouter_image':
        body={'model':model,'messages':[{'role':'user','content':prompt}],'modalities':['image','text']}
        if config.aspect_ratio:body['image_config']={'aspect_ratio':config.aspect_ratio}
        data=_post_json(_endpoint(row,'/chat/completions'),headers=headers,json=body,timeout=timeout)
        for c in data.get('choices',[]):
            for item in c.get('message',{}).get('images',[]):
                url=(item.get('image_url') or {}).get('url') or item.get('url')
                if url:
                    if url.startswith('data:'):return base64.b64decode(url.split(',',1)[1])
                    return _download(url)
    elif protocol=='alibaba_image':
        sizes={'16:9':'1920*1080','1:1':'1440*1440','9:16':'1080*1920','4:3':'1664*1248'}
        body={'model':model,'input':{'messages':[{'role':'user','content':[{'text':prompt}]}]},
              'parameters':{'size':sizes.get(config.aspect_ratio or '16:9','1920*1080'),'n':1,'watermark':False}}
        data=_post_json(_endpoint(row,'/api/v1/services/aigc/multimodal-generation/generation'),headers=headers,json=body,timeout=timeout)
        for c in data.get('output',{}).get('choices',[]):
            for p in c.get('message',{}).get('content',[]):
                if p.get('image'):return _download(p['image'])
    elif protocol=='openai_image':
        body={'model':model,'prompt':prompt,'size':config.size}
        data=_post_json(_endpoint(row,'/images/generations'),headers=headers,json=body,timeout=timeout)
        for item in data.get('data',[]):
            if item.get('b64_json'):return base64.b64decode(item['b64_json'])
            if item.get('url'):return _download(item['url'])
    else:raise ValueError('Unsupported registry image protocol')
    raise RuntimeError('Selected image provider returned no image')


class RegistryImageProvider:
    def __init__(self, config):
        _validate_scope(config)
        self.config=config
    def get_image(self, *, prompt, target_dir, alt_text=None):
        from .image_providers import ImagePayload
        rows=owner_chain(self.config.model_function,account_scope=self.config.account_scope)
        if any(r.get('owner_protocol') not in IMAGE_PROTOCOLS for r in rows):
            raise ValueError('Unsupported registry image protocol')
        enhanced=prompt+(f'\nStyle: {self.config.style_hint}.' if self.config.style_hint else '')
        for row in rows:
            try:data=_image(row,enhanced,self.config)
            except Exception:
                # The registry alone owns fallback. Never enter a provider's private chain.
                continue
            target_dir=Path(target_dir);target_dir.mkdir(parents=True,exist_ok=True)
            path=target_dir/('linkedin_image_'+uuid.uuid4().hex+'.png');path.write_bytes(data)
            return ImagePayload(prompt=enhanced,provider=row['owner_protocol'],path=path,alt_text=alt_text)
        raise RuntimeError('Central image chain unavailable')


def _write(path, data):
    tmp=path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(data,f);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)


class RegistryVideoProvider:
    def __init__(self, config):
        _validate_scope(config)
        self.config=config
    def get_video(self, *, prompt, target_dir):
        target_dir=Path(target_dir);target_dir.mkdir(parents=True,exist_ok=True)
        identity=[self.config.model_function,self.config.account_scope,self.config.tenant_id,prompt]
        digest=hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        jobs=target_dir/'.model_jobs';jobs.mkdir(mode=0o700,exist_ok=True)
        ledger=jobs/(digest+'.json')
        with open(jobs/(digest+'.lock'),'a') as lock:
            os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
            state=json.loads(ledger.read_text()) if ledger.exists() else None
            if state and state.get('state')=='complete' and Path(state['path']).is_file():return Path(state['path'])
            if state and not state.get('task_id'):
                raise RuntimeError('Previous video submission is uncertain; manual reconciliation required')
            if state and state.get('state')=='failed':raise RuntimeError('Previous video task failed; no automatic resubmission')
            if not state:
                rows=owner_chain(self.config.model_function,account_scope=self.config.account_scope)
                if not rows or any(r.get('owner_protocol') not in VIDEO_PROTOCOLS for r in rows):raise ValueError('Unsupported registry video protocol')
                # Missing credentials may advance before submission; accepted or uncertain jobs never advance.
                row=next((r for r in rows if _available_key(r)),None)
                if row is None:raise RuntimeError('Selected video credential is unavailable')
                if row['owner_protocol']=='replicate_video' and not (row.get('options') or {}).get('version'):
                    raise ValueError('Replicate route requires its explicit version')
                state={'state':'submitting','row':row}
                _write(ledger,state)
                key=owner_key(row);options=row.get('options') or {};protocol=row['owner_protocol']
                if protocol=='replicate_video' and self.config.style_hint:
                    prompt=f'{prompt}. Style: {self.config.style_hint}'
                headers={'Authorization':'Bearer '+key}
                if protocol=='alibaba_video':
                    headers['X-DashScope-Async']='enable'
                    body={'model':row['model'],'input':{'prompt':prompt},'parameters':{'watermark':False,'ratio':options.get('ratio','16:9')}}
                    url=_endpoint(row,'/api/v1/services/aigc/video-generation/video-synthesis')
                else:
                    if not options.get('version'):raise ValueError('Replicate route requires its explicit version')
                    body={'version':options['version'],'input':{'prompt':prompt,'duration':options.get('duration',8),'aspect_ratio':options.get('aspect_ratio','16:9')}}
                    url=_endpoint(row,'/v1/predictions')
                result=_post_json(url,headers=headers,json=body,timeout=row.get('timeout_s',60))
                task=(result.get('output') or {}).get('task_id') if protocol=='alibaba_video' else result.get('id')
                if not task:raise RuntimeError('Video accepted without task identity; reconciliation required')
                state.update(state='pending',task_id=task);_write(ledger,state)
            row=state['row'];key=owner_key(row)
            if not key:raise RuntimeError('Pending video credential unavailable')
            protocol=row['owner_protocol'];headers={'Authorization':'Bearer '+key};deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                suffix='/api/v1/tasks/' if protocol=='alibaba_video' else '/v1/predictions/'
                result=_get_json(_endpoint(row,suffix+state['task_id']),headers=headers,timeout=30)
                out=result.get('output') or {};status=out.get('task_status') if protocol=='alibaba_video' else result.get('status')
                if status in ('SUCCEEDED','succeeded'):
                    url=out.get('video_url') if protocol=='alibaba_video' else (out[0] if isinstance(out,list) else out)
                    if not url:raise RuntimeError('Completed video has no output URL')
                    path=target_dir/('video_'+digest+'.mp4');path.write_bytes(_download(url,180))
                    state.update(state='complete',path=str(path));_write(ledger,state);return path
                if status in ('FAILED','CANCELED','failed','canceled'):
                    state['state']='failed';_write(ledger,state);raise RuntimeError('Selected video task failed')
                if status=='UNKNOWN':raise RuntimeError('Video task unknown; reconciliation required')
                time.sleep(10)
            raise RuntimeError('Video polling timed out; next attempt resumes the same task')
