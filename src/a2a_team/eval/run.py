"""Campagne de mesures (section 12 de docs/SPEC.md).

    uv run python -m a2a_team.eval.run --split test --archs p2p orchestrator baseline --runs 3
    uv run python -m a2a_team.eval.run --split test --summary-only        # régénère summary.csv et figures

Résultats dans results/<campagne>/ :
- runs/<scénario>__<arch>__r<k>/ : trace.jsonl, llm_calls.jsonl, result.json de chaque exécution ;
- runs.jsonl : une ligne de mesures par exécution terminée ;
- summary.csv, stabilite.csv, resume.md, figures/*.png : agrégats (moyenne, écart-type) par architecture et difficulté ;
- meta.json : machine, GPU, version d'Ollama, empreinte du modèle, commit git, configuration, pour chaque session.

Ordre des exécutions : répétition 1 sur tous les scénarios, puis répétition 2, puis 3. Les difficultés sont
alternées. Une campagne arrêtée en cours de route donne donc déjà des résultats équilibrés.
Reprise : relancer la même commande ; les exécutions déjà présentes dans runs.jsonl sont sautées.
Une exécution interrompue par une panne d'infrastructure (Ollama arrêté, port occupé) n'est pas enregistrée.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from a2a_team.architectures.runner import ARCHS, run
from a2a_team.eval.metrics import run_key, run_row
from a2a_team.eval.summary import write_summary
from a2a_team.llm import load_config, make_client
from a2a_team.models import Scenario
from a2a_team.scenarios.generator import load_split, split_hash

ROOT = Path(__file__).resolve().parents[3]
RESULTS = ROOT / "results"
# Empreinte du split test figé le 17/11/2026 (docs/DECISIONS.md). La campagne refuse de tourner s'il a changé.
TEST_SPLIT_SHA256 = "666c84da46a73aeeac5cf0f6233dd28c8c3f250f8be90e38188193a87c086f98"
INFRA_MARKERS = ("ConnectError", "ConnectionError", "Connection refused", "Failed to connect", "10061", "10048",
                 "10013", "address already in use", "RemoteProtocolError", "ReadTimeout", "model not found",
                 "not found, try pulling")


# ---------------------------------------------------------------------------------------------- utilitaires
def say(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    h, m = sec // 3600, (sec % 3600) // 60
    return f"{h} h {m:02d}" if h else f"{m} min {sec % 60:02d} s"


def keep_awake(on: bool) -> None:
    """Empêche la mise en veille de Windows pendant la campagne (sans effet ailleurs)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


def _cmd(args: list[str]) -> str | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=15, cwd=ROOT).stdout.strip() or None
    except Exception:
        return None


def ollama_host(cfg: dict) -> str:
    host = (cfg["llm"].get("host") or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
    host = host.replace("0.0.0.0", "localhost")
    return host if host.startswith("http") else "http://" + host


def ollama_status(cfg: dict) -> dict | None:
    """Version d'Ollama et modèle installé, ou None si le serveur ne répond pas."""
    import httpx
    host = ollama_host(cfg)
    try:
        tags = httpx.get(f"{host}/api/tags", timeout=10).json()
        version = httpx.get(f"{host}/api/version", timeout=10).json().get("version")
    except Exception:
        return None
    model = cfg["llm"]["model"]
    found = [m for m in tags.get("models", []) if m.get("name") in (model, f"{model}:latest")]
    return {"version": version, "model": model, "model_installed": bool(found),
            "model_digest": found[0].get("digest") if found else None}


def wait_for_ollama(cfg: dict, minutes: float = 10) -> bool:
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        st = ollama_status(cfg)
        if st and st["model_installed"]:
            return True
        time.sleep(15)
    return False


def machine_info() -> dict:
    gpu = _cmd(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
    return {"platform": platform.platform(), "processor": platform.processor(), "cpu_count": os.cpu_count(),
            "python": sys.version.split()[0], "gpu": gpu,
            "ollama_env": {k: os.environ.get(k) for k in ("OLLAMA_NUM_PARALLEL", "OLLAMA_FLASH_ATTENTION")}}


def is_infra_error(error: str | None) -> bool:
    return bool(error) and any(m.lower() in error.lower() for m in INFRA_MARKERS)


# ---------------------------------------------------------------------------------------------- plan
def load_scenarios(split: str, only: list[str] | None = None) -> list[Scenario]:
    if split == "demo":
        scen = [Scenario.load(f) for f in sorted((ROOT / "data" / "scenarios" / "demo").glob("*.json"))]
    else:
        scen = load_split(split)
    if only:
        scen = [s for s in scen if s.id in only]
    return scen


def interleave(scenarios: list[Scenario]) -> list[Scenario]:
    """Alterne les difficultés (confortable, tendu, impossible, confortable...) en gardant l'ordre des ids."""
    groups: dict[str, list[Scenario]] = {}
    for s in sorted(scenarios, key=lambda s: s.id):
        groups.setdefault(s.difficulty, []).append(s)
    order = [d for d in ("tendu", "confortable", "impossible") if d in groups] + \
            [d for d in groups if d not in ("tendu", "confortable", "impossible")]
    out = []
    while any(groups.values()):
        for d in order:
            if groups[d]:
                out.append(groups[d].pop(0))
    return out


def plan_runs(scenarios: list[Scenario], archs: list[str], runs: int) -> list[tuple[Scenario, str, int]]:
    """Répétition par répétition. La baseline est déterministe : une seule exécution par scénario."""
    out = []
    for k in range(runs):
        for i, s in enumerate(interleave(scenarios)):
            llm_archs = [a for a in archs if a != "baseline"]
            if i % 2:   # alterne l'ordre p2p / orchestrateur d'un scénario à l'autre
                llm_archs.reverse()
            if "baseline" in archs and k == 0:
                out.append((s, "baseline", 0))
            out += [(s, a, k) for a in llm_archs]
    return out


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:   # dernière ligne tronquée par une coupure : ignorée, l'exécution sera refaite
                pass
    return rows


# ---------------------------------------------------------------------------------------------- campagne
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--split", choices=["test", "dev", "demo"], default="test")
    p.add_argument("--archs", nargs="+", choices=ARCHS, default=ARCHS)
    p.add_argument("--runs", type=int, default=None, help="répétitions (défaut : eval.runs_per_scenario)")
    p.add_argument("--campagne", default=None, help="nom du dossier dans results/ (défaut : campagne_<split>)")
    p.add_argument("--scenarios", nargs="*", default=None, help="sous-ensemble de scénarios (ids)")
    p.add_argument("--max-hours", type=float, default=None, help="ne lance plus de nouvelle exécution après N heures")
    p.add_argument("--summary-only", action="store_true")
    p.add_argument("--fake-llm", action="store_true", help="essai technique sans Ollama (résultats sans valeur)")
    p.add_argument("--transport", choices=["http", "memory"], default="http")
    p.add_argument("--config", default=None)
    p.add_argument("--results-dir", default=None, help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    cfg = load_config(a.config)
    runs = a.runs or cfg.get("eval", {}).get("runs_per_scenario", 3)
    name = a.campagne or f"campagne_{a.split}" + ("_fake" if a.fake_llm else "")
    camp = Path(a.results_dir) / name if a.results_dir else RESULTS / name
    camp.mkdir(parents=True, exist_ok=True)
    rows_file = camp / "runs.jsonl"

    if a.summary_only:
        write_summary(camp, figures=True)
        say(f"Résumé régénéré dans {camp}")
        return 0

    # --- vérifications avant de lancer quoi que ce soit
    if a.split == "test" and not a.scenarios:
        h = split_hash("test")
        if h != TEST_SPLIT_SHA256:
            say(f"ERREUR : le split test a changé (hash {h[:12]}..., attendu {TEST_SPLIT_SHA256[:12]}...). "
                f"Campagne refusée.")
            return 2
    llm_needed = any(x != "baseline" for x in a.archs)
    status = None
    if llm_needed and not a.fake_llm and cfg["llm"]["provider"] == "ollama":
        status = ollama_status(cfg)
        if not status:
            say(f"ERREUR : Ollama ne répond pas sur {ollama_host(cfg)}. Lancez Ollama puis relancez la commande.")
            return 3
        if not status["model_installed"]:
            say(f"ERREUR : le modèle {cfg['llm']['model']} n'est pas installé (ollama pull {cfg['llm']['model']}).")
            return 3

    scenarios = load_scenarios(a.split, a.scenarios)
    if not scenarios:
        say("Aucun scénario trouvé.")
        return 2
    todo_all = plan_runs(scenarios, a.archs, runs)
    rows = read_rows(rows_file)
    done = {r["key"] for r in rows}
    todo = [(s, arch, k) for s, arch, k in todo_all if run_key(s.id, arch, k) not in done]

    meta_file = camp / "meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {"sessions": []}
    meta["sessions"].append({
        "started": datetime.now().isoformat(timespec="seconds"), "split": a.split, "archs": a.archs, "runs": runs,
        "fake_llm": a.fake_llm, "transport": a.transport, "git_commit": _cmd(["git", "rev-parse", "HEAD"]),
        "git_dirty": bool(_cmd(["git", "status", "--porcelain", "--", "src", "config.yaml", "data"])),
        "split_hash": split_hash(a.split) if a.split != "demo" else None, "ollama": status,
        "machine": machine_info(), "config": cfg, "planned": len(todo_all), "already_done": len(todo_all) - len(todo)})
    meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    say(f"Campagne « {name} » : {len(scenarios)} scénarios, {', '.join(a.archs)}, {runs} répétition(s).")
    say(f"{len(todo_all)} exécutions prévues, {len(todo_all) - len(todo)} déjà faites, {len(todo)} à faire.")
    say(f"Résultats : {camp}")
    if not todo:
        write_summary(camp, figures=True)
        say("Rien à faire : résumé régénéré.")
        return 0

    llm = make_client(cfg, fake=a.fake_llm) if llm_needed else None
    max_rounds = cfg["limits"]["max_rounds"]
    hints = cfg["limits"].get("orchestrator_hints", True)
    durations: dict[str, list[float]] = {}
    for r in rows:
        durations.setdefault(r["arch"], []).append(r["duration"])
    t_start = time.time()
    keep_awake(True)
    stopped = None
    try:
        for i, (s, arch, k) in enumerate(todo, 1):
            if a.max_hours and time.time() - t_start > a.max_hours * 3600:
                stopped = f"limite de {a.max_hours} h atteinte"
                break
            key = run_key(s.id, arch, k)
            run_dir = camp / "runs" / key
            label = f"({i}/{len(todo)}) {s.id} [{s.difficulty}] {arch} r{k + 1}"
            say(f"{label} ...")
            for attempt in range(2):
                if run_dir.exists():   # reste d'une exécution interrompue : on repart de zéro
                    shutil.rmtree(run_dir)
                res = run(arch, s, llm if arch != "baseline" else None, run_dir, max_rounds, a.transport,
                          repeat=k, orchestrator_hints=hints)
                if arch == "baseline" or a.fake_llm or not is_infra_error(res.error):
                    break
                say(f"  panne d'infrastructure : {res.error[:160]}")
                if attempt == 0:
                    say("  attente d'Ollama (10 min maximum) puis nouvel essai...")
                    time.sleep(5)
                    wait_for_ollama(cfg)
            if arch != "baseline" and not a.fake_llm and is_infra_error(res.error):
                shutil.rmtree(run_dir, ignore_errors=True)
                stopped = f"panne d'infrastructure persistante ({res.error[:120]})"
                break
            row = run_row(res, s, name, str(run_dir.relative_to(camp)))
            with open(rows_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)
            durations.setdefault(arch, []).append(res.duration)
            try:
                write_summary(camp, figures=False)
            except Exception as e:   # le résumé ne doit jamais arrêter la campagne
                say(f"  (résumé non mis à jour : {e})")
            rest = todo[i:]
            allmean = sum(sum(v) for v in durations.values()) / max(1, sum(len(v) for v in durations.values()))
            eta = sum((sum(durations[x]) / len(durations[x])) if durations.get(x) else allmean for _, x, _ in rest)
            say(f"  {'OK' if not res.error else 'ERREUR ' + res.error[:80]} en {fmt_duration(res.duration)} : "
                f"valide={res.report.valid} attribuées={res.report.assigned_ratio:.0%} "
                f"réunions={res.report.meetings_ratio:.0%} appels LLM={res.stats.get('llm_calls', 0)} | "
                f"reste {len(rest)} exécutions, environ {fmt_duration(eta)}")
    except KeyboardInterrupt:
        stopped = "interruption (Ctrl+C) ; relancer la même commande pour reprendre"
    finally:
        keep_awake(False)
        try:
            write_summary(camp, figures=True)
        except Exception as e:
            say(f"Résumé final impossible : {e}")
    if stopped:
        say(f"Campagne arrêtée : {stopped}.")
        return 1
    say(f"Campagne terminée en {fmt_duration(time.time() - t_start)}. Résumé : {camp / 'resume.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
