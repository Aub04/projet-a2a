# Instructions pour l'agent (à lire en entier avant toute action)

## Contexte

Projet métier de 5e année (ESAIP, IR5), groupe de 5 étudiants alternants. Sujet validé par la prof :
comparer deux organisations d'un système multi-agents basé sur le protocole A2A (Agent2Agent) :

1. **pair-à-pair** : les agents négocient directement entre eux (protocole de type Contract Net) ;
2. **orchestrateur** : un agent coordinateur collecte les informations, propose un planning, les agents valident ou refusent.

Chaque membre du groupe est représenté par un agent LLM qui connaît son profil (compétences, disponibilités,
préférences, limites). Le problème à résoudre : répartir une liste de tâches et caler des réunions.

Calendrier réel :
- semaine du 16 au 20 novembre 2026 : le groupe est à l'école, c'est là que se fait l'essentiel du développement ;
- du 23 novembre au 6 décembre : le groupe est en entreprise et ne travaille que le soir et le week-end ;
- **lundi 7 décembre 2026 : soutenance et rendu du rapport.**

Jalon impératif : **vendredi 20 novembre au soir, les deux architectures doivent tourner de bout en bout sur le scénario
« notre_groupe »** (phases 0 à 4 de docs/PLAN.md). Tout le reste vient après.

## Documents de référence

- `docs/spec_prof.md` : la spécification de 2 pages rendue à la prof. C'est l'engagement. Ne pas s'en écarter sans
  l'écrire dans `docs/DECISIONS.md` et le signaler à l'humain.
- `docs/SPEC.md` : la spécification technique détaillée. C'est ta référence pour tout ce qui est code.
- `docs/PLAN.md` : les étapes à suivre dans l'ordre. Coche chaque case quand elle est réellement terminée et testée.
- `data/profiles/*.yaml` et `data/project_tasks.yaml` : les vraies données du groupe, remplies par les étudiants.

## Règles de travail

1. **Avance phase par phase** selon `docs/PLAN.md`. Vise d'abord une version simple qui marche de bout en bout,
   puis améliore. Ne commence pas une phase tant que la précédente ne fonctionne pas.
2. **Commit Git après chaque étape terminée**, avec un message clair en français (ex. `phase 1 : validateur de planning + tests`).
3. **Tests** : pytest pour le validateur, le générateur, la baseline et la logique des protocoles. Les tests utilisent
   un faux LLM (`FakeLLM`, réponses scriptées) pour ne jamais dépendre d'Ollama. `uv run pytest` doit passer avant chaque commit.
4. **Ne jamais inventer l'API d'une bibliothèque.** Le SDK A2A a beaucoup changé (passage en 1.0 en 2026, types protobuf).
   Avant d'écrire du code A2A, lis la documentation et les exemples **de la version installée** (`uv pip show a2a-sdk`,
   code source dans `.venv`, dépôt github.com/a2aproject/a2a-python). Épingle les versions dans `pyproject.toml`.
5. **Décisions** : toute décision qui change le comportement prévu dans SPEC.md (simplification, contournement) va dans
   `docs/DECISIONS.md` avec la date, la raison et l'impact sur la comparaison.
6. **Garde le README à jour** : installation, commandes, où sont les résultats.
7. **Quand t'arrêter pour demander à l'humain** (et seulement là) :
   - les profils dans `data/profiles/` sont encore des modèles non remplis ;
   - Ollama ou le modèle ne sont pas installés et tu ne peux pas le faire toi-même ;
   - un choix risque de rendre la comparaison inéquitable entre les deux architectures ;
   - le temps de calcul estimé d'une campagne de mesures dépasse 4 heures.
   Sinon, avance de façon autonome.

## Équité de la comparaison (non négociable)

- Les agents membres sont **la même classe** (`MemberAgent`) avec **les mêmes prompts** dans les deux architectures.
  Seule la coordination change.
- Même modèle, même température, même graine, mêmes limites (tours de négociation, appels LLM) pour les deux.
- Toutes les décisions « dures » (capacité, compétence minimale, échéance, dépendances, limites du membre) sont vérifiées
  par du code avant l'envoi d'un message. Le LLM choisit parmi des options faisables ou refuse ; il ne calcule pas.
- Chaque appel LLM et chaque message A2A est journalisé (fichier JSONL par exécution).

## Anti-biais

- Les scénarios de test sont générés une fois, avec des graines dédiées, **avant** toute mise au point des prompts.
  Le hash SHA-256 du fichier de test est écrit dans `docs/DECISIONS.md`. Ne jamais les modifier ensuite.
- Les prompts se règlent uniquement sur les scénarios `dev`.
- Les échecs et les hypothèses non vérifiées sont rapportés tels quels.

## Rédaction (tout texte destiné au rapport ou à la prof)

- Français, style sobre d'étudiant ingénieur. Phrases simples, paragraphes plutôt que listes à rallonge.
- **Aucun tiret quadratin (—)** ni demi-cadratin (–) comme ponctuation. Pas de formules du type « Pourquoi c'est important : ».
- Pas de superlatifs ni de marketing. Chaque chiffre cité vient des fichiers de résultats.
- Là où il faut un avis ou une expérience personnelle du groupe, écris `[À COMPLÉTER PAR LE GROUPE]`.
- Le rapport final sera réécrit par les étudiants : produis un brouillon structuré et factuel, pas un texte final.

## Stack

- Python 3.11+, gestionnaire `uv`.
- `a2a-sdk` (serveurs et clients A2A, messages en `DataPart` JSON).
- LLM local via Ollama (bibliothèque `ollama`), modèle défini dans `config.yaml`. Sorties structurées en JSON
  (paramètre `format` avec un schéma JSON), validées par pydantic, avec 2 relances maximum en cas de JSON invalide.
  Option de secours : un endpoint compatible OpenAI configurable, utilisé alors pour les deux architectures.
- pydantic, PyYAML, pytest, matplotlib, pandas, Streamlit pour la démo.
