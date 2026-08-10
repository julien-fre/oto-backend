"""Lot M1 (blueprint ADR 0054/0063) — la table `nodes`, et les guides convertis dedans.

Ce que ces tests gardent n'est pas la mécanique SQL (elle a été exercée contre un
vrai PostgreSQL 17 : boot complet, conversion, rejeu, newer-wins), mais les
**décisions** que rien dans le code ne rappellerait à celui qui les défera par
mégarde :

1. la forme de la table est MESURÉE (banc M0, forme B) — l'élargir sans mesure est
   ce que 0063-D3 interdit nommément ;
2. **deux** index de requête, pas trois — et surtout aucun index de RECHERCHE :
   sur un vivier ils pèsent 99 % du temps d'écriture, leur sort se décide en M5 ;
3. la conversion suit TOUTE écriture de `guides` du même boot (l'ordre est le seul
   risque du lot, comme le seed-avant-colonne de L1) ;
4. elle est rejouable : gardée `to_regclass`, arbitrée `ON CONFLICT`, newer-wins ;
5. le concept de « guide » a disparu — une couche de contexte est une PAGE
   (0055-D4), et la livraison est une propriété ;
6. M1 livre la table et les guides, RIEN d'autre : personne d'autre que la façade
   ne touche encore `nodes`.
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DB = _ROOT / "oto_mcp" / "db"
_SCHEMA_SRC = (_DB / "_schema.py").read_text(encoding="utf-8")
_INIT_SRC = (_DB / "_init.py").read_text(encoding="utf-8")
_GUIDES_SRC = (_DB / "guides.py").read_text(encoding="utf-8")


def _nodes_block() -> str:
    m = re.search(r"CREATE TABLE IF NOT EXISTS nodes \((.*?)\n\);", _SCHEMA_SRC, re.S)
    assert m, "le bloc `CREATE TABLE … nodes` a disparu de _schema.py"
    return m.group(1)


def test_nodes_carries_the_measured_shape():
    """La forme B du banc, colonne pour colonne. Un ajout « au cas où » se paie cent
    mille fois sur un vivier (0063-D3 garde-fou 1) : il doit passer par une mesure,
    donc par un échec de ce test plutôt que par une revue distraite."""
    body = _nodes_block()
    cols = {m.group(1) for m in re.finditer(r"^\s{4}([a-z_]+) ", body, re.M)}
    assert cols == {"id", "public_id", "parent_id", "position", "kind", "owner_type",
                    "owner_id", "props", "claimed_by", "claimed_until",
                    "created_at", "updated_at"}, sorted(cols)


def test_owner_id_is_text_because_a_user_owner_is_a_sub():
    """Écart ASSUMÉ avec la forme mesurée (`owner_id BIGINT`) : un propriétaire de
    scope `user` est un sub Logto — `users.sub` EST la clé primaire, il n'existe
    aucun id numérique d'utilisateur — et `platform` n'a pas d'id du tout. Le
    remettre en BIGINT n'est pas un ajustement de type : c'est inventer un surrogate
    par utilisateur, donc une migration d'identité."""
    assert re.search(r"^\s{4}owner_id TEXT NOT NULL", _nodes_block(), re.M)


def test_exactly_two_query_indexes_and_no_search_index():
    """L'arbre et le propriétaire (0063-D3 garde-fou 2). Les deux index partiels de
    M-f (ownership d'une ligne, prédicat du bail) appartiennent à M4 ; un index de
    recherche appartient à M5 — l'ajouter ici le ferait porter, sans mesure, par le
    lot qui convertira le million de lignes."""
    idx = re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+) ON nodes\(([^)]*)\)",
                     _SCHEMA_SRC)
    assert idx == [("idx_nodes_parent", "parent_id"),
                   ("idx_nodes_owner", "owner_type, owner_id")], idx
    for src, name in ((_SCHEMA_SRC, "_schema.py"), (_INIT_SRC, "_init.py"),
                      ((_DB / "search.py").read_text(encoding="utf-8"), "search.py")):
        assert not re.search(r"ON nodes\s+USING", src, re.I), (
            f"un index d'accès parallèle est posé sur `nodes` dans {name} — sur un "
            "vivier, les GIN de recherche pèsent 99 % du temps d'écriture (banc M0). "
            "Leur sort se décide en M5, pas ici.")


def test_public_id_uniqueness_is_named():
    """Contrainte NOMMÉE (docs/live-migrations.md) : un `DROP CONSTRAINT IF EXISTS`
    futur ne doit pas pouvoir viser la contrainte toute neuve d'une install fraîche.
    Cette unicité porte AUSSI l'invariant que `guides` tenait en
    `UNIQUE (scope, owner_id, slug)` — l'identifiant public en est dérivé."""
    assert "CONSTRAINT nodes_public_id_key UNIQUE (public_id)" in _nodes_block()


def test_conversion_follows_every_write_to_guides_of_the_same_boot():
    """L'ordre est le seul risque du lot. Si la conversion précédait le backfill du
    readme plateforme (`platform_instructions` → `guides`) qui vit juste au-dessus,
    ce readme n'arriverait dans `nodes` qu'au boot SUIVANT — c'est-à-dire qu'un
    redémarrage servirait une couche vide. Même famille de piège que le
    seed-avant-colonne du lot L1 (tenants)."""
    convert = _INIT_SRC.index("CONVERT_GUIDES_TO_NODES_SQL")
    writes = [m.start() for m in re.finditer(
        r"(INSERT INTO guides|UPDATE guides SET)", _INIT_SRC)]
    assert writes, "plus aucune écriture de `guides` dans _init — test à réviser"
    assert max(writes) < convert, (
        "la conversion `guides` → `nodes` doit suivre TOUTE écriture de `guides` du "
        "même boot, sinon ce que ce boot vient de semer n'est converti qu'au suivant.")


def test_conversion_is_replayable():
    """Trois propriétés, toutes déjà payées ailleurs (docs/live-migrations.md) :
    gardée `to_regclass` (après le DROP de `guides`, un boot reste un no-op au lieu
    de casser), arbitrée sur une contrainte nommée (rejouer ne duplique pas), et
    **newer-wins** (une page éditée depuis la nouvelle surface n'est pas écrasée par
    la copie ; une écriture de la prod pendant la fenêtre est rattrapée)."""
    assert "to_regclass('guides')" in _INIT_SRC
    sql = _GUIDES_SRC[_GUIDES_SRC.index("CONVERT_GUIDES_TO_NODES_SQL"):]
    assert "ON CONFLICT ON CONSTRAINT nodes_public_id_key DO UPDATE" in sql
    assert "WHERE EXCLUDED.updated_at > nodes.updated_at" in sql


def test_public_id_has_a_single_definition():
    """L'identifiant public est DÉRIVÉ de la clé naturelle, et c'est ce qui rend la
    conversion rejouable sans index de plus. Deux dérivations qui divergeraient d'un
    caractère (la conversion d'un côté, la façade de l'autre) rempliraient la table
    de doublons au boot suivant, en silence. Une seule fonction, deux appelants."""
    assert _GUIDES_SRC.count("def _public_id_sql(") == 1
    callers = re.findall(r"_public_id_sql\(", _GUIDES_SRC)
    assert len(callers) == 3, callers      # la définition + la façade + la conversion


def test_the_notion_of_guide_kind_does_not_exist():
    """0055-D4 : une couche de contexte EST une page. Le lot ne déménage pas une
    table, il dissout un concept — s'il restait un `kind='guide'`, la livraison
    resterait une NATURE et le modèle unique serait un modèle à exceptions."""
    from oto_mcp.db import guides as G
    assert G._KIND == "page"
    assert "'page', g.scope" in G.CONVERT_GUIDES_TO_NODES_SQL
    # Un seul genre nommé dans toute la façade, et il vient de la constante.
    assert set(re.findall(r"kind = '([^']+)'", _GUIDES_SRC)) == {"{_KIND}"}
    # La livraison, elle, est une clé de `props` — donc une propriété.
    assert "props->>'delivery'" in _GUIDES_SRC


def test_the_facade_no_longer_reads_the_legacy_table():
    """La bascule de lecture (0063-D4) : la surface `oto_guide` / `/api/me/guides/*`
    est inchangée, mais elle lit `nodes`. Aucun SQL de la façade ne doit plus
    toucher `guides` — sinon on retombe dans la double lecture, donc dans les deux
    vérités qui divergent."""
    facade = _GUIDES_SRC[:_GUIDES_SRC.index("CONVERT_GUIDES_TO_NODES_SQL")] \
        + _GUIDES_SRC[_GUIDES_SRC.index("# --- On-demand"):]
    assert not re.search(r"(FROM|INTO|UPDATE|TABLE) guides", facade), facade


def test_user_owned_nodes_follow_a_tenant_switch():
    """`migrate_sub` repointe les ressources d'un compte migré. Les couches de
    contexte d'un utilisateur ont changé de table : sans cette entrée, une bascule
    de tenant les orphelinerait — exactement le trou que l'inventaire avait déjà eu
    sur les ressources possédées (Phase H B1)."""
    from oto_mcp.db.users import _SUB_COLUMNS
    assert ("nodes", "owner_id") in _SUB_COLUMNS
    assert ("guides", "owner_id") in _SUB_COLUMNS, (
        "`guides` reste écrite par la PROD pendant la fenêtre de promotion : les "
        "deux tables se repointent tant que la legacy n'est pas droppée.")


def test_nothing_else_touches_nodes_yet():
    """M1 livre la table et les guides convertis, rien d'autre. Ce garde-fou
    n'interdit rien pour toujours — il force à ce que le premier autre lecteur de
    `nodes` (les pages en M2, les tableaux en M3) soit un acte délibéré, avec sa
    revue, plutôt qu'un effet de bord."""
    allowed = {"oto_mcp/db/guides.py", "oto_mcp/db/_schema.py", "oto_mcp/db/_init.py",
               "oto_mcp/db/users.py"}
    offenders = []
    for path in (_ROOT / "oto_mcp").rglob("*.py"):
        rel = str(path.relative_to(_ROOT))
        if rel in allowed:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"\b(FROM|INTO|UPDATE|TABLE|JOIN) nodes\b", line):
                offenders.append(f"{rel}: {line.strip()}")
    assert not offenders, (
        "quelqu'un lit/écrit `nodes` hors du périmètre M1 : "
        f"{offenders}. Si c'est voulu, c'est un autre lot — retirer ce test dans le "
        "même commit pour que la bascule soit visible en revue.")
