# Kit de lancement : projet métier A2A

Ce dossier contient tout ce qu'il faut pour qu'un agent Claude Code réalise le projet. Il deviendra ensuite le dépôt Git
du projet (l'agent remplacera ce README par la vraie documentation).

## Contenu

| Fichier | Rôle |
| --- | --- |
| `CLAUDE.md` | Instructions permanentes de l'agent (lu automatiquement par Claude Code) |
| `PROMPT.md` | Le message à coller pour lancer l'agent, et celui pour reprendre plus tard |
| `docs/spec_prof.md` | La spec de 2 pages rendue à la prof |
| `docs/SPEC.md` | La spec technique détaillée pour l'agent |
| `docs/PLAN.md` | Les étapes, cochées par l'agent au fur et à mesure |
| `docs/DECISIONS.md` | Le journal des décisions tenu par l'agent |
| `data/profiles/m1.yaml` à `m5.yaml` | Vos profils, **à remplir par chacun** |
| `data/project_tasks.yaml` | Les tâches de notre projet (à ajuster si besoin) |

## Avant de lancer (30 minutes)

1. **Chacun remplit son profil** dans `data/profiles/` (un fichier par personne, `A_REMPLIR: false` à la fin).
2. Relire `data/project_tasks.yaml` et ajuster durées et échéances.
3. Sur la machine qui fera tourner le projet, installer : Python 3.11+, `uv`, Git, et Ollama.
   Puis télécharger le modèle : `ollama pull qwen3:8b` (ou un autre modèle de 7-9 milliards de paramètres ;
   l'agent l'indiquera dans `config.yaml`). Il faut idéalement une carte graphique avec 8 Go de mémoire.
   Sans GPU, prévoir l'option API gratuite prévue dans la spec.

## Lancer l'agent

Ouvrir Claude Code dans ce dossier, puis coller le premier message de `PROMPT.md`.
Laisser l'agent avancer ; il s'arrête de lui-même s'il a besoin de vous (profils manquants, modèle absent,
calcul trop long, choix qui touche à l'équité de la comparaison).

## Ce que vous devez vérifier vous-mêmes

- **Vendredi 20 novembre** : lancer les deux architectures sur `notre_groupe` avec la commande donnée par l'agent,
  et lire une trace de négociation. Si ça ne tourne pas, c'est la priorité absolue avant de rentrer en entreprise.
- **Les résultats** : relire `results/` et `docs/DECISIONS.md`, vérifier que les chiffres ont du sens.
- **La notation à l'aveugle** : chacun note les plannings A et B dans le CSV fourni, sans regarder lequel est lequel.
- **Le rapport** : `rapport/brouillon.md` est un brouillon factuel. Il faut le réécrire avec vos mots et compléter
  les passages `[À COMPLÉTER PAR LE GROUPE]`.
- **La soutenance** : chacun doit pouvoir expliquer sa partie du code et les choix faits. Répartissez-vous les
  modules (voir la section 6 de la spec) et lisez-les avant le 7 décembre.
