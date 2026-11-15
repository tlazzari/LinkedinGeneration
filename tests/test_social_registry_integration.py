"""Exercise scheduler media entry points without imports that run brand registration."""
import ast
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]/'linkedin_generation'
PKG='isolated_social_integration';pkg=types.ModuleType(PKG);pkg.__path__=[str(ROOT/'social')];sys.modules[PKG]=pkg

def load(name):
    spec=importlib.util.spec_from_file_location(PKG+'.'+name,ROOT/'social'/(name+'.py'))
    m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);return m
POLICY=load('model_policy');MAX=load('max_plan_client')

def function(path,name,env):
    tree=ast.parse(path.read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(path),'exec'),env);return env[name]

class IntegrationTests(unittest.TestCase):
    def test_company_failure_has_no_private_fallback(self):
        client=Mock();client.complete.side_effect=RuntimeError('central unavailable');fallback=Mock()
        with patch.object(MAX,'text_client',return_value=client):
            wrapper=MAX.wrap_for_brand('tnt',fallback)
            with self.assertRaises(RuntimeError):wrapper.complete('post',max_tokens=10)
        self.assertEqual(client.complete.call_args.kwargs['max_tokens'],1600)
        fallback.complete.assert_not_called()
    def test_tenant_keeps_original_token_budget_and_system(self):
        client=Mock();client.complete.return_value='tenant post'
        with patch.object(MAX,'text_client',return_value=client):
            self.assertEqual(MAX.wrap_for_brand('tenant7').complete('post',max_tokens=120,system='own voice'),'tenant post')
        client.complete.assert_called_once_with('post',max_tokens=120,system='own voice')
    def test_tenant_scheduler_preserves_logo_aspect_news_and_gif_settings(self):
        policy=types.ModuleType('linkedin_generation.social.model_policy');policy.media_config=Mock(side_effect=lambda c,b:c)
        created=[];provider=Mock();provider.get_image.return_value='image-result'
        def create(c):created.append(c);return provider
        env={'Path':Path,'ImageProviderConfig':lambda **kw:types.SimpleNamespace(**kw),'create_image_provider':create,
             'SETA_LOGO_PATH':Path('/nonexistent-company-logo'),'logging':Mock(),'GoogleImagenProvider':types.SimpleNamespace(IMAGE_MODEL='old-default')}
        fn=function(ROOT/'seta_post_scheduler.py','generate_image_for_post',env)
        image=types.SimpleNamespace(model='old-default',size='1080x1080',aspect_ratio='1:1',style_hint='brand',use_animated_gif=True,gif_num_frames=5,gif_frame_duration=900,logo_path='/tenant/logo.png',curated_library=[])
        campaign=types.SimpleNamespace(image_provider=image);post=types.SimpleNamespace(image_prompt='subject',news_articles=[types.SimpleNamespace(title='actual news')],alt_text='alt')
        with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,{'linkedin_generation.social.model_policy':policy}):
            self.assertEqual(fn(campaign=campaign,post=post,target_dir=Path(tmp),brand_key='tenant7'),'image-result')
        self.assertEqual(created[0].logo_path,'/tenant/logo.png');self.assertEqual(created[0].aspect_ratio,'1:1')
        self.assertTrue(created[0].use_animated_gif);self.assertEqual(created[0].gif_num_frames,5)
        self.assertEqual(policy.media_config.call_args.args[1],'tenant7')
        self.assertIn('actual news',provider.get_image.call_args.kwargs['prompt'])
    def test_scheduler_clients_have_no_direct_provider_construction(self):
        for name in ['linkedin_post_scheduler.py','seta_post_scheduler.py']:
            tree=ast.parse((ROOT/name).read_text())
            calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)]
            self.assertFalse(any(isinstance(n.func,ast.Name) and n.func.id=='create_llm_client' for n in calls))

if __name__=='__main__':unittest.main()
