"""Le vocabulaire des REFUS des gestes de ligne, déclaré une fois pour les cinq routes.

⚠️ **Même raison d'être que `_forme.py`, et la même contrainte** : `claim.py` déclare
les mêmes refus d'adresse que `rows.py`, et l'importer depuis `rows` chargerait `rows`
en premier — donc changerait l'ordre d'enregistrement des capacités, or cette table est
FIGÉE (Starlette sert le PREMIER chemin qui matche). Un tiers neutre, importé par les
deux, ne touche à rien.

Une seule définition, donc : un refus qui change de phrase la change partout. Deux
copies divergeraient, et le client généré lirait la plus ancienne des deux.

Ces refus étaient LEVÉS depuis toujours et déclarés NULLE PART (oto#217) : seul
`revision_conflict` sur le patch l'était. Un intégrateur savait recevoir un 200 sans
savoir quels 4xx attendre — il traitait en panne un refus parfaitement normal.
"""
from __future__ import annotations

from .._types import DeclaredError

# Ce que TOUT geste de ligne peut refuser : l'adresse, le tableau, son régime.
_REFUS_D_ADRESSE = (
    DeclaredError(404, "datastore_not_found",
                  "le tableau ne se voit pas depuis l'org de l'appel : le message dit "
                  "dans laquelle il vit quand c'en est une autre du même porteur"),
    DeclaredError(403, "datastore_read_only",
                  "le tableau est partagé en LECTURE : aucun geste d'écriture n'y passe"),
)

# Le jeton mal placé — les gestes qui résolvent une ADRESSE de ligne (`slot:`, numéro).
_JETON_MAL_PLACE = DeclaredError(
    400, "jeton_mal_place",
    "`datastore` ou `row_id` porte un jeton à sa mauvaise place (`slot:` sur "
    "l'identifiant de ligne, par exemple) : le message dit la conduite qui aboutit")

# Ce qu'un geste d'ÉCRITURE de ligne ajoute : le bail, et la saisie refusée au seuil
# du stockage.
_REFUS_D_ECRITURE = (
    DeclaredError(409, "row_locked",
                  "la ligne est sous bail ACTIF d'un autre travail : le message dit "
                  "jusqu'à quand et comment lever (libérer, ou porter le run qui tient "
                  "la ligne)"),
    DeclaredError(400, "invalid_row_input",
                  "l'appel est refusé par le stockage avant d'écrire — dont une "
                  "`expected_revision` illisible (la `_revision` est une chaîne de "
                  "chiffres)"),
)

_PRECONDITION_REFUSEE = DeclaredError(
    409, "revision_conflict",
    "`expected_revision` ne vaut plus la révision en place : rien n'est fait, "
    "`details.current_revision` porte la révision actuelle — relire, décider de "
    "nouveau, rejouer")

#: oto#141 — la fusion implicite sur la clé métier, refusée à partir de sa date.
_CLE_DEJA_PORTEE = DeclaredError(
    409, "business_key_exists",
    "sans `upsert=true` (à partir de la date annoncée par `upsert`) : une écriture qui "
    "AJOUTE (ni `id` ni `key`) porte une valeur de clé métier qu'une ligne a déjà, ou "
    "deux lignes d'un même lot portent la même : rien n'est écrit (un lot est jugé "
    "ENTIER avant sa première ligne). Le message et `details` nomment la ligne en place "
    "(`id`) ou les rangs (`doublons`, `existantes`) ; désigner par `id`/`key` pour "
    "modifier, ou passer `upsert=true` pour fusionner")

#: oto#124 — une colonne que le schéma ne déclare pas, refusée à partir de sa date.
_COLONNE_NON_DECLAREE = DeclaredError(
    400, "unknown_column",
    "à partir de la date annoncée par la description : l'écriture pose une colonne que "
    "le schéma ne déclare pas, sur n'importe quel tableau. Rien n'est écrit (un lot est "
    "jugé ENTIER avant sa première ligne). `details.colonnes` les nomme ; le geste est "
    "de déclarer la colonne (`PATCH …/schema`, `{\"fields\": [{\"key\": …}]}`) puis "
    "de réécrire")

_LIGNE_ABSENTE = DeclaredError(
    404, "row_not_found", "aucune ligne de cet `_id` dans ce tableau")

_WORKER_REQUIS = DeclaredError(
    400, "worker_required",
    "le `worker` manque : c'est le libellé stable de qui réserve, rejoué tel quel à "
    "la libération — c'est LA garde du bail. Un jeton PORTÉ le doit aussi pour "
    "libérer : la libération forcée est réservée à une session interactive")

# Ce que la réservation d'une ligne NOMMÉE juge en plus : l'état de cette ligne-là.
# `claim_next` pioche — il ne les rend pas, une file vide n'étant pas un refus.
_LIGNE_NON_RESERVABLE = (
    DeclaredError(409, "row_claimed",
                  "la ligne est déjà sous bail actif d'un autre : le message dit qui "
                  "la tient et jusqu'à quand"),
    DeclaredError(409, "row_outside_claimable",
                  "la ligne est hors du périmètre que le tableau déclare réservable "
                  "(`lifecycle.claimable`) : `details.claimable` le porte"),
)

_CLAIM_INVALIDE = DeclaredError(
    400, "invalid_claim",
    "la réservation est refusée par le stockage (filtre insensé, bail hors bornes)")
