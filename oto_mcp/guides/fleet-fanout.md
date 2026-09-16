---
title: "Trop de lignes pour une conversation : arme un fleet"
description: quand une file dépasse ce qu'une conversation peut boucler, arme un `oto_fleet` plutôt que de dérouler `data_claim_next` jusqu'au plafond de tours
---

# Trop de lignes pour une conversation

À lire **avant** de boucler le patron du guide `work-queue` sur un vivier de plusieurs
dizaines de lignes : enrichir 200 entreprises, pousser un lot de comptes vers un CRM,
tout traitement identique répété ligne à ligne. `work-queue` reste juste — mais une
conversation a un plafond de TOURS (24 par défaut, 64 au maximum, jamais de reprise
automatique), et un run qui le heurte s'arrête `blocked`, pas `done`. Boucler 200 lignes
dans une seule conversation, c'est parier sur ce plafond avant même d'avoir vu la file.

## Le principe : oto produit le travail, pas toi

`oto_fleet` n'est **pas** un second bouclage à écrire. `op=create` déclare (procédure +
allowlist + cible), `op=launch` ARME — et c'est tout ce que tu as à faire. Aucun
ordonnanceur à démarrer : dès qu'un worker interroge la file de l'org et n'a rien de
prêt, oto lui **fabrique** le prochain travail du fleet armé, un par un, jusqu'à
`max_rows` ou un arrêt. C'est le même modèle que `data_claim_next` (bail atomique,
`FOR UPDATE SKIP LOCKED`) mais un cran au-dessus : chaque ligne devient un travail
**séparé**, avec son propre budget de tours — donc le plafond d'une conversation ne
s'applique plus à la file entière, seulement à chaque ligne.

⚠️ Ne confonds pas avec le dashboard : la surface « déclarer une campagne puis la
lancer » reste gelée côté produit (aucun bouton Launch n'y est promis). Ce que ce guide
décrit est différent — c'est TOI, dans cette conversation, qui armes un fleet borné pour
le travail qu'on te demande, pas un écran séparé qu'on expose à l'utilisateur.

## La procédure fait toujours autorité

Avant `oto_fleet op=create`, cherche une procédure existante (`oto_procedure op=list`)
qui couvre déjà ce traitement. N'en écris une ad hoc que si rien ne correspond — et dans
ce cas, comme pour un run normal, écris-la comme si elle allait être relue (le guide
`procedure-flowchart` s'applique pareil ici). `create` exige `label` + `procedure` +
`tools` ; il n'existe pas de raccourci en texte libre qui contournerait la procédure.

## Ne jamais armer sans un accord explicite, en clair

Armer un fleet dépense réellement (le worker qui le sert tire sur la clé de l'org) et
écrit dans un vrai tableau — sans qu'aucun bouton « Lancer » n'ait été cliqué nulle part.
Avant `op=launch`, dis en une phrase ce qui va se passer : le nombre de lignes visées
(compte réel via `data_rows` sur le `row_filter`, jamais une estimation), la procédure,
les outils déclarés — et attends un oui. Ce n'est pas une formalité : c'est le seul
endroit où un humain voit la dépense avant qu'elle parte.

## Borner, toujours

- `max_rows` = le compte RÉEL de lignes éligibles, jamais laissé vide.
- `max_tokens_per_row` et `max_consecutive_failures` : pose-les à chaque fois — sans le
  premier, seul le plafond de tours borne une ligne qui dérape ; sans le second, une
  file entière peut tourner à vide sur un même défaut avant qu'on s'en aperçoive.
- `tools` = exactement l'allowlist que la procédure déclare, jamais plus large que ce
  qu'un run à une ligne exigerait.

## Suivre et rendre compte

`op=state` rend la progression agrégée (`pending`/`claimed`/`done`/`failed`,
`reservable_rows`) — interroge-le pour répondre si on te demande où ça en est, dans la
même conversation, sans renvoyer vers un écran. `op=stop` DEMANDE l'arrêt (`stopping`) :
le fleet finit ce qui est engagé avant de devenir `stopped`, il ne s'interrompt pas net.

Voir aussi le guide `work-queue` (le même patron pour un volume qu'une conversation peut
boucler elle-même) et le guide `bulk-load` (garder les gros payloads hors contexte,
question orthogonale à celle-ci).
