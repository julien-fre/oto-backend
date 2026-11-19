"""Le store des limites d'envoi LinkedIn (`unipile_sending_limits`) et du compte glissant
de ce que chaque compte a fait (`unipile_sending_log`).

La règle (quelles limites, quelles fenêtres, quel refus) vit dans `oto_mcp/unipile_limites.py` ;
ce module ne fait que lire, poser, réserver et retirer. NON aplati : `poser`/`reserver`
sont trop communs pour la surface plate — `db.unipile_limites.<fn>`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ._conn import _connect

#: Borne d'une écriture : un envoi n'attend jamais la base longtemps.
_DUREE_MAX_MS = 5000
#: Au-delà, une ligne du journal ne sert plus aucune fenêtre (la plus longue : 7 jours).
_RETENTION = "8 days"


def _libre_a(premier, secondes: int) -> Optional[datetime]:
    """Quand l'action la plus ancienne de la fenêtre en sort (None : fenêtre vide)."""
    if premier is None:
        return None
    return datetime.fromtimestamp(float(premier) + int(secondes), tz=timezone.utc)


def limites(account_id: str) -> dict[str, int]:
    """Les limites posées sur ce compte : `{lim: max}`. Vide = aucune limite."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT lim, max_count FROM unipile_sending_limits WHERE account_id = %s",
            (account_id,)).fetchall()
    return {r["lim"]: int(r["max_count"]) for r in rows}


def poser(account_id: str, sub: str, valeurs: dict[str, Optional[int]]) -> None:
    """Pose (entier) ou retire (`None`) chaque limite nommée, en UNE transaction. Une
    limite non nommée garde sa valeur."""
    with _connect() as conn, conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = {_DUREE_MAX_MS}")
        for lim, n in sorted(valeurs.items()):
            if n is None:
                conn.execute("DELETE FROM unipile_sending_limits "
                             "WHERE account_id = %s AND lim = %s", (account_id, lim))
            else:
                conn.execute(
                    "INSERT INTO unipile_sending_limits (account_id, lim, max_count, sub) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (account_id, lim) DO UPDATE SET "
                    "max_count = EXCLUDED.max_count, sub = EXCLUDED.sub, "
                    "updated_at = NOW()", (account_id, lim, int(n), sub))


def utilise(account_id: str, action: str, secondes: int) -> tuple[int, Optional[datetime]]:
    """Combien d'actions `action` ce compte a faites sur les `secondes` dernières, et quand
    la plus ancienne d'entre elles sort de la fenêtre (None : aucune)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT count(*) AS n, EXTRACT(EPOCH FROM min(at))::float8 AS premier "
            "FROM unipile_sending_log "
            "WHERE account_id = %s AND action = %s "
            "AND at > NOW() - make_interval(secs => %s)",
            (account_id, action, int(secondes))).fetchone()
    return int(row["n"]), _libre_a(row["premier"], secondes)


def reserver(account_id: str, action: str, sub: Optional[str],
             fenetres: list[tuple[str, int, int]]) -> dict:
    """Réserve UNE action si aucune fenêtre n'est pleine, atomiquement : deux agents qui
    envoient en même temps depuis le même compte ne passent pas tous deux la dernière
    place (verrou consultatif par compte, le temps de la transaction).

    `fenetres` = `[(lim, max, secondes)]`. Rend `{"id": …}` (la réservation, à retirer
    si l'envoi échoue) ou `{"refus": {lim, max, used, resets_at}}` pour la première
    fenêtre pleine. Purge au passage ce qui ne sert plus aucune fenêtre de ce compte."""
    with _connect() as conn, conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = {_DUREE_MAX_MS}")
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                     ("unipile_sending:" + account_id,))
        conn.execute(f"DELETE FROM unipile_sending_log WHERE account_id = %s "
                     f"AND at < NOW() - interval '{_RETENTION}'", (account_id,))
        for lim, maxi, secondes in fenetres:
            row = conn.execute(
                "SELECT count(*) AS n, EXTRACT(EPOCH FROM min(at))::float8 AS premier "
            "FROM unipile_sending_log "
                "WHERE account_id = %s AND action = %s "
                "AND at > NOW() - make_interval(secs => %s)",
                (account_id, action, int(secondes))).fetchone()
            if int(row["n"]) >= int(maxi):
                return {"refus": {
                    "lim": lim, "max": int(maxi), "used": int(row["n"]),
                    "resets_at": _libre_a(row["premier"], secondes)}}
        row = conn.execute(
            "INSERT INTO unipile_sending_log (account_id, action, sub) VALUES (%s, %s, %s) "
            "RETURNING id", (account_id, action, sub)).fetchone()
    return {"id": int(row["id"])}


def retirer(reservation_id: int) -> None:
    """Retire une réservation dont l'envoi a échoué : seule une action faite compte."""
    with _connect() as conn:
        conn.execute("DELETE FROM unipile_sending_log WHERE id = %s", (int(reservation_id),))


def proprietaire(account_id: str) -> Optional[str]:
    """Le titulaire du compte : la personne dont une ligne VIVANTE le relie (le même compte
    relié dans plusieurs orgs l'est par la même personne)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT sub FROM unipile_accounts WHERE account_id = %s "
            "AND disconnected_at IS NULL ORDER BY connected_at LIMIT 1",
            (account_id,)).fetchone()
    return row["sub"] if row else None
