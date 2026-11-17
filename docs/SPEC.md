# Spécification technique détaillée

Ce document précise `docs/spec_prof.md` pour l'implémentation. En cas de doute, la spec prof fait foi sur le
périmètre, ce document fait foi sur les détails techniques.

## 1. Vue d'ensemble

Un scénario décrit une équipe (membres), des tâches, des réunions à caler et des imprévus. Une **architecture**
(pair-à-pair ou orchestrateur) fait négocier les agents et produit un **planning**. Un **validateur** vérifie les
contraintes dures et calcule les scores. Un **runner** lance les deux architectures (plus une baseline sans IA) sur
les mêmes scénarios et agrège les mesures. Une **interface Streamlit** rejoue tout en direct pour la démo.

```
scenario.json ──> architecture (p2p | orchestrator | baseline) ──> plan.json + trace.jsonl ──> validateur ──> métriques
```

## 2. Modèle de données (pydantic, `src/a2a_team/models.py`)

Granularité temporelle : **la semaine**. Le planning attribue chaque tâche à un membre et à une semaine.
Les réunions sont calées sur des créneaux d'une heure (jour + heure).

```python
class MemberProfile(BaseModel):
    id: str                          # "m1" ... "m5"
    name: str
    skills: dict[str, int]           # compétence -> niveau 1..5 ; clés possibles : dev_python, ia_llm, front_demo,
                                     # redaction, presentation, test_eval, devops
    capacity_hours: dict[str, float] # semaine -> heures disponibles, ex. {"S47": 25, "S48": 6, "S49": 6}
    max_tasks_per_week: int | None   # limite personnelle optionnelle
    preferences: list[str]           # compétences/types de tâches préférés, ex. ["dev_python", "ia_llm"]
    dislikes: list[str]              # types de tâches à éviter si possible
    hard_limits: list[str]           # limites en langage naturel, à titre informatif pour le LLM ; les limites
                                     # vérifiables sont dans les autres champs
    meeting_slots: list[str]         # créneaux possibles pour les réunions, format "S47-lun-14", "S48-mer-20"...

class Task(BaseModel):
    id: str
    title: str
    skill: str                       # compétence principale requise
    min_level: int                   # niveau minimal requis (1..5)
    hours: float
    deadline: str                    # semaine limite incluse, ex. "S48"
    depends_on: list[str] = []

class Event(BaseModel):
    trigger_round: int               # moment où l'événement est injecté dans la négociation
    type: Literal["absence", "new_task", "deadline_change"]
    member_id: str | None = None     # absence : capacité de ce membre mise à 0 sur `weeks`
    weeks: list[str] = []
    task: Task | None = None         # new_task
    task_id: str | None = None       # deadline_change
    new_deadline: str | None = None

class Scenario(BaseModel):
    id: str
    split: Literal["dev", "test", "demo"]
    seed: int
    weeks: list[str]                 # ex. ["S47", "S48", "S49"]
    members: list[MemberProfile]
    tasks: list[Task]
    meetings_needed: int             # nombre de réunions d'une heure avec tout le monde
    events: list[Event] = []
    difficulty: Literal["confortable", "tendu", "impossible"]

class Assignment(BaseModel):
    task_id: str
    member_id: str
    week: str

class Plan(BaseModel):
    assignments: list[Assignment]
    meetings: list[str]              # créneaux retenus
    unassigned: list[str]            # tâches non attribuées
    escalations: list[str]           # problèmes remontés aux humains (texte court)
```

## 3. Validateur et scores (`src/a2a_team/validator.py`)

**Contraintes dures** (chaque violation est comptée et typée) :
- capacité : somme des heures d'un membre sur une semaine <= `capacity_hours[semaine]` (après événements) ;
- compétence : `skills[task.skill] >= task.min_level` ;
- échéance : semaine attribuée <= `deadline` ;
- dépendances : semaine de la tâche >= semaine de chacune de ses dépendances ;
- `max_tasks_per_week` si défini ;
- réunions : chaque créneau retenu appartient aux `meeting_slots` de tous les membres présents cette semaine.

**Scores souples** :
- `assigned_ratio` : part des tâches attribuées ;
- `preference_ratio` : part des tâches attribuées dont le type est dans `preferences` du membre ; pénalité si dans `dislikes` ;
- `load_balance` : coefficient de variation du taux d'occupation (heures attribuées / capacité totale) entre membres ;
- `meetings_ratio` : réunions calées / réunions demandées ;
- `valid` : aucun manquement aux contraintes dures.

Pour les scénarios « impossible », une escalade explicite est le bon comportement : `escalation_correct` = au moins une
escalade quand aucune solution complète n'existe (vérifiable en comparant avec la capacité totale).

## 4. Baseline sans IA (`src/a2a_team/baseline.py`)

Algorithme glouton déterministe : trier les tâches par échéance puis dépendances (tri topologique) ; pour chaque tâche,
choisir parmi les membres qui ont la compétence et la capacité celui qui a le plus de capacité restante, en favorisant
une préférence à égalité ; semaine = la plus tôt possible compatible avec les dépendances. Réunions : premiers créneaux
communs. Sert de point de comparaison et de vérification que les scénarios sont solubles.

Bonus facultatif si le temps le permet : solution optimale avec OR-Tools (CP-SAT) pour mesurer l'écart à l'optimum.

## 5. Couche LLM (`src/a2a_team/llm.py`)

- Interface `LLMClient.decide(system: str, user: str, schema: type[BaseModel]) -> BaseModel`.
- Implémentations : `OllamaClient` (format JSON schema, `temperature` et `seed` depuis `config.yaml`),
  `OpenAICompatClient` (secours), `FakeLLM` (tests, réponses scriptées).
- Cache disque optionnel (clé = hash du modèle + prompts + graine) pour reprendre une campagne interrompue.
- Chaque appel journalise : agent, architecture, scénario, tour, tokens si disponibles, durée, réponse brute.

`config.yaml` :
```yaml
llm:
  provider: ollama            # ollama | openai_compat
  model: "qwen3:8b"           # à adapter au modèle réellement installé ; noter le choix final dans DECISIONS.md
  temperature: 0.2
  seed: 42
  max_retries: 2
limits:
  max_rounds: 6               # tours de négociation par tâche (p2p) ou par révision de planning (orchestrateur)
  max_llm_calls_per_scenario: 400
eval:
  runs_per_scenario: 3
```

## 6. Couche A2A (`src/a2a_team/a2a_layer/`)

- Chaque agent membre est un **serveur A2A** (ports 9101 à 9105 pour m1 à m5) avec une carte d'agent :
  nom « Agent de <prénom> », une compétence `negotiate_planning`.
- L'orchestrateur est un serveur A2A à part (port 9100), utilisé uniquement dans l'architecture orchestrateur.
- Les messages sont des **DataPart JSON** avec un champ `type` (voir section 7). Les envois sont synchrones
  (requête, réponse).
- Un lanceur démarre les serveurs dans des processus ou tâches asyncio, attend qu'ils répondent, puis lance la négociation.
- Chaque message envoyé et reçu est écrit dans `runs/<run_id>/trace.jsonl` : horodatage, émetteur, destinataire, type,
  contenu.
- Si l'utilisation réelle du transport HTTP A2A bloque le projet plus d'une demi-journée, solution de repli autorisée :
  transport en mémoire avec exactement les mêmes objets messages A2A. À noter dans DECISIONS.md.

## 7. Types de messages

| type | émetteur → destinataire | contenu |
| --- | --- | --- |
| `CFP` | annonceur → tous | tâche, semaines possibles |
| `PROPOSAL` | agent → annonceur | task_id, semaine proposée, courte justification |
| `REFUSAL` | agent → annonceur | task_id, raison courte |
| `AWARD` | annonceur → gagnant | task_id, semaine |
| `CONFIRM` / `DECLINE` | gagnant → annonceur | task_id |
| `MEETING_POLL` | initiateur → tous | liste de créneaux |
| `MEETING_VOTE` | agent → initiateur | créneaux acceptés |
| `INFO_REQUEST` | orchestrateur → agent | demande de disponibilités et préférences |
| `INFO_REPLY` | agent → orchestrateur | résumé : capacités, compétences, préférences, limites |
| `PLAN_PROPOSAL` | orchestrateur → agent | partie du planning qui concerne ce membre + réunions |
| `PLAN_FEEDBACK` | agent → orchestrateur | accept, ou objections avec la contrainte concernée |
| `EVENT` | système → agent(s) concerné(s) | événement injecté |
| `ESCALATION` | agent ou orchestrateur → humain (journal) | problème non résolu |

## 8. Agent membre (`MemberAgent`, commun aux deux architectures)

- Possède son profil (privé) et son état (heures déjà engagées par semaine, tâches acceptées).
- Pour chaque décision, le **code** calcule d'abord les options faisables (semaines où la capacité, l'échéance et les
  dépendances connues le permettent, compétence suffisante). Le **LLM** choisit parmi ces options ou refuse,
  en tenant compte des préférences et des limites. Si aucune option n'est faisable, réponse `REFUSAL` sans appel LLM.
- Décisions confiées au LLM : candidater ou non à un CFP (et pour quelle semaine) ; accepter un `AWARD` ;
  voter pour des créneaux ; accepter ou contester un `PLAN_PROPOSAL`.
- Prompt système : rôle (« tu représentes <prénom> dans l'organisation du travail du groupe, tu défends ses intérêts
  sans bloquer le groupe »), profil, engagements actuels. Prompt utilisateur : message reçu, options faisables, schéma
  de réponse attendu. Prompts stockés dans `src/a2a_team/prompts/` (fichiers texte), identiques pour les deux architectures.

## 9. Architecture pair-à-pair (Contract Net)

1. Les tâches sont traitées par ordre d'échéance puis de dépendances. Chaque tâche a un **annonceur** : les agents
   prennent ce rôle à tour de rôle (round-robin), pour qu'aucun agent ne soit un chef déguisé.
2. L'annonceur envoie un `CFP` à tous les autres agents (et se considère lui-même comme candidat possible).
3. Chaque agent répond `PROPOSAL` ou `REFUSAL`.
4. L'annonceur choisit parmi les propositions (décision LLM, il voit les propositions et justifications, pas les
   profils privés des autres) et envoie `AWARD`. Le gagnant `CONFIRM` ou `DECLINE` (si sa situation a changé).
5. Sans proposition : nouvelle annonce avec les semaines suivantes autorisées, jusqu'à `max_rounds`, puis tâche
   `unassigned` + `ESCALATION`.
6. Réunions : un agent (round-robin) envoie un `MEETING_POLL` avec ses créneaux ; les autres votent ; le premier
   créneau commun est retenu ; sinon nouveau sondage par l'agent suivant.
7. Événements : injectés au tour prévu ; l'agent concerné libère les tâches qu'il ne peut plus tenir et les ré-annonce.

Aucun agent n'a de vue globale : chacun ne connaît que ses messages.

## 10. Architecture orchestrateur

1. L'orchestrateur envoie `INFO_REQUEST` à chaque agent, qui répond `INFO_REPLY` (résumé de son profil).
2. L'orchestrateur (même LLM) produit un planning complet en JSON à partir des réponses. Le code vérifie le format,
   **pas** les contraintes : l'orchestrateur peut se tromper, c'est ce que les agents doivent détecter.
3. Il envoie à chaque agent `PLAN_PROPOSAL` (sa partie). Chaque agent accepte ou envoie des objections précises.
4. L'orchestrateur révise le planning en tenant compte des objections, jusqu'à `max_rounds` révisions.
5. Événements : l'agent concerné prévient l'orchestrateur, qui replanifie la partie impactée.
6. À la fin, ce qui n'a pas pu être accepté devient `unassigned` + `ESCALATION`.

## 11. Générateur de scénarios (`src/a2a_team/scenarios/generator.py`)

- Paramètres tirés avec une graine : 3 à 7 membres (5 le plus souvent), 8 à 25 tâches, 1 à 3 réunions, 0 à 3 événements,
  3 semaines, compétences aléatoires, capacités selon des rythmes d'alternance (une semaine forte, deux faibles, ou l'inverse).
- Difficulté pilotée par le ratio heures demandées / capacité compatible : confortable (< 0,7), tendu (0,85 à 1),
  impossible (> 1,15).
- Splits : `dev` = 20 scénarios (graines 1000 à 1019), `test` = 50 scénarios (graines 2000 à 2049),
  `demo` = le scénario `notre_groupe` construit depuis `data/profiles/` et `data/project_tasks.yaml`.
- Fichiers dans `data/scenarios/{dev,test,demo}/`. Le split test est généré une fois et figé (hash dans DECISIONS.md).
- Vérifier avec la baseline que les scénarios « confortable » et « tendu » ont au moins une solution valide.

## 12. Mesures et campagne (`src/a2a_team/eval/`)

Par exécution (scénario × architecture × répétition) :
- `valid`, nombre et type de violations, `assigned_ratio`, `preference_ratio`, `load_balance`, `meetings_ratio`,
  `escalation_correct` ;
- coût : messages A2A, appels LLM, tokens, durée totale ;
- imprévus : planning valide après événement, messages nécessaires pour s'en remettre ;
- stabilité : sur les 3 répétitions, part des affectations identiques (Jaccard moyen).

Sorties : `results/<campagne>/runs.jsonl`, `summary.csv` (moyenne et écart-type par architecture et difficulté),
figures PNG (barres par métrique, avec la baseline). Commande :
`uv run python -m a2a_team.eval.run --split test --archs p2p orchestrator baseline --runs 3`.
La campagne doit pouvoir reprendre après interruption (exécutions déjà faites sautées).

**Notation à l'aveugle** (satisfaction, sur `notre_groupe`) : exporter les deux plannings dans
`results/notation_aveugle/` sous les étiquettes « Planning A » et « Planning B » attribuées au hasard (correspondance
dans un fichier séparé), avec un CSV à remplir : membre, planning, note 1 à 5, commentaire.

**Budget** : avant la campagne test, mesurer la durée moyenne d'une exécution sur dev et estimer la durée totale.
Si plus de 4 heures, prévenir l'humain et proposer : moins de répétitions, sous-ensemble de 30 scénarios, ou API.

## 13. Interface de démo (`ui/app.py`, Streamlit)

- Choix du scénario (par défaut `notre_groupe`) et de l'architecture, bouton « Lancer ».
- Fil des messages en direct : émetteur, destinataire, type, résumé (les messages s'affichent au fur et à mesure).
- Planning final : tableau tâche / membre / semaine, réunions, tâches non attribuées, escalades.
- Bouton « Injecter un imprévu » (absence d'un membre, nouvelle tâche urgente, échéance avancée).
- Mode « duel » : lance les deux architectures et affiche les métriques côte à côte.
- Mode « rejouer » : relit un `trace.jsonl` existant, pour une démo sans dépendre du temps de calcul.

## 14. Arborescence attendue

```
.
├── CLAUDE.md
├── README.md
├── config.yaml
├── pyproject.toml
├── docs/ (spec_prof.md, SPEC.md, PLAN.md, DECISIONS.md)
├── data/ (profiles/, project_tasks.yaml, scenarios/)
├── src/a2a_team/
│   ├── models.py  validator.py  baseline.py  llm.py
│   ├── prompts/
│   ├── agents/ (member.py, orchestrator.py)
│   ├── a2a_layer/ (server.py, client.py, launcher.py, trace.py)
│   ├── architectures/ (p2p.py, orchestrated.py)
│   ├── scenarios/ (generator.py, from_profiles.py)
│   └── eval/ (run.py, metrics.py, figures.py, blind.py)
├── ui/app.py
├── tests/
├── results/
└── rapport/ (brouillon.md, figures/)
```
