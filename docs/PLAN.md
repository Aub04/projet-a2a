# Plan de réalisation

Cocher une case seulement quand l'étape fonctionne et que `uv run pytest` passe. Commit après chaque case cochée.

**Jalon impératif : vendredi 20 novembre au soir, phases 0 à 4 terminées.**

## Phase 0. Mise en place

- [x] (brouillons rédigés par l'agent, à relire par chacun) Vérifier que les 5 profils `data/profiles/*.yaml` sont remplis (sinon, s'arrêter et demander)
- [x] `git init`, `.gitignore` (`.venv`, `runs/`, cache LLM), premier commit avec le kit
- [x] `pyproject.toml` avec uv, dépendances épinglées : a2a-sdk, ollama, pydantic, pyyaml, pytest, pandas, matplotlib, streamlit
- [ ] **À FAIRE sur le PC de calcul** Vérifier qu'Ollama répond et que le modèle de `config.yaml` est installé ; noter le modèle retenu dans `docs/DECISIONS.md`
- [x] Lire la documentation et les exemples de la version installée de `a2a-sdk` ; résumer en 10 lignes dans `docs/DECISIONS.md` l'API utilisée (serveur, carte d'agent, envoi de message, DataPart)
- [x] Preuve de concept : deux serveurs A2A locaux qui s'échangent un DataPart JSON, l'un répond grâce à un appel au LLM

## Phase 1. Données, validateur, baseline

- [x] `models.py` (section 2 de SPEC.md)
- [x] `validator.py` avec tests (un cas valide, un cas par type de violation)
- [x] `baseline.py` avec tests
- [x] `scenarios/from_profiles.py` : construit le scénario `notre_groupe` depuis `data/`
- [x] `scenarios/generator.py` ; générer `dev` (20) et `test` (50) ; écrire le hash SHA-256 du dossier test dans `docs/DECISIONS.md`
- [x] Vérifier avec la baseline que les scénarios confortables et tendus sont solubles ; rapport court dans DECISIONS.md

## Phase 2. LLM et agent membre

- [x] `llm.py` : `OllamaClient`, `OpenAICompatClient`, `FakeLLM`, journalisation, cache
- [x] Prompts de l'agent membre dans `prompts/`
- [x] `MemberAgent` : calcul des options faisables par le code, choix par le LLM, tests avec FakeLLM
- [x] Serveur A2A d'un agent membre + lanceur des 5 serveurs + trace JSONL

## Phase 3. Architecture pair-à-pair

- [x] Protocole Contract Net (section 9) : CFP, propositions, attribution, confirmation, relances, escalade
- [x] Sondage de réunions
- [x] Tests du protocole avec FakeLLM
- [ ] **À FAIRE (profils + Ollama)** Exécution réelle de bout en bout sur `notre_groupe` : planning produit, validé, trace lisible

## Phase 4. Architecture orchestrateur

- [x] Agent orchestrateur (serveur A2A) : collecte, planning, révisions (section 10)
- [x] Tests avec FakeLLM
- [ ] **À FAIRE (profils + Ollama)** Exécution réelle de bout en bout sur `notre_groupe`
- [x] Commande unique : `uv run python -m a2a_team.run_demo --arch p2p|orchestrator|baseline --scenario notre_groupe`

**Fin du jalon du vendredi 20 novembre.** Écrire dans `docs/DECISIONS.md` un point d'étape : ce qui marche, ce qui manque.

## Phase 5. Imprévus

- [x] Injection des événements (absence, nouvelle tâche, échéance avancée) dans les deux architectures
- [x] Mesure « valide après événement » et « messages pour s'en remettre »

## Phase 6. Évaluation

- [ ] `eval/metrics.py` et `eval/run.py` (reprise après interruption)
- [ ] Mesurer le temps moyen sur `dev`, estimer la campagne test ; demander à l'humain si plus de 4 h
- [ ] Mise au point des prompts sur `dev` uniquement (noter chaque changement dans DECISIONS.md)
- [ ] Campagne finale sur `test` : p2p, orchestrateur, baseline, 3 répétitions
- [ ] `summary.csv` + figures dans `rapport/figures/`
- [ ] Export de la notation à l'aveugle pour `notre_groupe`

## Phase 7. Démo

- [ ] Interface Streamlit (section 13) : lancement, fil des messages, planning, imprévus, duel, rejouer
- [ ] Enregistrer deux traces de démo (une par architecture) pour le mode « rejouer »
- [ ] `docs/DEMO.md` : déroulé de 5 minutes, commandes exactes, plan B si le live plante

## Phase 8. Rapport (brouillon pour le groupe)

- [ ] `rapport/brouillon.md` : contexte, protocole A2A, les deux architectures, méthode d'évaluation, résultats chiffrés,
      limites, perspectives ; style sobre, aucun tiret quadratin, `[À COMPLÉTER PAR LE GROUPE]` là où il faut leur avis
- [ ] README final : installation, commandes, reproduction des résultats
