"""Lot M3 (#301) — M-g : le rang d'une fratrie se prend dans l'INTERVALLE.

Le banc M0 a chiffré les deux gestes possibles : renuméroter une fratrie de 45 000
frères coûte **20 secondes**, insérer dans l'écart entre deux voisins **1,4 ms**.
La règle qui en sort (blueprint `chantier-modele-contenu.md` §5, M-g) inverse les
rôles — l'intervalle est le chemin nominal, la réindexation n'est plus qu'un
rattrapage.

Ce qui est gardé ici :

1. l'arithmétique de l'intervalle, sans base (le cas qui compte — l'épuisement — ne
   s'observe qu'après seize insertions au même point) ;
2. **l'insertion nominale NE RENUMÉROTE PAS** — c'est tout le propos : un test qui
   vérifie seulement l'ordre passerait aussi bien avec une réindexation, donc il ne
   prouverait rien du coût ;
3. le rattrapage se déclenche quand l'écart est épuisé, et pas avant ;
4. la fratrie de la RACINE est bornée au propriétaire (sinon un rattrapage
   renumérote le contenu d'orgs qui ne se connaissent pas).
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE, le VRAI `init_db()` — même recette qu'aux lots M1/M2."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_m3p_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield init_db
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


# ── l'arithmétique, sans base ────────────────────────────────────────────────

def test_the_first_rank_of_an_empty_sibling_set():
    from oto_mcp.db.nodes import POSITION_GAP, midpoint

    assert midpoint(None, None) == POSITION_GAP


def test_appending_costs_one_gap_and_never_looks_left():
    """En fin de fratrie il n'y a pas de voisin de droite : le rang est le dernier
    plus l'écart, quel que soit le nombre de frères. C'est le cas nominal d'une
    conversion — et il est en O(1), pas en O(fratrie)."""
    from oto_mcp.db.nodes import POSITION_GAP, midpoint

    assert midpoint(POSITION_GAP, None) == 2 * POSITION_GAP
    assert midpoint(45_000 * POSITION_GAP, None) == 45_001 * POSITION_GAP


def test_inserting_between_two_neighbours_halves_the_interval():
    from oto_mcp.db.nodes import midpoint

    assert midpoint(0, 1024) == 512
    assert midpoint(512, 1024) == 768
    assert midpoint(None, 1024) == 512          # en tête : la moitié du premier


def test_an_exhausted_interval_is_a_refusal_not_a_collision():
    """Deux voisins collés n'ont PAS de rang libre entre eux. Rendre `after` (ou
    `before`) fabriquerait un doublon silencieux — l'ordre deviendrait celui de la
    clé primaire, sans que rien ne le dise. Refuser est ce qui déclenche le
    rattrapage."""
    from oto_mcp.db.nodes import midpoint

    assert midpoint(10, 11) is None             # collés
    assert midpoint(10, 10) is None             # même rang (fratrie déjà abîmée)
    assert midpoint(None, 1) is None            # rien sous le premier rang
    assert midpoint(10, 12) == 11               # le dernier écart qui tienne


def test_sixteen_insertions_fit_in_one_gap():
    """L'écart de 2^16 n'est pas une décoration : il porte seize insertions
    successives AU MÊME POINT avant d'appeler le rattrapage. C'est ce qui rend ce
    dernier exceptionnel plutôt que périodique."""
    from oto_mcp.db.nodes import POSITION_GAP, midpoint

    after, before, n = 0, POSITION_GAP, 0
    while (mid := midpoint(after, before)) is not None:
        before, n = mid, n + 1                  # on réinsère toujours juste après 0
    assert n == 16


# ── le geste, contre un vrai PostgreSQL ──────────────────────────────────────

def _node(owner_id: str, *, parent_id=None, position=None) -> int:
    """Un nœud NATIF minimal (pas une conversion) — de quoi peupler une fratrie."""
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return int(conn.execute(
            "INSERT INTO nodes (public_id, kind, owner_type, owner_id, parent_id, "
            "position, props) VALUES (%s, 'page', 'org', %s, %s, %s, '{}'::jsonb) "
            "RETURNING id",
            ("nod_" + uuid.uuid4().hex[:24], owner_id, parent_id, position),
        ).fetchone()["id"])


def _positions(ids: list[int]) -> list[int]:
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        rows = conn.execute("SELECT id, position FROM nodes WHERE id = ANY(%s)",
                            (ids,)).fetchall()
    by_id = {r["id"]: r["position"] for r in rows}
    return [by_id[i] for i in ids]


def test_appending_a_sibling_moves_nobody(live):
    """**Le test qui porte la décision.** Vérifier l'ordre ne suffirait pas : une
    réindexation le donne aussi, pour 20 secondes sur 45 000 frères. Ce qu'il faut
    prouver, c'est que **les rangs des frères existants sont INCHANGÉS**."""
    from oto_mcp.db._conn import _connect
    from oto_mcp.db.nodes import POSITION_GAP, place_at_end

    parent = _node("m3p-1")
    a, b = _node("m3p-1", parent_id=parent), _node("m3p-1", parent_id=parent)
    with _connect() as conn:
        place_at_end(conn, a, parent_id=parent, owner_type="org", owner_id="m3p-1")
        place_at_end(conn, b, parent_id=parent, owner_type="org", owner_id="m3p-1")
    avant = _positions([a, b])
    assert avant == [POSITION_GAP, 2 * POSITION_GAP]

    c = _node("m3p-1", parent_id=parent)
    with _connect() as conn:
        pos = place_at_end(conn, c, parent_id=parent, owner_type="org", owner_id="m3p-1")
    assert _positions([a, b]) == avant                     # personne n'a bougé
    assert pos == 3 * POSITION_GAP


def test_inserting_between_two_siblings_moves_nobody_either(live):
    from oto_mcp.db._conn import _connect
    from oto_mcp.db.nodes import place_after, place_at_end

    parent = _node("m3p-2")
    a, b = _node("m3p-2", parent_id=parent), _node("m3p-2", parent_id=parent)
    with _connect() as conn:
        place_at_end(conn, a, parent_id=parent, owner_type="org", owner_id="m3p-2")
        place_at_end(conn, b, parent_id=parent, owner_type="org", owner_id="m3p-2")
    avant = _positions([a, b])

    milieu = _node("m3p-2", parent_id=parent)
    with _connect() as conn:
        pos = place_after(conn, milieu, after_id=a, parent_id=parent,
                          owner_type="org", owner_id="m3p-2")
    assert _positions([a, b]) == avant
    assert avant[0] < pos < avant[1]


def test_the_catch_up_only_fires_when_the_interval_is_exhausted(live):
    """Le rattrapage EXISTE — sans lui, une fratrie saturée n'aurait plus de rang
    libre et l'insertion échouerait ou dupliquerait. Deux voisins collés (1 et 2)
    ne laissent rien entre eux : la réindexation les réétale, puis la place demandée
    se prend dans le nouvel écart. Et l'ancre reste le NŒUD, pas son rang : après le
    rattrapage, le rang `1` ne désigne plus rien."""
    from oto_mcp.db._conn import _connect
    from oto_mcp.db.nodes import POSITION_GAP, place_after

    parent = _node("m3p-3")
    a = _node("m3p-3", parent_id=parent, position=1)
    b = _node("m3p-3", parent_id=parent, position=2)
    milieu = _node("m3p-3", parent_id=parent)

    with _connect() as conn:
        pos = place_after(conn, milieu, after_id=a, parent_id=parent,
                          owner_type="org", owner_id="m3p-3")
    apres = _positions([a, b])
    assert apres == [POSITION_GAP, 2 * POSITION_GAP]       # le rattrapage a joué
    assert apres[0] < pos < apres[1]                       # et la place est la bonne


def test_the_catch_up_stops_at_the_owner_at_the_root(live):
    """À la racine, « tous les nœuds sans parent » n'est pas une fratrie mais la
    table entière. Si le scope n'était pas borné au propriétaire, le rattrapage d'une
    org renumérotererait les racines de toutes les autres — et, au lot M4, ce serait
    la table entière qui se réécrirait pour une insertion."""
    from oto_mcp.db._conn import _connect
    from oto_mcp.db.nodes import POSITION_GAP, place_after, place_at_end

    autre = _node("m3p-voisin", position=7)                # racine d'un AUTRE owner
    a = _node("m3p-4", position=1)
    b = _node("m3p-4", position=2)
    milieu = _node("m3p-4")

    with _connect() as conn:
        place_after(conn, milieu, after_id=a, parent_id=None,
                    owner_type="org", owner_id="m3p-4")
    assert _positions([autre]) == [7]                      # intact : autre fratrie
    assert _positions([a, b]) == [POSITION_GAP, 2 * POSITION_GAP]   # rattrapé

    # Et le placement en fin ne voit que sa propre fratrie.
    seul = _node("m3p-5")
    with _connect() as conn:
        pos = place_at_end(conn, seul, parent_id=None, owner_type="org",
                           owner_id="m3p-5")
    assert pos == POSITION_GAP                             # premier de SA fratrie
