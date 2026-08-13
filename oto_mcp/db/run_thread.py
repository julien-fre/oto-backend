"""Le fil d'un run hébergé — append-only, borné, effaçable (chantier runner R1).

Trois règles portées par ce module, parce qu'elles sont plus sûres ici qu'en prose :

1. **`seq` est attribué PAR LA BASE, atomiquement** (MAX+1 dans l'INSERT). Deux
   écrivains concurrents ne produisent jamais deux tours au même rang — le second
   heurte l'UNIQUE et réessaie sur le rang suivant. Le worker n'invente jamais un
   rang, il reçoit celui qu'il a obtenu.
2. **La purge est une fonction de CE module** (`prune_run_messages`), appelée au
   boot comme `prune_tool_calls`. Le fil est l'état d'exécution, pas la vérité du
   run : l'effacer n'ampute rien (ADR 0064-D3) — c'est le journal qui porte l'audit.
3. **Personne d'autre n'écrit dans cette table.** Le fil ne se corrige pas, ne se
   réordonne pas : append, lecture, purge. Un tour faux se raconte dans le tour
   suivant, comme dans une vraie conversation.
"""
from __future__ import annotations

import json
from typing import Optional

from ._conn import _connect

# Deux écrivains sur le même run est un cas RARE (le worker, plus un « continuer »
# du dashboard) : trois essais absorbent la course sans masquer un vrai problème.
_SEQ_RETRIES = 3


def append_run_message(run_id: str, role: str, content: dict,
                       provider_raw: Optional[dict] = None) -> dict:
    """Appose un tour au fil et rend `{seq, created_at}` — le rang fait foi."""
    payload = json.dumps(content, ensure_ascii=False)
    raw = json.dumps(provider_raw, ensure_ascii=False) if provider_raw is not None else None
    last = None
    for _ in range(_SEQ_RETRIES):
        try:
            with _connect() as conn:
                row = conn.execute(
                    """
                    INSERT INTO run_messages (run_id, seq, role, content, provider_raw)
                    SELECT %s, COALESCE(MAX(seq), 0) + 1, %s, %s::jsonb, %s::jsonb
                    FROM run_messages WHERE run_id = %s
                    RETURNING seq, created_at
                    """,
                    (run_id, role, payload, raw, run_id),
                ).fetchone()
                return {"seq": row["seq"], "created_at": row["created_at"]}
        except Exception as e:  # noqa: BLE001 — unique_violation = course sur seq
            last = e
            if "run_messages_run_id_seq_key" not in str(e):
                raise
    raise RuntimeError(f"append_run_message: rang toujours pris après "
                       f"{_SEQ_RETRIES} essais ({last})")


def get_run_messages(run_id: str, after_seq: int = 0, limit: int = 200,
                     include_raw: bool = False) -> list[dict]:
    """Les tours d'un run, dans l'ordre. `after_seq` pagine (reprise de lecture) ;
    `include_raw` joint le segment provider — réservé par la CAPACITÉ au
    propriétaire du run (le brut porte les blocs de thinking)."""
    cols = "seq, role, content, created_at" + (", provider_raw" if include_raw else "")
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {cols} FROM run_messages WHERE run_id = %s AND seq > %s "
            f"ORDER BY seq LIMIT %s",
            (run_id, after_seq, max(1, min(int(limit), 500))),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run_head(run_id: str) -> Optional[dict]:
    """`{sub, org_id}` du run — la base de l'autz du fil (le fil hérite des droits
    de SON run, aucun modèle de droits nouveau)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT sub, org_id FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
    return dict(row) if row else None


def prune_run_messages(keep_days: int = 30) -> int:
    """Purge les tours plus vieux que `keep_days`. Le run et son journal restent
    entiers — un run hébergé dont le fil est purgé se reprend par le journal,
    comme n'importe quel run (ADR 0064-D3)."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM run_messages WHERE created_at < NOW() - make_interval(days => %s)",
            (int(keep_days),),
        )
        return cur.rowcount or 0
