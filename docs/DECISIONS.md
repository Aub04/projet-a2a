# Journal des décisions

Chaque entrée : date, décision, raison, impact sur la comparaison entre les deux architectures.

## 2026-11-17 : point de départ

- Périmètre : spec rendue à la prof (docs/spec_prof.md). Deux architectures A2A (pair-à-pair, orchestrateur),
  LLM open source en local via Ollama, un agent par membre (5 membres), baseline sans IA.
- Les decision models envisagés au départ ont été abandonnés : agents LLM classiques uniquement.

## 2026-11-17 : état de départ constaté

- Les cinq profils `data/profiles/m1.yaml` à `m5.yaml` sont encore les modèles (`A_REMPLIR: true`, contenu identique).
  Le scénario `notre_groupe` ne peut pas être construit tant qu'ils ne sont pas remplis. En attendant, le code est
  testé sur les scénarios générés et, pour les essais techniques seulement, sur les modèles
  (`--allow-templates`, fichier écrit dans `runs/`, jamais dans `data/scenarios/demo/`).
- Machine de développement : i7-1260P, 16 Go de RAM, pas de GPU dédié (Iris Xe). Ollama n'était pas installé.

## 2026-11-17 : générateur de scénarios par solution cachée

- Décision : les tâches sont construites à partir d'une affectation valide tirée au hasard (« solution cachée ») au
  lieu d'être tirées puis filtrées par la baseline. La solution cachée est rangée dans `data/scenarios/<split>/ref/`,
  elle n'est jamais montrée aux agents.
- Raison : avec la génération libre, aucun scénario « tendu » (ratio 0,85 à 1) n'était résolu par la baseline
  gloutonne, même après 500 tirages. La solution cachée garantit qu'un planning valide et complet existe pour tous les
  scénarios confortables et tendus (tâches et réunions), ce qui est ce que la spec demande de vérifier.
- Impact : aucun sur l'équité (mêmes scénarios pour les deux architectures). La baseline sert toujours de point de
  comparaison, mais elle n'est plus le critère de solubilité. Ratio de difficulté = heures demandées / capacité totale.
- Écart à la spec : un scénario « impossible » l'est par la capacité totale (ratio > 1,15), sans vérification
  supplémentaire.

## 2026-11-17 : splits dev et test figés

- `dev` : 20 scénarios (graines 1000 à 1019), 8 confortables, 8 tendus, 4 impossibles.
  SHA-256 du dossier `data/scenarios/dev` : `184b5fe707351180afbd332e10c3ddcf3826dee3699c06cbd42e25a8d99b8c3c`
- `test` : 50 scénarios (graines 2000 à 2049), 20 confortables, 20 tendus, 10 impossibles.
  **SHA-256 du dossier `data/scenarios/test` : `666c84da46a73aeeac5cf0f6233dd28c8c3f250f8be90e38188193a87c086f98`**
  (hash des fichiers `*.json` triés par nom, nom + contenu en fins de ligne LF ; vérifié par `tests/test_scenarios.py`).
- Générés avant toute écriture de prompt. Le générateur refuse d'écraser un split existant.

## 2026-11-17 : vérification de solubilité avec la baseline

Toutes les instances confortables et tendues ont une solution de référence valide et complète. La baseline gloutonne
de la section 4 trouve un planning complet dans les cas suivants :

| split | difficulté | scénarios | baseline complète | baseline complète après imprévus |
| --- | --- | --- | --- | --- |
| dev | confortable | 8 | 5 | 4 |
| dev | tendu | 8 | 0 | 0 |
| test | confortable | 20 | 15 | 16 |
| test | tendu | 20 | 0 | 0 |

La baseline ne trouve donc jamais de solution complète sur les scénarios tendus alors qu'il en existe une : le glouton
« plus grande capacité restante » consomme la capacité de membres dont la compétence est rare. C'est un résultat à
garder dans le rapport (la baseline est un plancher, pas une référence optimale). Détail par scénario :
`uv run python -m a2a_team.scenarios.generator --check`.

## 2026-11-17 : API de a2a-sdk 1.3.0 utilisée (lue dans le code installé)

- Version épinglée : `a2a-sdk[http-server]==1.3.0` (spec A2A 1.0, types protobuf).
- Serveur : une classe dérivée de `a2a.server.agent_execution.AgentExecutor` (méthodes `execute(context, event_queue)`
  et `cancel`) ; le message reçu est `context.message`, la réponse est publiée par `event_queue.enqueue_event(message)`.
- `DefaultRequestHandler(agent_executor, task_store=InMemoryTaskStore(), agent_card)` (alias de `DefaultRequestHandlerV2`).
- Routes Starlette : `create_agent_card_routes(card)` (carte sur `/.well-known/agent-card.json`) et
  `create_jsonrpc_routes(handler, rpc_url="/")`, servies par uvicorn.
- Carte : `AgentCard(name, description, version, supported_interfaces=[AgentInterface(url, protocol_binding="JSONRPC",
  protocol_version="1.0")], capabilities, default_input_modes, default_output_modes, skills=[AgentSkill(...)])`.
- Client : `await create_client(url, client_config=ClientConfig(streaming=False, httpx_client=...))` résout la carte ;
  `client.send_message(SendMessageRequest(message=...))` est un itérateur asynchrone de `StreamResponse`
  (champ `message` quand l'agent répond par un message direct).
- DataPart : `a2a.helpers.proto_helpers.new_data_message(dict, role=Role.ROLE_USER)` et `get_data_parts(parts)`.
  Les nombres passent par `google.protobuf.Value` et reviennent en flottants : on rétablit les entiers à la réception.
- Preuve de concept validée : serveurs locaux qui échangent des DataPart JSON, réponses produites par un appel LLM
  (faux LLM en attendant Ollama) ; voir `run_demo --fake-llm`.

## 2026-11-17 : choix de mise en œuvre des protocoles

Ces choix s'appliquent de la même façon aux deux architectures, sauf mention contraire.

1. **Imprévus après la négociation initiale.** La négociation se fait d'abord sur le scénario sans imprévus, puis les
   imprévus sont injectés un par un dans l'ordre de `trigger_round`, et l'architecture répare. Raison : un « tour » n'a
   pas le même sens dans les deux architectures (une tâche annoncée contre une révision de planning) ; injecter au même
   moment logique rend la mesure « valide après imprévu » et « messages pour s'en remettre » comparable.
   L'imprévu est envoyé par le système à l'agent concerné (le membre absent, le détenteur de la tâche, sinon un agent
   pris à tour de rôle). En p2p il libère et ré-annonce ; en orchestrateur il prévient l'orchestrateur, qui replanifie.
2. **Message ajouté en p2p : `AWARD_NOTICE`** (annonceur vers tous, après confirmation). C'est le « reject-proposal »
   du Contract Net classique, et il sert aussi à connaître la semaine des dépendances sans vue globale. Il est compté
   dans le coût du p2p.
3. **Messages ajoutés en orchestrateur : `START`, `PLAN_FINAL`.** `START` vient du système (non compté). `PLAN_FINAL`
   informe un membre de sa partie définitive quand des éléments contestés ont été retirés (compté).
4. **Messages du système non comptés** : `ANNOUNCE` (c'est le tour de tel agent d'annoncer telle tâche), `START_POLL`,
   `START`, `EVENT` du système vers l'agent concerné. Ils jouent le rôle d'horloge de la simulation. Les `ACK` et les
   `ESCALATION` (écrites au journal) ne sont pas comptés non plus.
5. **Pas d'appel LLM quand il n'y a pas de choix** : refus sans option faisable (prévu par la spec), attribution
   directe quand l'annonceur n'a reçu qu'une proposition, vote vide sans créneau compatible, objections dures
   d'une proposition de planning (envoyées par le code sans demander au LLM).
6. **Relances p2p** : une tâche sans proposition est ré-annoncée aux mêmes semaines (jamais après l'échéance, ce serait
   une violation) avec la mention de relance, jusqu'à `max_rounds`. Si tous les refus sont « durs » (aucune option
   faisable pour personne), on escalade tout de suite.
7. **Orchestrateur** : après une révision, seuls les membres dont la partie a changé (ou qui ont objecté) sont
   reconsultés. Après `max_rounds` révisions, les éléments encore contestés sont retirés du planning et escaladés.
8. **Erreur LLM persistante** (JSON invalide après 2 relances) : repli par le code (refus, ou acceptation si aucune
   objection dure), journalisé dans `llm_calls.jsonl` avec `fallback: true`.
9. **Transport** : HTTP A2A réel par défaut. Le transport mémoire (mêmes objets Message A2A) sert aux tests et reste
   disponible avec `--transport memory`.

## 2026-11-17 : profils du groupe rédigés par l'agent (écart à la spec prof)

- Décision : à la demande d'Aubin, les cinq profils (Aubin, Gaël, Mahdi, Thomas, Mohammed) ont été rédigés par
  l'agent comme brouillon plausible, au lieu d'être remplis par chacun. Celui d'Aubin s'appuie sur son parcours réel
  (majeure IA, alternance, freelance web) ; les quatre autres sont inventés.
- Écart : `docs/spec_prof.md` indique que chacun remplit lui-même son profil. **À signaler à la prof si les profils ne
  sont pas relus.**
- Impact : aucun sur l'équité entre architectures (même scénario pour les deux). En revanche, la notation à l'aveugle
  de la satisfaction n'a de sens que si chacun a relu et corrigé son profil avant les exécutions réelles.
- Caractéristiques choisies pour rendre la négociation intéressante : deux membres très demandeurs d'`ia_llm`
  (Aubin, Mahdi), une semaine S49 presque vide pour Mahdi (3 h), Thomas limité à 2 tâches par semaine, Gaël qui veut
  écrire le rapport, seulement 3 créneaux de réunion communs pour 2 réunions.
- Scénario obtenu : 76 h de tâches pour 140 h disponibles (ratio 0,54, « confortable ») ; baseline, p2p et
  orchestrateur (faux LLM) trouvent un planning complet et valide.

## 2026-11-17 : modèle retenu et temps de réponse

- Modèle : `qwen3:8b` via Ollama 0.40.16 (ID `500a1f067a9f`, 5,2 Go), `think: false`, température 0,2, graine 42,
  contexte 8192. Inchangé par rapport à `config.yaml`.
- Mesure sur le PC d'Aubin (i7-1260P, 16 Go, sans GPU) : décision de candidature d'un agent membre, 547 tokens en
  entrée et 70 en sortie, 59,6 s au premier appel (chargement du modèle) puis 10,9 s.
- Un modèle 27B a été essayé puis abandonné : 16,8 Go, il ne tient pas dans la mémoire de cette machine.

## 2026-11-17 : point d'étape du jalon (premières exécutions réelles sur notre_groupe)

Les deux architectures ont tourné de bout en bout sur `notre_groupe` avec `qwen3:8b`, sur de vrais serveurs A2A HTTP.
Traces archivées dans `results/notre_groupe_2026-11-17/`. Une seule exécution par architecture : ce sont des
observations, pas encore des résultats.

| | p2p | orchestrateur | baseline |
| --- | --- | --- | --- |
| tâches attribuées | 15/15 | 10/15 | 15/15 |
| réunions calées | 1/2 | 2/2 | 2/2 |
| planning valide | oui | oui | oui |
| préférences (score) | +0,60 | +0,80 | +0,40 |
| messages A2A | 258 | 61 | 0 |
| appels LLM | 113 | 13 | 0 |
| durée | 1848 s | 902 s | < 1 s |

Constats à vérifier sur les scénarios dev avant d'en tirer quoi que ce soit :

- **Orchestrateur** : les 21 objections reçues sont toutes des contraintes dures (16 capacité, 4 compétence,
  1 nombre de tâches), concentrées sur S47. En 6 révisions, le modèle n'arrive pas à faire tenir les tâches de S47 dans
  les heures disponibles ; 5 tâches finissent retirées et escaladées. Le code a bien joué son rôle (aucune violation
  dans le planning final).
- **P2P, réunions** : 3 créneaux sont communs à tous, mais les agents n'acceptent que 2 ou 3 des créneaux compatibles
  à chaque sondage, sans raison liée au profil. Aucun accord n'est trouvé pour la 2e réunion après 5 sondages.
  Piste de mise au point (sur dev uniquement) : prompt de vote.
- Hypothèse de départ (orchestrateur plus économe) : cohérente avec cette exécution (4 fois moins de messages,
  9 fois moins d'appels LLM, 2 fois plus rapide). Hypothèse « p2p respecte mieux les préférences » : pas observée ici
  (+0,60 contre +0,80), mais l'orchestrateur a attribué moins de tâches, ce qui biaise le score.

Ce qui manque : phase 6 (campagne de mesures ; à 11 s par appel sur ce PC, la campagne test dépasse largement 4 h,
à mesurer sur le PC de calcul), phase 7 (interface Streamlit), phase 8 (brouillon du rapport).

## 2026-11-20 : préparation de la campagne finale

Changements de comportement, réglés sur `dev` uniquement (aucun regard sur `test`) :

1. **Vote de réunion par refus (p2p).** Le 17/11, les agents n'acceptaient que 2 ou 3 des créneaux compatibles, avec
   des préférences inventées (« évite les mercredis » sans rien de tel dans le profil), et la 2e réunion n'a pas été
   calée. Le schéma de réponse devient `{"refused": [...], "reason": "..."}` : un créneau compatible est accepté sauf
   refus motivé par le profil. Le prompt `member_vote.txt` est commun aux deux architectures (seul le p2p fait des
   sondages). Impact : favorise le p2p sur `meetings_ratio`, ce qui corrige un défaut de prompt, pas du protocole.
2. **Aides de révision de l'orchestrateur** (`limits.orchestrator_hints`, activé par défaut). Le 17/11, les
   21 objections étaient des dépassements d'heures que le LLM n'arrivait pas à corriger en 6 révisions (5 tâches
   retirées). Au moment d'une révision, le code ajoute au prompt le tableau de charge du planning précédent
   (heures prévues / disponibles par membre et semaine), une proposition de place pour chaque tâche contestée (les
   propositions tiennent ensemble : elles sont réservées l'une après l'autre) et la liste des créneaux de réunion
   libres pour tous. Un premier essai avec des options calculées tâche par tâche montrait le modèle déplacer un bloc
   de 4 tâches d'un membre à un seul autre (dev_1013). Ces calculs n'utilisent que ce que l'orchestrateur a reçu
   (`INFO_REPLY`). C'est
   l'application à l'orchestrateur de la règle déjà suivie par les membres : « le LLM choisit parmi des options
   faisables, il ne calcule pas ». Le premier planning reste produit sans aide, et le code ne corrige rien lui-même.
   Impact : favorise l'orchestrateur sur `assigned_ratio`. Pour mesurer l'effet, une campagne avec
   `orchestrator_hints: false` reste possible. **[À VALIDER PAR LE GROUPE]**
3. **Graine par répétition.** Avec `seed: 42` fixe et une température de 0,2, les 3 répétitions donnaient les mêmes
   réponses. La graine envoyée au LLM devient `seed + numéro de répétition` (42, 43, 44), identique pour les deux
   architectures. Sans cela la mesure de stabilité n'a pas de sens.

Campagne (`src/a2a_team/eval/`, lancement en une commande par `campagne.cmd` ou `campagne.sh`) :

- ordre : répétition 1 sur les 50 scénarios test, puis 2, puis 3 ; difficultés alternées. Une campagne arrêtée en
  route donne déjà une répétition complète et équilibrée ; reprise automatique ;
- la baseline est déterministe : une seule exécution par scénario ;
- une exécution en panne d'infrastructure (Ollama arrêté, port occupé) n'est pas enregistrée et sera refaite ;
  une erreur du LLM (JSON invalide, budget d'appels) est un résultat et reste enregistrée ;
- `meta.json` garde la machine, le GPU, la version d'Ollama, l'empreinte du modèle et le commit git de chaque session.

Budget : sur le PC d'Aubin (CPU seul), un scénario test prendrait environ 50 min pour les deux architectures, soit
plus de 120 h pour 3 répétitions. La campagne tourne donc sur le PC de calcul. L'estimation est affichée au
lancement à partir d'un appel mesuré ; elle dépasse 4 h dans tous les cas réalistes, d'où l'ordre par répétition et
l'option `--max-hours`.

**Scénario de démo tendu.** `notre_groupe` est confortable (la baseline le résout seule). `notre_groupe_tendu`
(`uv run python -m a2a_team.scenarios.tendu_demo`) garde nos membres, tâches et créneaux avec 80 % des heures
disponibles : une recherche exhaustive trouve un planning complet, la baseline n'attribue que 14 tâches sur 15.
Il sert à la démonstration seulement. La comparaison chiffrée avec la baseline se fait sur le split test, qui
contient déjà 20 scénarios tendus où la baseline ne trouve jamais de solution complète alors qu'il en existe une.
Rien ne garantit à l'avance que les agents fassent mieux : c'est ce que la campagne mesure.

Essai réel partiel de la version finale (orchestrateur sur `dev_1013`, interrompu après 4 plannings, 612 s) : les
4 plannings sont au bon format, les aides apparaissent dans le prompt. Les objections baissent à la 2e et 3e révision
(2 membres acceptent), puis le modèle remet un créneau de réunion déjà refusé et laisse T10 sur un membre sans place.
Pas de conclusion possible sur une exécution partielle : la campagne mesurera.
