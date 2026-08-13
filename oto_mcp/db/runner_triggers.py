"""Les déclencheurs du runner — la config qui fabrique des jobs (chantier R3).

Le module ne connaît pas le cron : il stocke, liste, et surtout **consomme une
échéance par compare-and-swap** — prod et preprod partagent la même base, deux
ticks tournent, un seul doit gagner chaque échéance. Le calcul de la prochaine
échéance (croniter, dans le fuseau du déclencheur) vit dans `runner_tick`, à un
seul endroit.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ._conn import _connect

_COLS = ("id, org_id, sub, label, procedure, project_id, tools, input, max_steps, "
         "cron, tz, enabled, next_due, last_enqueued_at, created_at")


def create_trigger(org_id: int, sub: str, *, procedure: str, cron: str, tz: str,
                   next_due, tools: list, project_id: Optional[int] = None,
                   input: Optional[str] = None, label: Optional[str] = None,
                   max_steps: Optional[int] = None) -> dict:
    with _connect() as conn:
        row = conn.execute(
            f"""
            INSERT INTO runner_triggers
                   (org_id, sub, label, procedure, project_id, tools, input,
                    max_steps, cron, tz, next_due)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
            RETURNING {_COLS}
            """,
            (org_id, sub, label, procedure, project_id,
             json.dumps(list(tools), ensure_ascii=False), input, max_steps,
             cron, tz, next_due),
        ).fetchone()
    return dict(row)


def list_triggers(org_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM runner_triggers WHERE org_id = %s ORDER BY id",
            (org_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_trigger(trigger_id: int, org_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_COLS} FROM runner_triggers WHERE id = %s AND org_id = %s",
            (trigger_id, org_id),
        ).fetchone()
    return dict(row) if row else None


def update_trigger(trigger_id: int, org_id: int, champs: dict[str, Any]) -> Optional[dict]:
    """Mise à jour partielle, org-scopée. `champs` ne contient QUE des colonnes
    déjà validées par la capacité (jamais de SQL construit sur l'entrée brute)."""
    autorises = {"label", "procedure", "project_id", "tools", "input", "max_steps",
                 "cron", "tz", "enabled", "next_due"}
    inconnu = set(champs) - autorises
    if inconnu:
        raise ValueError(f"colonnes hors contrat : {sorted(inconnu)}")
    if not champs:
        return get_trigger(trigger_id, org_id)
    sets, vals = [], []
    for k, v in champs.items():
        if k == "tools":
            sets.append("tools = %s::jsonb")
            vals.append(json.dumps(list(v), ensure_ascii=False))
        else:
            sets.append(f"{k} = %s")
            vals.append(v)
    with _connect() as conn:
        row = conn.execute(
            f"UPDATE runner_triggers SET {', '.join(sets)} "
            f"WHERE id = %s AND org_id = %s RETURNING {_COLS}",
            (*vals, trigger_id, org_id),
        ).fetchone()
    return dict(row) if row else None


def delete_trigger(trigger_id: int, org_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM runner_triggers WHERE id = %s AND org_id = %s",
            (trigger_id, org_id),
        )
        return bool(cur.rowcount)


def due_triggers(limit: int = 50) -> list[dict]:
    """Les déclencheurs à échéance — lecture nue, TOUTES orgs (le tick est un
    service de plateforme). La consommation se fait par CAS, pas ici."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM runner_triggers "
            f"WHERE enabled AND next_due <= NOW() ORDER BY next_due LIMIT %s",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def consume_due(trigger_id: int, seen_next_due, new_next_due) -> bool:
    """Compare-and-swap sur l'échéance : True = CE tick a gagné et doit enfiler ;
    False = un tick concurrent (l'autre environnement, même base) l'a déjà fait."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE runner_triggers
               SET next_due = %s, last_enqueued_at = NOW()
             WHERE id = %s AND next_due = %s AND enabled
            """,
            (new_next_due, trigger_id, seen_next_due),
        )
        return bool(cur.rowcount)
