"""Le plafond d'engagement d'une org sur une clé plateforme (`org_spend_caps`).

Une ligne par (org, connecteur) : ce qui restait engageable quand le commerce l'a
calculé (`units_left`, à `as_of`) et ce que l'org a engagé depuis (`engaged`). La règle
(qui réserve, quand refuser) vit dans `access/spend_cap.py` ; ce module ne fait que
compter.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ._conn import _connect


def poser(org_id: int, connector: str, units_left: Optional[int], as_of: datetime,
          engaging_tool: str) -> Optional[dict]:
    """Pose le plafond de l'org (`None` le retire) et rend la ligne posée.

    `engaged` n'est PAS remis à zéro : il est recalculé du journal — la somme des
    `engaged_credits` tracés par `engaging_tool` sous l'org APRÈS `as_of`. Le commerce a
    lu son grand livre jusqu'à `as_of` ; ce qui a été soumis depuis, il ne l'a pas vu,
    et une remise à zéro le rendrait engageable une seconde fois.

    ⚠️ Fenêtre assumée : une soumission qui a RÉSERVÉ mais dont la ligne de journal
    n'est pas encore écrite quand la poussée arrive (quelques secondes, le temps de
    l'appel au fournisseur) n'est pas recomptée — sa réservation est perdue jusqu'à la
    poussée suivante, où le commerce l'a vue. L'erreur va dans le sens du client, et
    ne porte que sur un lot."""
    with _connect() as conn:
        if units_left is None:
            conn.execute("DELETE FROM org_spend_caps WHERE org_id = %s AND connector = %s",
                         (int(org_id), connector))
            return None
        row = conn.execute(
            """
            INSERT INTO org_spend_caps (org_id, connector, units_left, as_of, engaged, set_at)
            VALUES (%(org)s, %(c)s, %(u)s, %(as_of)s,
                    (SELECT COALESCE(SUM((l.args->>'engaged_credits')::bigint), 0)
                       FROM tool_calls l
                      WHERE l.org_id = %(org)s AND l.tool = %(tool)s AND l.ok = TRUE
                        AND l.created_at > %(as_of)s
                        AND l.args->>'engaged_credits' ~ '^[0-9]+$'),
                    now())
            ON CONFLICT (org_id, connector) DO UPDATE SET
                units_left = EXCLUDED.units_left, as_of = EXCLUDED.as_of,
                engaged = EXCLUDED.engaged, set_at = EXCLUDED.set_at
            RETURNING units_left, engaged, as_of
            """,
            {"org": int(org_id), "c": connector, "u": int(units_left), "as_of": as_of,
             "tool": engaging_tool}).fetchone()
    return dict(row) if row else None


def reserver(org_id: int, connector: str, units: int) -> Optional[dict]:
    """Réserve `units` sur le plafond de l'org. `None` = pas de plafond (rien réservé) ;
    sinon `{"ok", "units_left", "engaged"}` — `ok` faux = rien réservé, le plafond ne
    les couvre pas.

    Une requête conditionnelle, atomique : deux soumissions simultanées ne peuvent pas
    réserver toutes les deux la dernière part. La relecture qui suit un refus ne sert
    qu'à distinguer « pas de plafond » de « plafond atteint »."""
    with _connect() as conn:
        row = conn.execute(
            """
            UPDATE org_spend_caps SET engaged = engaged + %(n)s
             WHERE org_id = %(org)s AND connector = %(c)s
               AND engaged + %(n)s <= units_left
            RETURNING units_left, engaged
            """,
            {"org": int(org_id), "c": connector, "n": int(units)}).fetchone()
        if row:
            return {"ok": True, "units_left": int(row["units_left"]),
                    "engaged": int(row["engaged"])}
        row = conn.execute(
            "SELECT units_left, engaged FROM org_spend_caps "
            "WHERE org_id = %s AND connector = %s", (int(org_id), connector)).fetchone()
    if not row:
        return None
    return {"ok": False, "units_left": int(row["units_left"]), "engaged": int(row["engaged"])}


def liberer(org_id: int, connector: str, units: int) -> None:
    """Rend `units` réservés par une soumission que le fournisseur a refusée."""
    with _connect() as conn:
        conn.execute(
            "UPDATE org_spend_caps SET engaged = GREATEST(0, engaged - %s) "
            "WHERE org_id = %s AND connector = %s", (int(units), int(org_id), connector))
