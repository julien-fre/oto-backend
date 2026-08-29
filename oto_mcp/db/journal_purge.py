"""Purge RÉTROACTIVE des jetons déjà écrits en clair dans le journal (#558).

Le masquage à l'écriture (`oto_mcp/journal_secrets.py`) ne vaut que pour les lignes
à venir. Celles déjà posées portent, sur toute la fenêtre de rétention, des jetons
d'upload, de partage et d'invitation en clair — dont ceux que le modèle de données
refuse explicitement de persister ainsi.

**Une réparation, pas une suppression** : on ne jette pas la ligne de journal (elle
porte la télémétrie de surface : qui, quand, quel code, quelle durée), on la ramène
à ce qu'elle aurait dû être — la route réduite. Le geste reste donc lisible dans les
lentilles de supervision, il cesse seulement de nommer le secret.

Ce module ne décide de RIEN : il reçoit les plans dérivés de la table de routes
servie et une fonction de masque. Deux listes divergeraient ; il n'y en a qu'une.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Iterable

from ._conn import _connect

logger = logging.getLogger(__name__)

# Borne de sécurité de la passe `args` : au-delà, on s'arrête et on le DIT plutôt
# que de tenir une transaction ouverte sur la table la plus écrite du serveur.
_MAX_ARG_ROWS = 50_000


def _conditions(prefixe: str, reduit: str, plus_specifiques: Iterable[str]):
    """Les lignes REST dont la route porte encore un secret en clair.

    `tool` vaut `MÉTHODE /route`, d'où le `'% '` en tête de chaque motif. Les
    préfixes plus spécifiques sont EXCLUS : sans ça, la passe générique
    (`/api/invitations/`) écraserait ce que la passe spécifique
    (`/api/invitations/code/`) vient de réduire, et le nom de la route serait perdu.
    """
    ou = ["kind = 'rest'", "tool LIKE %s", "tool NOT LIKE %s"]
    params: list = [f"% {prefixe}%", f"% {reduit}"]
    for q in plus_specifiques:
        ou.append("tool NOT LIKE %s")
        params.append(f"% {q}%")
    return " AND ".join(ou), params


def purge_route_tokens(plans, *, dry_run: bool = True) -> dict:
    """Réduit les routes REST qui portent encore leur jeton. `{route: compte}`."""
    out: dict[str, int] = {}
    with _connect() as conn:
        for prefixe, reduit, plus_specifiques in plans:
            where, params = _conditions(prefixe, reduit, plus_specifiques)
            if dry_run:
                row = conn.execute(
                    f"SELECT count(*) AS n FROM tool_calls WHERE {where}",
                    tuple(params)).fetchone()
                n = int(row["n"]) if row else 0
            else:
                cur = conn.execute(
                    f"UPDATE tool_calls SET tool = split_part(tool, ' ', 1) || %s "
                    f"WHERE {where}",
                    tuple([" " + reduit] + params))
                n = cur.rowcount or 0
            if n:
                out[reduit] = n
    return out


def purge_arg_tokens(champs_par_outil: dict[str, frozenset],
                     masque: Callable[[str], str], *,
                     dry_run: bool = True) -> dict:
    """Masque les mêmes secrets là où ils sont passés en ARGUMENTS d'outil.

    Le jeton d'invitation a deux portes : la route (`/api/invitations/{token}`) et
    l'outil (`oto_org op=accept_invite`). Réparer une seule des deux laisserait le
    secret lisible par l'autre — c'est la forme même du défaut d'origine.

    Le masque se calcule en Python (HMAC clé), donc ligne à ligne : la passe est
    bornée, et rend le compte de ce qu'elle a laissé si la borne est atteinte.
    """
    if not champs_par_outil:
        return {}
    outils = sorted(champs_par_outil)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, tool, args FROM tool_calls "
            " WHERE tool = ANY(%s) AND args IS NOT NULL "
            " ORDER BY id LIMIT %s",
            (outils, _MAX_ARG_ROWS + 1)).fetchall()
    reste = max(0, len(rows) - _MAX_ARG_ROWS)
    rows = rows[:_MAX_ARG_ROWS]
    a_reparer: list[tuple[int, dict]] = []
    for r in rows:
        args = r["args"]
        if not isinstance(args, dict):
            continue
        champs = champs_par_outil.get(r["tool"], frozenset())
        neuf = dict(args)
        touche = False
        for k in champs:
            v = neuf.get(k)
            if isinstance(v, str) and v and not v.startswith("#"):
                neuf[k] = masque(v)
                touche = True
        if touche:
            a_reparer.append((int(r["id"]), neuf))
    out = {"rows": len(a_reparer)}
    if reste:
        # Dit, jamais tu : une passe qui s'arrête en silence laisse croire qu'elle
        # a fini (cf. les « succès déguisés » du 27/08).
        out["not_scanned"] = reste
    if dry_run or not a_reparer:
        return out
    with _connect() as conn:
        for rid, neuf in a_reparer:
            conn.execute("UPDATE tool_calls SET args = %s::jsonb WHERE id = %s",
                         (json.dumps(neuf), rid))
    return out
