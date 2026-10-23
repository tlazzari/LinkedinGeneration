"""Post text written by Claude on Tom's Max subscription (2026-10-23).

Tom, 2026-10-23: "use max subscription for the posts". Dry runs that day showed
Gemini 2.5 Flash treating instructions as suggestions: it ignored the post's form,
padded the text and invented a source ("as reported by Handelsblatt"). Claude follows
structure and voice far better. The gates in post_quality / confidentiality do not
change: a better writer just gets refused less often.

HOW. Through /opt/scripts/claude_chain.py with order ('max',) only: Claude Code
headless (`claude -p`) on CLAUDE_CODE_OAUTH_TOKEN, the same route as the Seta
knowledge base. If the Max plan fails (usage limit, missing binary - it has gone
missing at night before), the brand's normal client (Gemini -> OpenRouter) writes
the post instead, so a Max-plan outage never costs a post.

WHO. Only brands Tom runs himself (MAX_PLAN_BRANDS). NEVER a Bolla tenant: a tenant
is a paying customer, and the Max subscription is Tom's own - claude_chain.py states
the same boundary for the multi-user CRM pages.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Seta 2026-10-23, TNT the same day ("move tnt too").
MAX_PLAN_BRANDS = frozenset({"seta", "tnt"})
MAX_PLAN_MODEL = "claude-opus-5-5"
SYSTEM = ("You write LinkedIn posts for a company page. Follow every instruction in the "
          "request exactly and return only the JSON document it asks for - no preface, "
          "no code fences, no commentary.")


class MaxPlanClient:
    """complete(prompt, **kw) -> text, like the commonlib clients."""

    def __init__(self, fallback: Any, model: str = MAX_PLAN_MODEL) -> None:
        self.fallback = fallback
        self.model = model
        self.last_provider = ""
        self.label = f"max:{model}"

    def complete(self, prompt: str, **kwargs: Any) -> str:
        max_tokens = int(kwargs.get("max_tokens") or 1600)
        try:
            if "/opt/scripts" not in sys.path:
                sys.path.insert(0, "/opt/scripts")
            from claude_chain import complete as _chain
            text, provider = _chain(prompt, system=SYSTEM, max_tokens=max(max_tokens, 1600),
                                    model=self.model, order=("max",), timeout=300)
        except Exception as exc:        # noqa: BLE001 - never let the route cost a post
            text, provider = "", ""
            logger.warning("MAX_PLAN: claude_chain unavailable (%s)", exc)
        if text:
            self.last_provider = f"max:{self.model}"
            logger.info("MAX_PLAN: post text written by %s", self.model)
            return text
        logger.warning("MAX_PLAN_FALLBACK: the Max plan returned nothing - writing this "
                       "post with the brand's normal model instead")
        self.last_provider = getattr(self.fallback, "label", type(self.fallback).__name__)
        return self.fallback.complete(prompt, **kwargs)


def wrap_for_brand(brand_key: str, client: Any, enabled: bool = True) -> Any:
    if enabled and brand_key in MAX_PLAN_BRANDS:
        return MaxPlanClient(fallback=client)
    return client


__all__ = ["MaxPlanClient", "MAX_PLAN_BRANDS", "MAX_PLAN_MODEL", "wrap_for_brand"]
