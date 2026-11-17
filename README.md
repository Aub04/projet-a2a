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
uv run pytest                 # 40 tests, sans LLM (faux LLM scripté)
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
  run_demo.py         commande unique
data/
  profiles/           profils des 5 membres (à remplir)
  project_tasks.yaml  tâches réelles du projet
  scenarios/          dev/, test/ (figé, hash dans DECISIONS.md), demo/
```
