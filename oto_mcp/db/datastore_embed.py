"""Embeddings des LIGNES de datastore (#67 V2.2) — sémantique OPT-IN par namespace.

Miroir de `doc_embeddings`/`aux_embed` pour des rows keyées `(ns_id, row_id)`. Seuls
les namespaces `semantic_search = TRUE` sont indexés (coût variable maîtrisé). Le worker
`embed_worker` draine `list_dirty_rows` (rows dirty des namespaces opt-in) ; la recherche
ajoute `search_datastore_rows_semantic` quand un vecteur de requête est fourni, scopée
EXACTEMENT comme le lexical (mêmes `ns_ids` accessibles → invariant « cherchable ⇔ lisible »).
"""
from __future__ import annotations

from .datastore import ROW_VALUES_TEXT_SQL

# Le texte embarqué doit être celui des VALEURS, pas des enveloppes (#318) : sans ça
# la provenance entre dans le vecteur sémantique au même titre que le contenu, et
# deux lignes se ressemblent parce qu'elles viennent de la même source. Aucun index
# d'expression ici (le HNSW porte sur le vecteur), donc rien ne bloque — contrairement
# à `oto_search`, dont les GIN lexicaux sont bâtis sur l'expression elle-même.
#
# L'alias de table oblige à qualifier la colonne.
_VALUES_TEXT = ROW_VALUES_TEXT_SQL.replace("data", "r.data")

from ._conn import _connect


def list_dirty_rows(limit: int = 16) -> list[dict]:
    """Rows à (ré)indexer : `embed_dirty` ET namespace opt-in (`semantic_search`).
    Forme `{ns_id, row_id, text}` (text = le JSON rendu). Ne prend que du contenu non
    trivial (une row vide n'a rien à indexer)."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT r.ns_id, r.row_id, {_VALUES_TEXT} AS text "
            "FROM datastore_rows r JOIN user_datastores d ON d.id = r.ns_id "
            f"WHERE r.embed_dirty AND d.semantic_search AND length({_VALUES_TEXT}) > 2 "
            "ORDER BY r.ns_id, r.row_id LIMIT %s",
            (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_row_embedding_sha(ns_id: int, row_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT content_sha FROM datastore_row_embeddings WHERE ns_id = %s AND row_id = %s",
            (ns_id, row_id)).fetchone()
        return row["content_sha"] if row else None


def clear_row_dirty(ns_id: int, row_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE datastore_rows SET embed_dirty = FALSE "
                     "WHERE ns_id = %s AND row_id = %s", (ns_id, row_id))


def upsert_row_embedding(ns_id: int, row_id: str, content_sha: str,
                         embedding_literal: str, model: str) -> None:
    """Pose/rafraîchit l'embedding d'une row ET baisse son dirty, en UNE transaction
    (idempotent, ne perd pas une écriture concurrente)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO datastore_row_embeddings (ns_id, row_id, content_sha, embedding, model) "
            "VALUES (%s, %s, %s, %s::halfvec, %s) "
            "ON CONFLICT (ns_id, row_id) DO UPDATE SET content_sha = EXCLUDED.content_sha, "
            "embedding = EXCLUDED.embedding, model = EXCLUDED.model, updated_at = NOW()",
            (ns_id, row_id, content_sha, embedding_literal, model))
        conn.execute("UPDATE datastore_rows SET embed_dirty = FALSE "
                     "WHERE ns_id = %s AND row_id = %s", (ns_id, row_id))


def search_datastore_rows_semantic(query_literal: str, ns_ids: list[int], *,
                                   limit: int = 20, max_distance: float = 0.6) -> list[dict]:
    """kNN sémantique des lignes des namespaces ACCESSIBLES **et opt-in** (les autres
    n'ont pas d'embedding → naturellement absents). Cut-off de distance cosine comme les
    pages (sinon le kNN renvoie toujours `limit` lignes sans rapport). `excerpt` = début
    de la ligne (passage de repli). Scopé `ns_id = ANY` (jamais calculé ici)."""
    if not ns_ids:
        return []
    sql = (
        "WITH cand AS ("
        "  SELECT e.ns_id, e.row_id, e.embedding <=> %s::halfvec AS distance "
        "    FROM datastore_row_embeddings e "
        "    WHERE e.ns_id = ANY(%s) ORDER BY e.embedding <=> %s::halfvec LIMIT %s"
        ") "
        "SELECT c.ns_id, c.row_id, left(r.data::text, 200) AS excerpt, r.updated_at, c.distance "
        "FROM cand c JOIN datastore_rows r ON r.ns_id = c.ns_id AND r.row_id = c.row_id "
        "WHERE c.distance < %s ORDER BY c.distance LIMIT %s"
    )
    with _connect() as conn:
        rows = conn.execute(
            sql, (query_literal, ns_ids, query_literal, max(limit * 2, limit),
                  max_distance, limit)).fetchall()
        return [dict(r) for r in rows]
