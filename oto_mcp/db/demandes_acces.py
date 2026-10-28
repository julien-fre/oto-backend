"""Demandes d'accès : lecture et écriture de `access_requests`
(`docs/demandes-d-acces.md`).

NON aplati dans `db.*` : `creer`/`lire`/`decider` sont trop communs pour la surface
plate. Les appelants écrivent `db.demandes_acces.<fn>`.

Chaque lecture rend aussi `vivante` : en attente ET non échue, à l'horloge de la base
(celle qui a posé `expires_at`).

Ce module ne juge AUCUN droit : qui peut demander, voir ou décider se tranche dans la
capacité (`capabilities/demandes_acces.py`). Il garantit seulement qu'une demande ne se
décide qu'UNE fois — chaque transition est un `UPDATE … WHERE status = 'pending'`
conditionnel, et deux décisions croisées n'en font gagner qu'une.
"""
from __future__ import annotations

from typing import Optional

from psycopg.types.json import Jsonb

from ._conn import _connect

_COLONNES = ("id, org_id, requester_sub, kind, cible, cle_cible, demande, note, status, "
             "decided_by, decided_at, decision, decision_note, created_at, reminded_at, "
             "expires_at, (status = 'pending' AND expires_at > now()) AS vivante")

#: Une demande en attente ET non échue — la seule qui se décide.
_VIVANTE = "status = 'pending' AND expires_at > now()"

#: Délai minimal entre deux relances d'une même demande.
RELANCE_HEURES = 24


def creer(org_id: int, requester_sub: str, kind: str, cible: dict, cle_cible: str,
          demande: dict, note: Optional[str]) -> tuple[dict, bool]:
    """Pose la demande, ou rend celle qui attend déjà pour (demandeur, geste, cible).
    Rend `(demande, creee)`. Une demande en attente mais ÉCHUE passe d'abord `expired`,
    pour libérer l'unicité : redemander après l'échéance repart à neuf."""
    with _connect() as conn:
        conn.execute(
            "UPDATE access_requests SET status = 'expired' "
            "WHERE requester_sub = %s AND kind = %s AND cle_cible = %s "
            "AND status = 'pending' AND expires_at <= now()",
            (requester_sub, kind, cle_cible))
        r = conn.execute(
            f"INSERT INTO access_requests (org_id, requester_sub, kind, cible, cle_cible, "
            f"demande, note) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            f"ON CONFLICT (requester_sub, kind, cle_cible) WHERE status = 'pending' "
            f"DO NOTHING RETURNING {_COLONNES}",
            (int(org_id), requester_sub, kind, Jsonb(cible), cle_cible, Jsonb(demande),
             note)).fetchone()
        if r is not None:
            return dict(r), True
        r = conn.execute(
            f"SELECT {_COLONNES} FROM access_requests WHERE requester_sub = %s "
            f"AND kind = %s AND cle_cible = %s AND status = 'pending'",
            (requester_sub, kind, cle_cible)).fetchone()
    return dict(r), False


def lire(request_id: int) -> Optional[dict]:
    with _connect() as conn:
        r = conn.execute(f"SELECT {_COLONNES} FROM access_requests WHERE id = %s",
                         (int(request_id),)).fetchone()
    return dict(r) if r else None


def en_attente(org_id: int, *, limit: int = 200) -> list[dict]:
    """Les demandes vivantes d'une org, la plus ancienne d'abord : la capacité filtre
    ensuite celles que l'appelant peut décider."""
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT {_COLONNES} FROM access_requests WHERE org_id = %s AND {_VIVANTE} "
            f"ORDER BY id LIMIT %s", (int(org_id), int(limit))).fetchall()]


def du_demandeur(requester_sub: str, org_id: int, *, limit: int = 50) -> list[dict]:
    """Les demandes de `requester_sub` dans une org, la plus récente d'abord, toutes
    issues confondues."""
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT {_COLONNES} FROM access_requests WHERE requester_sub = %s "
            f"AND org_id = %s ORDER BY id DESC LIMIT %s",
            (requester_sub, int(org_id), int(limit))).fetchall()]


def decider(request_id: int, status: str, *, decided_by: str,
            decision: Optional[dict] = None, note: Optional[str] = None) -> Optional[dict]:
    """Pose l'issue d'une demande VIVANTE (`approved` | `declined` | `cancelled`). Rend
    la demande décidée, ou None si elle ne l'était plus à décider (déjà décidée, ou
    échue) — la seconde de deux décisions croisées."""
    with _connect() as conn:
        r = conn.execute(
            f"UPDATE access_requests SET status = %s, decided_by = %s, "
            f"decided_at = now(), decision = %s, decision_note = %s "
            f"WHERE id = %s AND {_VIVANTE} RETURNING {_COLONNES}",
            (status, decided_by, Jsonb(decision) if decision is not None else None, note,
             int(request_id))).fetchone()
    return dict(r) if r else None


def relancer(request_id: int) -> bool:
    """Marque une relance, au plus une par `RELANCE_HEURES` ; faux si trop tôt."""
    with _connect() as conn:
        return conn.execute(
            f"UPDATE access_requests SET reminded_at = now() WHERE id = %s AND {_VIVANTE} "
            f"AND coalesce(reminded_at, created_at) <= now() - make_interval(hours => %s)",
            (int(request_id), RELANCE_HEURES)).rowcount == 1
