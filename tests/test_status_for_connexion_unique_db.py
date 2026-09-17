"""oto-backend — `status_for`, suite RÉELLE de #999 (17/09/2026).

Le banc mécanique de #999 (`test_status_for_connexion_unique.py`) mocke
entièrement le pool (`_FauxPool`) : il prouve qu'UNE connexion est empruntée,
jamais ce qui se passe SUR cette connexion une fois empruntée. Or c'est
justement ce que la revue d'oto cd a mis en doute — deux risques que seule une
vraie transaction PostgreSQL peut trancher :

1. Avant l'autocommit posé dans ce lot, `reuse_connection()` tenait tout
   `status_for` dans UNE SEULE transaction. PostgreSQL met TOUTE la transaction
   en échec dès qu'une requête y échoue — une erreur attrapée par l'appelant
   (ex. une clé illisible sur un connecteur) aurait dû faire lever
   `InFailedSqlTransaction` à la lecture SUIVANTE, même valide. Le premier banc
   ci-dessous le prouve sur une vraie base : rouge sans autocommit, vert avec.
2. Le second compte les VRAIS allers-retours (`_exec_command` de psycopg, qui
   porte `BEGIN`/`COMMIT`, plus les exécutions de requête) — pas les emprunts au
   pool (déjà couverts par #999) ni les lectures d'instances (#997, qui visait
   la mauvaise grandeur, cf. mesure prod d'oto cd).
"""
from __future__ import annotations

from unittest import mock

import pytest


@pytest.fixture(scope="module")
def live(pg_module_dsn):
    import os
    pytest.importorskip("psycopg")
    url_avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant


def test_une_erreur_attrapee_ne_gate_pas_les_lectures_suivantes(live):
    """Le risque n°1 d'oto cd, sur vraie base. Sans autocommit sur la connexion
    réutilisée, ce banc rougirait sur la deuxième requête (`InFailedSqlTransaction
    : current transaction is aborted`) — c'est exactement le défaut qu'elle a
    signalé : un connecteur en échec n'aurait plus dû dégrader QUE lui-même."""
    from oto_mcp.db import _conn

    with _conn.reuse_connection():
        # Une requête invalide, ATTRAPÉE par l'appelant — patron des chemins
        # réels sous `status_for` (`try: … except Exception: …`, `# noqa: BLE001`).
        with _conn._connect() as conn:
            try:
                conn.execute("SELECT * FROM une_table_qui_n_existe_pas_du_tout")
            except Exception:  # noqa: BLE001 — c'est le patron réel qu'on exerce
                pass

        # La lecture SUIVANTE, valide, dans la MÊME portée réutilisée : elle doit
        # réussir. Avant ce lot, elle levait `InFailedSqlTransaction`.
        with _conn._connect() as conn:
            ligne = conn.execute("SELECT 1 AS un").fetchone()
    assert ligne["un"] == 1, (
        "une lecture valide, après une erreur attrapée dans la même portée "
        "reuse_connection(), doit réussir — l'autocommit isole chaque requête")


def test_les_allers_retours_ne_dependent_pas_du_nombre_de_connecteurs(live):
    """Compte les VRAIS allers-retours (`_exec_command`, qui porte `BEGIN`/
    `COMMIT`, + les exécutions de requête) — pas les emprunts au pool (#999) ni
    les lectures d'instances (#997, la mauvaise grandeur, cf. mesure prod
    d'oto cd : le temps n'avait pas bougé malgré un banc vert sur ce point)."""
    import psycopg
    from oto_mcp.db import _conn

    def _compte_allers_retours(n: int, *, reuse: bool) -> int:
        appels = {"n": 0}
        exec_command_orig = psycopg.Connection._exec_command
        cursor_execute_orig = psycopg.Cursor.execute

        def _exec_command_espion(self, *a, **kw):
            appels["n"] += 1
            return exec_command_orig(self, *a, **kw)

        def _execute_espion(self, *a, **kw):
            appels["n"] += 1
            return cursor_execute_orig(self, *a, **kw)

        with mock.patch.object(psycopg.Connection, "_exec_command", _exec_command_espion), \
             mock.patch.object(psycopg.Cursor, "execute", _execute_espion):
            ctx = _conn.reuse_connection() if reuse else _nullcontext()
            with ctx:
                for _ in range(n):
                    with _conn._connect() as conn:
                        conn.execute("SELECT 1").fetchone()
        return appels["n"]

    from contextlib import contextmanager

    @contextmanager
    def _nullcontext():
        yield

    # Sous reuse_connection() (autocommit) : chaque _connect() n'ajoute QUE sa
    # requête — aucun BEGIN/COMMIT propre à lui. Le compte croît avec N, mais
    # UNIQUEMENT par la requête elle-même, jamais par un aller-retour de
    # transaction supplémentaire.
    n_petit = _compte_allers_retours(3, reuse=True)
    n_grand = _compte_allers_retours(10, reuse=True)
    assert n_petit == 3, f"3 connect() sous reuse_connection() : {n_petit} allers-retours, attendu 3"
    assert n_grand == 10, f"10 connect() sous reuse_connection() : {n_grand} allers-retours, attendu 10"

    # Contrôle négatif : HORS reuse_connection(), chaque _connect() paie son
    # propre BEGIN/COMMIT en plus de la requête — le compte est nettement plus
    # élevé pour le même N, preuve que le banc mesure la bonne chose.
    n_hors_reuse = _compte_allers_retours(3, reuse=False)
    assert n_hors_reuse > 3, (
        f"hors reuse_connection(), 3 connect() ne devraient PAS coûter 3 "
        f"allers-retours seulement (obtenu {n_hors_reuse}) — sinon ce banc ne "
        "distingue plus les deux chemins")
