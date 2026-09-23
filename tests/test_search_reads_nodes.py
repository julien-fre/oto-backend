"""#282 — la recherche et l'outbox lisent `nodes`, pas la table `guides` gelée.

Le lot M1 (blueprint ADR 0054/0063) a repointé la façade des guides sur `nodes`.
**Deux lecteurs vivaient hors de cette façade** et y sont restés : la recherche
transverse (`db/search.py`) et l'outbox d'embeddings (`db/aux_embed.py`).
Conséquence : un guide écrit depuis M1 restait listé, lisible et injecté au
contexte, mais sortait de `oto_search` — lexicalement ET sémantiquement — sans que
rien ne le signale. Une régression muette, la pire espèce.

Ce que ces tests gardent (convention du repo : logique pure + gardes, le chemin SQL
est exercé au déploiement — il l'a été ici contre un PostgreSQL 17 + pgvector) :

1. **les trois requêtes lisent `nodes`** — c'est l'assertion qui échoue sans le
   correctif ;
2. **l'expression indexée EST celle de la requête**, au caractère près : deux copies
   qui divergent ne se voient pas (le résultat reste juste), le planner abandonne
   simplement l'index et la recherche s'effondre en silence sur un vivier ;
3. **le comportement observable ne change pas** : même scope (platform ∪ org active
   ∪ user), même filtre de livraison, mêmes clés de retour ;
4. **rien de `guides` n'est retiré** — la PROD tourne l'ancien code sur CETTE MÊME
   base ; dropper la table, ses index ou ses embeddings y casserait la recherche
   instantanément (docs/live-migrations.md) ;
5. **le keying des embeddings ne collisionne pas** avec celui de la prod.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from oto_mcp.db import aux_embed as A
from oto_mcp.db import search as S

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DB = _ROOT / "oto_mcp" / "db"


class _Cur:
    def __init__(self, sink, sql, params):
        sink.append((sql, params))
        self.rowcount = 0

    def fetchall(self):
        return []

    def fetchone(self):
        return None


def _spy(monkeypatch, module):
    """Remplace `_connect` du module par un mouchard : capture (sql, params) et
    renvoie zéro ligne. On teste le SQL ÉMIS, pas son exécution."""
    sink: list[tuple[str, tuple]] = []

    class _Conn:
        def execute(self, sql, params=None):
            return _Cur(sink, sql, params)

    class _CM:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(module, "_connect", lambda *a, **k: _CM())
    return sink


# ── 1. les trois lecteurs ont quitté la table gelée ──────────────────────────

def test_lexical_guide_search_reads_nodes(monkeypatch):
    """L'assertion qui échoue sans le correctif : `search_guides_fts` cherchait
    `FROM guides`, où plus aucun guide n'est écrit depuis M1."""
    sink = _spy(monkeypatch, S)
    S.search_guides_fts("duckdb", 7, "u1")
    sql = sink[0][0]
    assert re.search(r"\bFROM nodes\b", sql), sql
    assert not re.search(r"\bguides\b", sql), sql


def test_embedding_outbox_reads_nodes(monkeypatch):
    """Même panne du côté sémantique : sans outbox sur `nodes`, un guide neuf n'est
    jamais embeddé — donc jamais trouvé par le sens non plus."""
    sink = _spy(monkeypatch, A)
    A.list_dirty_aux(16)
    outbox = [sql for sql, _ in sink if "nodes" in sql or "guides" in sql]
    assert outbox, sink
    assert not any("guides" in sql for sql in outbox), outbox
    assert "props->>'delivery' = 'on-demand'" in outbox[0], outbox[0]


def test_semantic_guide_search_joins_nodes(monkeypatch):
    sink = _spy(monkeypatch, A)
    A.search_guides_semantic("[0.1]", 7, "u1")
    sql = sink[0][0]
    assert "JOIN nodes g ON g.id = e.ref" in sql, sql
    assert "guides" not in sql, sql


# ── 2. l'index et la requête viennent de la MÊME expression ──────────────────

def test_index_expression_is_the_query_expression():
    """La règle de `db/search.py` : les expressions indexées sont la source unique
    index ↔ requête. Une expression recopiée à la main d'un côté ne casse RIEN de
    visible — les résultats restent justes, le planner cesse juste d'utiliser
    l'index. C'est ce silence qui la rend dangereuse."""
    ddl = "\n".join(S.index_ddl())
    for name, expr in (("idx_nodes_fts", S._vec(S.NODES_TEXT)),
                       ("idx_nodes_trgm", S._trgm(S.NODES_TEXT))):
        assert f"CREATE INDEX IF NOT EXISTS {name} ON nodes USING GIN ({expr})" in ddl, name
    # Et pas de prédicat partiel aujourd'hui : la table porte des dizaines de lignes,
    # le prédicat se décidera quand les LIGNES de tableau y entreront (M4). Le
    # calibrer sur une population qui n'existe pas est l'erreur qui a produit #282.
    for line in ddl.splitlines():
        if " ON nodes USING GIN" in line:
            assert " WHERE " not in line, line


def test_query_uses_the_indexed_expression(monkeypatch):
    """Le texte cherché dans le WHERE est, au caractère près, celui de l'index."""
    sink = _spy(monkeypatch, S)
    S.search_guides_fts("duckdb", 7, "u1")
    sql = sink[0][0]
    assert S._vec(S.NODES_TEXT) in sql, sql        # FTS tokenisée
    assert S._fold(S.NODES_TEXT) in sql, sql       # repli ILIKE (index trigramme)


# ── 3. le comportement observable ne change pas ──────────────────────────────

def test_scope_and_delivery_are_preserved(monkeypatch):
    """Le `scope` de la surface EST l'`owner_type` du nœud, la livraison une clé de
    `props` : même vocabulaire, même prédicat. platform (tous) ∪ org active ∪ user,
    on-demand seulement — un readme injecté n'est pas un how-to."""
    sink = _spy(monkeypatch, S)
    S.search_guides_fts("duckdb", 7, "u1")
    sql, params = sink[0]
    assert "props->>'delivery' = 'on-demand'" in sql
    assert "owner_type = 'platform'" in sql
    assert "(owner_type = 'org' AND owner_id = %s)" in sql
    assert "(owner_type = 'user' AND owner_id = %s)" in sql
    assert params[2:4] == ("7", "u1"), params      # après les 2 params de la tsquery


def test_return_shape_is_unchanged(monkeypatch):
    """`search.py` lit `scope`/`slug`/`title`/`description`/`updated_at` sur chaque
    hit : la projection nœud → forme historique doit les rendre, sinon la fusion RRF
    lève un KeyError sur un chemin que seul un vrai guide déclenche."""
    sink = _spy(monkeypatch, S)
    S.search_guides_fts("duckdb", 7, "u1")
    sql = sink[0][0]
    for alias in ("owner_type AS scope", "props->>'slug' AS slug",
                  "AS title", "AS description", "updated_at"):
        assert alias in sql, alias


def test_writing_a_guide_puts_it_back_in_the_outbox():
    """`guides` marquait `embed_dirty = TRUE` à chaque écriture. `nodes` n'a pas de
    colonne (sa forme est mesurée, 0063-D3) : le drapeau est une propriété — mais il
    doit être posé aux MÊMES endroits, sinon la sémantique décroche à nouveau."""
    src = (_DB / "guides.py").read_text(encoding="utf-8")
    for fn in ("def set_guide_db(", "def seed_guide_db("):
        body = src[src.index(fn):]
        body = body[:body.index("\ndef ", 1)]
        assert "'embed_dirty', TRUE" in body, fn
    # …et l'outbox le relit sous ce nom exact.
    assert "props->>'embed_dirty'" in A.NODE_DIRTY_SQL


# ── 4. plus RIEN du code ne touche la table `guides` (oto#239, 23/09/2026) ───
#
# Le sens de cette section s'est INVERSÉ. Jusqu'au tag prod, il fallait prouver que
# rien n'était retiré : la production tournait l'ancien code sur CETTE MÊME base et
# lisait encore la table. Depuis, la table est morte — dernière écriture applicative
# le 10/08 — et ce qui restait était pire qu'une archive : un `CREATE TABLE IF NOT
# EXISTS` qui la faisait RENAÎTRE après un `DROP`, des `ALTER`/`UPDATE` de démarrage
# qui écrivaient dans une table que plus rien ne lit pour servir, et une recopie
# « la plus récente gagne » jouée à chaque boot, c'est-à-dire une synchronisation
# permanente là où il fallait une fenêtre de promotion.
#
# ⚠️ Ce que ces tests ne gardent PAS, et c'est voulu : le `DROP TABLE guides`. DDL non
# additive sur une base partagée prod/preprod, jamais au démarrage — décision
# d'Alexis, exécutée par l'opérationnel une fois ce code livré
# (docs/live-migrations.md). Le code qui ne la touche plus part d'abord.

def test_no_code_path_touches_the_legacy_table():
    """Plus une seule instruction SQL ne nomme `guides` dans la couche base."""
    for path in (_DB / "_init.py", _DB / "search.py", _DB / "aux_embed.py",
                 _DB / "guides.py", *sorted((_DB / "schema").glob("*.py"))):
        src = path.read_text(encoding="utf-8")
        fautifs = [l.strip() for l in src.splitlines()
                   if re.search(r"\b(FROM|JOIN|INTO|ON|TABLE|UPDATE)\s+guides\b", l)
                   and not l.strip().startswith(("#", "--", "*"))]
        assert not fautifs, f"{path.name}: {fautifs}"


def test_the_seeding_ddl_cannot_resurrect_the_table():
    """Un `CREATE TABLE IF NOT EXISTS guides` laissé dans le schéma assemblé rendrait
    le `DROP` d'exploitation sans effet : la table renaîtrait au démarrage suivant."""
    from oto_mcp.db import _schema
    assert "CREATE TABLE IF NOT EXISTS guides" not in _schema._SCHEMA


def test_the_legacy_search_indexes_are_no_longer_posted():
    """Les deux index `idx_guides_*` ne servaient plus aucune requête d'ici, et leur
    DDL faisait du module de recherche un écrivain de la table morte."""
    ddl = "\n".join(S.index_ddl())
    assert "ON guides" not in ddl
    assert "guides" not in S.RANKED_SOURCES, (
        "`guides` dans RANKED_SOURCES = un `ALTER TABLE guides ADD COLUMN search_vec` "
        "au démarrage ET un `UPDATE guides SET search_vec` par la boucle de fond.")


def test_no_drop_of_the_legacy_table_at_boot():
    """Le retrait de la table est une DDL non additive : elle n'appartient pas au
    démarrage, sur une base partagée prod/preprod."""
    for path in (_DB / "_init.py", _DB / "search.py", _DB / "guides.py",
                 *sorted((_DB / "schema").glob("*.py"))):
        src = path.read_text(encoding="utf-8")
        fautifs = [l.strip() for l in src.splitlines()
                   if re.search(r"DROP\s+(TABLE|INDEX|COLUMN).*guides", l, re.I)
                   and not l.strip().startswith(("#", "--"))]
        assert not fautifs, f"{path.name}: {fautifs}"


def test_embedding_keying_cannot_collide_with_production():
    """`ref` est désormais un `nodes.id` là où la prod y met un `guides.id` — deux
    séquences indépendantes. Sous le MÊME genre, les deux keyings se recouvriraient
    (`UNIQUE (kind, ref)`) et la prod servirait l'embedding d'un AUTRE guide, sans la
    moindre erreur. Le genre doit donc être neuf."""
    assert A.NODE_KIND != "guide"
    src = (_DB / "aux_embed.py").read_text(encoding="utf-8")
    for literal in ("kind = 'guide'", "'guide' AS kind"):
        assert literal not in src, f"{literal} réapparu = collision de keying avec la prod"


def test_backfill_is_idempotent_and_scoped_to_the_new_keying():
    """Les lignes converties au lot M1 n'ont pas d'embedding sous le nouveau keying :
    sans ce backfill, elles resteraient hors de la recherche sémantique et personne
    ne les rouvrirait jamais. Une fois l'embedding posé, il ne re-marque plus."""
    sql = A.MARK_NODES_TO_EMBED_SQL
    assert f"kind = '{A.NODE_KIND}'" in sql
    assert "IS NOT TRUE" in sql          # ne re-marque pas ce qui l'est déjà
    assert "props->>'delivery' = 'on-demand'" in sql


def test_the_legacy_table_appears_nowhere_in_the_search_module():
    """Garde de dérive : dans `db/search.py`, `guides` ne doit plus apparaître dans
    aucune requête NI dans aucun DDL — la recherche lit `nodes` (#282)."""
    src = (_DB / "search.py").read_text(encoding="utf-8")
    lines = [l.strip() for l in src.splitlines()
             if re.search(r"\bON guides\b|\bFROM guides\b|\bJOIN guides\b", l)]
    assert not lines, lines
