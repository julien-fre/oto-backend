---
title: Déploiement de la plateforme
type: reference
description: >-
  `main` = tronc = preprod, tag vX.Y.Z = prod : pourquoi le modèle tronc unique, les domaines
  RÉELLEMENT servis (prod `.cx`, preprod `.ninja`), ce que fait — ou ne fait pas — le workflow
  de release, la garde « préprod verte » et la fenêtre finie du healthcheck. Vaut pour le backend
  et le dashboard ; les commandes sont dans `commands.md`.
---

# Déploiement — modèle tronc unique

> Les **commandes** (push, tag, logs, inspection de base) vivent dans `commands.md` ; ce doc
> porte le **modèle**, ses motifs et ses pièges. Le détail machine (box, scripts serveur,
> bleu/vert) est dans le dépôt d'infra privé ; la déclaration d'une autre cible de déploiement,
> dans `instance-cible.md`.

## Un tronc `main`, un checkout, pull/commit/push

Plus de dualité `canari`/`main` (backend et dashboard). Motif : les PR des sessions Claude
Code (web) ciblaient la branche de prod par défaut et se faisaient refuser par une garde —
« ça ne déploie plus ». Bascule vers le trunk-based :

- **`main` = tronc = PREPROD.** On travaille directement dans le checkout du dépôt :
  `git pull --rebase` avant de bosser, commit, puis `git pull --rebase && git push` →
  **déploiement preprod automatique** (`mcp.oto.ninja` / `manage.oto.ninja`, porte
  `needs: test`). Depuis le 08/10/2026 (#1185), ce `test` ne joue que les **volets touchés**
  par le diff (base = `before` du push) **plus le socle** de gardes transverses (sortie
  déclarée des capacités, façade d'erreur MCP, contrat servi, concordance carte/client,
  portée, inventaire d'env, silences, SQL hors boucle, surfaces figées) : table
  `tests/volets.toml`, script `scripts/selection_tests.py`, le résumé du run dit ce qui a été
  retenu et pourquoi. Base inconnue, fichier transverse ou hors volet : suite complète. On ne commite sur `main` que du shippable en preprod. Les PR ouvertes par
  Claude Code (web) ciblent `main` : elles déploient la preprod **au merge**.
- **tag `vX.Y.Z` = PROD.** La mise en prod est un **acte explicite** : poser le tag sur un
  commit de `main` et le pousser déclenche « Deploy prod » (garde → déploiement → healthcheck
  → rollback). Rollback = redéployer le tag précédent. Les tags `v*` sont **immuables**
  (ruleset GitHub : un tag de release ne se re-pointe pas). Côté backend, le tag est passé
  au script serveur (`oto-backend.sh <tag>` : `git reset --hard <tag>`). Côté dashboard :
  artefact seul, build au tag.
- **La suite complète tourne au tag, une seule fois** (#1185, 08/10/2026) : le job `test` de
  « Deploy prod » la joue sur l'arbre du tag et `deploy` l'attend. La prod est donc servie
  après la durée de la suite (~9-13 min), plus ~1 min 30 après le tag. Une relance « Re-run
  failed jobs » ne rejoue pas un `test` déjà vert. Sur un rouge, le résumé du run dit si
  chaque fichier rouge aurait été joué au push (constat demandé par #1185 ; sélection
  recalculée parent → sha, donc approchée).
- **La prod exige aussi une préprod verte** : un run de préprod vert sur le sha exact du tag
  (`scripts/garde_preprod_verte.py`), refusé sinon, en nommant le run lu et sa conclusion.
  Ce vert dit « déployé en préprod, sélection et socle verts », **pas** « suite complète ». Donc : taguer un commit **déjà poussé sur
  `main`** et dont le run « Deploy preprod » est vert. Porte de secours (run purgé, incident
  GitHub) : « Deploy prod » en `workflow_dispatch` avec `sans_garde_preprod: true` —
  délibéré, tracé, hors d'atteinte d'une simple poussée de tag. Il ne contourne que la
  garde : la suite complète du tag tourne toujours.
- ⚠️ **La fenêtre du healthcheck serveur est finie** (120 s à ce jour, ~60 s constatés à
  l'origine) : un lot qui ajoute un travail one-shot au boot (re-projection, migration au
  démarrage) la vérifie — et l'élargit — **avant** le tag, sinon rollback automatique sur un
  déploiement sain. Ce qui n'a rien à faire au boot va en maintenance (`oto-mcp maintenance …`).
- **Prod et preprod partagent la MÊME base.** Ce qu'on écrit depuis la preprod est la donnée
  de prod ; seule la prod fait tourner les boucles de fond qui agissent sur un tiers
  (`live-migrations.md`).
- **Release log = `RELEASES.md` du dépôt public `oto`** : notes datées **rédigées à la
  main** par le mainteneur, format libre, niveau produit/plateforme (cross-repo), décorrélé
  des tags (qui restent le mécanisme de déploiement). Pas de génération automatique en CI ni
  de GitHub Releases par tag (rejeté : « garder la main sur les notes »). Un agent peut
  proposer un brouillon (matière : les trailers `Changelog:` des commits), le mainteneur valide.
- **`main` est protégée légèrement** : la contrainte vise les tiers (proposition + check
  `test`), pas l'équipe — le mainteneur garde le push direct sur `main`, qui est
  l'itération preprod rapide (cf. `LICENSING.md` du dépôt public `oto` pour la forme de la
  protection).
- **Dépôts mono-branche** (sites, CLI, plugin…) : inchangés, un push = leur unique flux.

## Plusieurs sessions sur un même checkout : `--autostash` est interdit

Mesuré le 09/09/2026, après l'avoir causé. `--autostash` remise puis restaure le **contenu**
du travail en cours — mais **pas l'INDEX**. Deux sessions qui poussent à tour de rôle se
**dé-indexent mutuellement, en silence** : ce qui était `git add`é redevient non stagé.

La conséquence est plus grave que la gêne. L'index est précisément ce dont on se sert pour ne
prendre QUE ses fichiers dans un arbre partagé — indexer nommément est la protection contre
le commit trop large. Cette protection est effacée par le geste le plus banal du dépôt. Et le
mode d'échec est le pire : un `git commit` sur un index vidé **ne lève aucune erreur**, il
rend un commit vide ou incomplet, et on croit avoir livré.

Trois règles : ne jamais s'appuyer sur un index qui a passé du temps ; indexer et commiter
dans le même souffle ; vérifier juste avant de commiter, pas cinq minutes plus tôt. Si le
`pull --rebase` refuse à cause d'un travail en cours, **ne pas remiser le checkout
partagé** : commiter ses fichiers nommément d'abord.

## Les domaines servis

Mesurés le 03/09/2026 (jusque-là, la doc nommait les *hostnames de transition* du cutover
de la base chiffrée, pas les adresses réellement servies) :

| | backend | dashboard |
|---|---|---|
| **PROD** (tag `v*`) | `mcp.oto.cx` | `manage.oto.cx` |
| **PREPROD** (push `main`) | `mcp.oto.ninja` | `manage.oto.ninja` |

**Le piège est actif, pas théorique** : les anciens noms répondent encore.

- Un ancien hostname de dashboard répond **200** mais sert un **vieux build** (un chunk
  unique là où la prod en sert onze). **Ce n'est pas la prod** : une mesure faite là
  « prouve » que la production ne sert pas ce qu'elle sert. Un 404 aurait protégé ; un 200
  sur un build fossile ne protège de rien.
- `dashboard-canari.oto.ninja` répond **302** vers `manage.oto.ninja` : bon endroit, si l'outil
  de mesure suit les redirections.
- `mcp-canari.oto.ninja` sert la **même** instance de preprod que `mcp.oto.ninja` : alias
  vivant, mais le nom canonique est `mcp.oto.ninja`.

Source de vérité côté dashboard : son propre `docs/deploiement.md`.

## Poser un tag ne fait pas ce qu'on croit

Lu dans les workflows (`release.yml` et `deploy*.yml` du backend, `tag-release.yml` et
`deploy*.yml` du dashboard).

- **Le workflow de release tague le SOMMET DU TRONC à l'instant du déclenchement, et
  n'accepte aucun commit nommé.** Le dashboard fait un checkout `ref: main` puis `git tag`
  sur ce HEAD ; le backend tague le HEAD du ref sur lequel il est dispatché (et refuse de
  publier si « Deploy preprod » est rouge sur ce commit). Leur seul input de version est un
  `vX.Y.Z`, **jamais un SHA**. Un tag qui doit viser un commit précis, différent du sommet,
  se pose donc **localement** (`git tag vX.Y.Z <commit> && git push origin vX.Y.Z`) ;
  passer par le workflow taguerait autre chose, silencieusement.
- **Sur le dashboard, le tag posé par le workflow ne déclenche PAS le déploiement.** Un tag
  créé avec le `GITHUB_TOKEN` ne déclenche aucun autre workflow (garde anti-récursion
  GitHub) : `deploy.yml` (`on: push: tags: ["v*"]`) reste au repos. Il faut ensuite lancer
  « Deploy prod » en `workflow_dispatch` avec le même tag : **deux actes**. Le backend
  contourne la garde : `release.yml` **appelle** `deploy.yml` (`workflow_call`) juste après
  avoir posé la version : **un seul acte**. Les deux dépôts ne se pilotent donc pas pareil ;
  un tag posé depuis Actions sur le dashboard et laissé là, c'est une version gravée et rien
  de déployé.
