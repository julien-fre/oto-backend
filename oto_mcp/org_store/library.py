"""La BIBLIOTHÈQUE PUBLIQUE de guides (table `doctrine_library`).

Le catalogue cherchable des guides publiés : publier (borné à son auteur),
lister, lire, dépublier — et **forker** une entrée dans son org, ce qui la
transforme en procédure d'org versionnée.

Étage 1 du package : consomme `instructions` (le fork écrit une procédure d'org).
"""
from __future__ import annotations

import json
from typing import Optional

from . import instructions
from ..db import _connect


# --- bibliothèque publique de guides (marketplace, table doctrine_library) ---
#
# Un catalogue cherchable de guides PUBLIÉS, chaque entrée portant un AUTEUR
# ('otomata' = la plateforme, ou 'org' = un créateur privé). Preview + fork dans
# son org (copie versionnée via set_instruction). En clair (prose publiable).
# Deny-by-default sur la surface anonyme : visibility='public' uniquement, jamais
# 'unlisted' (servi seulement par slug exact à un appelant authentifié).

_LIBRARY_COLS = (
    "id, slug, title, description, body_md, slots, author_kind, author_org_id, "
    "author_display, category, tags, visibility, source_org_id, source_slug, "
    "forked_from, version, published_by, created_at, updated_at"
)
_LIBRARY_META_COLS = (
    "id, slug, title, description, author_kind, author_org_id, author_display, "
    "category, tags, visibility, version, created_at, updated_at"
)


class LibrarySlugTaken(Exception):
    """Le slug public est déjà porté par une entrée qui appartient à QUELQU'UN
    D'AUTRE (autre org, ou la plateforme). Volontairement pauvre : le message
    rendu à l'appelant ne doit jamais nommer le propriétaire — une entrée
    `unlisted` se lit par slug exact, son slug tient donc lieu de lien secret et
    un refus précis en confirmerait l'existence (même règle que le 404
    non-disclosant du gate d'org, ADR 0023)."""


def publish_guide(*, slug: str, title: str = "", description: str = "",
                     body_md: str, author_kind: str, author_org_id: Optional[int] = None,
                     author_display: str = "", category: str = "",
                     tags: Optional[list] = None, visibility: str = "public",
                     source_org_id: Optional[int] = None, source_slug: Optional[str] = None,
                     forked_from: Optional[int] = None,
                     published_by: Optional[str] = None,
                     slots: Optional[list] = None) -> dict:
    """Publie (ou RE-publie **la sienne**) dans la bibliothèque. Upsert par `slug` :
    re-publier le même slug incrémente `version` et remplace le corps. Sérialisé
    par slug via verrou advisory. Renvoie la row publiée (avec body).

    ⚠️ Écriture BORNÉE À L'AUTEUR : si le slug existe et que son couple
    `(author_kind, author_org_id)` diffère de celui de l'appelant, on lève
    `LibrarySlugTaken` — publier ne prend jamais possession de l'entrée d'autrui
    (le nom EST l'adresse : `UNIQUE (slug)`, toute l'API adresse par slug).
    L'appartenance est lue SOUS le verrou advisory, donc sans course. Une entrée
    orpheline (`author_kind='org'`, `author_org_id` NULL après suppression de
    l'org — ON DELETE SET NULL) n'est réclamable par personne : seul un admin
    plateforme peut la dépublier."""
    slug = instructions.normalize_slug(slug)
    if not slug:
        raise ValueError("slug requis")
    if not (body_md or "").strip():
        raise ValueError("body_md requis")
    if author_kind not in ("otomata", "org"):
        raise ValueError("author_kind invalide (otomata|org)")
    if author_kind == "org" and author_org_id is None:
        # Sinon l'entrée naît sans propriétaire : indépublicable par son auteur et
        # hors de portée du contrôle d'appartenance ci-dessous.
        raise ValueError("author_org_id requis pour author_kind='org'")
    if visibility not in ("public", "unlisted"):
        raise ValueError("visibility invalide (public|unlisted)")
    tags = list(tags or [])
    with _connect() as conn:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"dl:{slug}",))
            cur = conn.execute(
                "SELECT version, author_kind, author_org_id FROM doctrine_library "
                "WHERE slug = %s", (slug,)
            ).fetchone()
            if cur and (cur["author_kind"], cur["author_org_id"]) != (author_kind, author_org_id):
                raise LibrarySlugTaken(slug)
            new_version = (cur["version"] + 1) if cur else 1
            row = conn.execute(
                f"""
                INSERT INTO doctrine_library
                    (slug, title, description, body_md, slots, author_kind, author_org_id,
                     author_display, category, tags, visibility, source_org_id,
                     source_slug, forked_from, version, published_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (slug) DO UPDATE SET
                    title = EXCLUDED.title, description = EXCLUDED.description,
                    body_md = EXCLUDED.body_md, slots = EXCLUDED.slots,
                    -- `author_kind`/`author_org_id` volontairement ABSENTS du SET :
                    -- l'appartenance d'une entrée ne change jamais par un upsert
                    -- (elle est vérifiée sous le verrou juste au-dessus). Les
                    -- remettre ici rouvrirait la prise de possession silencieuse.
                    author_display = EXCLUDED.author_display,
                    category = EXCLUDED.category, tags = EXCLUDED.tags,
                    visibility = EXCLUDED.visibility,
                    source_org_id = EXCLUDED.source_org_id,
                    source_slug = EXCLUDED.source_slug,
                    forked_from = EXCLUDED.forked_from, version = EXCLUDED.version,
                    published_by = EXCLUDED.published_by, updated_at = NOW()
                RETURNING {_LIBRARY_COLS}
                """,
                (slug, title, description, body_md, json.dumps(slots or []),
                 author_kind, author_org_id,
                 author_display, category, tags, visibility, source_org_id,
                 source_slug, forked_from, new_version, published_by),
            ).fetchone()
            return dict(row)


def list_library(*, query: Optional[str] = None, category: Optional[str] = None,
                 author_kind: Optional[str] = None, author_org_id: Optional[int] = None,
                 include_unlisted: bool = False, limit: int = 100) -> list[dict]:
    """Liste/recherche la bibliothèque (métadonnées + `snippet`, SANS body complet).
    Par défaut visibility='public' uniquement (surface anonyme/vitrine) ;
    `include_unlisted` élargit (surface authentifiée). `query` = substring sur
    title/description/body."""
    where = [] if include_unlisted else ["visibility = 'public'"]
    params: list = []
    q = (query or "").strip()
    if q:
        like = f"%{q}%"
        where.append("(title ILIKE %s OR description ILIKE %s OR body_md ILIKE %s)")
        params += [like, like, like]
    if category:
        where.append("category = %s")
        params.append(category)
    if author_kind:
        where.append("author_kind = %s")
        params.append(author_kind)
    if author_org_id is not None:
        where.append("author_org_id = %s")
        params.append(author_org_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sel = _LIBRARY_META_COLS + (", body_md" if q else "")
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {sel} FROM doctrine_library{clause} "
            f"ORDER BY updated_at DESC LIMIT %s",
            tuple(params) + (int(limit),),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if q:
            d["snippet"] = instructions._snippet(d.pop("body_md", "") or "", q)
        out.append(d)
    return out


def get_library_entry(*, entry_id: Optional[int] = None, slug: Optional[str] = None,
                      include_unlisted: bool = False) -> Optional[dict]:
    """Une entrée complète (avec body_md) par id OU slug. Respecte la visibilité :
    sans `include_unlisted`, ne renvoie que les entrées publiques."""
    if entry_id is None and not slug:
        raise ValueError("entry_id ou slug requis")
    vis = "" if include_unlisted else " AND visibility = 'public'"
    with _connect() as conn:
        if entry_id is not None:
            row = conn.execute(
                f"SELECT {_LIBRARY_COLS} FROM doctrine_library WHERE id = %s{vis}",
                (entry_id,),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT {_LIBRARY_COLS} FROM doctrine_library WHERE slug = %s{vis}",
                (instructions.normalize_slug(slug),),
            ).fetchone()
    return dict(row) if row else None


def fork_into_org(*, entry_id: int, org_id: int, new_slug: Optional[str] = None,
                  set_by: Optional[str] = None) -> dict:
    """Copie une entrée de la bibliothèque dans org_instructions de `org_id` sous
    un nouveau slug (défaut = slug source, suffixé -2/-3… si collision). Réutilise
    set_instruction → le guide forké devient un skill d'org versionné (v1)."""
    entry = get_library_entry(entry_id=entry_id, include_unlisted=True)
    if not entry:
        raise ValueError("entrée de bibliothèque inconnue")
    base_slug = instructions.normalize_slug(new_slug or entry["slug"])
    slug = base_slug
    n = 2
    while instructions.get_instruction(org_id, slug) is not None:
        slug = f"{base_slug}-{n}"
        n += 1
    version = instructions.set_instruction(
        org_id, slug, entry["body_md"], title=entry.get("title") or "",
        description=entry.get("description") or "", set_by=set_by,
        slots=entry.get("slots") or [],
    )
    return {
        "org_id": org_id, "slug": slug, "version": version,
        "forked_from": entry["id"], "source_title": entry.get("title") or "",
    }


def unpublish_guide(entry_id: int) -> bool:
    """Retire une entrée publiée. False si elle n'existait pas."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM doctrine_library WHERE id = %s", (entry_id,))
        return (cur.rowcount or 0) > 0
