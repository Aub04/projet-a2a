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
