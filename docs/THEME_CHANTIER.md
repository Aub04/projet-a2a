# Variante « chantier » : coordination de sous-traitants avec informations privées

Brouillon de travail, à relire et réécrire par le groupe.

## Pourquoi cette variante

Sur le thème d'origine (répartir les tâches de notre groupe), toutes les informations utiles sont structurées et
peuvent être données à un programme central. Dans ce cas, un algorithme central simple est difficile à battre : il
voit tout, il ne négocie pas, il ne coûte rien. Un système multi-agents n'a d'intérêt que dans les situations où cette
centralisation n'est pas possible. La plus courante en entreprise : des organisations indépendantes doivent se
coordonner sans vouloir partager toutes leurs données. C'est aussi le cas d'usage pour lequel le protocole A2A a été
conçu (des agents de fournisseurs différents qui coopèrent sans base de données commune).

## La situation

Un chantier de rénovation réunit 4 à 7 entreprises indépendantes : plomberie, électricité, chauffage et ventilation,
plâtrerie, menuiserie, carrelage, peinture. Il faut planifier sur trois semaines les interventions (par exemple
« Plâtrerie, logement B2 »), dans l'ordre imposé par les corps de métier : les réseaux (plomberie, électricité,
chauffage) avant les cloisons, les cloisons avant les menuiseries et les sols, la peinture en dernier. Il faut aussi
caler des réunions de chantier où toutes les entreprises sont présentes.

Chaque entreprise déclare ses heures d'équipe par semaine. Mais la plupart sont engagées sur d'autres chantiers
certaines semaines, ce qu'elles ne disent pas : leurs clients et leurs prix sont confidentiels. Leurs vraies heures
disponibles sont donc plus faibles que leurs déclarations ces semaines-là (entre 15 et 60 % des heures déclarées).

## Qui sait quoi

| | voit les heures déclarées | voit les vraies heures |
| --- | --- | --- |
| coordinateur central (baseline) | toutes | aucune |
| oracle (référence, irréaliste) | toutes | toutes |
| agent d'une entreprise (p2p et orchestrateur) | toutes | les siennes seulement |
| orchestrateur LLM | toutes (via `INFO_REPLY`) | aucune ; il les devine par les objections |

Le planning est jugé sur les vraies heures. Le coordinateur central produit donc des plannings qui paraissent
complets mais sont souvent irréalisables. L'oracle montre ce qu'on obtiendrait si les entreprises acceptaient de
tout partager. Les agents connaissent leur propre réalité : en p2p, chacun ne se positionne que sur ce qu'il peut
vraiment faire ; avec l'orchestrateur, chacun objecte quand une proposition dépasse ses vraies heures.

## Ce que montrent les scénarios avant tout LLM

Vérification `uv run python -m a2a_team.scenarios.chantier --check` sur le split test (50 scénarios) :

| difficulté | scénarios | coordinateur central complet | oracle complet |
| --- | --- | --- | --- |
| confortable | 20 | 1 | 13 |
| tendu | 20 | 0 | 0 |
| impossible | 10 | 0 | 0 |

Sur les scénarios confortables, l'information privée fait passer l'algorithme central de 13 plannings complets à 1.
Les scénarios tendus restent hors de portée des deux gloutons, comme dans le thème d'origine.

Essai technique avec le faux LLM (réponses scriptées, coopératives, sans intelligence) : le p2p obtient des plannings
réalisables à 95 % sur les scénarios confortables, contre 63 % pour le coordinateur central. Ces chiffres ne valent pas
résultat : seule la campagne avec le vrai modèle compte. Ils montrent que la mécanique fonctionne.

## Questions que la campagne doit trancher

1. Les agents LLM font-ils mieux que le coordinateur central qui n'a que les déclarations ?
2. De combien restent-ils en dessous de l'oracle (le coût de la confidentialité) ?
3. Entre p2p et orchestrateur, lequel se rapproche le plus de l'oracle, et pour quel coût en messages et appels LLM ?
4. L'orchestrateur arrive-t-il à corriger ses propositions à partir des objections, sans connaître les vraies heures ?

[À COMPLÉTER PAR LE GROUPE] : faut-il présenter cette variante à la prof comme extension du sujet validé ?
