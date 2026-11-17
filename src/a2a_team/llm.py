"""Couche LLM (section 5 de docs/SPEC.md).

`LLMClient.decide(system, user, schema, meta)` renvoie une instance pydantic validée. Le JSON est demandé avec un
schéma (sortie structurée) ; en cas de JSON invalide, 2 relances au plus avec le message d'erreur, puis `LLMError`.
Chaque tentative est journalisée (agent, architecture, scénario, décision, tokens, durée, réponse brute).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[2]


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((Path(path) if path else ROOT / "config.yaml").read_text(encoding="utf-8"))


class LLMClient:
    name = "base"

    def __init__(self, model: str = "", temperature: float = 0.2, seed: int = 42, max_retries: int = 2,
                 max_calls: int | None = None, cache_dir: Path | None = None):
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.max_retries = max_retries
        self.max_calls = max_calls
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.calls = 0
        self.log: Callable[[dict], None] = lambda rec: None
        self.context: dict[str, Any] = {}     # architecture, scénario, répétition : ajoutés à chaque journal
        self._lock = threading.Lock()

    # --- à fournir par les implémentations -----------------------------------------------------------
    def _complete(self, messages: list[dict], json_schema: dict, meta: dict) -> tuple[str, int | None, int | None]:
        raise NotImplementedError

    # --- interface commune ------------------------------------------------------------------------------
    def reset(self, **context) -> None:
        self.calls = 0
        self.context = context

    def _cache_key(self, messages, schema_json) -> str:
        blob = json.dumps([self.name, self.model, self.temperature, self.seed, self.context.get("repeat", 0),
                           messages, schema_json], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def decide(self, system: str, user: str, schema: type[BaseModel], meta: dict | None = None) -> BaseModel:
        meta = meta or {}
        schema_json = schema.model_json_schema()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = ""
        for attempt in range(self.max_retries + 1):
            with self._lock:
                if self.max_calls is not None and self.calls >= self.max_calls:
                    raise BudgetExceeded(f"limite de {self.max_calls} appels LLM atteinte")
                self.calls += 1
            key = self._cache_key(messages, schema_json)
            cache_file = self.cache_dir / f"{key}.json" if self.cache_dir else None
            t0 = time.time()
            cached = False
            if cache_file and cache_file.exists():
                rec = json.loads(cache_file.read_text(encoding="utf-8"))
                raw, tin, tout, cached = rec["raw"], rec["tokens_in"], rec["tokens_out"], True
            else:
                raw, tin, tout = self._complete(messages, schema_json, meta)
            duration = time.time() - t0
            try:
                result = schema.model_validate_json(raw)
                ok, last_error = True, ""
            except (ValidationError, ValueError) as e:
                ok, last_error = False, str(e).splitlines()[0][:300]
            if ok and cache_file and not cached:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"raw": raw, "tokens_in": tin, "tokens_out": tout}), "utf-8")
            self.log({**self.context, "agent": meta.get("agent"), "decision": meta.get("decision"),
                      "attempt": attempt, "provider": self.name, "model": self.model, "tokens_in": tin,
                      "tokens_out": tout, "duration": round(duration, 3), "cached": cached, "valid": ok,
                      "error": last_error or None, "system": system, "user": user, "raw": raw})
            if ok:
                return result
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"Réponse invalide ({last_error}). Réponds uniquement par un objet "
                                            f"JSON conforme au schéma demandé."}]
        raise LLMError(f"JSON invalide après {self.max_retries + 1} tentatives : {last_error}")

    async def adecide(self, system: str, user: str, schema: type[BaseModel], meta: dict | None = None):
        return await asyncio.to_thread(self.decide, system, user, schema, meta)


class OllamaClient(LLMClient):
    name = "ollama"

    def __init__(self, host: str | None = None, think: bool | None = False, num_ctx: int = 8192, **kw):
        super().__init__(**kw)
        import ollama
        self.client = ollama.Client(host=host) if host else ollama.Client()
        self.think = think
        self.num_ctx = num_ctx

    def _complete(self, messages, json_schema, meta):
        r = self.client.chat(model=self.model, messages=messages, format=json_schema, think=self.think,
                             options={"temperature": self.temperature, "seed": self.seed, "num_ctx": self.num_ctx})
        return r.message.content or "", r.prompt_eval_count, r.eval_count


class OpenAICompatClient(LLMClient):
    name = "openai_compat"

    def __init__(self, base_url: str, api_key_env: str = "LLM_API_KEY", response_format: str = "json_schema",
                 **kw):
        super().__init__(**kw)
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key=os.environ.get(api_key_env, "absent"))
        self.response_format = response_format

    def _complete(self, messages, json_schema, meta):
        if self.response_format == "json_schema":
            rf = {"type": "json_schema", "json_schema": {"name": "reponse", "schema": json_schema}}
        else:   # certains fournisseurs n'acceptent que le mode JSON simple : le schéma est alors dans le prompt
            rf = {"type": "json_object"}
            messages = [dict(messages[0], content=messages[0]["content"] + "\n\nSchéma JSON de la réponse :\n"
                             + json.dumps(json_schema, ensure_ascii=False))] + messages[1:]
        r = self.client.chat.completions.create(model=self.model, messages=messages, temperature=self.temperature,
                                                seed=self.seed, response_format=rf)
        u = r.usage
        return r.choices[0].message.content or "", getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None)


Policy = Callable[[str, dict], dict]


class FakeLLM(LLMClient):
    """Faux LLM pour les tests : la réponse est calculée par une politique à partir de `meta`
    (nom de la décision et options faisables), sans regarder le texte du prompt."""
    name = "fake"

    def __init__(self, policy: Policy | None = None, overrides: dict[str, Policy] | None = None, **kw):
        kw.setdefault("model", "fake")
        super().__init__(**kw)
        self.policy = policy
        self.overrides = overrides or {}
        self.seen: list[dict] = []

    def _complete(self, messages, json_schema, meta):
        self.seen.append(meta)
        decision = meta.get("decision", "")
        if decision in self.overrides:
            out = self.overrides[decision](decision, meta)
        elif self.policy:
            out = self.policy(decision, meta)
        else:
            out = default_policy(decision, meta)
        raw = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        return raw, len(messages[-1]["content"]) // 4, len(raw) // 4


def default_policy(decision: str, meta: dict) -> dict:
    """Comportement coopératif simple, utilisé par défaut par FakeLLM."""
    if decision == "bid":
        return {"decision": "propose", "week": meta["options"][0], "justification": "disponible"}
    if decision == "confirm_award":
        return {"decision": "confirm", "reason": "toujours disponible"}
    if decision == "choose_award":
        return {"winner": meta["options"][0], "reason": "première proposition"}
    if decision == "meeting_vote":
        return {"accepted": list(meta["options"]), "comment": ""}
    if decision == "plan_feedback":
        return {"decision": "accept", "objections": []}
    if decision == "orchestrator_plan":
        return meta["fake_plan"]()
    raise LLMError(f"décision inconnue pour FakeLLM : {decision}")


def make_client(config: dict | None = None, fake: bool = False, cache_dir: Path | None = None) -> LLMClient:
    config = config or load_config()
    c = config["llm"]
    kw = dict(model=c["model"], temperature=c["temperature"], seed=c["seed"], max_retries=c["max_retries"],
              max_calls=config["limits"]["max_llm_calls_per_scenario"], cache_dir=cache_dir)
    if fake:
        return FakeLLM(**{**kw, "model": "fake"})
    if c["provider"] == "ollama":
        return OllamaClient(host=c.get("host"), think=c.get("think", False), num_ctx=c.get("num_ctx", 8192), **kw)
    if c["provider"] == "openai_compat":
        o = c["openai_compat"]
        return OpenAICompatClient(base_url=o["base_url"], api_key_env=o.get("api_key_env", "LLM_API_KEY"),
                                  response_format=o.get("response_format", "json_schema"), **kw)
    raise ValueError(f"fournisseur inconnu : {c['provider']}")
