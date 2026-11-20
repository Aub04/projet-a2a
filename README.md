# Projet métier IR5, variante « chantier » (A2A)

> Ce dossier est la variante **chantier** du projet (branche `theme/chantier`). Le projet du groupe sur notre propre
> répartition de tâches est dans le dossier voisin `A2A notre groupe` (branche `main`). Le moteur est le même ; ici,
> les agents représentent des entreprises sous-traitantes qui ne partagent pas leurs vraies disponibilités.
> Explication complète : `docs/THEME_CHANTIER.md`.

Architectures comparées : **p2p** (Contract Net entre entreprises), **orchestrator** (un coordinateur LLM propose,
les entreprises acceptent ou objectent), **baseline** (coordinateur central glouton qui ne voit que les heures
déclarées), **oracle** (même glouton avec toutes les informations privées, référence irréaliste).

Scénarios : `uv run python -m a2a_team.scenarios.chantier` (dev 20, test 50 figé, demo `chantier_demo`).
Campagne : identique au projet du groupe (`campagne.cmd` / `campagne.sh`), avec le scénario de démo
`chantier_demo` et les quatre architectures.

---

# Projet métier IR5 : pair-à-pair ou orchestrateur ? (A2A)

Deux organisations d'un même système multi-agents basé sur le protocole A2A, comparées sur la répartition des tâches
et des réunions d'un groupe de projet :

- **p2p** : les agents négocient entre eux (Contract Net : annonce, propositions, attribution, confirmation) ;
- **orchestrator** : un agent coordinateur collecte les profils, propose un planning, les agents acceptent ou objectent ;
- **baseline** : algorithme glouton sans IA, point de comparaison.

Chaque membre est représenté par un agent LLM (même classe `MemberAgent`, mêmes prompts dans les deux architectures).
Toutes les contraintes dures sont vérifiées par le code ; le LLM choisit parmi des options faisables ou refuse.

Documents : `docs/spec_prof.md` (engagement), `docs/SPEC.md` (spec technique), `docs/PLAN.md` (avancement),
`docs/DECISIONS.md` (journal des décisions et écarts à la spec).

## Installation (sur n'importe quel PC Windows, Linux ou macOS)

Prérequis : Git, [uv](https://docs.astral.sh/uv/) et [Ollama](https://ollama.com). Python 3.11 est installé par uv.

```bash
git clone <url-du-depot> projet-a2a
cd projet-a2a
uv sync                       # crée .venv avec les versions épinglées
uv run pytest                 # 49 tests, sans LLM (faux LLM scripté)
ollama pull qwen3:8b          # environ 5 Go ; il faut idéalement un GPU avec 8 Go de mémoire
```

Le modèle utilisé est défini dans `config.yaml` (`llm.model`). Si un autre modèle est installé, changer cette ligne
et le noter dans `docs/DECISIONS.md`. Les deux architectures utilisent toujours le même modèle et les mêmes réglages.

Sans GPU, un endpoint compatible OpenAI peut remplacer Ollama : `provider: openai_compat`, renseigner `base_url`,
et mettre la clé dans la variable d'environnement `LLM_API_KEY` (jamais dans un fichier versionné).

## Commandes

```bash
# Vérification rapide sans LLM (faux LLM, quelques secondes)
uv run python -m a2a_team.run_demo --arch p2p --scenario dev_1001 --fake-llm
uv run python -m a2a_team.run_demo --arch orchestrator --scenario dev_1001 --fake-llm

# Démo réelle avec Ollama sur notre groupe (les profils data/profiles/ doivent être remplis)
uv run python -m a2a_team.scenarios.from_profiles          # écrit data/scenarios/demo/notre_groupe.json
uv run python -m a2a_team.run_demo --arch p2p --scenario notre_groupe
uv run python -m a2a_team.run_demo --arch orchestrator --scenario notre_groupe
uv run python -m a2a_team.run_demo --arch baseline --scenario notre_groupe

# Contrôle des scénarios générés (solubilité, baseline)
uv run python -m a2a_team.scenarios.generator --check
```

Options de `run_demo` : `--transport memory` (sans serveurs HTTP, mêmes objets messages A2A), `--no-events`
(ignore les imprévus), `--allow-templates` (essai technique sur des profils non remplis).

Pendant l'exécution, chaque message A2A s'affiche (émetteur, destinataire, type). Les serveurs A2A écoutent sur
`127.0.0.1` : 9100 pour l'orchestrateur, 9101 et suivants pour les membres. Chaque agent publie sa carte sur
`/.well-known/agent-card.json`.

## Résultats

Chaque exécution écrit dans `runs/<date>_<arch>_<scenario>/` :

- `trace.jsonl` : tous les messages A2A (horodatage, émetteur, destinataire, type, contenu, phase) ;
- `llm_calls.jsonl` : tous les appels au LLM (agent, décision, prompts, réponse brute, tokens, durée) ;
- `result.json` : planning initial et final, rapport du validateur, coût, réaction aux imprévus.

`runs/` n'est pas versionné.

## Campagne de mesures (PC de calcul, une seule commande)

Prérequis : Git, et de préférence une carte NVIDIA avec 8 Go de mémoire vidéo. Le script installe uv et Ollama s'ils
manquent, démarre Ollama, télécharge le modèle, installe les dépendances, lance les tests, puis la campagne.

```bash
git pull
.\campagne.cmd            # Windows (ou double-clic sur campagne.cmd)
./campagne.sh             # Linux / macOS
```

Options : `-Runs 1` / `--runs 1` (une seule répétition), `-MaxHours 8` / `--max-hours 8` (arrêt propre après 8 h),
`-SansDemo` / `--sans-demo`. Pour reprendre après un arrêt (Ctrl+C, coupure, limite d'heures), relancer la même
commande : les exécutions déjà faites sont sautées. La campagne fait d'abord une répétition complète sur les
50 scénarios, puis la 2e, puis la 3e : on peut s'arrêter entre deux.

Ce qui est produit :

- `results/campagne_demo/` : `notre_groupe` et `notre_groupe_tendu`, une exécution par architecture ;
- `results/notation_aveugle/` : plannings A et B à noter par chaque membre (`notes.csv`), correspondance à part ;
- `results/campagne_test/` : `runs.jsonl` (une ligne par exécution), `summary.csv`, `stabilite.csv`, `resume.md`
  (tableaux prêts à lire), `figures/*.png`, `meta.json` (machine, GPU, version d'Ollama, modèle, commit) ;
- `results/resultats_<date>.zip` : tout cela (traces comprises) à renvoyer au groupe.

Commandes séparées si besoin :

```bash
uv run python -m a2a_team.eval.run --split test --archs p2p orchestrator baseline --runs 3
uv run python -m a2a_team.eval.run --split test --summary-only      # régénère résumé et figures
uv run python -m a2a_team.eval.notation results/campagne_demo --scenario notre_groupe
uv run python -m a2a_team.eval.campagne --essai C:/tmp/essai --runs 1   # essai technique avec faux LLM
uv run python -m a2a_team.scenarios.tendu_demo                       # régénère notre_groupe_tendu
```

## Organisation du code

```
src/a2a_team/
  models.py           modèle de données (pydantic)
  validator.py        contraintes dures et scores
  baseline.py         glouton sans IA
  llm.py              Ollama, endpoint OpenAI, FakeLLM, cache, journal
  prompts/            prompts (fichiers texte)
  agents/member.py    agent membre (commun) + rôles pair-à-pair
  agents/orchestrator.py
  a2a_layer/          serveur A2A, transports HTTP et mémoire, lanceur, trace
  architectures/runner.py   déroulé commun, imprévus, mesures
  scenarios/          générateur (dev 20, test 50 figés) et scénario notre_groupe
  eval/               campagne de mesures, résumé, figures, notation à l'aveugle
  run_demo.py         commande unique
data/
  profiles/           profils des 5 membres (à remplir)
  project_tasks.yaml  tâches réelles du projet
  scenarios/          dev/, test/ (figé, hash dans DECISIONS.md), demo/
```
