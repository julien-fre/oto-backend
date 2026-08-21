"""Store des acceptations légales (`legal_acceptances`) — ré-exporté par `db/__init__`.

Une ligne par (sub, doc_slug) = la dernière version acceptée. Trace de consentement
UNIQUEMENT ; les métadonnées des docs vivent dans `legal_docs.py`.
"""
from __future__ import annotations

from ._conn import _connect


def get_legal_acceptances(sub: str) -> dict[str, dict]:
    """slug → {version, accepted_at} des docs acceptés par `sub` (dernière version)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT doc_slug, version, accepted_at FROM legal_acceptances WHERE sub = %s",
            (sub,),
        ).fetchall()
        return {r["doc_slug"]: {"version": r["version"], "accepted_at": r["accepted_at"]}
                for r in rows}


def record_legal_acceptances(sub: str, items: list[tuple[str, str]]) -> None:
    """Upsert (slug, version) pour `sub` — restampe `accepted_at` à maintenant."""
    if not items:
        return
    with _connect() as conn:
        for slug, version in items:
            conn.execute(
                "INSERT INTO legal_acceptances (sub, doc_slug, version, accepted_at) "
                "VALUES (%s, %s, %s, NOW()) "
                "ON CONFLICT (sub, doc_slug) DO UPDATE SET "
                "version = EXCLUDED.version, accepted_at = EXCLUDED.accepted_at",
                (sub, slug, version),
            )


def get_tenant_legal_docs(tenant_slug: str) -> dict[str, dict]:
    """slug → {version, label, url} déclarés par CE tenant. Vide = aucun override —
    `legal_docs.docs_for` retombe alors sur `CURRENT_DOCS` tel quel."""
    if not tenant_slug:
        return {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT doc_slug, version, label, url FROM tenant_legal_docs WHERE tenant_slug = %s",
            (tenant_slug,),
        ).fetchall()
        return {r["doc_slug"]: {"version": r["version"], "label": r["label"], "url": r["url"]}
                for r in rows}


def set_tenant_legal_doc(tenant_slug: str, doc_slug: str, version: str, label: str, url: str) -> None:
    """Upsert l'override d'un tenant pour un slug — prend effet à la lecture suivante."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tenant_legal_docs (tenant_slug, doc_slug, version, label, url, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, NOW()) "
            "ON CONFLICT (tenant_slug, doc_slug) DO UPDATE SET "
            "version = EXCLUDED.version, label = EXCLUDED.label, url = EXCLUDED.url, "
            "updated_at = EXCLUDED.updated_at",
            (tenant_slug, doc_slug, version, label, url),
        )


def delete_tenant_legal_doc(tenant_slug: str, doc_slug: str) -> bool:
    """Retire l'override — le tenant retombe sur le défaut plateforme pour ce slug."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM tenant_legal_docs WHERE tenant_slug = %s AND doc_slug = %s",
            (tenant_slug, doc_slug),
        )
        return (cur.rowcount or 0) > 0
