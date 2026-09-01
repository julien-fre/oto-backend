"""Les procédures vues du plan GOUVERNANCE (ADR 0030) : identité, circulation, inventaires.

`instructions.py` sert l'autre plan, celui du CONTENU : une procédure qu'on lit, écrit,
versionne et archive à la clé `(owner_type, owner_id, slug)`. Ici c'est la procédure
comme **ressource possédée** — désignée par son `id` surrogate (ADR 0032 « stop using
slug »), qui CHANGE de propriétaire, et qui s'ÉNUMÈRE pour être gouvernée. Ces fonctions
alimentent le kind `doctrine` d'`ownership.py`, la cascade de livraison d'un projet
(`oto_resource`) et la vue opérateur plateforme.

Les deux plans ne se confondent pas, et c'est tout l'intérêt de la séparation : lire le
CORPS d'une procédure et pouvoir la DÉPLACER sont deux droits distincts (ADR 0030,
`can_access` vs `can_govern`). Un fichier par plan rend la frontière visible au lieu de
la laisser à un commentaire de section.

Découpé le 01/09/2026 (issue `oto`#27) : `instructions.py` était à 499 lignes pour un
plafond de 500, et le lot d'avant avait raboté sa prose pour y tenir — un module qu'on
ne peut plus commenter est un module qu'on ne peut plus corriger. La couture était déjà
écrite dans le fichier, en bannière de section ; elle est devenue un fichier.
**Déplacement pur** : aucun corps de fonction n'a changé, la surface `org_store.<fn>`
est identique, aucun appelant n'a bougé.

Étage 1 du DAG du package, comme `library` : dépend d'`instructions`, et de lui seul.
Les références croisées passent par `instructions.<nom>` et jamais par un import à plat —
c'est ce qui fait qu'un `monkeypatch.setattr(org_store, …)` les atteint (cf. `_Facade`).
"""
from __future__ import annotations

from typing import Optional

from . import instructions
from ..db import _connect


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
    otype, oid = instructions._owner(owner_type, owner_id)
    candidate = slug
    for i in range(2, 100):
        taken = conn.execute(
            f"SELECT 1 FROM org_instructions WHERE {instructions._OWNER_WHERE} AND slug = %s "
            "UNION ALL "
            f"SELECT 1 FROM org_instruction_revisions WHERE {instructions._OWNER_WHERE} "
            "AND slug = %s "
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
    otype, oid = instructions._owner(owner_type, owner_id)
    src = get_instruction_by_id(instruction_id)
    if src is None:
        raise ValueError(f"procédure #{instruction_id} introuvable")
    with _connect() as conn:
        dest_slug = _free_instruction_slug(conn, otype, oid, src["slug"])
    instructions.set_instruction(otype, oid, dest_slug, src["body_md"],
                                 title=src.get("title"),
                                 description=src.get("description"),
                                 set_by=set_by, slots=src.get("slots") or [])
    created = instructions.get_instruction(otype, oid, dest_slug)
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
    otype, oid = instructions._owner(new_owner_type, new_owner_id)
    src = get_instruction_by_id(instruction_id)
    if src is None:
        raise ValueError(f"procédure #{instruction_id} introuvable")
    prev = (str(src["owner_type"]), str(src["owner_id"]))
    if prev == (otype, oid):
        return src["slug"]
    with _connect() as conn:
        with conn.transaction():
            org_id = instructions._parent_org_id(conn, otype, oid)
            dest_slug = _free_instruction_slug(conn, otype, oid, src["slug"])
            conn.execute(
                "UPDATE org_instruction_revisions SET owner_type = %s, owner_id = %s, "
                f"org_id = %s, slug = %s WHERE {instructions._OWNER_WHERE} AND slug = %s",
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
    clause = " OR ".join([f"({instructions._OWNER_WHERE})"] * len(owners))
    params: tuple = tuple(str(x) for pair in owners for x in pair) + (instructions.BASE_SLUG,)
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
            (instructions.BASE_SLUG,),
        ).fetchall()
        return [dict(r) for r in rows]
