---
title: Contributions de forks
type: reference
description: >-
  Trois pièges qui ont chacun coûté des jours d'attente sur des PR saines : l'approbation des
  workflows redemandée à chaque commit, le workflow exécuté depuis la branche HEAD et non la
  base, et le pin oto-core qui se bumpe sur le tronc.
---

# Recevoir une contribution d'un FORK — trois pièges

> Complète `docs/verrou-dependances.md` (le pin et `uv.lock`) et le CLA du dépôt public
> `oto` (`CLA.md`, `LICENSING.md`) : une PR externe ne se merge qu'une fois le CLA signé.

Les contributeurs externes travaillent sur leur fork : la mécanique n'est pas celle d'une
branche interne, et chacun de ces trois points a coûté des jours d'attente sur des PR saines.

- **L'approbation des workflows est redemandée à CHAQUE nouveau commit d'un fork**, pas une
  fois par PR. Tant qu'elle manque, le check requis `test` **n'existe pas** : la PR affiche
  `BLOCKED` alors qu'aucune vérification n'a jamais tourné, ce qui se lit à tort comme un
  échec (deux PR ont dormi trois jours ainsi). Les repérer :
  `gh api "repos/O/R/actions/runs?status=action_required"` puis
  `gh api -X POST repos/O/R/actions/runs/<id>/approve`.
- **Un event `pull_request` exécute le workflow de la branche HEAD, jamais celui de la
  base** : réparer la CI sur `main` ne répare AUCUNE PR ouverte tant que `main` n'est pas
  mergée dedans (`gh api -X PUT repos/O/R/pulls/N/update-branch`). Vrai pour tout changement
  de `.github/workflows/`.
- **Le pin oto-core se bumpe SUR LE TRONC, jamais dans le fork.** L'API Contents rend 404 sur
  le dépôt personnel d'un contributeur (`maintainer_can_modify` ne vaut que pour un
  `git push`), et y écrire est de toute façon à éviter. Bumper le pin sur `main` puis
  `update-branch` sert la garde version-skew **sans toucher au dépôt d'autrui**.
  Corollaire : un contributeur n'a pas à attendre un tag pour avancer, le mainteneur le pose
  et le propage.

## Branche interne contre fork : deux règles, deux domaines

Le commentaire du `pyproject.toml` dit : *« on tague oto-core et on bump CE pin par PR ;
rollback = revert ce commit (ramène aussi le bon oto-core) »*. Le troisième piège dit
l'inverse : **sur le tronc, jamais dans le fork.** Les deux ont raison dans leur domaine :

- **branche interne** : le pin se bumpe **dans la PR**. L'argument est l'**atomicité du
  retour arrière** : le revert du commit ramène le bon `oto-core` avec lui.
- **PR venue d'un FORK** : le pin se bumpe **sur le tronc**, puis `update-branch`.
  L'argument est plus fort que l'atomicité : **on n'écrit pas dans le dépôt d'autrui**. L'API
  Contents rend d'ailleurs 404 et le contrôle de permissions refuse ce chemin : deux
  signaux mécaniques qui disent la même chose.

Une session a un jour demandé le bump **dans les branches** de deux PR de fork, contre cette
règle. Ça a fonctionné (même commentaire de version dans les deux branches, donc le conflit
sur le manifeste au second merge se résout de lui-même) **mais la méthode était fausse**, et
le contrôle de permissions avait refusé une première fois avant de laisser passer. Une
consigne qui ne passe qu'au deuxième essai d'un garde-fou mérite d'être relue avant d'être
répétée.

⚠️ Le prochain lot qui touche ce commentaire du `pyproject.toml` doit **nommer l'exception du
fork** : un texte qui énonce une règle sans son exception fabrique la contradiction que
quelqu'un tranchera de travers dans six mois.
