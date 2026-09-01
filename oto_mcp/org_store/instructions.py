"""Les PROCÉDURES (table `org_instructions`) : guide versionné, possédé par un SCOPE.

Le modèle unifié servi par `oto_procedure` : lecture/écriture/recherche d'une
procédure, son historique de versions, et sa vie de **ressource possédée**
(ADR 0030 : id surrogate, copie et déplacement de propriétaire).

⚠️ **Un seul jeu de fonctions, keyé sur `(owner_type, owner_id)`** — la forme que
la table porte déjà (unicité vivante `(owner_type, owner_id, slug)`, sur la table
ET sur ses révisions). Jusqu'au 31/08/2026 il en existait DEUX : celui-ci filtrait
en dur `owner_type='org'`, `group_store` filtrait en dur `owner_type='group'`, et
les deux avaient déjà divergé (l'équipe écrivait `slots='[]'` en dur, ne servait
pas les slots et ignorait l'archivage). Un palier de plus par la même méthode
aurait fait un TROISIÈME jeu — cf. oto-backend#681.

`owner_type` accepté : `org` et `group` (les deux ont une org PARENTE, donc
`org_id` — dénormalisé, FK, NOT NULL — reste toujours renseigné). Le palier
personnel (`user`) est la phase 2 de #681 : il exige de relâcher cette colonne, ce
que ce module refuse explicitement plutôt que d'écrire une ligne bancale.

⚠️ À ne pas confondre avec `oto_mcp/instructions.py`, qui RÉSOUT les instructions
à l'appel ; ici c'est le store.

Feuille du package : n'importe aucun de ses frères — ni `group_store`, qui dépend
de lui (l'org parente d'une équipe se lit en SQL direct sur `org_groups`, même
parti pris que l'invariant org↔groupe dans `members.py`).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from ..db import _connect


# --- instructions : guide de base + skills versionnés ----------------------
#
# Modèle unifié servi par oto_procedure(op='get') / oto_*_instruction(s). Le slug réservé
# BASE_SLUG ("claude_md") = le guide de base (servi d'office) ; les autres =
# des skills chargés à la demande. En clair (prose, hors coffre), lu à l'appel
# (pas de cache). Écriture = incrément de version + snapshot d'historique.

BASE_SLUG = "claude_md"
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")

# Les paliers de propriété qu'une procédure connaît AUJOURD'HUI. `user` est la
# phase 2 de #681 (la colonne `org_id` doit d'abord devenir nullable) : l'ajouter
# ici sans ce préalable écrirait `org_id = NULL` sur une colonne NOT NULL.
OWNER_TYPES: tuple[str, ...] = ("org", "group")

_OWNER_WHERE = "owner_type = %s AND owner_id = %s"


class InstructionExists(Exception):
    """Le slug visé porte DÉJÀ une procédure : une CRÉATION ne l'écrase pas (#662).
    L'écriture est un upsert depuis toujours (la version monte, l'état antérieur part
    en révision) — mais un client qui CRÉE, avec un slug fabriqué chez lui pour un
    agent neuf, n'attend pas de remplacer la procédure d'org qui portait ce nom. Il
    l'apprenait en relisant. D'où ce refus nommé. `archived` : slug pris par une
    procédure ARCHIVÉE, donc absente des listings — sans la nuance le refus semblerait
    porter sur rien, et écrire par-dessus ne désarchiverait pas la ligne (la
    « création » naîtrait invisible)."""

    def __init__(self, slug: str, version: int, archived: bool):
        self.slug, self.version, self.archived = slug, version, archived
        super().__init__(f"slug `{slug}` déjà pris (v{version})")


class InstructionVersionConflict(Exception):
    """Écriture optimiste refusée : la procédure a changé depuis la lecture du client.
    `current_version` vaut `None` quand elle n'existe pas (ou plus) : annoncer une
    version attendue, c'est affirmer avoir lu quelque chose, et l'absence dément cette
    lecture autant qu'un numéro différent. Même parti pris qu'ADR 0044 pour les
    instances de connecteur et qu'`expected_rev` côté pages (`db.DocConflict`) : le
    second écrivain relit et rejoue, il n'écrase pas."""

    def __init__(self, current_version: Optional[int]):
        self.current_version = current_version
        super().__init__("procédure modifiée depuis la lecture")


def normalize_slug(slug: str) -> str:
    """Slug canonique : minuscules, [a-z0-9_-], séparateurs compactés. '' si vide."""
    return _SLUG_RE.sub("-", (slug or "").strip().lower()).strip("-_")


def _owner(owner_type: str, owner_id: int | str) -> tuple[str, str]:
    """Valide la paire propriétaire et la rend sous la forme EXACTE des colonnes
    (`owner_id` est du TEXTE). Lève `ValueError` sur un palier inconnu — pas de repli
    silencieux vers l'org, qui écrirait la procédure chez quelqu'un d'autre."""
    otype = (owner_type or "").strip()
    if otype not in OWNER_TYPES:
        raise ValueError(
            f"owner_type `{owner_type}` inconnu — attendu : {' | '.join(OWNER_TYPES)}")
    oid = str(owner_id).strip()
    if not oid:
        raise ValueError("owner_id requis")
    return otype, oid


def _parent_org_id(conn, owner_type: str, owner_id: str) -> int:
    """L'org PARENTE du propriétaire = la valeur d'`org_instructions.org_id`, colonne
    dénormalisée NOT NULL (FK vers `orgs`, porteuse de la cascade de suppression).

    Une org EST son org ; une équipe tient la sienne dans `org_groups` (`org_id` NOT
    NULL). Lu en SQL direct : `org_store` n'importe jamais `group_store` (cycle)."""
    if owner_type == "org":
        return int(owner_id)
    row = conn.execute(
        "SELECT org_id FROM org_groups WHERE id = %s", (int(owner_id),)).fetchone()
    if row is None:
        raise ValueError(f"équipe #{owner_id} inconnue")
    return int(row["org_id"])


def _snippet(body: str, query: str, width: int = 200) -> str:
    """Extrait de `body` autour de la 1ʳᵉ occurrence de `query` (pour la recherche)."""
    i = body.lower().find(query.lower())
    if i < 0:
        return body[:width].strip()
    start = max(0, i - width // 3)
    end = min(len(body), i + len(query) + (2 * width) // 3)
    return ("…" if start else "") + body[start:end].strip() + ("…" if end < len(body) else "")


def get_instruction(owner_type: str, owner_id: int | str, slug: str,
                    version: Optional[int] = None) -> Optional[dict]:
    """Une PROCÉDURE (courante, ou une `version` archivée précise). None si absente.

    ⚠️ Ne sert plus le readme : `claude_md` était intercepté ici et servi depuis `guides`
    sous la FORME d'une instruction (compat de migration 0042). Le readme n'est pas une
    procédure — il se lit sur la surface guide (`guide_store.get_init_guide(scope, id)`,
    capacité `me.guides.*`). Un appel avec ce slug renvoie donc None.

    ⚠️ Une version archivée vient de la table des RÉVISIONS, qui ne porte ni `id` ni
    `updated_at` : la forme rendue est plus petite."""
    otype, oid = _owner(owner_type, owner_id)
    slug = normalize_slug(slug)
    with _connect() as conn:
        if version is None:
            row = conn.execute(
                "SELECT id, org_id, owner_type, owner_id, slug, title, description, "
                "body_md, slots, version, set_by, created_at, updated_at "
                f"FROM org_instructions WHERE {_OWNER_WHERE} AND slug = %s",
                (otype, oid, slug),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT org_id, owner_type, owner_id, slug, title, description, "
                "body_md, slots, version, set_by, created_at "
                f"FROM org_instruction_revisions WHERE {_OWNER_WHERE} "
                "AND slug = %s AND version = %s",
                (otype, oid, slug, version),
            ).fetchone()
        return dict(row) if row else None


def list_instructions(owner_type: str, owner_id: int | str,
                      include_base: bool = False) -> list[dict]:
    """Métadonnées des instructions (SANS body) = l'index des skills. Exclut la
    guide de base sauf `include_base` (surface admin), et TOUJOURS les
    procédures archivées.

    Toujours, faute d'appelant qui veuille le contraire : le jour où une surface
    admin voudra les voir, elle ajoutera son paramètre avec son besoin sous les
    yeux. C'est le point de l'archivage : cette fonction alimente aussi bien
    l'index que l'IA lit (`instructions.skills_index_md`, qui enrichit la
    description d'`oto_procedure` au tools/list) que `oto_procedure op=list`.
    Une procédure retirée du service doit cesser d'être proposée à l'agent — un
    archivage qui la laisserait dans cet index ne serait qu'un habillage."""
    otype, oid = _owner(owner_type, owner_id)
    where = _OWNER_WHERE if include_base else _OWNER_WHERE + " AND slug <> %s"
    where += " AND archived_at IS NULL"
    params: tuple = (otype, oid) if include_base else (otype, oid, BASE_SLUG)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, slug, title, description, version, updated_at "
            f"FROM org_instructions WHERE {where} ORDER BY slug",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def list_instruction_bodies(owner_type: str, owner_id: int | str) -> list[dict]:
    """Slug + body_md des instructions d'un propriétaire (hors guide de base) — pour
    dériver les références d'outils `<tool:slug>` (compteur « guide-only », ADR 0024)."""
    otype, oid = _owner(owner_type, owner_id)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT slug, body_md FROM org_instructions WHERE {_OWNER_WHERE} "
            "AND slug <> %s",
            (otype, oid, BASE_SLUG),
        ).fetchall()
        return [dict(r) for r in rows]


def search_instructions(owner_type: str, owner_id: int | str, query: str,
                        include_base: bool = False) -> list[dict]:
    """Recherche substring (title/description/body) dans les instructions du scope.
    Renvoie les métadonnées + un `snippet` ; le body complet passe par get_instruction."""
    otype, oid = _owner(owner_type, owner_id)
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    # Archivées exclues sans option d'inclusion : chercher, c'est chercher ce qui
    # est en service (même raison que `list_instructions`).
    base_filter = "AND archived_at IS NULL " + ("" if include_base else "AND slug <> %s ")
    head: tuple = (otype, oid) if include_base else (otype, oid, BASE_SLUG)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, slug, title, description, body_md, version, updated_at "
            f"FROM org_instructions WHERE {_OWNER_WHERE} " + base_filter +
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


def set_instruction(owner_type: str, owner_id: int | str, slug: str, body_md: str,
                    title: Optional[str] = None, description: Optional[str] = None,
                    set_by: Optional[str] = None, slots: Optional[list] = None,
                    must_create: bool = False,
                    expected_version: Optional[int] = None) -> int:
    """Crée/met à jour une instruction ; renvoie la NOUVELLE version et archive un
    snapshot. `title`/`description`/`slots` None = conserver l'existant ('' / [] à
    la création). `slots` = entités requises déclarées (ADR 0035, validées en amont
    par `slots.validate_slots`). Sérialisé par (owner, slug) via verrou advisory.

    Deux gardes anti-écrasement (#662), opt-in, vérifiées SOUS le verrou — qui
    sérialise deux écritures simultanées sans empêcher la seconde d'écraser :
    `must_create` veut le slug LIBRE (sinon `InstructionExists`, geste de création),
    `expected_version` la version que le client a lue (sinon
    `InstructionVersionConflict`, édition concurrente). Aucune par défaut : l'écriture
    nue reste l'upsert que la console MCP et le dashboard exercent depuis toujours. Le
    défaut corrigé est l'absence de tout moyen de NE PAS écraser, pas l'upsert."""
    otype, oid = _owner(owner_type, owner_id)
    slug = normalize_slug(slug)
    if not slug:
        raise ValueError("slug requis")
    if not (body_md or "").strip():
        raise ValueError("body_md requis")
    # Le readme vit dans `guides` (ADR 0042) et s'écrit sur la surface guide : cette
    # API-ci est celle des PROCÉDURES (slots, versions). Plus de redirection silencieuse.
    if slug == BASE_SLUG:
        raise ValueError(
            f"`{BASE_SLUG}` est le readme, pas une procédure — écris-le via la "
            f"surface guide (scope='{otype}', delivery='init').")
    with _connect() as conn:
        with conn.transaction():
            org_id = _parent_org_id(conn, otype, oid)
            # Verrou + arbitre sur la clé OWNER : l'unicité vivante est
            # (owner_type, owner_id, slug) — la PK legacy (org_id, slug) est tombée.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                         (f"oi:{otype}:{oid}:{slug}",))
            cur = conn.execute(
                "SELECT version, title, description, slots, archived_at "
                f"FROM org_instructions WHERE {_OWNER_WHERE} AND slug = %s",
                (otype, oid, slug),
            ).fetchone()
            # Gardes anti-écrasement DANS la transaction verrouillée : entre un
            # pré-check hors verrou et l'INSERT, une écriture concurrente se glisse.
            if must_create and cur is not None:
                raise InstructionExists(slug, cur["version"],
                                        cur["archived_at"] is not None)
            if expected_version is not None and (
                    cur is None or cur["version"] != expected_version):
                raise InstructionVersionConflict(cur["version"] if cur else None)
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (owner_type, owner_id, slug) DO UPDATE SET
                    title = EXCLUDED.title, description = EXCLUDED.description,
                    body_md = EXCLUDED.body_md, slots = EXCLUDED.slots,
                    version = EXCLUDED.version,
                    set_by = EXCLUDED.set_by, updated_at = NOW()
                """,
                (org_id, otype, oid, slug, new_title, new_desc, body_md, new_slots,
                 new_version, set_by),
            )
            conn.execute(
                """
                INSERT INTO org_instruction_revisions
                    (org_id, owner_type, owner_id, slug, version, title, description,
                     body_md, slots, set_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (org_id, otype, oid, slug, new_version, new_title, new_desc, body_md,
                 new_slots, set_by),
            )
            return new_version


def list_instruction_versions(owner_type: str, owner_id: int | str, slug: str) -> list[dict]:
    """Historique d'une procédure (métadonnées par version, plus récent d'abord).
    Le readme n'est pas une procédure et n'a pas d'historique (ADR 0042) → []."""
    otype, oid = _owner(owner_type, owner_id)
    slug = normalize_slug(slug)
    if slug == BASE_SLUG:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT version, title, set_by, created_at FROM org_instruction_revisions "
            f"WHERE {_OWNER_WHERE} AND slug = %s ORDER BY version DESC",
            (otype, oid, slug),
        ).fetchall()
        return [dict(r) for r in rows]


def archive_instruction(owner_type: str, owner_id: int | str, slug: str) -> bool:
    """Archive une procédure (soft-delete) : elle sort de tous les listings, la
    ligne et ses révisions restent. False si elle n'existait pas.

    Idempotent en pratique — ré-archiver rafraîchit l'horodatage plutôt que
    d'échouer, le résultat visé (« elle n'est plus en service ») étant déjà
    atteint. Pas de désarchivage sur cette surface : même choix que
    `db/projects.archive_project`, dont l'inverse n'existe pas non plus côté
    app. Ce qu'archiver garantit ici, c'est que RIEN n'est détruit — contrairement
    à `delete_instruction` juste en dessous, qui emporte l'historique."""
    otype, oid = _owner(owner_type, owner_id)
    slug = normalize_slug(slug)
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE org_instructions SET archived_at = NOW(), updated_at = NOW() "
            f"WHERE {_OWNER_WHERE} AND slug = %s", (otype, oid, slug)
        )
        return (cur.rowcount or 0) > 0


def delete_instruction(owner_type: str, owner_id: int | str, slug: str) -> bool:
    """Supprime une instruction ET son historique. False si elle n'existait pas."""
    otype, oid = _owner(owner_type, owner_id)
    slug = normalize_slug(slug)
    with _connect() as conn:
        with conn.transaction():
            cur = conn.execute(
                f"DELETE FROM org_instructions WHERE {_OWNER_WHERE} AND slug = %s",
                (otype, oid, slug),
            )
            removed = (cur.rowcount or 0) > 0
            conn.execute(
                f"DELETE FROM org_instruction_revisions WHERE {_OWNER_WHERE} AND slug = %s",
                (otype, oid, slug),
            )
    return removed


# --- guide = ressource possédée (ADR 0030, épic « couverture des autres types »,
# livraison de projet #52) : l'identité PUBLIQUE d'un guide est son `id` surrogate
# (ADR 0032 « stop using slug ») ; son propriétaire est porté par `owner_type/owner_id`.
# Ces fonctions alimentent le kind `doctrine` d'`ownership.py` + la cascade de
# livraison d'un projet (`oto_resource`).

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


def _free_instruction_slug(conn, owner_type: str, owner_id: int | str, slug: str) -> str:
    """Slug libre chez `(owner_type, owner_id)` : le slug tel quel, sinon suffixé
    (-2, -3…). On ne remplace JAMAIS une procédure existante de la cible.

    ⚠️ La sonde porte sur la clé d'unicité RÉELLE `(owner_type, owner_id, slug)`.
    Jusqu'au 31/08/2026 elle sondait `owner_type='org' AND org_id=%s` : tant que
    `owner_id = org_id::text` les deux coïncident, mais une seule ligne d'un autre
    palier (ou d'une autre org parente) suffisait à faire répondre « libre » — et
    l'`ON CONFLICT DO UPDATE` qui suit ÉCRASAIT la procédure en place, sans un mot.

    Les RÉVISIONS sont sondées aussi : elles portent la même clé (+ version), donc un
    slug libre côté table vivante mais pris côté historique ferait échouer l'insertion
    du snapshot — et un déplacement ne peut pas emmener son historique sur une
    collision."""
    otype, oid = _owner(owner_type, owner_id)
    candidate = slug
    for i in range(2, 100):
        taken = conn.execute(
            f"SELECT 1 FROM org_instructions WHERE {_OWNER_WHERE} AND slug = %s "
            "UNION ALL "
            f"SELECT 1 FROM org_instruction_revisions WHERE {_OWNER_WHERE} AND slug = %s "
            "LIMIT 1",
            (otype, oid, candidate) * 2,
        ).fetchone()
        if taken is None:
            return candidate
        candidate = f"{slug}-{i}"
    raise ValueError(f"aucun slug libre dérivé de `{slug}` chez {owner_type} {owner_id}")


def copy_instruction_to_owner(instruction_id: int, owner_type: str, owner_id: int | str,
                              set_by: Optional[str] = None) -> dict:
    """Copie une procédure chez un AUTRE propriétaire (livraison par transfert de
    projet, #52) : nouvelle procédure v1 chez la cible (slug suffixé si pris — jamais
    d'écrasement), l'originale reste intacte chez la source. Renvoie
    {id, slug, owner_type, owner_id, org_id} de la copie."""
    otype, oid = _owner(owner_type, owner_id)
    src = get_instruction_by_id(instruction_id)
    if src is None:
        raise ValueError(f"procédure #{instruction_id} introuvable")
    with _connect() as conn:
        dest_slug = _free_instruction_slug(conn, otype, oid, src["slug"])
    set_instruction(otype, oid, dest_slug, src["body_md"],
                    title=src.get("title"), description=src.get("description"),
                    set_by=set_by, slots=src.get("slots") or [])
    created = get_instruction(otype, oid, dest_slug)
    return {"id": created["id"], "slug": dest_slug, "owner_type": otype,
            "owner_id": oid, "org_id": created["org_id"]}


def move_instruction(instruction_id: int, new_owner_type: str,
                     new_owner_id: int | str) -> str:
    """DÉPLACE une procédure d'un palier à l'autre — org ↔ équipe (#681).

    L'`id` surrogate NE BOUGE PAS : c'est lui que `project_links.target_ref` et
    `resource_grants.resource_id` désignent, donc c'est lui qui fait survivre le lien
    de projet et les partages au déplacement. Les RÉVISIONS suivent dans la MÊME
    transaction : une procédure et son historique ne se séparent pas (le chemin
    précédent les déplaçait dans une seconde connexion et, sur collision, laissait
    l'historique chez la source en n'écrivant qu'un warning — 26 versions perdues de
    vue pour un slug déjà pris). Slug suffixé si pris chez la cible (sur la table
    vivante ET l'historique). Renvoie le slug final."""
    otype, oid = _owner(new_owner_type, new_owner_id)
    src = get_instruction_by_id(instruction_id)
    if src is None:
        raise ValueError(f"procédure #{instruction_id} introuvable")
    prev = (str(src["owner_type"]), str(src["owner_id"]))
    if prev == (otype, oid):
        return src["slug"]
    with _connect() as conn:
        with conn.transaction():
            org_id = _parent_org_id(conn, otype, oid)
            dest_slug = _free_instruction_slug(conn, otype, oid, src["slug"])
            conn.execute(
                "UPDATE org_instruction_revisions SET owner_type = %s, owner_id = %s, "
                f"org_id = %s, slug = %s WHERE {_OWNER_WHERE} AND slug = %s",
                (otype, oid, org_id, dest_slug, prev[0], prev[1], src["slug"]),
            )
            cur = conn.execute(
                "UPDATE org_instructions SET owner_type = %s, owner_id = %s, org_id = %s, "
                "slug = %s, updated_at = NOW() WHERE id = %s",
                (otype, oid, org_id, dest_slug, instruction_id),
            )
            if (cur.rowcount or 0) != 1:
                raise ValueError(f"procédure #{instruction_id} introuvable")
    return dest_slug


def list_instructions_for_owners(owners: list[tuple[str, str]]) -> list[dict]:
    """Procédures (hors base) des propriétaires donnés — plan GOUVERNANCE
    (métadonnées + propriétaire, sans body). Alimente
    `oto_resource(op=list, resource_type='doctrine')`."""
    if not owners:
        return []
    clause = " OR ".join([f"({_OWNER_WHERE})"] * len(owners))
    params: tuple = tuple(str(x) for pair in owners for x in pair) + (BASE_SLUG,)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, org_id, owner_type, owner_id, slug, title, description, "
            f"version, updated_at FROM org_instructions WHERE ({clause}) AND slug <> %s "
            # Archivée = hors service : elle ne se propose plus comme ressource
            # à lier à un projet.
            "AND archived_at IS NULL ORDER BY owner_type, owner_id, slug",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def list_all_instructions() -> list[dict]:
    """Toutes les procédures nommées, TOUS paliers (vue opérateur plateforme —
    gouvernance). Le filtre `owner_type='org'` d'avant #681 cachait à l'opérateur
    exactement les lignes qu'il est là pour voir."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, org_id, owner_type, owner_id, slug, title, description, "
            "version, updated_at FROM org_instructions WHERE slug <> %s "
            "ORDER BY org_id, owner_type, owner_id, slug",
            (BASE_SLUG,),
        ).fetchall()
        return [dict(r) for r in rows]
