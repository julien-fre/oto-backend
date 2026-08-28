"""Store des acceptations légales (`legal_acceptances`) — ré-exporté par `db/__init__`.

**Un HISTORIQUE** depuis #487 : une ligne par ACCEPTATION (sub, doc, version, date,
IP, user-agent, contexte, org de session), jamais écrasée. La question du gate
(« a-t-il accepté la version courante ? ») se pose donc à la ligne la plus RÉCENTE
de chaque doc, et c'est tout ce que `get_legal_acceptances` rend.

Trace de consentement UNIQUEMENT ; les métadonnées des docs (version courante,
libellé, URL) vivent dans `legal_docs.py`.
"""
from __future__ import annotations

from ._conn import _connect


def get_legal_acceptances(sub: str) -> dict[str, dict]:
    """slug → {version, accepted_at} de la **dernière** acceptation de `sub`, par doc.

    La table est un historique : « la » ligne d'un doc n'existe plus, il y en a une
    par acceptation. `DISTINCT ON` prend la plus récente CÔTÉ SERVEUR — rapatrier
    tout l'historique pour n'en garder qu'une ligne par doc ferait grossir la lecture
    la plus empruntée du gate à chaque ré-acceptation.

    Le départage se fait sur `id` et pas seulement sur la date : `accepted_at` vaut
    `NOW()`, qui est l'horloge de la TRANSACTION — les trois documents d'un `accept`
    d'achat portent le même horodatage à la microseconde près, et deux acceptations
    d'un même doc dans une même transaction seraient indépartageables sans lui."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (doc_slug) doc_slug, version, accepted_at "
            "FROM legal_acceptances WHERE sub = %s "
            "ORDER BY doc_slug, accepted_at DESC, id DESC",
            (sub,),
        ).fetchall()
        return {r["doc_slug"]: {"version": r["version"], "accepted_at": r["accepted_at"]}
                for r in rows}


def record_legal_acceptances(sub: str, items: list[tuple[str, str]], *,
                             context: str | None = None,
                             org_id: int | None = None,
                             ip: str | None = None,
                             user_agent: str | None = None) -> None:
    """AJOUTE une ligne par (slug, version) accepté. Jamais d'écrasement (#487).

    L'écriture d'avant était un upsert sur `(sub, doc_slug)` : accepter les CGV 2.0
    effaçait la trace de l'acceptation des CGV 1.0. Ce qu'on doit pouvoir opposer,
    c'est « à telle date, depuis telle adresse, il a accepté telle version » — une
    ligne mutable ne le porte pas.

    Les quatre satellites SITUENT l'acte et sont tous facultatifs : ils viennent du
    transport (`client_trace`) ou de la session, et une trace absente reste `NULL`
    plutôt qu'une valeur inventée. `org_id` = l'org de session au moment de
    l'acceptation, c'est-à-dire le PAYEUR (ADR 0043) quand le contexte est `purchase`.

    Une seule transaction pour tout le lot : les trois documents d'un achat sont
    acceptés d'un seul geste, ils ne peuvent pas l'être à moitié."""
    if not items:
        return
    with _connect() as conn:
        for slug, version in items:
            conn.execute(
                "INSERT INTO legal_acceptances "
                "(sub, doc_slug, version, context, org_id, ip, user_agent, accepted_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())",
                (sub, slug, version, context, org_id, ip, user_agent),
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
