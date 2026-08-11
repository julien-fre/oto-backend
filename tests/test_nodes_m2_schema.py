"""Lot M2 (blueprint ADR 0054/0063) — la table `blocks`, et ce qu'elle n'emporte pas.

Le corps d'un nœud devient une séquence de blocs stockés (0063-D2). Ce fichier ne
garde pas la mécanique SQL (elle s'exerce contre un vrai PostgreSQL dans
`test_nodes_m2_conversion.py`) mais les **décisions** que rien dans le code ne
rappellerait à celui qui les déferait :

1. la forme reste SOBRE — même discipline que `nodes` (0063-D3 garde-fou 1) ;
2. **les révisions ne deviennent pas des blocs** : un instantané sérialisé doit
   rester atomique et lisible tel quel, jamais reconstitué par assemblage ;
3. le DDL de ce lot est **purement additif** — prod et preprod partagent la base,
   et la prod tourne encore l'ancien code dessus.
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DB = _ROOT / "oto_mcp" / "db"
_SCHEMA_SRC = (_DB / "_schema.py").read_text(encoding="utf-8")
_INIT_SRC = (_DB / "_init.py").read_text(encoding="utf-8")


def _blocks_block() -> str:
    m = re.search(r"CREATE TABLE IF NOT EXISTS blocks \((.*?)\n\);", _SCHEMA_SRC, re.S)
    assert m, "le bloc `CREATE TABLE … blocks` a disparu de _schema.py"
    return m.group(1)


def test_blocks_carries_only_what_a_block_is():
    """Le nœud, le rang, le type, la charge utile — et l'identité (0059-D3). Une
    colonne « au cas où » se paie autant ici que sur `nodes` : il y a plus de blocs
    que de nœuds. Les champs de bloc vont dans `props`."""
    cols = {m.group(1) for m in re.finditer(r"^\s{4}([a-z_]+) ", _blocks_block(), re.M)}
    assert cols == {"id", "public_id", "node_id", "position", "type", "props",
                    "created_at", "updated_at"}, sorted(cols)


def test_exactly_one_query_index():
    """Le corps d'un nœud, dans l'ordre : c'est la seule question posée à cette
    table. Tout autre index doit être justifié par un usage réel (0063-D3
    garde-fou 2), donc casser ce test plutôt que passer en revue distraite."""
    idx = re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+) ON blocks\(([^)]*)\)",
                     _SCHEMA_SRC)
    assert idx == [("idx_blocks_node", "node_id, position")], idx


def test_public_id_uniqueness_is_named():
    """Même raison que sur `nodes` (docs/live-migrations.md) : un
    `DROP CONSTRAINT IF EXISTS` futur ne doit pas pouvoir viser la contrainte toute
    neuve d'une install fraîche."""
    assert "CONSTRAINT blocks_public_id_key UNIQUE (public_id)" in _blocks_block()


def test_revisions_do_not_become_blocks():
    """0063-D2, et c'est le point le moins intuitif du lot : le corps COURANT est en
    table (pour l'adressage), l'HISTORIQUE reste un document sérialisé (pour
    l'intégrité). `doc_revisions` garde donc son `body_md` — la voir gagner un
    `node_id` ou perdre son corps signerait une reconstitution par assemblage."""
    m = re.search(r"CREATE TABLE IF NOT EXISTS doc_revisions \((.*?)\n\);",
                  _SCHEMA_SRC, re.S)
    assert m and re.search(r"^\s{4}body_md TEXT NOT NULL", m.group(1), re.M), m
    assert "node_id" not in m.group(1)


def test_the_lot_adds_and_never_takes_away():
    """Prod et preprod partagent LA MÊME base, et la prod tourne l'ancien code
    dessus : un DROP exécuté au boot preprod casse la production dans la seconde.
    Ce lot ne retire donc rien de `docs`, `projects` ni de leurs satellites — leur
    sort appartient au lot qui suivra le tag prod (docs/live-migrations.md)."""
    targets = r"(docs|projects|doc_revisions|doc_links|doc_embeddings|doc_change_requests)"
    offenders = []
    for path in (_DB / "_init.py", _DB / "_schema.py", _DB / "nodes.py",
                 _DB / "blocks.py"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(rf"DROP\s+(TABLE|INDEX|COLUMN|CONSTRAINT)[^\n]*\b{targets}\b",
                         line, re.I):
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, offenders


def test_no_index_on_a_column_added_by_migration():
    """Piège déjà payé (docs/live-migrations.md, 20/07) : `_init` exécute `_SCHEMA`
    PUIS les ALTER, donc un `CREATE INDEX` posé dans `_schema.py` sur une colonne
    ajoutée par migration s'exécute contre l'ANCIENNE table — init_db KO, service
    down. `blocks` naît entière ici (table + index ensemble), et rien ne l'ALTER."""
    assert not re.search(r"ALTER TABLE blocks\b", _INIT_SRC), (
        "une colonne ajoutée à `blocks` par migration : son index doit alors vivre "
        "dans _init.py APRÈS l'ALTER, jamais dans _schema.py.")
