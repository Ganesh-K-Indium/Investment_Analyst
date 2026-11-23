"""
Runtime behaviour shared by the quant sub-agents.

  RoleChatModel    a chat model that is looked up when it is CALLED, from the settings of the request in force, so one
                   long-lived supervisor can serve users who chose different models.
  harden_tools     per-call timeout (from settings) and a short TTL cache for read-only market-data tools.
  request_preamble the per-request instructions (enabled agents, history window, benchmark, risk) added to the user's turn.
"""
import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.services import app_settings

logger = logging.getLogger("app.quant_runtime")


class RoleChatModel(BaseChatModel):
    role: str = "quant_agent"
    bound_tools: List[Any] = Field(default_factory=list)
    bind_kwargs: Dict[str, Any] = Field(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "role-chat-model"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        return self.model_copy(update={"bound_tools": list(tools), "bind_kwargs": kwargs})

    def _target(self):
        llm = app_settings.get_llm(self.role)
        return llm.bind_tools(self.bound_tools, **self.bind_kwargs) if self.bound_tools else llm

    def _generate(self, messages: List[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        msg = self._target().invoke(messages, stop=stop, **kwargs)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(self, messages: List[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        msg = await self._target().ainvoke(messages, stop=stop, **kwargs)
        return ChatResult(generations=[ChatGeneration(message=msg)])


# ---- tools ---------------------------------------------------------------------------------------------------------------

_CACHE: "OrderedDict[str, tuple]" = OrderedDict()
_CACHE_MAX = 256
_NEVER_CACHE = ("query_database",)       # user data: always live


def _key(name: str, args: Any) -> str:
    return hashlib.sha1((name + json.dumps(args, sort_keys=True, default=str)).encode()).hexdigest()


def harden_tools(tools: List[Any], agent_name: str) -> List[Any]:
    """Wrap each tool's coroutine with the request's timeout and a TTL cache. Settings are read per call, so they follow the user."""
    for t in tools:
        inner = getattr(t, "coroutine", None)
        if inner is None or getattr(t, "_hardened", False):
            continue

        def make(inner=inner, name=t.name):
            async def run(*a, **kw):
                q = app_settings.current().get("quant", {})
                ttl, timeout = q.get("cache_ttl", 0), q.get("tool_timeout", 45)
                k = _key(name, [a, kw]) if ttl and name not in _NEVER_CACHE else None
                if k and k in _CACHE and time.time() - _CACHE[k][0] < ttl:
                    _CACHE.move_to_end(k)
                    return _CACHE[k][1]
                try:
                    out = await asyncio.wait_for(inner(*a, **kw), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning("[%s] tool %s timed out after %ss", agent_name, name, timeout)
                    raise RuntimeError(f"The {name} data source did not answer within {timeout}s. Try again, or ask for something narrower.")
                if k:
                    _CACHE[k] = (time.time(), out)
                    while len(_CACHE) > _CACHE_MAX:
                        _CACHE.popitem(last=False)
                return out
            return run

        t.coroutine = make()
        t._hardened = True
    return tools


# ---- per-request instructions --------------------------------------------------------------------------------------------

def request_preamble(settings: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """One line of user preferences appended to the turn. The supervisor's prompt is fixed at startup; this is how a user's choices
    reach it. Agents the user switched off are named so the supervisor does not hand off to them."""
    from app.services.app_settings.schema import QUANT_AGENTS
    q = (settings or app_settings.current()).get("quant", {})
    off = [a for a in QUANT_AGENTS if a not in q.get("enabled_agents", QUANT_AGENTS)]
    parts = [f"default price history={q.get('history_window', '1y')}", f"benchmark={q.get('benchmark', 'SPY')}",
             f"risk profile={q.get('risk_profile', 'balanced')} (word conclusions accordingly)"]
    if off:
        parts.append("DO NOT use these agents (the user turned them off): " + ", ".join(f"{a}_agent" for a in off))
    return "[USER PREFERENCES: " + "; ".join(parts) + "]"
