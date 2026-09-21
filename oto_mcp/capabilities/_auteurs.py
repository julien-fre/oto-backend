"""Nommer l'auteur d'une écriture de procédure (#708).

`set_by` est écrit à chaque écriture, sur la ligne courante et sur chaque révision : c'est
un IDENTIFIANT de compte. Le servir nu rend la distinction vraie en base et illisible à
l'usage — l'agent qui relit une procédure ne peut ni dire qui l'a écrite en dernier, ni en
tenir compte. `set_by_name` est donc servi À CÔTÉ, jamais à la place : le nom d'abord, avec
repli sur l'email puis sur l'identifiant (`db.shell.names_of`) — jamais l'inverse.

Une absence se sert comme une absence : pas d'auteur en base (`set_by` nul, cas des
procédures anciennes) → `set_by_name` nul aussi, rien n'est déduit rétroactivement.
"""
from __future__ import annotations

from typing import Iterable

from ..db import shell as db_shell


def nommer_les_auteurs(lignes: Iterable[dict]) -> list[dict]:
    """Chaque ligne reçoit `set_by_name`, en UNE requête pour tout le lot."""
    lignes = list(lignes)
    noms = db_shell.names_of(r.get("set_by") for r in lignes)
    return [{**r, "set_by": r.get("set_by"),
             "set_by_name": (noms.get(r["set_by"]) or r["set_by"]) if r.get("set_by") else None}
            for r in lignes]


def nommer_l_auteur(ligne: dict) -> dict:
    return nommer_les_auteurs([ligne])[0]
