"""Central social model selection preserves brand/tenant identity and forbids private fallback."""
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('social_model_policy',ROOT/'linkedin_generation/social/model_policy.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

class ModelPolicyTests(unittest.TestCase):
    def setUp(self):
        self.registry=types.SimpleNamespace(load=lambda:{'functions':{n:{} for n in ('social_post_tnt','social_post_seta','social_post_tenant','social_post_fallback','social_image_tenant','social_image_t42_custom')}})
    def test_builtin_brands_keep_distinct_company_functions(self):
        self.assertEqual(M.route_for('post','tnt',configs={},registry=self.registry),('social_post_tnt','company',None))
        self.assertEqual(M.route_for('post','seta',configs={},registry=self.registry),('social_post_seta','company',None))
    def test_tenant_requires_trusted_positive_identity_not_key_guess(self):
        with self.assertRaises(ValueError):M.route_for('post','t42_custom',configs={},registry=self.registry)
        self.assertEqual(M.route_for('post','t42_custom',configs={'t42_custom':{'tenant_id':42}},registry=self.registry),('social_post_tenant','tenant',42))
    def test_existing_per_brand_media_route_has_priority_over_shared_default(self):
        self.assertEqual(M.route_for('image','t42_custom',configs={'t42_custom':{'tenant_id':42}},registry=self.registry),('social_image_t42_custom','tenant',42))
    def test_disabled_company_subscription_uses_central_fallback_only(self):
        factory=Mock(return_value=object())
        result=M.text_client('tnt',enabled=False,configs={},registry=self.registry,factory=factory)
        factory.assert_called_once_with(function='social_post_fallback',account_scope='company')
        self.assertIs(result,factory.return_value)
    def test_tenant_client_transmits_tenant_scope_and_identity(self):
        factory=Mock(return_value=object())
        M.text_client('furniture',configs={'furniture':{'tenant_id':7}},registry=self.registry,factory=factory)
        factory.assert_called_once_with(function='social_post_tenant',account_scope='tenant',tenant_id=7)

    def test_live_social_routes_accept_the_temperature_the_generators_send(self):
        # 19/11 incident: the Seta/TNT generators always pass temperature=0.8. claude_cli/codex_cli have no such
        # control, and without option_policy temperature=native_default the gateway refused Opus in 0.2 s
        # (required_option_unsupported -> "outage"), so Gemini silently wrote every Seta/TNT post since 17/11.
        import json
        registry=Path('/opt/scripts/ai_models.json')
        if not registry.exists():self.skipTest('central registry not on this host')
        d=json.loads(registry.read_text())
        cli={'claude_cli','codex_cli'}
        checked=0
        for name,row in d['functions'].items():
            if not name.startswith('social_post_'):continue
            if any(d['models'][m]['adapter'] in cli for m in row['chain']):
                checked+=1
                self.assertEqual(row.get('option_policy'),{'temperature':'native_default'},
                                 name+': CLI model in chain but temperature would make the gateway skip it')
        self.assertGreaterEqual(checked,2,'expected the Seta and TNT routes to have a Claude/Codex CLI model')

if __name__=='__main__':unittest.main()
