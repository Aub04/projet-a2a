# Spécification du projet métier IR5 (version rendue à la prof)

## 1. Contexte et objectif

Les agents basés sur des LLM sont de plus en plus utilisés pour automatiser des tâches, et la question suivante est de les faire travailler ensemble. Le protocole A2A (Agent2Agent), lancé par Google puis confié à la Linux Foundation, propose une façon standard pour des agents de se présenter, de s'envoyer des messages et de se déléguer du travail. Le protocole ne dit pas en revanche comment organiser un groupe d'agents qui doit se mettre d'accord : est-ce qu'ils discutent directement entre eux, ou est-ce qu'un agent central coordonne les autres ?

Notre projet consiste à comparer ces deux organisations sur un cas concret. Nous allons développer deux versions d'un même système multi-agents, l'une en pair-à-pair et l'autre avec un orchestrateur, puis mesurer laquelle aboutit aux meilleures décisions, pour quel coût et avec quelle fiabilité.

## 2. Le scénario : un agent par membre du groupe

Nous sommes cinq dans le groupe, et chacun de nous aura un agent qui le représente. L'agent connaît le profil de son membre, que chacun remplit lui-même : ses compétences (développement, IA, rédaction, présentation…), ses disponibilités réelles selon son rythme d'alternance, ses préférences et ses limites (par exemple « pas plus de 5 heures de travail sur le projet pendant une semaine en entreprise »).

Le problème à résoudre est celui que vit n'importe quel groupe de projet : se répartir une liste de tâches (avec une durée estimée, les compétences nécessaires et une échéance) et caler des créneaux de réunion. Les agents doivent arriver à un planning qui respecte les contraintes de chacun. Pour tester leur capacité à s'adapter, nous ajouterons des imprévus en cours de route : un membre absent quelques jours, une tâche urgente qui arrive, une échéance avancée.

Pour la démonstration, nous utiliserons notre propre projet comme exemple. Pour l'évaluation, cinq profils ne suffisent pas : nous générerons aussi des équipes fictives (profils, tâches et imprévus tirés aléatoirement) afin d'avoir une cinquantaine de cas de test.

## 3. Les deux architectures

Dans la version pair-à-pair, il n'y a pas de chef. Quand une tâche doit être attribuée, elle est annoncée à tous les agents ; ceux dont le membre est disponible et compétent font une proposition, puis les agents négocient directement entre eux jusqu'à un accord. C'est le principe du Contract Net Protocol, un protocole classique des systèmes multi-agents, que nous adaptons à A2A. Chaque agent défend les intérêts de son membre, ce qui devrait favoriser le respect des préférences, au prix d'un nombre d'échanges plus élevé.

Dans la version avec orchestrateur, un agent supplémentaire joue le rôle de coordinateur. Il interroge chaque agent sur les disponibilités et les préférences de son membre, construit une proposition de planning et la soumet aux agents, qui peuvent la refuser si elle dépasse les limites de leur membre. Cette organisation est plus simple à suivre, mais toutes les décisions passent par l'orchestrateur.

Les agents des membres sont les mêmes dans les deux versions : seule l'organisation des échanges change, ce qui rend la comparaison équitable.

## 4. Mise en œuvre technique

Le projet sera développé en Python avec le SDK officiel A2A. Chaque agent est un petit serveur A2A qui publie sa « carte d'agent » (qui il représente, ce qu'il sait faire) et échange des messages avec les autres. Pour que les deux versions soient comparables, tous les agents utilisent le même LLM open source, exécuté en local avec Ollama : pas de coût d'API, et des conditions identiques d'une architecture à l'autre. Si les temps de réponse sont trop longs sur nos machines, nous passerons sur une API gratuite pour les deux versions à la fois.

Les propositions et réponses échangées entre agents seront au format JSON (tâche, membre, créneau, accord ou refus), ce qui permet de vérifier automatiquement le planning final. Le nombre de tours de négociation sera limité pour éviter que les agents tournent en rond. Enfin, une interface web simple affichera les échanges en direct et le planning obtenu, ce qui servira pour la démonstration.

## 5. Évaluation

Les deux architectures seront lancées sur les mêmes cas de test, avec le même modèle. Un algorithme de répartition simple, sans IA, servira de point de comparaison. Nous mesurerons :

- la qualité du planning : contraintes respectées (vérifiées automatiquement), tâches attribuées avant leur échéance, équilibre de la charge entre les membres ;
- le coût : nombre de messages échangés, nombre d'appels au LLM et temps total ;
- la réaction aux imprévus : le planning est-il corrigé correctement, et en combien d'échanges ;
- la stabilité : un même cas relancé plusieurs fois donne-t-il le même résultat ;
- la satisfaction, sur notre propre projet : chacun de nous note de 1 à 5 le planning qui lui est attribué, sans savoir quelle architecture l'a produit.

Notre hypothèse de départ est que l'orchestrateur sera plus rapide et plus économe en messages, mais que le pair-à-pair respectera mieux les préférences de chacun. L'évaluation dira si c'est le cas.

## 6. Organisation, planning et livrables

Le travail est découpé en cinq parties, une par membre : le socle commun A2A (agents, format des messages) ; l'architecture pair-à-pair ; l'architecture avec orchestrateur ; les scénarios (profils, générateur de cas, imprévus) et l'algorithme de référence ; l'évaluation et l'interface de démonstration. La rédaction du rapport est partagée entre tous.

Nous ne sommes à l'école que cette semaine : l'essentiel du développement doit donc être terminé vendredi. Le reste se fera à distance, le soir et le week-end, pendant nos deux semaines en entreprise.

| Période | Objectif |
| --- | --- |
| Cette semaine, à l'école (jusqu'au 20 novembre) | Socle A2A, profils des cinq membres, générateur de cas, puis les deux architectures en version fonctionnelle |
| Deux semaines en entreprise, le soir et le week-end | Lancement des mesures, analyse des résultats, interface de démonstration, rédaction du rapport |
| Lundi 7 décembre | Rendu du rapport et soutenance |

Les livrables sont le code (dépôt Git), le rapport présentant les résultats de la comparaison, et une démonstration où nos cinq agents organisent le travail de notre groupe en direct, dans les deux architectures.
