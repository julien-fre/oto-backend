"""`oto-mcp perimetre naitre` — faire naître la base d'une instance cible SANS démarrer l'app
(oto-backend#1161).

L'import d'un périmètre vise une base née par `init_db` où l'app n'a jamais démarré
(`importation.controler_vierge`) : le premier démarrage sème ses propres lignes (les
guides plateforme, `nodes` et `blocks`) sous des identifiants que l'import préserve.
Or seul le démarrage faisait naître le schéma d'une base neuve. Ce geste en est la
moitié « schéma », et elle seule : `init_db`, qui crée le schéma et pose la tête du
registre des migrations sur une base neuve (`db._version_alembic`), dans la même
transaction — rien de ce que `server._prepare_database` fait ensuite (backfills,
semis des blocs et des guides plateforme). Le démarrage qui suit l'import le complète :
ses semis tombent alors sur des séquences que l'import a portées au-delà de ses lignes.

La base doit être NEUVE au sens du démarrage (`_version_alembic.constater` : aucune
table au schéma courant). Une base qui porte déjà des tables, ou une `alembic_version`,
est refusée en le disant (`NaissanceRefusee`) : ce geste ne complète ni ne répare une
base, il en fait naître une.

La tête posée est celle du registre de l'arbre qui joue la commande : la lancer depuis
le tag que l'instance servira, celui dont l'export a la version de schéma.
"""
from __future__ import annotations

import psycopg

from ..db import _version_alembic, init_db


class NaissanceRefusee(RuntimeError):
    """La base n'est pas neuve : `naitre` ne s'y joue pas."""


def naitre(conn: psycopg.Connection) -> dict:
    """Fait naître le schéma de la base de `conn` (à `dict_row`), qui doit être neuve,
    par `init_db` seul ; rend la version posée et le nombre de tables nées."""
    etat = _version_alembic.constater(conn)
    if etat is not _version_alembic.EtatRegistre.NEUVE:
        tables = _tables(conn)
        conn.rollback()
        raise NaissanceRefusee(
            f"la base n'est pas neuve ({etat.value} : {tables} table(s) au schéma "
            "courant) : `naitre` fait naître une base vide, il ne complète ni ne répare "
            "une base existante — en créer une neuve")
    conn.rollback()
    init_db()
    version = [r["version_num"] for r in conn.execute("SELECT version_num FROM alembic_version")]
    tables = _tables(conn)
    conn.rollback()
    return {"version_schema": version, "tables": tables}


def _tables(conn) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM pg_class WHERE relnamespace = current_schema()::regnamespace "
        "AND relkind IN ('r', 'p')").fetchone()["n"]
