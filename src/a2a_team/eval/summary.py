"""Agrégats d'une campagne : summary.csv, stabilite.csv, resume.md et figures PNG (section 12 de docs/SPEC.md).

    uv run python -m a2a_team.eval.summary results/campagne_test

Moyenne et écart-type par architecture et difficulté (plus une ligne « toutes »). Les taux booléens (valide, complet,
escalade correcte) sont des moyennes de 0/1. `escalation_correct` n'a de sens que sur les scénarios « impossible ».
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from a2a_team.eval.metrics import stability

ARCH_ORDER = ["p2p", "orchestrator", "baseline", "oracle"]
ARCH_LABEL = {"p2p": "pair-à-pair", "orchestrator": "orchestrateur", "baseline": "coordinateur central (déclarations)",
              "oracle": "oracle (tout connu)"}
# Trois premiers créneaux de la palette catégorielle de référence (validés tous couples, daltonisme compris).
ARCH_COLOR = {"p2p": "#2a78d6", "orchestrator": "#eb6834", "baseline": "#1baf7a", "oracle": "#eda100"}
DIFF_ORDER = ["confortable", "tendu", "impossible", "toutes"]

METRICS = [   # (colonne, libellé, plus haut = mieux ?)
    ("complete", "planning complet et valide", True),
    ("valid", "planning valide", True),
    ("assigned_ratio", "tâches attribuées", True),
    ("feasible_ratio", "tâches attribuées réalisables", True),
    ("meetings_ratio", "réunions calées", True),
    ("preference_ratio", "score de préférences", True),
    ("dislike_ratio", "tâches évitées attribuées", False),
    ("load_balance", "déséquilibre de charge (CV)", False),
    ("escalation_correct", "escalade correcte (impossible)", True),
    ("silent_failure", "échec silencieux", False),
    ("events_valid_ratio", "valide après imprévu", True),
    ("events_messages", "messages par imprévu", False),
    ("messages", "messages A2A", False),
    ("llm_calls", "appels LLM", False),
    ("tokens_in", "tokens en entrée", False),
    ("duration", "durée (s)", False),
]
FIGURES = ["complete", "feasible_ratio", "assigned_ratio", "meetings_ratio", "preference_ratio", "load_balance", "messages",
           "llm_calls", "duration", "events_valid_ratio", "escalation_correct"]


def load_rows(camp: Path) -> pd.DataFrame:
    f = camp / "runs.jsonl"
    rows = []
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c, _, _ in METRICS:
        if c in df:
            df[c] = pd.to_numeric(df[c].map(lambda v: float(v) if v is not None else None), errors="coerce")
    return df


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    parts = [df, df.assign(difficulty="toutes")]
    full = pd.concat(parts, ignore_index=True)
    cols = [c for c, _, _ in METRICS if c in full]
    g = full.groupby(["arch", "difficulty"])
    mean, std = g[cols].mean(), g[cols].std()
    out = mean.add_suffix("_mean").join(std.add_suffix("_std"))
    out["n_runs"] = g.size()
    out["n_scenarios"] = g["scenario_id"].nunique()
    out["n_errors"] = g["error"].apply(lambda s: int(s.notna().sum()))
    out = out.reset_index()
    out["arch"] = pd.Categorical(out["arch"], [a for a in ARCH_ORDER if a in set(out["arch"])] +
                                 sorted(set(out["arch"]) - set(ARCH_ORDER)))
    out["difficulty"] = pd.Categorical(out["difficulty"], [d for d in DIFF_ORDER if d in set(out["difficulty"])])
    return out.sort_values(["difficulty", "arch"]).round(4)


def stability_table(df: pd.DataFrame) -> pd.DataFrame:
    st = stability(df.to_dict("records"))
    if not st:
        return pd.DataFrame(columns=["arch", "difficulty", "jaccard_mean", "n_scenarios"])
    diff = df.drop_duplicates("scenario_id").set_index("scenario_id")["difficulty"]
    t = pd.DataFrame([{"scenario_id": s, "arch": a, "jaccard": v, "difficulty": diff[s]} for (s, a), v in st.items()])
    t = pd.concat([t, t.assign(difficulty="toutes")])
    return (t.groupby(["arch", "difficulty"]).agg(jaccard_mean=("jaccard", "mean"), n_scenarios=("jaccard", "size"))
            .reset_index().round(4))


def paired_table(df: pd.DataFrame) -> pd.DataFrame:
    """Par scénario : qui fait mieux que la baseline sur « planning complet » et « tâches attribuées »."""
    base = df[df.arch == "baseline"].set_index("scenario_id")
    out = []
    for arch in [a for a in ("p2p", "orchestrator") if a in set(df.arch)]:
        m = df[df.arch == arch].groupby("scenario_id")[["complete", "assigned_ratio"]].mean()
        b = base[["complete", "assigned_ratio", "difficulty"]].rename(
            columns={"complete": "complete_base", "assigned_ratio": "assigned_ratio_base"})
        m = m.join(b, how="inner")
        for d, sub in list(m.groupby("difficulty")) + [("toutes", m)]:
            out.append({"arch": arch, "difficulty": d, "scenarios": len(sub),
                        "complet_alors_que_baseline_echoue": int(((sub.complete > 0) & (sub.complete_base == 0)).sum()),
                        "baseline_complete_mais_pas_arch": int(((sub.complete == 0) & (sub.complete_base > 0)).sum()),
                        "attribuees_mieux_que_baseline": int((sub.assigned_ratio > sub.assigned_ratio_base + 1e-9).sum()),
                        "attribuees_moins_bien": int((sub.assigned_ratio < sub.assigned_ratio_base - 1e-9).sum())})
    return pd.DataFrame(out)


def _fmt(v, std=None, pct=False) -> str:
    if v is None or pd.isna(v):
        return "n.d."
    fmt = "{:.0%}" if pct else ("{:.2f}" if abs(v) < 100 else "{:.0f}")
    s = fmt.format(v)
    if std is not None and not pd.isna(std):
        s += " ± " + fmt.format(std)
    return s


def resume_md(df: pd.DataFrame, agg: pd.DataFrame, stab: pd.DataFrame, paired: pd.DataFrame, camp: Path) -> str:
    pct = {"complete", "valid", "assigned_ratio", "feasible_ratio", "meetings_ratio", "escalation_correct", "silent_failure",
           "events_valid_ratio", "dislike_ratio"}
    lines = [f"# Résumé de la campagne {camp.name}", "",
             f"{len(df)} exécutions, {df.scenario_id.nunique()} scénarios, "
             f"{int(df.error.notna().sum())} exécutions terminées en erreur. Moyenne ± écart-type. "
             f"Fichier généré automatiquement, ne pas éditer à la main.", ""]
    shown = ["complete", "feasible_ratio", "assigned_ratio", "meetings_ratio", "preference_ratio", "load_balance",
             "escalation_correct", "events_valid_ratio", "messages", "llm_calls", "duration"]
    labels = {c: l for c, l, _ in METRICS}
    for d in [x for x in DIFF_ORDER if x in set(agg.difficulty.astype(str))]:
        sub = agg[agg.difficulty == d]
        lines += [f"## {d}", "", "| mesure | " + " | ".join(ARCH_LABEL.get(str(a), str(a)) for a in sub.arch) + " |",
                  "| --- |" + " --- |" * len(sub)]
        lines.append("| exécutions | " + " | ".join(str(int(n)) for n in sub.n_runs) + " |")
        for c in shown:
            if f"{c}_mean" not in sub or sub[f"{c}_mean"].isna().all():
                continue
            lines.append(f"| {labels[c]} | " + " | ".join(_fmt(m, s, c in pct) for m, s in
                                                           zip(sub[f"{c}_mean"], sub[f"{c}_std"])) + " |")
        lines.append("")
    if not paired.empty:
        lines += ["## Comparaison scénario par scénario avec la baseline", "",
                  "| architecture | difficulté | scénarios | complet là où la baseline échoue | baseline complète mais "
                  "pas l'architecture | plus de tâches attribuées | moins de tâches attribuées |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for r in paired.itertuples():
            lines.append(f"| {ARCH_LABEL.get(r.arch, r.arch)} | {r.difficulty} | {r.scenarios} | "
                         f"{r.complet_alors_que_baseline_echoue} | {r.baseline_complete_mais_pas_arch} | "
                         f"{r.attribuees_mieux_que_baseline} | {r.attribuees_moins_bien} |")
        lines.append("")
    if not stab.empty:
        lines += ["## Stabilité entre répétitions (Jaccard moyen des affectations)", "",
                  "| architecture | difficulté | Jaccard | scénarios |", "| --- | --- | --- | --- |"]
        for r in stab.itertuples():
            lines.append(f"| {ARCH_LABEL.get(r.arch, r.arch)} | {r.difficulty} | {r.jaccard_mean:.2f} | {r.n_scenarios} |")
        lines.append("")
    return "\n".join(lines)


def make_figures(agg: pd.DataFrame, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    out.mkdir(parents=True, exist_ok=True)
    pct = {"complete", "assigned_ratio", "feasible_ratio", "meetings_ratio", "events_valid_ratio",
           "escalation_correct"}
    labels = {c: (l, up) for c, l, up in METRICS}
    ink, muted, grid, surface = "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": "#c3c2b7", "axes.labelcolor": "#52514e",
                         "xtick.color": "#52514e", "ytick.color": muted, "font.family": "DejaVu Sans"})
    for c in FIGURES:
        col = f"{c}_mean"
        if col not in agg or agg[col].isna().all():
            continue
        sub = agg.dropna(subset=[col])
        diffs = [d for d in DIFF_ORDER if d in set(sub.difficulty.astype(str))]
        archs = [a for a in ARCH_ORDER if a in set(sub.arch.astype(str))]
        fig, ax = plt.subplots(figsize=(7.5, 4.2), dpi=150)
        fig.patch.set_facecolor(surface)
        ax.set_facecolor(surface)
        width = 0.8 / max(1, len(archs))
        top = 0.0
        labels_at = []
        for j, a in enumerate(archs):
            for i, d in enumerate(diffs):
                row = sub[(sub.arch == a) & (sub.difficulty == d)]
                if row.empty:
                    continue
                v, s = float(row[col].iloc[0]), row[f"{c}_std"].iloc[0]
                x = i - 0.4 + width * (j + 0.5)
                h = v * 100 if c in pct else v
                bw = width - 0.03   # écart de surface entre barres voisines
                ax.add_patch(FancyBboxPatch((x - bw / 2, min(0, h)), bw, abs(h), linewidth=0,
                                            boxstyle="round,pad=0,rounding_size=0.02", facecolor=ARCH_COLOR[a],
                                            mutation_aspect=0.05))
                e = 0.0
                if s is not None and not pd.isna(s):
                    e = s * 100 if c in pct else s
                    ax.plot([x, x], [h - e, h + e], color=muted, linewidth=1)
                top = max(top, h + e, h)
                labels_at.append((x, max(h + e, h, 0),
                                  f"{h:.0f} %" if c in pct else (f"{v:.2f}" if abs(v) < 10 else f"{v:.0f}")))
        for x, y, lab in labels_at:
            ax.text(x, y + (top or 1) * 0.015, lab, ha="center", va="bottom", fontsize=7.5, color=ink)
        ax.set_xlim(-0.6, len(diffs) - 0.4)
        lo = min(0.0, float(sub[col].min() * (100 if c in pct else 1)) * 1.2)
        ax.set_ylim(lo, max(100 * 1.12, top * 1.08) if c in pct else (top * 1.15 or 1))
        ax.set_xticks(range(len(diffs)), diffs)
        name, up = labels[c]
        ax.set_ylabel(name + (" (%)" if c in pct else ""))
        ax.set_title(f"{name[:1].upper() + name[1:]} par difficulté ({'plus haut' if up else 'plus bas'} = mieux)",
                     loc="left", fontsize=11, color=ink)
        ax.yaxis.grid(True, color=grid, linewidth=0.6)
        ax.set_axisbelow(True)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.axhline(0, color="#c3c2b7", linewidth=0.8)
        handles = [plt.Rectangle((0, 0), 1, 1, color=ARCH_COLOR[a]) for a in archs]
        ax.legend(handles, [ARCH_LABEL[a] for a in archs], frameon=False, ncol=len(archs), loc="upper left",
                  bbox_to_anchor=(0, -0.1), fontsize=9)
        fig.tight_layout()
        fig.savefig(out / f"{c}.png", facecolor=surface)
        plt.close(fig)


def write_summary(camp: Path, figures: bool = True) -> None:
    camp = Path(camp)
    df = load_rows(camp)
    if df.empty:
        return
    agg = aggregate(df)
    agg.to_csv(camp / "summary.csv", index=False, encoding="utf-8")
    stab = stability_table(df)
    stab.to_csv(camp / "stabilite.csv", index=False, encoding="utf-8")
    paired = paired_table(df) if "baseline" in set(df.arch) else pd.DataFrame()
    (camp / "resume.md").write_text(resume_md(df, agg, stab, paired, camp), encoding="utf-8")
    if figures:
        make_figures(agg, camp / "figures")


def main() -> None:
    camp = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results/campagne_test")
    write_summary(camp, figures=True)
    print(f"Résumé écrit dans {camp}")


if __name__ == "__main__":
    main()
