"""Le guide — primitif UNIQUE d'instruction (ADR 0042), sur ses deux axes.

Un guide = une unité de prose portée par **`scope`** (platform | org | group | user —
qui l'écrit, à qui elle s'applique) × **`delivery`** (`init` = concaténé dans l'artefact
injecté au handshake · `on-demand` = listé/chargé quand la tâche le demande). Ces deux
axes sont des PARAMÈTRES, pas des concepts distincts : « agent readme », « secret sauce »
et « instructions » ne sont plus que des libellés d'UI (§Convergence des surfaces,
Décision 5).

**Une capacité, deux faces** (ADR 0009) : `oto_guide` op-aware côté MCP + les routes
`/api/me/guides…` côté dashboard, **mêmes handlers, une seule autz par scope**
(`_owner_for_write`). Cœur = `guide_store` (table `guides`, tout-DB depuis le
2026-07-16 ; les fichiers `guides/*.md` ne sont que des seeds de boot).

`op=list` reste le catalogue **on-demand** : un readme `init` est DÉJÀ injecté, l'ajouter
à l'index que l'agent consulte pour charger de la prose serait un doublon de contexte.
On lit/écrit un init explicitement (`delivery='init'`).

Reste distinct : la **PROCÉDURE** (`org_instructions`, slots/versions — prose avec
sémantique d'exécution, ADR 0042 Décision 3) et la **fiche profil** (structurée,
`capabilities/profile.py`, Décision 6).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .. import db, group_store, guide_store, session_org, tool_alias
from ._authz import SUB_ONLY, refus_de_famille, refus_hors_vue
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from .docs import region
from .registry import CAPABILITIES

_MAX_BODY_BYTES = 64 * 1024


class _NoInput(BaseModel):
    pass


Delivery = Literal["init", "on-demand"]


class GuideRefInput(BaseModel):
    scope: str
    slug: str = ""                       # omis quand delivery='init' (slug canonique)
    delivery: Delivery = "on-demand"
    owner_id: Optional[str] = None       # org/équipe CIBLÉE (défaut : l'active)


class GuideSetInput(BaseModel):
    scope: str
    slug: str = ""
    delivery: Delivery = "on-demand"
    owner_id: Optional[str] = None
    # La borne est en OCTETS (`_set` compte `body.encode()`), et elle est PUBLIÉE :
    # `maxLength` dans le schéma servi, sans validation pydantic — la valider en
    # caractères changerait le code du refus (`invalid_input` au lieu de
    # `body_too_large`) pour le même dépassement. Un front tiers dérive sa garde de
    # saisie du contrat ; jusqu'au 29/08/2026 le contrat ne disait rien.
    body_md: str = Field(
        "", json_schema_extra={"maxLength": _MAX_BODY_BYTES},
        description=("Corps markdown, au plus 65 536 OCTETS UTF-8 — au-delà : 400 "
                     "`body_too_large`. `maxLength` compte des caractères : nécessaire, "
                     "pas suffisant (un caractère accentué pèse deux octets)."))
    title: str = ""
    description: str = ""


class GuideOpInput(BaseModel):
    """Face MCP op-aware (`oto_guide`). `scope` défaut 'user' à l'écriture — un agent
    qui rédige sans préciser écrit POUR SON utilisateur, jamais pour l'org/la plateforme."""
    op: Literal["list", "read", "write", "patch", "delete"] = "list"
    slug: Optional[str] = None
    scope: Optional[str] = None
    delivery: Delivery = "on-demand"
    owner_id: Optional[str] = None
    body_md: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    # op=patch — les MÊMES noms et la même sémantique que `oto_doc op=patch` (`DocInput`) :
    # qui connaît l'un connaît l'autre. Refusés sur toute autre op (`_PATCH_ONLY`).
    section: Optional[str] = Field(default=None, description=(
        "op=patch: the heading text of the section to edit (level and case ignored); "
        "`body_md` = its body, without the heading."))
    region: Optional[Literal["preamble"]] = Field(default=None, description=(
        "op=patch: `preamble` = what precedes the first heading, instead of `section`."))
    mode: Optional[Literal["replace", "append", "prepend", "delete"]] = Field(
        default=None, description=(
            "op=patch: replace (default) | append | prepend | delete (the target, "
            "heading included; no `body_md`)."))
    expected_rev: Optional[str] = Field(default=None, description=(
        "op=patch: the `rev` from op=read or a previous patch — refused if the guide "
        "changed since."))


def _target_org(ctx: ResolvedCtx, owner_id: Optional[str]) -> int:
    """L'org visée : celle passée explicitement (dashboard, route par-id), sinon l'active."""
    if owner_id:
        # Vue bornée (oto#270) : une autre org que O n'est pas lisible ici.
        borne = session_org.current_view_as_bound_org()
        if borne is not None and int(owner_id) != borne:
            raise refus_hors_vue(f"l'org #{owner_id}")
        return int(owner_id)
    if ctx.org_id is None:
        raise AuthzDenied(400, "no_active_org", "No active org — see `oto_use_org`.")
    return int(ctx.org_id)


def _target_group(ctx: ResolvedCtx, owner_id: Optional[str]) -> int:
    """L'équipe visée : celle passée explicitement, sinon l'active. ⚠️ Une vue qui gère
    UNE équipe donnée doit passer son id — l'équipe « active » est un contexte de session,
    pas ce que l'écran a sous les yeux."""
    if owner_id:
        # Vue bornée (oto#270) : une équipe hors de O n'est pas lisible ici.
        borne = session_org.current_view_as_bound_org()
        if borne is not None and (group_store.get_group(int(owner_id)) or {}).get(
                "org_id") != borne:
            raise refus_hors_vue(f"l'équipe #{owner_id}")
        return int(owner_id)
    from .. import access
    gid = ctx.group_id if ctx.group_id is not None else access.current_group(ctx.sub)
    if gid is None:
        raise AuthzDenied(400, "no_active_group",
                          "No active team — see `oto_use_group`.")
    return int(gid)


def _owner_for_write(ctx: ResolvedCtx, scope: str, owner_id: Optional[str] = None,
                     *, ondemand: bool = False) -> str:
    """Le owner_id d'écriture pour `scope`, avec autz sur la cible RÉELLE. platform →
    platform_admin ; org → org_admin de l'org visée ; group → chef de l'équipe visée
    (escalade roles.py) ; user → self ; tenant → platform_admin, ou (guide à la
    demande seulement) un admin de CE tenant. Lève AuthzDenied."""
    from .. import roles
    if scope == "user":
        return ctx.sub                    # jamais d'écriture pour un AUTRE user
    if scope == "org":
        oid = _target_org(ctx, owner_id)
        if not roles.is_org_admin(ctx.sub, oid):
            raise AuthzDenied(403, "forbidden", "Réservé à un admin de l'org (guide d'org).")
        return str(oid)
    if scope == "group":
        gid = _target_group(ctx, owner_id)
        if not roles.can_admin_group(ctx.sub, gid):
            raise AuthzDenied(403, "forbidden",
                              "Réservé au chef de l'équipe (guide d'équipe).")
        return str(gid)
    if scope == "platform":
        if not roles.is_platform_admin(ctx.sub):
            raise AuthzDenied(403, "forbidden", "Réservé à l'admin plateforme (guide plateforme).")
        return guide_store.PLATFORM_OWNER
    if scope == "tenant":
        # Le socle d'un tenant remplace le nôtre pour TOUS ses comptes : c'est la
        # marque et le mode d'emploi d'un produit tiers. L'écrire est un acte
        # d'exploitation, jamais une préférence — même palier que le socle plateforme.
        if roles.is_platform_admin(ctx.sub):
            return _target_tenant(ctx, owner_id)
        # Un guide À LA DEMANDE de tenant est le contenu du partenaire, pas le nôtre
        # (décision du 30/09/2026) : son admin l'écrit, sur SON tenant seulement — le
        # même rôle que `_authz.TENANT_ADMIN_OF`, lu sur le sub qualifié. Un membre ou
        # un admin d'une org cliente n'a pas ce rôle.
        if ondemand:
            from .. import db, tenancy
            slug = _target_tenant(ctx, owner_id)
            if tenancy.current().tenant_of(ctx.sub) == slug and db.is_tenant_admin(
                    slug, ctx.sub):
                return slug
            raise AuthzDenied(403, "forbidden",
                              f"Réservé à un admin du tenant `{slug}` ou à l'admin "
                              "plateforme (guide de tenant).")
        raise AuthzDenied(403, "forbidden",
                          "Réservé à l'admin plateforme (socle de tenant).")
    raise AuthzDenied(400, "bad_scope",
                      "scope éditable = platform | tenant | org | group | user.")


def _target_tenant(ctx: ResolvedCtx, owner_id: Optional[str]) -> str:
    """Le slug du tenant visé — explicite, ou celui de l'appelant.

    Refuse un tenant inconnu du registre plutôt que d'écrire une ligne que personne ne
    lira : le registre est construit au boot, donc une faute de frappe resterait muette
    jusqu'à ce qu'on cherche pourquoi la marque ne change pas."""
    from .. import tenancy
    slug = (owner_id or "").strip() or tenancy.current().tenant_of(ctx.sub)
    if not slug or slug == tenancy.primary_slug():
        raise AuthzDenied(400, "bad_owner",
                          "Name the target tenant (its slug) — `platform` has its "
                          "own scope.")
    if not any(e.slug == slug for e in tenancy.current().entries()):
        raise AuthzDenied(404, "unknown_tenant",
                          f"No tenant `{slug}` in the registry. A tenant is declared "
                          "in the database and the registry is built at startup.")
    return slug


def _owner_for_read(ctx: ResolvedCtx, scope: str, owner_id: Optional[str] = None) -> str:
    """Le owner_id de LECTURE d'un readme init. Sans cible explicite : l'identité
    courante du scope (ce que le handshake injecte déjà, aucun droit supplémentaire).
    Avec une cible : appartenance requise — un readme d'org/équipe n'est pas public."""
    from .. import roles
    if scope == "user":
        return ctx.sub
    if scope == "org":
        oid = _target_org(ctx, owner_id)
        if owner_id and not roles.is_org_member(ctx.sub, oid):
            raise AuthzDenied(403, "forbidden", "Reserved to members of the org.")
        return str(oid)
    if scope == "group":
        gid = _target_group(ctx, owner_id)
        if owner_id and not roles.can_read_group(ctx.sub, gid):
            raise AuthzDenied(403, "forbidden", "Reserved to members of the team.")
        return str(gid)
    if scope == "platform":
        return guide_store.PLATFORM_OWNER
    if scope == "tenant":
        # Lecture : son propre tenant sans rien demander (c'est ce que le handshake
        # injecte déjà) ; un autre tenant demande le palier plateforme.
        from .. import roles, tenancy
        sien = tenancy.current().tenant_of(ctx.sub)
        if owner_id and owner_id != sien and not roles.is_platform_admin(ctx.sub):
            raise AuthzDenied(403, "forbidden", "Reserved to the platform admin.")
        return _target_tenant(ctx, owner_id)
    raise AuthzDenied(400, "bad_scope", "scope = platform | tenant | org | group | user.")


def _init_ref(ctx: ResolvedCtx, scope: str, slug: str, *, write: bool,
              owner_id: Optional[str] = None) -> tuple[str, str]:
    """`(ident passé au store, slug affichable)` d'un readme init.

    ⚠️ Asymétrie de `guide_store` : pour la PLATEFORME l'ident EST le slug du bloc
    (il en existe plusieurs — défaut `secret_sauce`), l'owner étant constant ; pour
    org/group/user l'ident est l'OWNER et le slug est canonique (`readme`) → l'appelant
    n'a pas à le connaître. L'autz passe toujours par le résolveur d'owner du mode."""
    owner = (_owner_for_write(ctx, scope, owner_id) if write
             else _owner_for_read(ctx, scope, owner_id))
    if scope == "platform":
        s = slug or guide_store.PLATFORM_SLUG
        return s, s
    return owner, guide_store.INIT_SLUG


class GuideRef(BaseModel):
    """Entrée de catalogue : de quoi CHOISIR un guide, sans en charger le corps."""
    slug: str
    # Le palier qui SERT ce guide, posé en dur par `guide_store.list_guides_for` — seul
    # émetteur. `tenant` = le guide du partenaire, qui REMPLACE celui de la plateforme
    # de même slug (#437 : le commentaire disait « platform | org | user »).
    scope: Literal["platform", "tenant", "org", "user"]
    title: str = ""
    description: str = ""


class GuideList(BaseModel):
    guides: list[GuideRef]


class GuideView(BaseModel):
    """Enveloppe COMMUNE de get et set, pour les deux livraisons — d'où les champs
    optionnels, qui ne coexistent pas tous :
    - un readme `init` porte `delivery` et `updated_at`, jamais de titre ;
    - un guide `on-demand` porte titre/description ;
    - `set` d'un on-demand ne renvoie PAS le corps (l'appelant vient de l'écrire) ;
    - la lecture d'un on-demand porte `rev`, l'ETag de son contenu — ce qu'`oto_guide
      op=patch` attend en `expected_rev`.
    """
    scope: str
    slug: str
    delivery: Optional[str] = None
    body_md: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    updated_at: Optional[str] = None
    rev: Optional[str] = None


class GuideDeleted(BaseModel):
    """Un readme `init` ne se supprime pas, il se VIDE — d'où `delivery` ici et un
    `deleted` qui vaut « la couche ne s'affiche plus », pas « la ligne est partie »."""
    scope: str
    slug: str
    deleted: bool
    delivery: Optional[str] = None


def _list(ctx: ResolvedCtx, inp: _NoInput) -> dict:
    """Catalogue des guides on-demand visibles : plateforme ∪ org active ∪ perso (DB)."""
    return {"guides": guide_store.list_guides_for(ctx.sub, ctx.org_id)}


def _init_view(scope: str, slug: str, state: dict) -> dict:
    """Forme d'un readme init, alignée sur celle d'un guide on-demand."""
    return {"scope": scope, "slug": slug, "delivery": "init",
            "body_md": state.get("body_md") or "", "updated_at": state.get("updated_at")}


def _avec_rev(g: dict) -> dict:
    """Le guide lu, plus `rev` : l'ETag de son contenu STOCKÉ (`db.guide_rev`), à
    repasser en `expected_rev` à `op=patch`. À poser AVANT la réécriture aux noms du
    produit — c'est le canonique que la base compare."""
    return {**g, "rev": db.guide_rev(g.get("title"), g.get("description"),
                                     g.get("body_md"))}


def _get(ctx: ResolvedCtx, inp: GuideRefInput) -> dict:
    if inp.delivery == "init":
        # Lecture de SON contexte (org/équipe actives) — ce que le handshake injecte.
        ident, slug = _init_ref(ctx, inp.scope, inp.slug, write=False,
                                owner_id=inp.owner_id)
        return _init_view(inp.scope, slug, guide_store.get_init_guide(inp.scope, ident))
    if not inp.slug:
        raise AuthzDenied(400, "missing_slug", "`slug` is required for an on-demand guide.")
    if inp.scope == "tenant" and inp.owner_id:
        # Un tenant NOMMÉ : son auteur relit ce qu'il vient d'écrire. La lecture en
        # cascade ne suit que le tenant de l'appelant, donc l'admin plateforme qui
        # écrit pour un partenaire écrivait sans pouvoir se relire.
        from .. import db
        owner = _owner_for_read(ctx, "tenant", inp.owner_id)
        row = db.get_guide_db("tenant", owner, inp.slug)
        g = ({"slug": inp.slug, "scope": "tenant", "title": row["title"],
              "description": row["description"], "body_md": row["body_md"]}
             if row else None)
    else:
        # `scope` vide = pas de filtre : le store cherche tenant → plateforme → org →
        # user (1er match).
        g = guide_store.read_guide_scoped(inp.slug, scope=inp.scope or None,
                                          org_id=ctx.org_id, sub=ctx.sub)
    if g is None:
        where = f" (scope {inp.scope})" if inp.scope else ""
        raise AuthzDenied(404, "not_found",
                          f"Guide `{inp.slug}`{where} not found — list them with op=list.")
    # Les outils CITÉS dans le corps, au nom du produit de ce compte. Le socle le fait
    # depuis toujours (`instructions.session_layers`), les descriptions d'outils et les
    # messages d'erreur aussi — pas le texte d'un guide, alors que c'est le texte que le
    # socle prescrit d'aller lire. Sans ça le renommage se retourne contre lui-même : la
    # notice prescrit `oto_doc`, l'agent l'appelle (le serveur l'accepte), et le client
    # réaffiche `Oto doc` — le nom qu'on voulait faire disparaître, à l'endroit exact où
    # il se voit.
    #
    # ⚠️ Portée : les NOMS D'OUTILS, pas le nom du produit. Un guide qui écrit « la
    # boîte à outils oto » en toutes lettres continuera de le dire — la prose d'un guide
    # est du contenu, et la réécrire par substitution serait réécrire le texte de
    # quelqu'un. Le remède de ce cas-là est l'étage tenant ci-dessus : un partenaire que
    # notre prose gêne écrit la sienne.
    g = _avec_rev(g)
    prefix = tool_alias.prefix_for(ctx.sub)
    if not prefix:
        return g
    return {**g, "body_md": tool_alias.rewrite_prose(g["body_md"], prefix)}


def _set(ctx: ResolvedCtx, inp: GuideSetInput) -> dict:
    # Le symétrique de `_get` : le corps est LU aux noms du produit, il s'ÉCRIT au
    # canonique. Sans ça, relire un guide puis le réenregistrer stockait `acme_doc`.
    body = tool_alias.canonical_prose(inp.body_md or "", ctx.sub)
    poids = len(body.encode())
    if poids > _MAX_BODY_BYTES:
        # La borne était publiée depuis le 29/08 (`maxLength` ci-dessus) ; il manquait la
        # MESURE. Sans elle l'auteur raccourcit à l'aveugle, et il ne peut même pas la
        # calculer : `maxLength` compte des caractères, la garde compte des OCTETS, et
        # deux textes de même longueur ne pèsent pas pareil (leçon du datastore, signal
        # #383 — « pas de combien on dépasse fait deviner »).
        raise AuthzDenied(
            400, "body_too_large",
            f"`body_md` pèse {poids} octets UTF-8 pour {_MAX_BODY_BYTES} au plus "
            f"({poids - _MAX_BODY_BYTES} de trop). La borne est en OCTETS : un caractère "
            "accentué en pèse deux, donc compter les caractères la sous-estime.")
    if inp.delivery == "init":
        # Un corps vide EFFACE la couche (la note se retire comme elle s'écrit) — là où
        # un guide on-demand vide n'aurait aucun sens (rien à charger).
        ident, slug = _init_ref(ctx, inp.scope, inp.slug, write=True,
                                owner_id=inp.owner_id)
        try:
            state = guide_store.set_init_guide(inp.scope, ident, body)
        except guide_store.GuideDeliveryConflict as e:
            raise AuthzDenied(409, "delivery_conflict", str(e))
        return _init_view(inp.scope, slug, state)
    owner_id = _owner_for_write(ctx, inp.scope, inp.owner_id, ondemand=True)
    if not inp.slug:
        raise AuthzDenied(400, "missing_slug", "`slug` is required for an on-demand guide.")
    if not body.strip():
        raise AuthzDenied(400, "missing_body", "`body_md` requis.")
    try:
        return guide_store.set_guide(inp.scope, owner_id, inp.slug, body,
                                     inp.title or "", inp.description or "")
    # ⚠️ AVANT `GuideError`, dont c'est une sous-classe : sans cet ordre le conflit de
    # livraison sortirait en 400 `invalid_guide` — « ta saisie est mal formée » — alors
    # que la saisie est valide et que c'est l'ÉTAT en place qui refuse.
    except guide_store.GuideDeliveryConflict as e:
        raise AuthzDenied(409, "delivery_conflict", str(e))
    except guide_store.GuideError as e:
        raise AuthzDenied(400, "invalid_guide", str(e))
    except db.SlugDeLAutreFamille as e:        # une procédure porte ce slug (oto#100)
        raise refus_de_famille(e)


def _delete(ctx: ResolvedCtx, inp: GuideRefInput) -> dict:
    if inp.delivery == "init":
        # Retirer une couche injectée = la vider (pas de ligne à supprimer : le rendu
        # omet déjà les couches vides).
        ident, slug = _init_ref(ctx, inp.scope, inp.slug, write=True,
                                owner_id=inp.owner_id)
        # ⚠️ Vider un readme, c'est l'ÉCRIRE vide : ce chemin passe par le même
        # upsert que `_set`, donc il rencontre le même conflit de livraison. Sans
        # cette branche il sortirait en 500 « erreur interne » — un refus prévu
        # rendu comme une panne, et l'appelant n'a alors rien à faire de la réponse.
        try:
            guide_store.set_init_guide(inp.scope, ident, "")
        except guide_store.GuideDeliveryConflict as e:
            raise AuthzDenied(409, "delivery_conflict", str(e))
        return {"scope": inp.scope, "slug": slug, "delivery": "init", "deleted": True}
    owner_id = _owner_for_write(ctx, inp.scope, inp.owner_id, ondemand=True)
    if not inp.slug:
        raise AuthzDenied(400, "missing_slug", "`slug` is required for an on-demand guide.")
    deleted = guide_store.delete_guide(inp.scope, owner_id, inp.slug)
    if not deleted:
        raise AuthzDenied(404, "not_found", f"Guide `{inp.slug}` (scope {inp.scope}) absent.")
    return {"scope": inp.scope, "slug": inp.slug, "deleted": True}


# Les arguments qui n'ont de sens que pour `op=patch`. Refusés sur toute autre op,
# jamais avalés : un `expected_rev` ignoré par `op=write` laisserait croire à un
# contrôle de conflit qui n'a pas lieu.
_PATCH_ONLY = ("section", "region", "mode", "expected_rev")


def _patch(ctx: ResolvedCtx, inp: GuideOpInput) -> dict:
    """`op=patch` : éditer UNE région d'un guide on-demand sans en réémettre le corps.

    Le manque : `op=write` REMPLACE le corps. Ajouter une ligne datée à un journal de
    ~60 000 caractères tenu dans un guide voulait dire le relire et le réémettre en
    entier — et risquer d'en abîmer le reste sans le voir. La grammaire, le moteur et
    les refus sont ceux d'`oto_doc op=patch`, par le même code (`docs/region`) : aucune
    seconde grammaire à apprendre, aucune seconde copie des phrases.

    ⚠️ Trois choses propres au guide :
    - le DROIT est celui d'`op=write`, résolu par la même ligne (`_owner_for_write`)
      et AVANT toute lecture — un refus ne décrit jamais un guide à qui ne l'écrit pas ;
    - le patch porte sur un guide EXISTANT : il ne crée rien (`not_found`) ;
    - lecture et écriture se font sous le verrou de la ligne (`db.patch_guide_db`) :
      sans `expected_rev`, deux ajouts simultanés s'enchaînent au lieu de s'écraser.

    Un readme `init` est refusé : il est injecté entier dans chaque session, donc
    tenu court — il se réécrit, il ne se rapièce pas."""
    scope = inp.scope or "user"
    if inp.delivery == "init":
        raise AuthzDenied(
            400, "unsupported_delivery",
            "op=patch edits an on-demand guide. A readme (delivery='init') is injected "
            "whole into every session, so it is kept short and rewritten whole: "
            "op=write with delivery='init'.")
    owner_id = _owner_for_write(ctx, scope, inp.owner_id, ondemand=True)
    slug = inp.slug or ""
    mode = inp.mode or "replace"
    # La forme de l'appel est celle d'`oto_doc op=patch`, jugée par le même code.
    vise_section = region.valider(inp.section, inp.region, mode, inp.body_md)
    if inp.title is not None or inp.description is not None:
        raise AuthzDenied(
            400, "unsupported_on_patch",
            "op=patch edits the body only: `title` and `description` are set with "
            "op=write. Remove them.")
    # Le symétrique de `_get`, comme dans `_set` : ce qui a été LU aux noms du produit
    # revient au canonique — le titre visé comme le contenu ajouté.
    section = tool_alias.canonical_prose(inp.section, ctx.sub) if vise_section else None
    contenu = (None if inp.body_md is None
               else tool_alias.canonical_prose(inp.body_md, ctx.sub))
    emporte: dict = {}

    def appliquer(corps: str) -> str:
        """Le corps patché, ou un refus — joué par la base sous le verrou de la ligne."""
        # Le moteur et ses refus sont ceux d'`oto_doc op=patch` ; seuls le nom de ce
        # qu'on patche et le verbe qui le réécrit entier changent (`region.GUIDE`).
        fait = region.appliquer(corps, section, contenu, mode, region.GUIDE,
                                affiche=inp.section)
        emporte.update(removed=fait.emporte, subsections=fait.sous_sections)
        # Les deux bornes d'`op=write`, jugées sur le RÉSULTAT : un guide on-demand
        # n'est jamais vide, et son corps tient sous `_MAX_BODY_BYTES`.
        nouveau = fait.corps.strip()
        if not nouveau:
            raise AuthzDenied(
                400, "empty_guide",
                "This patch would leave the guide empty, and an on-demand guide cannot "
                "be: nothing was written. To remove the guide: op=delete.")
        poids = len(nouveau.encode())
        if poids > _MAX_BODY_BYTES:
            raise AuthzDenied(
                400, "body_too_large",
                f"After this patch the guide would weigh {poids} UTF-8 bytes for "
                f"{_MAX_BODY_BYTES} at most ({poids - _MAX_BODY_BYTES} too many) — the "
                "cap is in BYTES, an accented character weighs two. Nothing was "
                "written. Make room first: mode=delete on a section that is no longer "
                "needed, or move older content to another guide.")
        return nouveau

    try:
        accuse = guide_store.patch_guide(scope, owner_id, slug, appliquer,
                                         expected_rev=inp.expected_rev)
    except db.GuideConflict as e:
        raise AuthzDenied(
            409, "conflict",
            f"The guide changed since you read it (current rev {e.current_rev}). "
            "Nothing was written. Read it again (op=read) and redo your patch on the "
            "current version.")
    # ⚠️ AVANT `GuideError`, dont c'est une sous-classe — même ordre que `_set`.
    except guide_store.GuideDeliveryConflict as e:
        raise AuthzDenied(409, "delivery_conflict", str(e))
    except guide_store.GuideError as e:
        raise AuthzDenied(400, "invalid_guide", str(e))
    if accuse is None:
        graine = (" A platform guide still served from its seed file has no stored "
                  "copy yet: op=write stores one."
                  if scope == "platform" and guide_store.file_guide(slug) else "")
        raise AuthzDenied(
            404, "not_found",
            f"No guide `{slug}` (scope {scope}) to patch: op=patch edits an EXISTING "
            f"guide and creates nothing. List them with op=list, create one with "
            f"op=write.{graine}")
    # Un ACCUSÉ, jamais le corps : ce patch existe pour ne pas faire voyager 60 000
    # caractères, dans un sens comme dans l'autre.
    out = dict(accuse)
    if emporte.get("removed") is not None:
        out["removed"] = emporte["removed"]
    if emporte.get("subsections"):
        enfants = emporte["subsections"]
        out["removed_subsections"] = enfants
        verbe = ("removed the WHOLE section, heading included" if mode == "delete"
                 else "replaced the WHOLE section")
        out["warning"] = (
            f"mode={mode} on « {inp.section} » {verbe}, so also {len(enfants)} "
            f"sub-section(s): {', '.join(enfants)}. A guide keeps no revision "
            "history: if you did not mean to lose them, write them back now.")
    return out


def _une_seule_consigne(ctx: ResolvedCtx, lu: dict) -> None:
    """Refuse de servir À L'AGENT un guide dont une PROCÉDURE porte le slug dans la
    même portée (oto#100).

    Jusqu'ici rien ne tranchait : `oto_guide` lisait le guide, `oto_procedure` et le
    runner la procédure, et l'agent recevait l'une ou l'autre selon l'outil appelé.
    Mesuré en production : la procédure tenue à jour (v3), le guide resté à une
    version antérieure — servi sans un mot. La procédure est la consigne qu'exécute
    le runner et la seule versionnée : le refus y renvoie, en la nommant, et dit
    comment retirer le doublon. Face MCP seulement : l'écran (`me.guides.get`) doit
    pouvoir montrer ce guide à qui va le supprimer."""
    owner = {"org": ctx.org_id, "user": ctx.sub}.get(lu.get("scope"))
    if owner is None:                      # plateforme, tenant : aucune procédure là
        return
    p = db.procedure_du_slug(lu["scope"], str(owner), lu["slug"])
    if p is None or p["archived"]:         # une procédure RETIRÉE n'est plus une consigne
        return
    s, sc = lu["slug"], lu["scope"]
    raise AuthzDenied(
        409, "family_conflict",
        f"`{s}` (scope {sc}) exists TWICE: this guide, and a PROCEDURE "
        f"(#{p['guide_id']}, v{p['version']}) with the same slug in the same scope. Two "
        "texts, only one of which is kept up to date — the guide is not served. The "
        "instruction is the procedure: "
        f"`oto_procedure(op='get', slug='{s}', scope='{sc}')`. To remove the "
        f"duplicate: `oto_guide(op='delete', slug='{s}', scope='{sc}')`.", p)


def _guide_op(ctx: ResolvedCtx, inp: GuideOpInput) -> dict:
    """Dispatch de la face MCP sur les MÊMES handlers que les faces REST."""
    if inp.op != "patch":
        en_trop = [n for n in _PATCH_ONLY if getattr(inp, n) is not None]
        if en_trop:
            raise AuthzDenied(
                400, "unsupported_patch_arg",
                f"{', '.join(f'`{n}`' for n in en_trop)}: op=patch only — op={inp.op} "
                "would ignore it. Remove it, or use op=patch.")
    if inp.op == "list":
        return _list(ctx, _NoInput())
    init = inp.delivery == "init"
    if not inp.slug and not init:
        # Un init a un slug canonique par scope — seul un on-demand doit être nommé.
        raise AuthzDenied(400, "missing_slug", "`slug` is required (see op=list).")
    if inp.op == "read":
        # Lecture on-demand : `scope` est un FILTRE optionnel (le store cherche dans
        # l'ordre de visibilité plateforme → org → user quand il est omis). Un init,
        # lui, se lit toujours à un scope donné (défaut : le sien).
        lu = _get(ctx, GuideRefInput(scope=inp.scope or ("user" if init else ""),
                                     slug=inp.slug or "", delivery=inp.delivery,
                                     owner_id=inp.owner_id))
        if not init:
            _une_seule_consigne(ctx, lu)
        return lu
    scope = inp.scope or "user"
    if inp.op == "delete":
        return _delete(ctx, GuideRefInput(scope=scope, slug=inp.slug or "",
                                          delivery=inp.delivery, owner_id=inp.owner_id))
    if inp.op == "patch":
        return _patch(ctx, inp)
    return _set(ctx, GuideSetInput(scope=scope, slug=inp.slug or "", delivery=inp.delivery,
                                   owner_id=inp.owner_id, body_md=inp.body_md or "",
                                   title=inp.title or "", description=inp.description or ""))


# Autz = SUB_ONLY (authentifié) + garde par SCOPE inline (platform_admin / org_admin / self) :
# le combinateur ne peut pas dériver l'org d'un champ `scope` libre.
# UNE capacité, deux faces (ADR 0042 §Convergence des surfaces) : `oto_guide` op-aware côté
# MCP + les routes `/api/me/guides…` côté dashboard, mêmes handlers. Jusqu'au 2026-07-28 la
# face MCP était un tool écrit à la main (`tools/guide.py`) qui redéclarait sa propre autz.
CAPABILITIES += [
    Capability(
        key="me.guide", handler=_guide_op, Input=GuideOpInput, authz=SUB_ONLY,
        description=(
            "Load or author oto INSTRUCTION PROSE — a how-to loaded when needed, or a readme "
            "injected into every session. Two axes: `scope` = platform | tenant | org | group | "
            "user (who writes it, whom it applies to) and `delivery` = 'on-demand' (default: a "
            "guide you load for a task) | 'init' (a readme concatenated into what you receive "
            "at handshake). op=list → the on-demand catalog you can see (platform ∪ your org ∪ "
            "your own) ; op=read (slug, optional scope) → its markdown body and `rev` ; op=write / delete "
            "(slug, scope, body_md, title?, description?) → author for the PLATFORM (platform "
            "admin), a TENANT (its tenant admin or a platform admin; `owner_id` = the tenant "
            "slug, which also reads it back), your ORG (org admin), your TEAM (team lead) or "
            "YOURSELF (scope=user, the default). With delivery='init' the slug is canonical per scope — omit it (the "
            "user's own readme is `scope='user', delivery='init'`; an empty body clears that "
            "layer). op=patch (slug, scope, `mode`, ONE target: `section` or `region`) → edit "
            "ONE region of an existing on-demand guide WITHOUT re-sending its body (how you "
            "append to a long guide or a journal): same grammar as `oto_doc` op=patch, same "
            "writers as op=write, and the reply is a receipt, not the body. `expected_rev` = "
            "the `rev` of op=read: refused if the guide changed since. "
            "Read the relevant guide BEFORE a non-trivial task (e.g. bulk-load). "
            "This is PROSE: a repeatable process with slots is a procedure (`oto_procedure`), "
            "and what oto knows about the user is their profile card (`oto_profile`)."),
        mcp="oto_guide",
        errors=(DeclaredError(409, "family_conflict",
                              "une PROCÉDURE porte ce slug dans la même portée "
                              "(oto#100) — à l'écriture, un guide neuf ne s'y crée pas ; "
                              "à la lecture par l'agent, le guide en double n'est pas "
                              "servi. Le refus nomme la procédure ; rien n'est écrit"),),
    ),
    Capability(
        key="me.guides.list", handler=_list, Input=_NoInput, authz=SUB_ONLY, mcp=None,
        Output=GuideList,
        description="List the on-demand guides you can see (platform ∪ your org ∪ your own).",
        rest=RestBinding("GET", "/api/me/guides"),
    ),
    # Multi-binding : `/api/me/…` = le scope COURANT (org/équipe actives) ; `/api/orgs/{id}/…`
    # et `/api/groups/{id}/…` = une cible EXPLICITE — un écran qui gère une équipe donnée ne
    # doit pas dépendre de l'équipe « active » de la session (même métier, même autz dessous).
    Capability(
        key="me.guides.get", handler=_get, Input=GuideRefInput, authz=SUB_ONLY, mcp=None,
        Output=GuideView,
        description="Read one guide body by scope+slug (`?delivery=init` for a readme).",
        rest=(RestBinding("GET", "/api/me/guides/{scope}/{slug}"),
              RestBinding("GET", "/api/orgs/{id}/guides/{scope}/{slug}", {"id": "owner_id"}),
              RestBinding("GET", "/api/groups/{id}/guides/{scope}/{slug}", {"id": "owner_id"})),
    ),
    Capability(
        key="me.guides.set", handler=_set, Input=GuideSetInput, authz=SUB_ONLY, mcp=None,
        Output=GuideView,
        errors=(DeclaredError(400, "body_too_large",
                              "`body_md` dépasse 65 536 octets UTF-8"),
                DeclaredError(409, "delivery_conflict",
                              "ce `(scope, slug)` porte déjà une couche de l'AUTRE "
                              "livraison — un guide à charger ne remplace pas un "
                              "readme injecté, ni l'inverse ; rien n'est écrit"),
                DeclaredError(409, "family_conflict",
                              "une PROCÉDURE porte déjà ce slug dans la même portée "
                              "(oto#100) — un guide neuf ne s'y crée pas ; le refus "
                              "nomme la procédure, rien n'est écrit"),),
        description=(
            "Create/update a guide (scope=platform|org|group|user|tenant). "
            "`delivery='init'` writes that scope's injected readme (empty body clears it). "
            "WHO MAY WRITE, per scope — checked on the REAL target, not the active one: "
            "`user` = yourself only; `org` = an admin of that org; `group` = that team's "
            "lead (org admins escalate); `platform` = a platform admin; `tenant` = a platform "
            "admin, or for an on-demand guide an admin of THAT tenant (its injected readme "
            "stays platform-only). "
            "⚠️ The flag to read before showing an editor is `can_edit` on the org or "
            "team view — NEVER `can_write_instructions`, which governs PROCEDURES and "
            "is true for any team MEMBER: reading it here shows an editor that the "
            "server refuses."),
        rest=(RestBinding("PUT", "/api/me/guides/{scope}/{slug}"),
              RestBinding("PUT", "/api/orgs/{id}/guides/{scope}/{slug}", {"id": "owner_id"}),
              RestBinding("PUT", "/api/groups/{id}/guides/{scope}/{slug}", {"id": "owner_id"})),
    ),
    Capability(
        key="me.guides.delete", handler=_delete, Input=GuideRefInput, authz=SUB_ONLY, mcp=None,
        Output=GuideDeleted,
        errors=(DeclaredError(409, "delivery_conflict",
                              "ce `(scope, slug)` porte une couche de l'AUTRE "
                              "livraison — vider un readme injecté ne retire pas un "
                              "guide à charger ; rien n'est écrit"),),
        description=("Delete a guide (scope=platform|tenant|org|group|user) — same writers as "
                     "`me.guides.set`: a tenant's on-demand guide by its tenant admin or a "
                     "platform admin."),
        rest=RestBinding("DELETE", "/api/me/guides/{scope}/{slug}"),
    ),
]
