"""Journalisation d'une exécution : messages A2A (trace.jsonl) et appels LLM (llm_calls.jsonl)."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

# Messages qui ne comptent pas dans le coût de coordination : accusés de réception, ordres du système
# (horloge de la simulation, injection des imprévus) et escalades (écrites au journal, pas envoyées à un agent).
NOT_COUNTED = {"ACK", "ESCALATION"}
SYSTEM = "systeme"
HUMAN = "humain"


class Tracer:
    def __init__(self, run_dir: Path | None = None, on_event: Callable[[dict], None] | None = None):
        self.run_dir = Path(run_dir) if run_dir else None
        if self.run_dir:
            self.run_dir.mkdir(parents=True, exist_ok=True)
        self.on_event = on_event
        self.messages: list[dict] = []
        self.llm_calls: list[dict] = []
        self.escalations: list[str] = []
        self._lock = threading.Lock()
        self.t0 = time.time()

    def _write(self, name: str, rec: dict) -> None:
        if self.run_dir:
            with open(self.run_dir / name, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def message(self, sender: str, recipient: str, payload: dict[str, Any], phase: str = "") -> None:
        rec = {"ts": round(time.time() - self.t0, 3), "kind": "a2a", "sender": sender, "recipient": recipient,
               "type": payload.get("type", "?"), "content": payload.get("content", {}), "phase": phase}
        with self._lock:
            self.messages.append(rec)
            self._write("trace.jsonl", rec)
        if self.on_event:
            self.on_event(rec)

    def escalate(self, sender: str, text: str, phase: str = "") -> None:
        self.escalations.append(text)
        self.message(sender, HUMAN, {"type": "ESCALATION", "content": {"texte": text}}, phase)

    def llm(self, rec: dict) -> None:
        rec = {"ts": round(time.time() - self.t0, 3), **rec}
        with self._lock:
            self.llm_calls.append(rec)
            self._write("llm_calls.jsonl", rec)
        if self.on_event:
            self.on_event({"kind": "llm", **rec})

    def counted(self, phase: str | None = None) -> list[dict]:
        return [m for m in self.messages
                if m["type"] not in NOT_COUNTED and SYSTEM not in (m["sender"], m["recipient"])
                and (phase is None or m["phase"] == phase)]

    def stats(self) -> dict:
        counted = self.counted()
        return {
            "messages": len(counted),
            "messages_by_type": dict(Counter(m["type"] for m in counted)),
            "llm_calls": len(self.llm_calls),
            "tokens_in": sum(c.get("tokens_in") or 0 for c in self.llm_calls),
            "tokens_out": sum(c.get("tokens_out") or 0 for c in self.llm_calls),
            "llm_seconds": round(sum(c.get("duration") or 0 for c in self.llm_calls), 2),
        }


def read_trace(run_dir: Path) -> list[dict]:
    f = Path(run_dir) / "trace.jsonl"
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
