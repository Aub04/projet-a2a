"""Campagne complète en une commande (appelée par campagne.cmd / campagne.sh à la racine du dépôt).

    uv run python -m a2a_team.eval.campagne [--runs 3] [--sans-demo] [--max-hours N]

Étapes :
1. vérifications : Ollama répond, le modèle est installé, split test intact ; mesure du temps d'un appel LLM et
   estimation de la durée totale ;
2. scénarios de démo (notre_groupe et notre_groupe_tendu), une exécution par architecture, dans
   results/campagne_demo/, puis export de la notation à l'aveugle ;
3. campagne finale sur le split test (50 scénarios, p2p + orchestrateur + baseline, N répétitions) dans
   results/campagne_test/ ;
4. archive results/resultats_<date>.zip à renvoyer au groupe.
Relancer la même commande reprend là où la campagne s'est arrêtée.
"""

from __future__ import annotations

import argparse
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from a2a_team.eval import run as camp_run
from a2a_team.eval.run import RESULTS, fmt_duration, ollama_status, say
from a2a_team.llm import load_config, make_client

# Temps mesurés le 17/11 sur le PC d'Aubin (CPU seul) pour notre_groupe (15 tâches) : un appel de candidature de
# référence prenait 10,9 s ; p2p 1848 s, orchestrateur 902 s. Sert seulement à une estimation grossière.
REF_CALL_S, REF_P2P_S, REF_ORCH_S, REF_TASKS, TEST_AVG_TASKS = 10.9, 1848.0, 902.0, 15, 17.9


def calibrate(cfg: dict) -> float | None:
    """Durée d'un appel LLM représentatif (décision de candidature), modèle déjà chargé."""
    from pydantic import BaseModel

    class Bid(BaseModel):
        decision: str
        week: str
        justification: str

    llm = make_client(cfg)
    system = ("Tu représentes Camille dans l'organisation du travail d'un groupe de projet. Profil : compétences "
              "dev_python 4, redaction 2 ; heures disponibles S47 12 h, S48 6 h, S49 6 h ; aime dev_python ; évite "
              "presentation. Engagements actuels : aucun.")
    user = ("Annonce de m2 : tâche T05 « Agent membre et prompts » (ia_llm niveau 3, 6 h, échéance S47). Semaines "
            "faisables pour Camille (vérifié par le programme) : S47. Réponds par une candidature ou un refus. "
            'Format : {"decision": "propose" ou "refuse", "week": "S47", "justification": "..."}')
    try:
        llm.reset(arch="calibration", scenario="calibration", repeat=0)
        llm.decide(system, user, Bid, {"agent": "calibration", "decision": "bid"})   # charge le modèle
        t0 = time.time()
        for _ in range(3):
            llm.decide(system, user, Bid, {"agent": "calibration", "decision": "bid"})
        return (time.time() - t0) / 3
    except Exception as e:
        say(f"Mesure du temps d'appel impossible : {e}")
        return None


def zip_results(paths: list[Path], root: Path = RESULTS) -> Path:
    out = root / f"resultats_{datetime.now():%Y%m%d_%H%M}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for d in paths:
            if not d.exists():
                continue
            for f in d.rglob("*"):
                if f.is_file() and "llm_cache" not in f.parts:
                    z.write(f, f.relative_to(root))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=int, default=None, help="répétitions sur le split test (défaut : config, 3)")
    p.add_argument("--sans-demo", action="store_true", help="saute les scénarios de démo")
    p.add_argument("--max-hours", type=float, default=None, help="arrête la campagne test proprement après N heures")
    p.add_argument("--essai", type=Path, default=None,
                   help="essai technique : faux LLM, transport mémoire, résultats dans ce dossier (pas de mesure)")
    a = p.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    cfg = load_config()
    runs = a.runs or cfg.get("eval", {}).get("runs_per_scenario", 3)

    root = a.essai or RESULTS
    extra = ["--fake-llm", "--transport", "memory", "--results-dir", str(root)] if a.essai else []
    say("=== 1. Vérifications ===")
    st = {"version": "essai", "model": "fake", "model_installed": True, "model_digest": ""} if a.essai         else ollama_status(cfg)
    if not st:
        say("ERREUR : Ollama ne répond pas. Lancez Ollama puis relancez la commande.")
        return 3
    if not st["model_installed"]:
        say(f"ERREUR : modèle {cfg['llm']['model']} absent. Lancez : ollama pull {cfg['llm']['model']}")
        return 3
    say(f"Ollama {st['version']}, modèle {st['model']} ({(st['model_digest'] or '')[:12]}).")
    gpu = camp_run.machine_info()["gpu"]
    say(f"GPU : {gpu or 'non détecté (nvidia-smi absent) : le calcul risque d être très lent'}")
    t_call = None if a.essai else calibrate(cfg)
    if t_call:
        k = t_call / REF_CALL_S * TEST_AVG_TASKS / REF_TASKS
        per_rep = 50 * (REF_P2P_S + REF_ORCH_S) * k
        say(f"Un appel LLM prend {t_call:.1f} s (10,9 s sur le PC d'Aubin sans GPU).")
        say(f"Estimation grossière : {fmt_duration(per_rep)} par répétition sur les 50 scénarios test, "
            f"{fmt_duration(per_rep * runs)} pour {runs} répétition(s). L'estimation se précise pendant la campagne.")
        say("Les répétitions sont faites l'une après l'autre : une campagne arrêtée en route reste exploitable.")

    status = 0
    if not a.sans_demo:
        say("=== 2. Scénarios de démo (notre_groupe, notre_groupe_tendu) ===")
        status = camp_run.main(["--split", "demo", "--runs", "1", "--campagne", "campagne_demo"] + extra)
        if status == 0:
            from a2a_team.eval.notation import export
            for sid in ("notre_groupe", "notre_groupe_tendu"):
                try:
                    say(f"Notation à l'aveugle : "
                        f"{export(root / 'campagne_demo', sid, root / 'notation_aveugle' if a.essai else None)}")
                except SystemExit as e:
                    say(f"Notation à l'aveugle non exportée : {e}")
        elif status != 1:
            return status

    if status == 0:
        say("=== 3. Campagne finale sur le split test ===")
        args = ["--split", "test", "--runs", str(runs), "--campagne", "campagne_test"] + extra
        if a.max_hours:
            args += ["--max-hours", str(a.max_hours)]
        status = camp_run.main(args)

    say("=== 4. Archive des résultats ===")
    z = zip_results([root / "campagne_demo", root / "campagne_test", root / "notation_aveugle"], root)
    say(f"Archive : {z} ({z.stat().st_size / 1e6:.1f} Mo). Envoyez ce fichier au groupe.")
    say(f"Résumé lisible : {root / 'campagne_test' / 'resume.md'}")
    if status:
        say("La campagne n'est pas terminée : relancez la même commande pour reprendre.")
    return status


if __name__ == "__main__":
    sys.exit(main())
