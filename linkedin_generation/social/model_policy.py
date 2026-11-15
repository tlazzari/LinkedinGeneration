"""Bind brand functions to the one shared registry; never pick a private model."""
from __future__ import annotations
import re
import sys


def _scripts():
    if '/opt/scripts' not in sys.path:sys.path.insert(0,'/opt/scripts')


def route_for(kind, brand_key, *, configs=None, registry=None):
    if kind not in {'post','image','video'} or not isinstance(brand_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',brand_key):
        raise ValueError('Invalid brand model function')
    if configs is None:
        from .brand_store import load_configs
        configs=load_configs()
    scope='company' if brand_key in {'tnt','seta'} else 'tenant'
    tenant_id=None
    if scope=='tenant':
        value=(configs.get(brand_key) or {}).get('tenant_id')
        if isinstance(value,bool) or not str(value or '').isdigit() or int(value)<=0:
            raise ValueError('Tenant brand requires its explicit positive tenant_id')
        tenant_id=int(value)
    if registry is None:
        _scripts();import model_registry as registry
    functions=registry.load()['functions']
    specific='social_'+kind+'_'+brand_key
    name=specific if specific in functions else 'social_'+kind+('_tenant' if scope=='tenant' else '_'+brand_key)
    if name not in functions:raise ValueError('Missing central social model function')
    return name,scope,tenant_id


def text_client(brand_key, *, enabled=True, configs=None, registry=None, factory=None):
    name,scope,tenant_id=route_for('post',brand_key,configs=configs,registry=registry)
    if scope=='company' and not enabled:name='social_post_fallback'
    if factory is None:
        from commonlib.llm_clients import create_llm_client
        factory=create_llm_client
    kwargs={'function':name,'account_scope':scope}
    if tenant_id is not None:kwargs['tenant_id']=tenant_id
    return factory(**kwargs)


def media_config(config, brand_key, *, kind='image'):
    from dataclasses import replace
    name,scope,tenant_id=route_for(kind,brand_key)
    return replace(config,model_function=name,account_scope=scope,tenant_id=tenant_id,owner_profile=None)


def owner_chain(function, *, account_scope='company'):
    _scripts();import model_owner_policy
    return model_owner_policy.chain(function,account_scope=account_scope)


def owner_key(row):
    _scripts();import model_owner_policy
    return model_owner_policy.key(row)
