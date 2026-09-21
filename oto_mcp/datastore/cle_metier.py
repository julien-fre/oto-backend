"""La clé métier DÉCLARÉE face à son index d'unicité : ce qui ne doit pas y entrer, et
ce qu'on dit quand l'index refuse malgré tout.

L'index `ds_bkey_<ns>` est PARTIEL sur `IS NOT NULL` (`db.datastore_ensure_key_index`) :
une clé ABSENTE n'y entre pas, mais une clé VIDE — `""`, et ce que `@empty` ou `@clear`
posent à la création — y entre comme n'importe quelle valeur. Deux lignes « sans clé »
écrites ainsi portent donc la même valeur `""` pour l'index.

Mesuré par le signal feedback 994 : deux lignes `@empty` sur la clé déclarée, la seconde
tombait en `UniqueViolation` brute (500) — la recherche de convergence cherchait le mot
`"@empty"` là où la base porte `""`. Et le voisin, pire parce que muet : une clé `""` en
clair ne cherche rien, s'insère, heurte l'index, puis la convergence cherche `""`… et
TROUVE l'autre ligne sans clé — deux entités fusionnées en une, sans un mot.

La cause se ferme donc à l'ENTRÉE (`refuser_cle_metier_vide`) : une clé vide ne désigne
aucune entité, elle ne s'écrit pas. Le filet (`ligne_de_la_course_perdue`) reste pour la
seule violation qui subsiste — une vraie course — et vit ICI, une fois, pour l'écriture
unitaire comme pour le lot.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import db
from . import schema as dsv2
from .columns import mots_resolus_a_la_creation


def refuser_cle_metier_vide(schema: Optional[dict], user_data: dict) -> None:
    """Refuse le geste qui écrit la clé métier DÉCLARÉE à vide — `""`, `@empty`,
    `@clear`, nus ou en couche `valeur`. Une clé absente (ou `null`) passe : elle
    n'entre pas dans l'index, et la ligne naît non rapprochable, ce qui est DIT
    ailleurs (`off_non_rapprochables`).

    Jugée sur la valeur telle que la création la poserait (`mots_resolus_a_la_creation`,
    la MÊME résolution que l'écriture) : pas de seconde liste des mots qui vident."""
    key = (schema or {}).get("key")
    if not isinstance(key, str) or not key or user_data.get(key) is None:
        return
    posee = mots_resolus_a_la_creation(schema, {key: user_data[key]}).get(key)
    if posee is None or dsv2.unwrap(posee) != "":
        return
    raise ValueError(
        f"`{key}` est la clé métier de ce tableau et ce geste l'écrit vide "
        f"({user_data[key]!r}) : une clé métier vide ne désigne aucune entité — toutes "
        f"les lignes « sans clé » porteraient la même, et se confondraient. Rien n'a été "
        f"écrit. Écris la vraie valeur de `{key}` ; si elle est inconnue, omets la "
        f"colonne (la ligne naît sans clé, non rapprochable).")


def ligne_de_la_course_perdue(ns_id: int, key: Optional[str], kv: Any,
                              violation: Exception) -> str:
    """La ligne qui a gagné la course sous l'index de clé métier (#109 ch.3), vers
    laquelle l'écriture perdante converge en fusion.

    Sans clé déclarée à chercher, la violation est inexpliquée : elle repart telle
    quelle, erreur franche. Avec une clé que la recherche ne retrouve pas, l'index a
    vu une ligne qui n'est plus là — refus NOMMÉ, jamais l'exception driver brute."""
    if not key or kv is None:
        raise violation
    gagnante = db.datastore_find_row_id_by_key(ns_id, key, kv)
    if gagnante is not None:
        return gagnante
    raise ValueError(
        f"une autre ligne a pris la clé métier `{key}`={kv!r} pendant cette écriture, "
        f"puis n'a plus été retrouvée (supprimée ou modifiée entre-temps). Rien n'a été "
        f"écrit pour cette ligne : relis le tableau (data_rows) et rejoue-la.") from None
