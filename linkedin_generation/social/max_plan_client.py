"""Compatibility wrapper for company social text, selected in the central registry.

The saved defaults retain Claude Max for TNT/Seta. Tenants use their scoped API
route, and all fallbacks are owned by that route rather than this wrapper.
"""
from __future__ import annotations
from typing import Any
from .model_policy import text_client

MAX_PLAN_BRANDS=frozenset({'seta','tnt'})
MAX_PLAN_MODEL='central-registry'  # compatibility label, never submitted as a model ID
SYSTEM=('You write LinkedIn posts for a company page. Follow every instruction in the '
        'request exactly and return only the JSON document it asks for - no preface, '
        'no code fences, no commentary.')

class MaxPlanClient:
    def __init__(self, fallback: Any=None, model: str|None=None, *, brand_key='seta', enabled=True) -> None:
        if model is not None:raise ValueError('Edit the central social model profile instead of overriding a model ID')
        self.fallback=fallback  # retained for callers, never used after central failure
        self.client=text_client(brand_key,enabled=enabled)
        self.token_floor=brand_key in MAX_PLAN_BRANDS and enabled
        self.last_provider=''
        self.label=getattr(self.client,'label','registry:social')
    def complete(self,prompt: str,**kwargs: Any) -> str:
        options=dict(kwargs)
        if self.token_floor:options['max_tokens']=max(int(options.get('max_tokens') or 1600),1600)
        if self.token_floor:options.setdefault('system',SYSTEM)
        self.last_provider=''
        result=self.client.complete(prompt,**options)
        self.last_provider=getattr(self.client,'last_provider','')
        self.label=getattr(self.client,'label','registry:social')
        return result

def wrap_for_brand(brand_key: str,client: Any=None,enabled: bool=True)->Any:
    return MaxPlanClient(fallback=client,brand_key=brand_key,enabled=enabled)

__all__=['MaxPlanClient','MAX_PLAN_BRANDS','MAX_PLAN_MODEL','wrap_for_brand']
