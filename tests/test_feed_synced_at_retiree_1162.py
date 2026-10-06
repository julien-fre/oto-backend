"""`unipile_accounts.feed_synced_at` retirée (oto-backend#1162).

Le `DROP` de cette colonne morte n'était écrit nulle part : un commentaire de démarrage
le confiait à l'exploitation, qui l'a joué sur la base partagée. Une base née avant ce
geste l'a gardée, et un import de périmètre vers elle a refusé des « colonnes
différentes ». Une révision a rejoué le retrait sur toute base vivante ; elle est
antérieure à la référence du registre (squash, docs/migrations-versionnees.md §5.4) —
la règle qu'elle a fait écrire reste : un retrait passe toujours par une révision (§5.3).

Ce qui dure : plus aucun code servi ne nomme la colonne, une base neuve ne l'a pas, et
le démarrage ne la repose pas.
"""
from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


# ── Sans base ─────────────────────────────────────────────────────────────────

def test_plus_aucun_code_servi_ne_nomme_la_colonne():
    """Hors du registre des révisions, la colonne n'existe plus : ni dans le `CREATE
    TABLE` (une base neuve la recréerait), ni dans un ordre ou un commentaire de
    démarrage (un commentaire n'est pas la trace d'un DDL). L'identifiant d'une révision
    retirée par un squash, que le démarrage nomme pour refuser une base ancienne, est le
    nom d'une révision, pas celui de la colonne."""
    from oto_mcp.db._version_alembic import SQUASHS
    revisions_retirees = {r for squash in SQUASHS for r in squash.retirees}

    def nomme_la_colonne(texte: str) -> bool:
        for revision in revisions_retirees:
            texte = texte.replace(revision, "")
        return "feed_synced_at" in texte

    porteurs = sorted(
        p.relative_to(RACINE).as_posix()
        for p in (RACINE / "oto_mcp").rglob("*.py")
        if "/migrations/" not in p.as_posix()
        and nomme_la_colonne(p.read_text(encoding="utf-8")))
    assert not porteurs, f"`feed_synced_at` encore nommée hors des révisions : {porteurs}"


# ── Sur une vraie base ────────────────────────────────────────────────────────

def _a_la_colonne(dsn: str) -> bool:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = "
            "'unipile_accounts' AND column_name = 'feed_synced_at'").fetchone() is not None


def test_une_base_neuve_n_a_pas_la_colonne_et_le_demarrage_ne_la_repose_pas(
        live, pg_module_dsn):
    from oto_mcp.db import init_db
    assert not _a_la_colonne(pg_module_dsn), "le CREATE TABLE ne doit plus la déclarer"
    init_db()
    assert not _a_la_colonne(pg_module_dsn), "le démarrage a reposé la colonne"
