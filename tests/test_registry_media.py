"""Fake transports exercise media outcomes, paid-job recovery and grounded requests."""
import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[1]/'linkedin_generation/social'
PKG='isolated_social_registry'
pkg=types.ModuleType(PKG);pkg.__path__=[str(ROOT)];sys.modules[PKG]=pkg

def load(name):
    spec=importlib.util.spec_from_file_location(PKG+'.'+name,ROOT/(name+'.py'))
    m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);return m
try:
    import requests
except ModuleNotFoundError:
    sys.modules['requests']=types.SimpleNamespace(post=Mock(),get=Mock())
POLICY=load('model_policy');M=load('registry_media');NEWS=load('news_search')
# Avoid importing optional image SDKs: the outcome contract is the same plain payload.
sys.modules[PKG+'.image_providers']=types.SimpleNamespace(ImagePayload=lambda **kw:types.SimpleNamespace(**kw))

def config():return types.SimpleNamespace(model_function='social_video_seta',account_scope='company',tenant_id=None,style_hint=None,aspect_ratio='16:9',size='1024x1024')
def row(protocol='alibaba_video'):
    return {'profile':'test','owner_protocol':protocol,'model':'chosen-model','base_url':'https://fake.invalid','cred':'FAKE','options':{'version':'fixed-version'},'timeout_s':42}
def response(data=None,content=b'actual-image-or-video'):
    raw=json.dumps(data).encode() if data is not None else content
    return types.SimpleNamespace(status_code=200,raise_for_status=Mock(),iter_content=lambda **kw:[raw],close=Mock())

class MediaTests(unittest.TestCase):
    def test_redirects_refused_and_response_bounded(self):
        redirected=response({});redirected.status_code=302
        with patch.object(M.requests,'post',return_value=redirected) as post:
            with self.assertRaisesRegex(RuntimeError,'redirect'):
                M._post_json('https://fake.invalid',headers={'Authorization':'Bearer secret'})
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
            self.assertTrue(post.call_args.kwargs['stream'])
            redirected.close.assert_called_once()
        oversized=response();oversized.iter_content=lambda **kw:[b'a'*5,b'b'*5]
        with self.assertRaisesRegex(RuntimeError,'size limit'):M._read_response(oversized,9)
        oversized.close.assert_called_once()
    def test_media_requires_tenant_identity_before_transport(self):
        c=config();c.account_scope='tenant'
        with patch.object(M.requests,'post') as post:
            with self.assertRaises(ValueError):M.RegistryVideoProvider(c)
            with self.assertRaises(ValueError):M.RegistryImageProvider(c)
            post.assert_not_called()
    def test_imagen_uses_predict_without_private_model_replacement(self):
        r=row('google_image');r['model']='imagen-selected'
        with patch.object(M,'owner_key',return_value='key'),patch.object(M.requests,'post',return_value=response({'predictions':[{'bytesBase64Encoded':base64.b64encode(b'proof').decode()}]})) as post:
            self.assertEqual(M._image(r,'drawing',config()),b'proof')
            self.assertIn('imagen-selected:predict',post.call_args.args[0])
    def test_terminal_failure_is_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(M,'owner_chain',return_value=[row()]),patch.object(M,'owner_key',return_value='fake-key'),patch.object(M.requests,'post',return_value=response({'output':{'task_id':'failed-job'}})) as post,patch.object(M.requests,'get',return_value=response({'output':{'task_status':'FAILED'}})):
            provider=M.RegistryVideoProvider(config())
            for unused in range(2):
                with self.assertRaises(RuntimeError):provider.get_video(prompt='x',target_dir=Path(tmp))
            self.assertEqual(post.call_count,1)
    def test_image_only_uses_central_chain_and_selected_model_endpoint_key(self):
        rows=[row('google_image'),row('alibaba_image')]
        ok=response({'output':{'choices':[{'message':{'content':[{'image':'https://output.invalid/image'}]}}]}})
        with tempfile.TemporaryDirectory() as tmp,patch.object(M,'owner_chain',return_value=rows),patch.object(M,'owner_key',return_value='fake-key'),patch.object(M.requests,'post',side_effect=[RuntimeError('Google down'),ok]) as post,patch.object(M.requests,'get',return_value=response()):
            payload=M.RegistryImageProvider(config()).get_image(prompt='drawing',target_dir=Path(tmp))
            self.assertEqual(payload.path.read_bytes(),b'actual-image-or-video')
            self.assertEqual(post.call_count,2)
            self.assertEqual(post.call_args.kwargs['json']['model'],'chosen-model')
            self.assertEqual(post.call_args.kwargs['timeout'],42)
            self.assertTrue(post.call_args.args[0].startswith('https://fake.invalid/'))
    def test_unknown_image_protocol_fails_before_paid_call(self):
        with patch.object(M,'owner_chain',return_value=[row('unknown')]),patch.object(M.requests,'post') as post:
            with self.assertRaises(ValueError):M.RegistryImageProvider(config()).get_image(prompt='x',target_dir=Path('/unused'))
            post.assert_not_called()
    def test_poll_failure_resumes_persisted_job_without_resubmission(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(M,'owner_chain',return_value=[row()]),patch.object(M,'owner_key',return_value='fake-key'),patch.object(M.requests,'post',return_value=response({'output':{'task_id':'paid-job-1'}})) as post,patch.object(M.requests,'get',side_effect=[TimeoutError('lost poll'),response({'output':{'task_status':'SUCCEEDED','video_url':'https://out.invalid/video'}}),response()]),patch.object(M.time,'sleep'):
            provider=M.RegistryVideoProvider(config())
            with self.assertRaises(TimeoutError):provider.get_video(prompt='subject',target_dir=Path(tmp))
            ledger=next((Path(tmp)/'.model_jobs').glob('*.json'))
            self.assertEqual(json.loads(ledger.read_text())['task_id'],'paid-job-1')
            path=provider.get_video(prompt='subject',target_dir=Path(tmp))
            self.assertEqual(path.read_bytes(),b'actual-image-or-video');self.assertEqual(post.call_count,1)
            self.assertEqual(provider.get_video(prompt='subject',target_dir=Path(tmp)),path);self.assertEqual(post.call_count,1)
    def test_submission_uncertainty_is_never_replayed(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(M,'owner_chain',return_value=[row()]),patch.object(M,'owner_key',return_value='fake-key'),patch.object(M.requests,'post',side_effect=TimeoutError('unknown acceptance')) as post:
            provider=M.RegistryVideoProvider(config())
            with self.assertRaises(TimeoutError):provider.get_video(prompt='x',target_dir=Path(tmp))
            with self.assertRaisesRegex(RuntimeError,'uncertain'):provider.get_video(prompt='x',target_dir=Path(tmp))
            self.assertEqual(post.call_count,1)
    def test_replicate_version_and_duration_preserved(self):
        r=row('replicate_video');r['options'].update(duration=8,aspect_ratio='16:9')
        with tempfile.TemporaryDirectory() as tmp,patch.object(M,'owner_chain',return_value=[r]),patch.object(M,'owner_key',return_value='fake-key'),patch.object(M.requests,'post',return_value=response({'id':'id-r'})) as post,patch.object(M.requests,'get',side_effect=[response({'status':'succeeded','output':['https://out.invalid/video']}),response()]):
            M.RegistryVideoProvider(config()).get_video(prompt='x',target_dir=Path(tmp))
            self.assertEqual(post.call_args.kwargs['json'],{'version':'fixed-version','input':{'prompt':'x','duration':8,'aspect_ratio':'16:9'}})
    def test_grounding_remains_required_and_each_attempt_counts(self):
        payload={'tools':[{'google_search':{}}],'contents':[]};rows=[row('google_grounded_text')]*2
        with patch.object(POLICY,'owner_chain',return_value=rows),patch.object(POLICY,'owner_key',return_value='key'),patch.object(NEWS,'_gemini_calls_left',return_value=10),patch.object(NEWS,'_gemini_spend') as spend,patch.object(NEWS.requests,'post',side_effect=[TimeoutError(),response({'candidates':[]})]) as post:
            self.assertEqual(NEWS._registry_grounded_request(payload),{'candidates':[]})
            self.assertEqual(spend.call_count,2)
            self.assertEqual(post.call_args.kwargs['json']['tools'],[{'google_search':{}}])
            self.assertIn('chosen-model:generateContent',post.call_args.args[0])
        with patch.object(POLICY,'owner_chain',return_value=[row('openai_image')]),patch.object(NEWS.requests,'post') as post:
            with self.assertRaises(ValueError):NEWS._registry_grounded_request(payload)
            post.assert_not_called()

if __name__=='__main__':unittest.main()
