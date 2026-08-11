"""Embeddings des sources NON-page (oto/#6 C) : briefs de projet + guides on-demand.

Miroir de `doc_embeddings` pour des sources keyées `(kind, ref)` dans `aux_embeddings`.
Le worker `embed_worker` draine `list_dirty_aux` après les docs ; la recherche ajoute
`search_briefs_semantic`/`search_guides_semantic` quand un vecteur de requête est fourni.
Scope guides IDENTIQUE au lexical (`search_guides_fts`) : platform ∪ org active ∪ user.

**Les guides sont des NŒUDS depuis le lot M1** (blueprint ADR 0054/0063) : cette
outbox lit `nodes` depuis #282, plus la table `guides` gelée — sans quoi un guide
écrit depuis M1 n'entrait dans aucun index et sortait de `oto_search` en silence.

⚠️ **Le genre est `node`, pas `guide`, et ce n'est pas cosmétique** : `ref` est
désormais un `nodes.id`, alors que les lignes `kind='guide'` déjà en base portent un
`guides.id` — deux séquences indépendantes. Sous le même genre, les deux keyings se
recouvriraient (`UNIQUE (kind, ref)`) et la PROD, qui tourne l'ancien code sur CETTE
MÊME base, servirait l'embedding d'un AUTRE guide sans la moindre erreur. Les lignes
`kind='guide'` restent donc intactes le temps de la fenêtre ; leur purge appartient
au lot qui suivra le tag prod (docs/live-migrations.md).
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect

# Le genre d'un embedding keyé sur `nodes.id`. Générique par construction : les pages
# et les tableaux qui rejoindront `nodes` (lots M2/M3) s'y rangeront tels quels.
NODE_KIND = "node"

# Texte indexé d'une couche de contexte, mêmes trois champs qu'au temps de `guides`
# (titre + chapô + corps) — la prose vit dans `props`.
_NODE_TEXT = ("coalesce(props->>'title','') || E'\n' || coalesce(props->>'description','') "
              "|| E'\n' || coalesce(props->>'body_md','')")

# Marqueur d'outbox d'un nœud. `nodes` ne porte PAS de colonne `embed_dirty` : la
# forme de la table est MESURÉE (banc M0, forme B) et chaque colonne se paie cent
# mille fois sur un vivier (0063-D3 garde-fou 1) → le drapeau est une propriété,
# comme la livraison. **Posé à l'écriture par `db/guides.py`** (`'embed_dirty', TRUE`
# dans les `jsonb_build_object` de la façade), lu et retiré ici.
NODE_DIRTY_SQL = "(props->>'embed_dirty') = 'true'"

# Backfill de l'outbox (#282), joué par `_init` APRÈS la conversion `guides`→`nodes` :
# toute couche on-demand sans embedding SOUS LE NOUVEAU KEYING est remise à indexer.
# Miroir du backfill que `guides` avait (`UPDATE guides SET embed_dirty = TRUE WHERE
# … NOT IN (SELECT ref FROM aux_embeddings …)`) — sans lui, les lignes déjà converties
# au lot M1 resteraient hors de la recherche sémantique, personne ne les rouvrant
# jamais. Idempotent : une fois l'embedding posé, la ligne n'est plus re-marquée.
MARK_NODES_TO_EMBED_SQL = f"""
    UPDATE nodes SET props = props || '{{"embed_dirty": true}}'::jsonb
     WHERE props->>'delivery' = 'on-demand'
       AND ({NODE_DIRTY_SQL}) IS NOT TRUE
       AND length(COALESCE(props->>'body_md', '')) > 0
       AND id NOT IN (SELECT ref FROM aux_embeddings WHERE kind = '{NODE_KIND}')
"""


def list_dirty_aux(limit: int = 16) -> list[dict]:
    """Briefs + guides on-demand à (ré)indexer (`embed_dirty`), forme uniforme
    `{kind, ref, text}`. Ne prend que les sources à TEXTE non trivial (un brief vide
    n'a rien à indexer)."""
    with _connect() as conn:
        briefs = conn.execute(
            "SELECT 'brief' AS kind, id AS ref, "
            "coalesce(name,'') || E'\n' || coalesce(brief_md,'') AS text "
            "FROM projects WHERE embed_dirty AND archived_at IS NULL "
            "AND length(coalesce(brief_md,'')) > 0 ORDER BY id LIMIT %s",
            (limit,)).fetchall()
        guides = conn.execute(
            f"SELECT '{NODE_KIND}' AS kind, id AS ref, {_NODE_TEXT} AS text "
            f"FROM nodes WHERE {NODE_DIRTY_SQL} AND props->>'delivery' = 'on-demand' "
            "AND length(coalesce(props->>'body_md','')) > 0 ORDER BY id LIMIT %s",
            (limit,)).fetchall()
        return [dict(r) for r in list(briefs) + list(guides)]


def _clear_aux_dirty(conn, kind: str, ref: int) -> None:
    if kind == "brief":
        conn.execute("UPDATE projects SET embed_dirty = FALSE WHERE id = %s", (ref,))
        return
    # Nœud : le drapeau est une clé de `props`, on la retire (absente ⟹ propre).
    conn.execute("UPDATE nodes SET props = props - 'embed_dirty' WHERE id = %s", (ref,))


def upsert_aux_embedding(kind: str, ref: int, content_sha: str,
                         embedding_literal: str, model: str) -> None:
    """Pose/rafraîchit l'embedding d'un brief/guide ET baisse son dirty, en UNE
    transaction (idempotent, ne perd pas une écriture concurrente)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO aux_embeddings (kind, ref, content_sha, embedding, model) "
            "VALUES (%s, %s, %s, %s::halfvec, %s) "
            "ON CONFLICT (kind, ref) DO UPDATE SET content_sha = EXCLUDED.content_sha, "
            "embedding = EXCLUDED.embedding, model = EXCLUDED.model, updated_at = NOW()",
            (kind, ref, content_sha, embedding_literal, model))
        _clear_aux_dirty(conn, kind, ref)


def get_aux_embedding_sha(kind: str, ref: int) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT content_sha FROM aux_embeddings WHERE kind = %s AND ref = %s",
            (kind, ref)).fetchone()
        return row["content_sha"] if row else None


def clear_aux_dirty(kind: str, ref: int) -> None:
    with _connect() as conn:
        _clear_aux_dirty(conn, kind, ref)


def search_briefs_semantic(query_literal: str, project_ids: list[int], *,
                           limit: int = 20, max_distance: float = 0.6) -> list[dict]:
    """Briefs proches du sens de la requête, scopés aux projets accessibles (mêmes
    `project_ids` que le lexical `search_project_briefs`)."""
    if not project_ids:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT p.id, p.name, p.updated_at, "
            "left(p.brief_md, 400) AS body_excerpt, e.embedding <=> %s::halfvec AS distance "
            "FROM aux_embeddings e JOIN projects p ON p.id = e.ref "
            "WHERE e.kind = 'brief' AND p.id = ANY(%s) AND (e.embedding <=> %s::halfvec) < %s "
            "ORDER BY e.embedding <=> %s::halfvec LIMIT %s",
            (query_literal, project_ids, query_literal, max_distance, query_literal, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def search_guides_semantic(query_literal: str, org_id: Optional[int], sub: str, *,
                           limit: int = 20, max_distance: float = 0.6) -> list[dict]:
    """Guides on-demand proches du sens, MÊME scope que le lexical `search_guides_fts` :
    plateforme ∪ org active ∪ user. Jointure sur `nodes` (#282) : `scope` EST
    l'`owner_type`, la prose vit dans `props` — mêmes clés de retour qu'avant."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT g.owner_type AS scope, g.owner_id, g.props->>'slug' AS slug, "
            "coalesce(g.props->>'title','') AS title, "
            "coalesce(g.props->>'description','') AS description, g.updated_at, "
            "left(coalesce(g.props->>'body_md',''), 400) AS body_excerpt, "
            "e.embedding <=> %s::halfvec AS distance "
            "FROM aux_embeddings e JOIN nodes g ON g.id = e.ref "
            f"WHERE e.kind = '{NODE_KIND}' AND g.props->>'delivery' = 'on-demand' "
            "AND (g.owner_type = 'platform' OR (g.owner_type = 'org' AND g.owner_id = %s) "
            "     OR (g.owner_type = 'user' AND g.owner_id = %s)) "
            "AND (e.embedding <=> %s::halfvec) < %s "
            "ORDER BY e.embedding <=> %s::halfvec LIMIT %s",
            (query_literal, str(org_id or ""), sub, query_literal, max_distance,
             query_literal, limit)
        ).fetchall()
        return [dict(r) for r in rows]
