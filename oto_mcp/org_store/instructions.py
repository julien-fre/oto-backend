"""Les PROCÉDURES d'org (table `org_instructions`) : doctrine versionnée.

Le modèle unifié servi par `oto_procedure` : lecture/écriture/recherche d'une
procédure d'org, son historique de versions, et sa vie de **ressource possédée**
(ADR 0030 : id surrogate, copie et transfert d'org).

⚠️ À ne pas confondre avec `oto_mcp/instructions.py`, qui RÉSOUT les instructions
à l'appel ; ici c'est le store.

Feuille du package : n'importe aucun de ses frères.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from ..db import _connect

_log = logging.getLogger(__name__)


# --- instructions d'org : doctrine de base + skills versionnés ----------------
#
# Modèle unifié servi par oto_procedure(op='get') / oto_*_instruction(s). Le slug réservé
# BASE_SLUG ("claude_md") = la doctrine de base (servie d'office) ; les autres =
# des skills chargés à la demande. En clair (prose, hors coffre), lu à l'appel
# (pas de cache). Écriture = incrément de version + snapshot d'historique.

BASE_SLUG = "claude_md"
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def normalize_slug(slug: str) -> str:
    """Slug canonique : minuscules, [a-z0-9_-], séparateurs compactés. '' si vide."""
    return _SLUG_RE.sub("-", (slug or "").strip().lower()).strip("-_")


def _snippet(body: str, query: str, width: int = 200) -> str:
    """Extrait de `body` autour de la 1ʳᵉ occurrence de `query` (pour la recherche)."""
    i = body.lower().find(query.lower())
    if i < 0:
        return body[:width].strip()
    start = max(0, i - width // 3)
    end = min(len(body), i + len(query) + (2 * width) // 3)
    return ("…" if start else "") + body[start:end].strip() + ("…" if end < len(body) else "")


def get_instruction(org_id: int, slug: str, version: Optional[int] = None) -> Optional[dict]:
    """Une PROCÉDURE (courante, ou une `version` archivée précise). None si absente.

    ⚠️ Ne sert plus le readme : `claude_md` était intercepté ici et servi depuis `guides`
    sous la FORME d'une instruction (compat de migration 0042). Le readme n'est pas une
    procédure — il se lit sur la surface guide (`guide_store.get_init_guide('org', id)`,
    capacité `me.guides.*`). Un appel avec ce slug renvoie donc None."""
    slug = normalize_slug(slug)
    with _connect() as conn:
        if version is None:
            row = conn.execute(
                "SELECT id, org_id, slug, title, description, body_md, slots, version, set_by, "
                "created_at, updated_at FROM org_instructions "
                "WHERE owner_type = 'org' AND org_id = %s AND slug = %s",
                (org_id, slug),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT org_id, slug, title, description, body_md, slots, version, set_by, "
                "created_at FROM org_instruction_revisions "
                "WHERE owner_type = 'org' AND org_id = %s AND slug = %s AND version = %s",
                (org_id, slug, version),
            ).fetchone()
        return dict(row) if row else None


def list_instructions(org_id: int, include_base: bool = False) -> list[dict]:
    """Métadonnées des instructions (SANS body) = l'index des skills. Exclut la
    doctrine de base sauf `include_base` (surface admin)."""
    # `owner_type='org'` : post-fusion (chantier procédures, cadrage 10/07) la table
    # porte aussi les lignes GROUP (org_id = org parente) — une liste d'org ne doit
    # jamais les ratisser.
    where = ("owner_type = 'org' AND org_id = %s" if include_base
             else "owner_type = 'org' AND org_id = %s AND slug <> %s")
    params: tuple = (org_id,) if include_base else (org_id, BASE_SLUG)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, slug, title, description, version, updated_at "
            f"FROM org_instructions WHERE {where} ORDER BY slug",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def list_instruction_bodies(org_id: int) -> list[dict]:
    """Slug + body_md des instructions d'une org (hors doctrine de base) — pour
    dériver les références d'outils `<tool:slug>` (compteur « doctrine-only », ADR 0024)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT slug, body_md FROM org_instructions "
            "WHERE owner_type = 'org' AND org_id = %s AND slug <> %s",
            (org_id, BASE_SLUG),
        ).fetchall()
        return [dict(r) for r in rows]


def search_instructions(org_id: int, query: str, include_base: bool = False) -> list[dict]:
    """Recherche substring (title/description/body) dans les instructions de l'org.
    Renvoie les métadonnées + un `snippet` ; le body complet passe par get_instruction."""
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    base_filter = "" if include_base else "AND slug <> %s "
    head: tuple = (org_id,) if include_base else (org_id, BASE_SLUG)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, slug, title, description, body_md, version, updated_at "
            "FROM org_instructions WHERE owner_type = 'org' AND org_id = %s " + base_filter +
            "AND (title ILIKE %s OR description ILIKE %s OR body_md ILIKE %s) "
            "ORDER BY (title ILIKE %s) DESC, (description ILIKE %s) DESC, updated_at DESC",
            head + (like, like, like, like, like),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["snippet"] = _snippet(d.pop("body_md", "") or "", q)
        out.append(d)
    return out


def set_instruction(org_id: int, slug: str, body_md: str, title: Optional[str] = None,
                    description: Optional[str] = None, set_by: Optional[str] = None,
                    slots: Optional[list] = None) -> int:
    """Crée/met à jour une instruction ; renvoie la NOUVELLE version et archive un
    snapshot. `title`/`description`/`slots` None = conserver l'existant ('' / [] à
    la création). `slots` = entités requises déclarées (ADR 0035, validées en amont
    par `slots.validate_slots`). Sérialisé par (org, slug) via verrou advisory."""
    slug = normalize_slug(slug)
    if not slug:
        raise ValueError("slug requis")
    if not (body_md or "").strip():
        raise ValueError("body_md requis")
    # Le readme vit dans `guides` (ADR 0042) et s'écrit sur la surface guide : cette
    # API-ci est celle des PROCÉDURES (slots, versions). Plus de redirection silencieuse.
    if slug == BASE_SLUG:
        raise ValueError(
            f"`{BASE_SLUG}` est le readme d'org, pas une procédure — écris-le via la "
            "surface guide (scope='org', delivery='init').")
    with _connect() as conn:
        with conn.transaction():
            # Verrou + arbitre sur la clé OWNER (chantier procédures B1) : la PK legacy
            # (org_id, slug) tombe en B2 — l'unicité vivante est (owner_type, owner_id, slug).
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"oi:org:{org_id}:{slug}",))
            cur = conn.execute(
                "SELECT version, title, description, slots FROM org_instructions "
                "WHERE owner_type = 'org' AND org_id = %s AND slug = %s",
                (org_id, slug),
            ).fetchone()
            new_version = (cur["version"] + 1) if cur else 1
            new_title = title if title is not None else (cur["title"] if cur else "")
            new_desc = description if description is not None else (cur["description"] if cur else "")
            new_slots = json.dumps(slots if slots is not None
                                   else ((cur["slots"] if cur else None) or []))
            conn.execute(
                """
                INSERT INTO org_instructions
                    (org_id, owner_type, owner_id, slug, title, description, body_md, slots,
                     version, set_by, updated_at)
                VALUES (%s, 'org', %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (owner_type, owner_id, slug) DO UPDATE SET
                    title = EXCLUDED.title, description = EXCLUDED.description,
                    body_md = EXCLUDED.body_md, slots = EXCLUDED.slots,
                    version = EXCLUDED.version,
                    set_by = EXCLUDED.set_by, updated_at = NOW()
                """,
                (org_id, str(org_id), slug, new_title, new_desc, body_md, new_slots,
                 new_version, set_by),
            )
            conn.execute(
                """
                INSERT INTO org_instruction_revisions
                    (org_id, owner_type, owner_id, slug, version, title, description,
                     body_md, slots, set_by)
                VALUES (%s, 'org', %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (org_id, str(org_id), slug, new_version, new_title, new_desc, body_md,
                 new_slots, set_by),
            )
            return new_version


def list_instruction_versions(org_id: int, slug: str) -> list[dict]:
    """Historique d'une procédure (métadonnées par version, plus récent d'abord).
    Le readme n'est pas une procédure et n'a pas d'historique (ADR 0042) → []."""
    slug = normalize_slug(slug)
    if slug == BASE_SLUG:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT version, title, set_by, created_at FROM org_instruction_revisions "
            "WHERE owner_type = 'org' AND org_id = %s AND slug = %s ORDER BY version DESC",
            (org_id, slug),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_instruction(org_id: int, slug: str) -> bool:
    """Supprime une instruction ET son historique. False si elle n'existait pas."""
    slug = normalize_slug(slug)
    with _connect() as conn:
        with conn.transaction():
            cur = conn.execute(
                "DELETE FROM org_instructions "
                "WHERE owner_type = 'org' AND org_id = %s AND slug = %s", (org_id, slug)
            )
            removed = (cur.rowcount or 0) > 0
            conn.execute(
                "DELETE FROM org_instruction_revisions "
                "WHERE owner_type = 'org' AND org_id = %s AND slug = %s",
                (org_id, slug),
            )
    return removed


# --- doctrine = ressource possédée (ADR 0030, épic « couverture des autres types »,
# livraison de projet #52) : l'identité PUBLIQUE d'une doctrine est son `id` surrogate
# (ADR 0032 « stop using slug ») ; son propriétaire est porté par `owner_type/owner_id`
# (chantier procédures, cadrage 10/07 — 'org' aujourd'hui, 'group' à la fusion B2 ; il
# dérivait d'`org_id` avant). Ces fonctions alimentent le kind `doctrine`
# d'`ownership.py` + la cascade de livraison d'un projet (`oto_resource`).

def get_instruction_by_id(instruction_id: int) -> Optional[dict]:
    """Une instruction par son id surrogate (identité publique). None si absente."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, org_id, owner_type, owner_id, slug, title, description, body_md, "
            "slots, version, set_by, created_at, updated_at "
            "FROM org_instructions WHERE id = %s",
            (instruction_id,),
        ).fetchone()
        return dict(row) if row else None


def _free_instruction_slug(conn, org_id: int, slug: str) -> str:
    """Slug libre dans `org_id` : le slug tel quel, sinon suffixé (-2, -3…). On ne
    remplace JAMAIS une doctrine existante de l'org cible (livraison non destructive)."""
    candidate = slug
    for i in range(2, 100):
        row = conn.execute(
            "SELECT 1 FROM org_instructions "
            "WHERE owner_type = 'org' AND org_id = %s AND slug = %s",
            (org_id, candidate),
        ).fetchone()
        if row is None:
            return candidate
        candidate = f"{slug}-{i}"
    raise ValueError(f"aucun slug libre dérivé de `{slug}` dans l'org {org_id}")


def copy_instruction_to_org(instruction_id: int, dest_org_id: int,
                            set_by: Optional[str] = None) -> dict:
    """Copie une doctrine dans une AUTRE org (livraison par transfert de projet, #52) :
    nouvelle doctrine v1 chez la cible (slug suffixé si pris — jamais d'écrasement),
    l'originale reste intacte chez la source. Renvoie {id, slug, org_id} de la copie."""
    src = get_instruction_by_id(instruction_id)
    if src is None:
        raise ValueError(f"doctrine #{instruction_id} introuvable")
    with _connect() as conn:
        dest_slug = _free_instruction_slug(conn, dest_org_id, src["slug"])
    set_instruction(dest_org_id, dest_slug, src["body_md"],
                    title=src.get("title"), description=src.get("description"),
                    set_by=set_by, slots=src.get("slots") or [])
    created = get_instruction(dest_org_id, dest_slug)
    return {"id": created["id"], "slug": dest_slug, "org_id": dest_org_id}


def reparent_instruction(instruction_id: int, new_org_id: int) -> str:
    """Déplace une doctrine vers une autre org (transfert d'ownership ADR 0030, id
    surrogate stable). Slug suffixé si pris chez la cible ; l'historique suit quand
    il ne collisionne pas (sinon il reste chez la source — append-only, pas de perte).
    Renvoie le slug final chez la cible."""
    src = get_instruction_by_id(instruction_id)
    if src is None:
        raise ValueError(f"doctrine #{instruction_id} introuvable")
    if int(src["org_id"]) == int(new_org_id):
        return src["slug"]
    with _connect() as conn:
        dest_slug = _free_instruction_slug(conn, new_org_id, src["slug"])
        conn.execute(
            "UPDATE org_instructions SET org_id = %s, owner_type = 'org', owner_id = %s, "
            "slug = %s, updated_at = NOW() WHERE id = %s",
            (new_org_id, str(new_org_id), dest_slug, instruction_id),
        )
    # L'historique suit dans un second temps (hors transaction principale) : une
    # collision de revisions chez la cible ne doit pas annuler le transfert — il
    # reste alors chez la source (append-only, rien n'est perdu).
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE org_instruction_revisions SET org_id = %s, owner_type = 'org', "
                "owner_id = %s, slug = %s "
                "WHERE owner_type = %s AND owner_id = %s AND slug = %s",
                (new_org_id, str(new_org_id), dest_slug,
                 src.get("owner_type") or "org", src.get("owner_id") or str(src["org_id"]),
                 src["slug"]),
            )
    except Exception:
        _log.warning("reparent_instruction: historique laissé chez la source "
                     "(collision revisions, doctrine #%s)", instruction_id)
    return dest_slug


def list_instructions_for_orgs(org_ids: list[int]) -> list[dict]:
    """Doctrines (hors base) des orgs données — plan GOUVERNANCE (métadonnées + org_id,
    sans body). Alimente `oto_resource(op=list, resource_type='doctrine')`."""
    if not org_ids:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, org_id, slug, title, description, version, updated_at "
            "FROM org_instructions "
            "WHERE owner_type = 'org' AND org_id = ANY(%s) AND slug <> %s ORDER BY org_id, slug",
            (org_ids, BASE_SLUG),
        ).fetchall()
        return [dict(r) for r in rows]


def list_all_instructions() -> list[dict]:
    """Toutes les doctrines nommées (vue opérateur plateforme — gouvernance)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, org_id, slug, title, description, version, updated_at "
            "FROM org_instructions WHERE owner_type = 'org' AND slug <> %s ORDER BY org_id, slug",
            (BASE_SLUG,),
        ).fetchall()
        return [dict(r) for r in rows]
