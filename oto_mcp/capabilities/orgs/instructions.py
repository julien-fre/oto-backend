"""Guide & instructions d'ORG (ADR 0009) — domaine migré en capacités.

Miroir d'`groups_guide` au grain org. Une opération co-déclarée une fois, ses
deux faces (MCP + REST) dérivées par les adaptateurs → fin de la duplication
d'autz `_resolve_org_write` (MCP) vs `_active_org_edit` (REST).

Deux paliers, par combinateur d'autz (pas de branche `org_id` à la main) :
- **membre** : scopé à l'**org active** (`org_id` injecté depuis l'état serveur).
  Lecture = `ORG_MEMBER`/`SUB_ONLY` ; écriture = `ORG_ADMIN`. Chemins `/api/me/*`,
  outil console `oto_procedure` (op=get/list/set/delete, ADR 0047 — ex 4 tools par-verbe).
- **admin** : org ciblée par `org_id` (cross-org = platform admin via l'escalade
  `roles`). Lecture = `ORG_MEMBER_OF` ; écriture = `ORG_ADMIN_OF`. Chemins
  `/api/admin/orgs/{id}/*`, outil `oto_admin_guide`.

Les handlers lisent `ctx.org_id` (injecté par l'autz) → **partagés** entre les
deux paliers. Le guide de **groupe** est lisible en mode membre
(`scope="group"`, complément du département actif) ; son écriture reste dans
`groups_guide`. Modèle versionné (slug réservé `claude_md` = guide de base).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel

from ... import (access, db, deprecations, group_store, guide_store, org_store,
                procedure_diagram, procedure_digest, roles,
                slots as slots_mod, tool_registry)
from .._authz import ORG_ADMIN, ORG_ADMIN_OF, ORG_ADMIN_OPT, ORG_MEMBER, ORG_MEMBER_OF, SUB_ONLY
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES

_OID = {"id": "org_id"}
_OID_SLUG = {"id": "org_id", "slug": "slug"}
_BASE = org_store.BASE_SLUG
# Outil MCP qui charge le guide (donc loggé dans `tool_calls`) → c'est lui que
# l'usage compte. UNE source pour le nom : sert de `mcp=` de la capacité de lecture
# ET de filtre dans `_instruction_usage` → plus de chaîne magique à dériver (le bug
# d'origine : un filtre sur un nom d'outil mort renvoyait toujours 0).
_GUIDE_GET_TOOL = "oto_procedure"


# ── Sorties ─────────────────────────────────────────────────────────────────
# Vocabulaire, parce qu'il piège : une **procédure** (skill nommée, versionnée) est
# un objet de CE module ; le **readme d'org** est un GUIDE `delivery='init'` (ADR
# 0042) qui vit ailleurs. Le slug réservé `claude_md` désigne le second — d'où une
# asymétrie visible plus bas : la liste l'annonce, `get` ne le sert pas.

class ReferencedTool(BaseModel):
    """Un `<tool:slug>` du corps, résolu **à la lecture** contre le registre vivant
    (ADR 0014). `status='missing'` = la référence ne désigne plus rien (outil renommé
    ou non monté) : le corps n'a pas changé, sa résolution si. Une entrée `missing`
    ne porte que `name` + `status` ; une entrée `ok` porte la fiche de l'outil."""
    name: str
    status: str


class GuideView(BaseModel):
    """⚠️ **Trois formes derrière une capacité**, choisies par l'ENTRÉE :

    1. `guide_id` fourni → une procédure par son id STABLE (le seul chemin qui
       traverse les orgs : l'accès passe par le seam ownership, donc une procédure
       PARTAGÉE à toi par une autre org est lisible ici). **C'est la seule forme que
       la face REST peut produire** — `guide_id` y est un segment de chemin.
    2. `slug` omis (MCP) → le *bundle de session* : readme d'org + readme d'équipe +
       index des procédures.
    3. `slug` fourni (MCP) → une procédure nommée.

    ⚠️ La clé de scope CHANGE de nom selon la forme : `org_id` en scope org,
    `group_id` en scope équipe — pas un champ nul, un champ absent.

    ⚠️ Sans org active, la forme 2 répond **200 avec un bundle vide** (`org_id: null`,
    `guide: ""`, `guides: []`), pas une erreur : `guide: ""` confond « pas d'org » et
    « org sans readme ».

    ⚠️ **Chaque clé est servie DEUX FOIS** le temps du préavis (#519) : sous son nom
    d'aujourd'hui (`guide_id`, `guide`, `group_guide`, `guides`) et sous celui
    d'hier, qui s'en va le 27/09/2026 — cf. `docs/alias-deprecies.md`. Écris le
    nouveau ; l'ancien est là pour ne casser personne, pas pour être choisi."""
    org_id: Optional[int] = None
    group_id: Optional[int] = None
    guide_id: Optional[int] = None
    doctrine_id: Optional[int] = None   # ALIAS déprécié (retrait 27/09/2026)
    scope: Optional[str] = None
    slug: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    version: Optional[int] = None
    body_md: Optional[str] = None
    # Entités requises déclarées (ADR 0035), citées `<slot:name>` dans la prose.
    slots: Optional[list] = None
    referenced_tools: Optional[list[ReferencedTool]] = None
    # Forme 2 seulement : le readme d'org (prose plate), son org, son équipe active.
    org: Optional[str] = None
    guide: Optional[str] = None
    doctrine: Optional[str] = None        # ALIAS déprécié (retrait 27/09/2026)
    group: Optional[str] = None
    group_guide: Optional[str] = None
    group_doctrine: Optional[str] = None  # ALIAS déprécié (retrait 27/09/2026)
    # Index (slug/title/description/scope) — SANS les corps.
    guides: Optional[list[dict]] = None
    doctrines: Optional[list[dict]] = None  # ALIAS déprécié (retrait 27/09/2026)
    # Présent seulement s'il y a un projet actif : les entités du projet contre
    # lesquelles résoudre les `<slot:>`. Dérivé best-effort — son ABSENCE peut donc
    # aussi vouloir dire « la dérivation a échoué », pas seulement « hors projet ».
    project_instance: Optional[dict] = None
    # Forme 3 avec `with_history=true`, en scope org uniquement.
    versions: Optional[list[dict]] = None


class GuideMeta(BaseModel):
    """État du readme d'org. ⚠️ **`version` est un faux compteur** : il vaut 1 s'il
    existe un readme, 0 sinon, et n'atteint JAMAIS 2 — le readme est de la prose plate
    sans historique (ADR 0042). L'afficher comme un numéro de révision promet un
    versionnage qui n'existe pas."""
    exists: bool
    version: int
    updated_at: Optional[str] = None


class InstructionIndexEntry(BaseModel):
    """Métadonnées d'une procédure — **sans le corps** (`body_md` s'obtient par
    `GET /api/me/instructions/{slug}`)."""
    id: int
    slug: str
    title: Optional[str] = None
    description: Optional[str] = None
    version: int
    updated_at: Optional[str] = None


class InstructionsBundle(BaseModel):
    """Readme + index des procédures de l'ORG ACTIVE.

    ⚠️ **Sans org active, c'est un 200 avec tout à vide** (`org_id: null`,
    `can_edit: false`, `guide.exists: false`, `instructions: []`) — pas un 400.
    Indiscernable, à la lecture, d'une org réelle qui n'aurait rien écrit.

    ⚠️ **`instructions` exclut le readme** (slug réservé `claude_md`), qui n'est décrit
    que par `guide` (servi aussi sous son nom d'hier, `doctrine`, jusqu'au
    27/09/2026). Et l'asymétrie va plus loin : `guide.exists: true` annonce
    un readme que `GET /api/me/instructions/claude_md` **ne sait pas servir** (404) —
    le readme se lit sur la surface guide, pas ici."""
    org_id: Optional[int] = None
    org_name: Optional[str] = None
    can_edit: bool
    guide: GuideMeta
    doctrine: GuideMeta   # ALIAS déprécié, retrait le 27/09/2026 (#519)
    instructions: list[InstructionIndexEntry]


class InstructionView(BaseModel):
    """Une procédure, corps compris. `slug` est le slug NORMALISÉ (l'entrée est
    tolérante).

    ⚠️ **`updated_at: null` ne veut pas dire « jamais modifiée »** : c'est le signe
    qu'on lit une VERSION ARCHIVÉE (`?version=N`), servie depuis la table des
    révisions, qui ne porte pas cette colonne. Sur la version courante, elle est
    toujours renseignée."""
    slug: str
    title: Optional[str] = None
    description: Optional[str] = None
    version: int
    body_md: str
    slots: list
    set_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class InstructionVersion(BaseModel):
    version: int
    title: Optional[str] = None
    set_by: Optional[str] = None
    created_at: Optional[str] = None


class InstructionVersions(BaseModel):
    """Historique d'une procédure, plus récente d'abord.

    ⚠️ **Une liste vide recouvre trois situations distinctes** et rend 200 dans les
    trois : le slug n'existe pas (aucun 404 n'est levé ici), c'est le readme (qui n'a
    par nature pas d'historique), ou la procédure n'a encore aucune révision archivée.
    Il faut `GET /api/me/instructions/{slug}` pour trancher."""
    slug: str
    versions: list[InstructionVersion]


class InstructionUsage(BaseModel):
    """Usage d'une procédure, dérivé du journal d'appels (ADR 0014).

    ⚠️ **`count` et `series` ne mesurent pas la même fenêtre** : `series` couvre les 30
    derniers jours, `count` et `callers` n'ont **aucun filtre de date** — ils comptent
    tout ce qui reste en base. `count` ≠ `sum(series)`, et l'écart n'est pas un bug.

    ⚠️ **`callers` peut être plus court que ce que `count` totalise** : les appelants
    sans compte `users` connu sont exclus de la liste mais comptés dans le total.

    ⚠️ **Sur le slug du readme (`claude_md`), le filtre par procédure disparaît** : le
    compte devient celui de TOUS les chargements de procédure de l'org, quelle qu'elle
    soit. Ce n'est pas l'usage d'un document, c'est le volume d'une surface.

    Autres bornes : seuls les appels RÉUSSIS comptent, et le périmètre est celui des
    membres ACTUELS de l'org — le départ d'un membre efface rétroactivement ses
    chargements."""
    slug: str
    count: int
    # Emails des appelants, du plus actif au moins actif.
    callers: list[str]
    # Exactement 30 entiers, du plus ancien au plus récent (jour UTC). Les jours sans
    # appel valent 0 — ici, contrairement au monitoring, la série est densifiée.
    series: list[int]


class InstructionWritten(BaseModel):
    """Écriture d'une procédure. Chaque écriture **incrémente la version** et archive
    un instantané ; il n'y a pas de mise à jour en place.

    Les checks croisés sont **non bloquants par conception** (ADR 0014/0035) : ils
    signalent le drift, ils ne refusent pas l'écriture. Donc `ok: true` avec des
    `unresolved_tools` ou des `unresolved_slots` non vides = **la procédure est
    enregistrée ET cassée**. C'est le seul endroit où ça se voit. `diagram_warning`
    (tulina-app-front#108) est du même régime : la procédure est enregistrée, mais sa
    page se rendra en état vide faute de schéma.

    `slots` renvoyé est l'état EFFECTIF après écriture (envoyer `slots: null` conserve
    l'existant, donc l'écho peut différer de ce qui a été posté)."""
    ok: bool
    org_id: Optional[int] = None
    slug: str
    # Le NOUVEAU numéro de version (jamais celui qu'on a envoyé).
    version: int
    # Constante d'écho : vaut toujours `true` quand la réponse existe.
    set: bool
    # Présent seulement si l'écriture était une restauration (`from_version`).
    reverted_from: Optional[int] = None
    referenced_tools: Optional[list[ReferencedTool]] = None
    # Refs `<tool:>` qui ne désignent plus rien — l'écriture a quand même eu lieu.
    unresolved_tools: Optional[list[str]] = None
    slots: Optional[list] = None
    # `<slot:name>` cité dans la prose sans déclaration correspondante.
    unresolved_slots: Optional[list[str]] = None
    # Déclaré mais jamais cité — l'inverse, tout aussi silencieux.
    unreferenced_slots: Optional[list[str]] = None
    slot_warnings: Optional[list[str]] = None
    suggested_slots: Optional[list] = None
    # Le SCHÉMA de la procédure (tulina-app-front#108) : le front en fait la vue par
    # défaut de la page du process, donc une procédure sans dessin s'y affiche vide.
    # `None` = le check a tourné et n'a rien à dire ; la clé est toujours présente,
    # pour qu'un client sache distinguer « rien à signaler » d'un serveur trop vieux.
    diagram_warning: Optional[str] = None
    # Le DIGEST d'ouverture (`procedure_digest`) : ce que le dernier déroulé a appris.
    # Même régime — la procédure est enregistrée, il lui manque son bloc d'ouverture.
    digest_warning: Optional[str] = None


class InstructionDeleted(BaseModel):
    """Suppression d'une procédure **et de tout son historique** — irréversible, aucune
    corbeille. `deleted` ne vaut jamais `false` (un slug absent lève un 404) : c'est
    une constante d'écho. `slug` est le slug normalisé."""
    ok: bool
    org_id: Optional[int] = None
    slug: str
    deleted: bool


class InstructionArchived(BaseModel):
    """Archivage d'une procédure — l'alternative NON destructive à la suppression.
    La ligne et TOUT son historique de révisions restent en base ; ce qui change,
    c'est qu'elle disparaît de tous les listings, y compris ceux que l'IA lit
    (l'index de skills derrière `oto_procedure`, `op=list`, l'index de guides),
    donc l'agent cesse de la proposer et de la suivre. `archived` ne vaut jamais
    `false` (un slug absent lève un 404) : c'est une constante d'écho.

    Pas de désarchivage sur cette surface, même choix que pour les projets : la
    procédure est récupérable en base, pas d'un clic dans l'app."""
    ok: bool
    org_id: Optional[int] = None
    slug: str
    archived: bool


class InstructionReverted(BaseModel):
    """Restauration d'une version passée. ⚠️ **`version` est un numéro NEUF, pas celui
    qu'on restaure** : revenir à la v2 d'une procédure en v6 produit une v7 dont le
    contenu est celui de la v2. L'historique n'est jamais rembobiné — `reverted_from`
    est la seule trace de l'intention."""
    ok: bool
    slug: str
    version: int
    reverted_from: int
    # Un retour en arrière peut ramener un corps d'avant le schéma OU le digest requis.
    diagram_warning: Optional[str] = None
    digest_warning: Optional[str] = None


def _inconnu(message: str) -> AuthzDenied:
    """« Guide inconnu » — code d'aujourd'hui, code d'hier dans `details.legacy_code`.

    Un code d'erreur ne se DOUBLE pas : il n'y a qu'un champ `error`. Le nouveau
    prend donc la place, et l'ancien est conservé à côté — un client qui teste
    `error == "unknown_doctrine"` a jusqu'au 27/09/2026 pour lire `legacy_code`, ou
    mieux, le nouveau code (#519, retrait #526).
    """
    return AuthzDenied(404, "unknown_guide", message,
                       deprecations.details_avec_code_dhier("unknown_guide"))


# ── Inputs — palier membre (org active, pas d'org_id) ───────────────────────
class EmptyInput(BaseModel):
    pass


class GuideGetInput(BaseModel):
    slug: Optional[str] = None
    guide_id: Optional[int] = None      # lecture par ID STABLE (ADR 0032) — y compris un guide PARTAGÉ à ton org (grant read, livraison #52)
    doctrine_id: Optional[int] = None   # ALIAS déprécié du précédent (retrait 27/09/2026, #519)
    scope: str = "org"
    version: Optional[int] = None
    with_history: bool = False


class GuideListInput(BaseModel):
    query: Optional[str] = None
    scope: Optional[str] = None


class InstrGetInput(BaseModel):
    slug: str
    version: Optional[int] = None


class SlugInput(BaseModel):
    slug: str


class InstrSetInput(BaseModel):
    slug: Optional[str] = None
    body_md: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    from_version: Optional[int] = None
    # ADR 0035 : entités requises déclarées [{name, type: tableau|connecteur|base,
    # description?, connector?, schema?}] — référencées <slot:name> dans la prose.
    # `schema` (slots tableau, ADR 0046) = schéma CIBLE du tableau attendu (fields/
    # strict/lifecycle/key) : au binding du slot dans un projet, un namespace vierge
    # est PROVISIONNÉ avec, un schéma différent lève un warning. None = conserver.
    slots: Optional[list] = None
    # #69 : épingle l'écriture à une org EXPLICITE (robuste au reset de session).
    # None = org active (self-service). Gardé org_admin sur l'org nommée.
    org: Optional[int] = None


class GuideDeleteInput(BaseModel):
    slug: str
    # #69 : idem set — org explicite optionnelle (None = org active).
    org: Optional[int] = None


class RevertInput(BaseModel):
    slug: str
    version: int


# ── Inputs — palier admin (org ciblée par org_id) ───────────────────────────
class AdminGuideGetInput(BaseModel):
    org_id: int
    slug: Optional[str] = None
    scope: str = "org"
    version: Optional[int] = None
    with_history: bool = False


class AdminGuideListInput(BaseModel):
    org_id: int
    query: Optional[str] = None
    scope: Optional[str] = None


class AdminInstrSetInput(BaseModel):
    org_id: int
    slug: Optional[str] = None
    body_md: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    from_version: Optional[int] = None
    slots: Optional[list] = None


class AdminSlugInput(BaseModel):
    org_id: int
    slug: str


def _project_instance(member_mode: bool) -> Optional[dict]:
    """Bloc « instance » du projet actif (ADR 0032 §5, B3) : les entités du projet
    (tableaux + connecteurs surchargés) contre lesquelles l'agent résout les
    placeholders de la procédure — « la procédure partage à 100 % les ressources du
    projet, pas de ressources propres ». None hors projet, ou en mode admin cross-org
    (le bracelet est une notion de session membre). Best-effort."""
    if not member_mode:
        return None
    pid = access.current_project()
    if pid is None:
        return None
    try:
        p = db.get_project_by_id(pid)
        if p is None:
            return None
        return {
            "project_id": pid,
            "name": p.get("name"),
            "entities": [
                {"target_type": l["target_type"], "target_ref": l["target_ref"],
                 "label": l.get("label"), "role": l.get("role"), "config": l.get("config") or {}}
                for l in db.list_project_links(pid)
            ],
        }
    # noqa: SILENT — instance de projet non résolue ⇒ pas d'épinglage, pas d'erreur
    except Exception:
        return None


# ── Handlers (core ; org_id depuis ctx → partagés membre/admin) ─────────────
async def _get_guide(ctx: ResolvedCtx, inp) -> dict:
    """Bundle session-start (slug omis) OU un guide nommé. En mode membre
    (`inp.org_id` absent) complète avec le guide du département actif."""
    org_id = ctx.org_id
    member_mode = getattr(inp, "org_id", None) is None
    slug = inp.slug
    scope = inp.scope
    version = inp.version

    # Lecture par ID STABLE (ADR 0032 « stop using slug ») — le chemin des liens de
    # projet ET des guides PARTAGÉS cross-org (grant read via oto_resource, #52) :
    # l'accès passe par le seam ownership (membre de l'org propriétaire ∪ grants),
    # pas par l'org active.
    # ⚠️ Le kind d'ownership `doctrine` ci-dessous est une VALEUR EN BASE
    # (`resource_grants.resource_type`) : elle ne se double pas, elle se migre — lot B4.
    # Les deux noms du paramètre sont acceptés (#519, l'ancien part le 27/09/2026).
    guide_id = getattr(inp, "guide_id", None)
    if guide_id is None:
        guide_id = getattr(inp, "doctrine_id", None)
    if guide_id is not None:
        from ... import ownership   # import paresseux (miroir _authz, zéro cycle au boot)
        instr = org_store.get_instruction_by_id(int(guide_id))
        if not instr:
            raise _inconnu(f"Aucun guide #{guide_id}.")
        if not ownership.can_access(ctx.sub, "doctrine", str(guide_id), "read"):
            raise AuthzDenied(403, "forbidden", "Accès refusé à ce guide.")
        if version is not None:
            versioned = org_store.get_instruction(instr["org_id"], instr["slug"], version)
            if not versioned:
                raise _inconnu(f"Guide #{guide_id} : pas de version {version}.")
            instr = {**versioned, "id": instr["id"]}
        return deprecations.avec_les_deux_noms({
            "org_id": instr["org_id"], "guide_id": int(guide_id),
            "scope": "org", "slug": instr["slug"], "title": instr["title"],
            "description": instr["description"], "version": instr["version"],
            "body_md": instr["body_md"], "slots": instr.get("slots") or [],
            "referenced_tools": await tool_registry.manifest_for(instr["body_md"])})

    if slug is None:
        # Début de session : guide de base + index (vide gracieux si pas d'org).
        if org_id is None:
            return deprecations.avec_les_deux_noms({
                "org_id": None, "org": None, "guide": "", "group_id": None,
                "group": None, "group_guide": "", "guides": [], "referenced_tools": []})
        o = org_store.get_org(org_id)
        # Le readme d'org/équipe est un GUIDE `delivery='init'` (ADR 0042), plus une
        # instruction déguisée : on le lit sur sa surface, pas via le store de procédures.
        base_body = guide_store.init_guide_body("org", org_id) or ""
        index = [{"slug": i["slug"], "title": i["title"],
                  "description": i["description"], "scope": "org"}
                 for i in org_store.list_instructions(org_id)]
        group_id = access.current_group(ctx.sub) if member_mode else None
        group_name, group_guide = None, ""
        if group_id is not None:
            g = group_store.get_group(group_id)
            group_name = g["name"] if g else None
            group_guide = guide_store.init_guide_body("group", group_id) or ""
            index += [{"slug": i["slug"], "title": i["title"],
                       "description": i["description"], "scope": "group"}
                      for i in group_store.list_group_instructions(group_id)]
        guide_body = base_body
        pi = _project_instance(member_mode)
        return deprecations.avec_les_deux_noms({
            "org_id": org_id, "org": o["name"] if o else None, "guide": guide_body,
            "group_id": group_id, "group": group_name, "group_guide": group_guide,
            "guides": index,
            "referenced_tools": await tool_registry.manifest_for(guide_body, group_guide),
            **({"project_instance": pi} if pi else {}),
        })

    # Un guide nommé précis.
    if scope == "group" and member_mode:
        group_id = access.current_group(ctx.sub)
        if group_id is None:
            raise AuthzDenied(400, "no_active_group", "Pas de département actif — vois `oto_use_group`.")
        instr = group_store.get_group_instruction(group_id, slug, version)
        scope_ref: dict = {"group_id": group_id}
    else:
        if org_id is None:
            raise AuthzDenied(400, "no_active_org", "Pas d'org active — vois `oto_use_org`.")
        instr = org_store.get_instruction(org_id, slug, version)
        scope_ref = {"org_id": org_id}
    if not instr:
        raise _inconnu(f"Aucun guide `{org_store.normalize_slug(slug)}` (scope {scope})"
                       + (f" en version {version}" if version is not None else "")
                       + ". Vois `oto_procedure(op='list')`.")
    out = {**scope_ref, "scope": scope, "slug": instr["slug"], "title": instr["title"],
           "description": instr["description"], "version": instr["version"],
           "body_md": instr["body_md"], "slots": instr.get("slots") or [],
           "referenced_tools": await tool_registry.manifest_for(instr["body_md"])}
    pi = _project_instance(member_mode)
    if pi:
        out["project_instance"] = pi
    if inp.with_history and "org_id" in scope_ref:
        out["versions"] = org_store.list_instruction_versions(org_id, slug)
    return out


def _list_guides(ctx: ResolvedCtx, inp) -> dict:
    """Catalogue des guides nommés (slug/title/description/version, sans corps)."""
    org_id = ctx.org_id
    member_mode = getattr(inp, "org_id", None) is None
    query = inp.query
    scope = inp.scope
    if org_id is None:
        return deprecations.avec_les_deux_noms({"org_id": None, "guides": []})
    out: list = []
    if scope in (None, "org"):
        include_base = not member_mode  # la surface admin inclut le guide de base
        rows = (org_store.search_instructions(org_id, query, include_base=include_base) if query
                else org_store.list_instructions(org_id, include_base=include_base))
        out += [{**r, "scope": "org"} for r in rows]
    group_id = access.current_group(ctx.sub) if (member_mode and scope in (None, "group")) else None
    if group_id is not None:
        rows = (group_store.search_group_instructions(group_id, query) if query
                else group_store.list_group_instructions(group_id))
        out += [{**r, "scope": "group"} for r in rows]
    return deprecations.avec_les_deux_noms(
        {"org_id": org_id, "group_id": group_id, "guides": out})


async def _set_instruction(ctx: ResolvedCtx, inp) -> dict:
    """Crée/met à jour une instruction (incrémente la version + archive un snapshot).
    `from_version` = restaure une version passée comme nouvelle (revert MCP) — corps,
    métadonnées ET slots. `slots` (ADR 0035) : None = conserver l'existant."""
    org_id = ctx.org_id
    norm = org_store.normalize_slug(inp.slug) if inp.slug else _BASE
    if not norm:
        raise AuthzDenied(400, "invalid_slug", "slug invalide (attendu [a-z0-9_-]).")
    body_md, title, description = inp.body_md, inp.title, inp.description
    slots_in = getattr(inp, "slots", None)
    if inp.from_version is not None:
        old = org_store.get_instruction(org_id, norm, inp.from_version)
        if not old:
            raise AuthzDenied(404, "unknown_version", f"Pas de version {inp.from_version} pour `{norm}`.")
        body_md, title, description = old["body_md"], old["title"], old["description"]
        slots_in = old.get("slots") or []
    if slots_in is not None:
        try:
            slots_in = slots_mod.validate_slots(slots_in)
        except ValueError as e:
            raise AuthzDenied(400, "invalid_slots", str(e))
    body_md = (body_md or "").strip()
    if not body_md:
        raise AuthzDenied(400, "body_md_required", "body_md vide (ou fournis `from_version`).")
    # Injecté dans le guide de base servi à chaque session → caper la taille.
    if len(body_md.encode()) > 64 * 1024:
        raise AuthzDenied(400, "body_too_large", "body_md > 64 KB.")
    if norm == _BASE:
        raise AuthzDenied(400, "reserved_slug",
                          f"`{_BASE}` est le readme d'org (prose injectée), pas une "
                          "procédure — édite-le sur la surface guide "
                          "(scope='org', delivery='init').")
    version = org_store.set_instruction(org_id, norm, body_md, title=title,
                                        description=description, set_by=ctx.sub,
                                        slots=slots_in)
    # Slots EFFECTIFS après écriture (None = conservés → relire la row) pour le
    # check croisé <slot:name> ↔ déclaration (ADR 0035, non bloquant comme 0014).
    effective_slots = slots_in
    if effective_slots is None:
        cur = org_store.get_instruction(org_id, norm)
        effective_slots = (cur or {}).get("slots") or []
    return {"ok": True, "org_id": org_id, "slug": norm, "version": version, "set": True,
            **({"reverted_from": inp.from_version} if inp.from_version is not None else {}),
            **await tool_registry.write_check(body_md),
            **slots_mod.slots_check(body_md, effective_slots),
            **procedure_diagram.diagram_check(body_md),
            **procedure_digest.digest_check(body_md)}


def _delete_instruction(ctx: ResolvedCtx, inp) -> dict:
    norm = org_store.normalize_slug(inp.slug)
    if not norm:
        raise AuthzDenied(400, "invalid_slug", "slug requis.")
    deleted = org_store.delete_instruction(ctx.org_id, norm)
    if not deleted:
        raise AuthzDenied(404, "not_found", f"Instruction `{norm}` absente.")
    return {"ok": True, "org_id": ctx.org_id, "slug": norm, "deleted": True}


def _archive_instruction(ctx: ResolvedCtx, inp) -> dict:
    norm = org_store.normalize_slug(inp.slug)
    if not norm:
        raise AuthzDenied(400, "invalid_slug", "slug requis.")
    archived = org_store.archive_instruction(ctx.org_id, norm)
    if not archived:
        raise AuthzDenied(404, "not_found", f"Instruction `{norm}` absente.")
    return {"ok": True, "org_id": ctx.org_id, "slug": norm, "archived": True}


# ── Handlers REST-only (org active) ─────────────────────────────────────────
def _instructions_list(ctx: ResolvedCtx, inp: EmptyInput) -> dict:
    """Guide de base (meta) + index des instructions nommées de l'org active.
    Bundle vide en 200 si pas d'org active (consommé par l'overview)."""
    org_id = ctx.org_id
    if org_id is None:
        return deprecations.avec_les_deux_noms({
            "org_id": None, "org_name": None, "can_edit": False,
            "guide": {"exists": False, "version": 0, "updated_at": None},
            "instructions": []})
    o = org_store.get_org(org_id)
    base = guide_store.get_init_guide("org", org_id)      # readme = guide init (ADR 0042)
    has_readme = bool((base["body_md"] or "").strip())
    return deprecations.avec_les_deux_noms({
        "org_id": org_id,
        "org_name": o["name"] if o else None,
        "can_edit": roles.is_org_admin(ctx.sub, org_id),
        "guide": {
            "exists": has_readme,
            "version": 1 if has_readme else 0,        # prose plate : pas d'historique
            "updated_at": base["updated_at"] if has_readme else None,
        },
        "instructions": org_store.list_instructions(org_id),
    })


def _instruction_get(ctx: ResolvedCtx, inp: InstrGetInput) -> dict:
    instr = org_store.get_instruction(ctx.org_id, inp.slug, version=inp.version)
    if not instr:
        raise AuthzDenied(404, "not_found", f"Instruction `{org_store.normalize_slug(inp.slug)}` absente.")
    return {
        "slug": instr["slug"], "title": instr["title"], "description": instr["description"],
        "version": instr["version"], "body_md": instr["body_md"],
        "slots": instr.get("slots") or [], "set_by": instr.get("set_by"),
        "created_at": instr.get("created_at"), "updated_at": instr.get("updated_at"),
    }


def _instruction_versions(ctx: ResolvedCtx, inp: SlugInput) -> dict:
    slug = org_store.normalize_slug(inp.slug)
    return {"slug": slug, "versions": org_store.list_instruction_versions(ctx.org_id, slug)}


def _instruction_revert(ctx: ResolvedCtx, inp: RevertInput) -> dict:
    slug = org_store.normalize_slug(inp.slug)
    old = org_store.get_instruction(ctx.org_id, slug, version=inp.version)
    if not old:
        raise AuthzDenied(404, "not_found", f"Pas de version {inp.version} pour `{slug}`.")
    version = org_store.set_instruction(ctx.org_id, slug, old["body_md"], title=old["title"],
                                        description=old["description"], set_by=ctx.sub,
                                        slots=old.get("slots") or [])
    # Revenir en arrière peut RAMENER une procédure d'avant le schéma requis : le signal
    # part ici aussi (la face MCP passe par `_set_instruction`, qui l'a déjà).
    return {"ok": True, "slug": slug, "version": version, "reverted_from": inp.version,
            **procedure_diagram.diagram_check(old["body_md"]),
            **procedure_digest.digest_check(old["body_md"])}


def _instruction_usage(ctx: ResolvedCtx, inp: SlugInput) -> dict:
    """Usage d'un guide (ADR 0014) : nb de chargements par l'agent, appelants,
    série journalière 30j — dérivé de `tool_calls` (`_GUIDE_GET_TOOL`), scopé org."""
    slug = org_store.normalize_slug(inp.slug)
    subs = [m["sub"] for m in org_store.list_org_members(ctx.org_id)]
    slug_filter = None if slug == _BASE else slug
    u = db.instruction_usage(subs, _GUIDE_GET_TOOL, slug_filter, days=30)
    today = date.today()
    series = [u["daily"].get(str(today - timedelta(days=29 - i)), 0) for i in range(30)]
    return {"slug": slug, "count": u["count"], "callers": u["callers"], "series": series}


CAPABILITIES += [
    # ── Lectures membre (org active) ────────────────────────────────────────
    Capability(
        key="org.guide.get", handler=_get_guide, Input=GuideGetInput,
        authz=SUB_ONLY, Output=GuideView,
        description=("Operational guide of your active org. The base guide is now "
                     "INJECTED into your session instructions at connect — call this with "
                     "`slug` to load ONE named skill's full markdown (list skills with "
                     "oto_procedure op=list). No-arg returns base + index, e.g. to refresh "
                     "after switching org with oto_use_org. `scope=group` targets your "
                     "active department. `guide_id` loads a guide by its STABLE id "
                     "(project procedure links) — including one SHARED to you/your org "
                     "by another org (delivered project)."),
        # Face REST par ID stable : résolution des liens `procedure` d'un projet côté
        # dashboard — y compris un projet LIVRÉ (guide d'une autre org, grant read).
        rest=RestBinding("GET", "/api/me/guides/{guide_id}"),
    ),
    Capability(
        key="org.instruction.list", handler=_instructions_list, Input=EmptyInput,
        authz=SUB_ONLY, Output=InstructionsBundle,
        rest=RestBinding("GET", "/api/me/instructions"),
    ),
    Capability(
        key="org.instruction.get", handler=_instruction_get, Input=InstrGetInput,
        authz=ORG_MEMBER, Output=InstructionView,
        rest=RestBinding("GET", "/api/me/instructions/{slug}"),
    ),
    Capability(
        key="org.instruction.versions", handler=_instruction_versions, Input=SlugInput,
        authz=ORG_MEMBER, Output=InstructionVersions,
        rest=RestBinding("GET", "/api/me/instructions/{slug}/versions"),
    ),
    Capability(
        key="org.instruction.usage", handler=_instruction_usage, Input=SlugInput,
        authz=ORG_MEMBER, Output=InstructionUsage,
        rest=RestBinding("GET", "/api/me/instructions/{slug}/usage"),
    ),
    # ── Écritures membre (org active, org_admin) ────────────────────────────
    Capability(
        key="org.instruction.set", handler=_set_instruction, Input=InstrSetInput,
        authz=ORG_ADMIN_OPT("org"), Output=InstructionWritten,
        description=("Write your org's guide (org_admin). Each write bumps the version "
                     "and archives a snapshot. slug omitted = base guide; given = a named "
                     "skill. `from_version` restores a past version as a new one (revert). "
                     "`slots` = the procedure's REQUIRED ENTITIES [{name, type: tableau|"
                     "connecteur|base, description?, connector?}] — reference them BY NAME "
                     "in the prose as <slot:name> (never a hardcoded instance: the project "
                     "binds name→instance). EVERY procedure OPENS with "
                     "`> **Self-improvement digest** — …` (what the last run taught and "
                     "what was fixed, dated) and must carry a FLOWCHART (one "
                     "untagged fenced block drawn in box characters, right after the « At a "
                     "glance » table and before the first phase heading) — it is the DEFAULT "
                     "view of the process page; read the `procedure-flowchart` guide first. "
                     "Response returns cross-check warnings "
                     "(unresolved/unreferenced slots, suggestions, `digest_warning`, "
                     "`diagram_warning`). "
                     "`org` pins the write to "
                     "an EXPLICIT org id (default = your active org) — pass it to stay robust "
                     "if a reconnect dropped your session org; you must be org_admin of it."),
        rest=RestBinding("PUT", "/api/me/instructions/{slug}"),
    ),
    Capability(
        key="org.instruction.delete", handler=_delete_instruction, Input=GuideDeleteInput,
        authz=ORG_ADMIN_OPT("org"), Output=InstructionDeleted,
        description=("Delete a guide and its history (org_admin). Pass the EXACT slug. "
                     "`org` pins to an explicit org id (default = active org; must be "
                     "org_admin of it)."),
        rest=RestBinding("DELETE", "/api/me/instructions/{slug}"),
    ),
    Capability(
        key="org.instruction.archive", handler=_archive_instruction, Input=GuideDeleteInput,
        authz=ORG_ADMIN_OPT("org"), Output=InstructionArchived,
        description=("Retire a guide WITHOUT destroying it (org_admin) — prefer this "
                     "to `delete` whenever the point is 'stop using it', not 'erase it'. "
                     "The procedure and its whole version history stay in place; it simply "
                     "leaves every listing, including the skills index you read, so it "
                     "stops being offered and followed. Pass the EXACT slug. `org` pins to "
                     "an explicit org id (default = active org; must be org_admin of it)."),
        rest=RestBinding("POST", "/api/me/instructions/{slug}/archive"),
    ),
    Capability(
        key="org.instruction.revert", handler=_instruction_revert, Input=RevertInput,
        authz=ORG_ADMIN, Output=InstructionReverted,
        rest=RestBinding("POST", "/api/me/instructions/{slug}/revert"),
    ),
    # ── Palier admin (org ciblée par org_id ; cross-org = platform admin) ────
    Capability(
        key="org.guide.admin_get", handler=_get_guide, Input=AdminGuideGetInput,
        authz=ORG_MEMBER_OF("org_id"),
        description="[ADMIN] Read another org's guide by id (base+index, or one skill).",
        rest=RestBinding("GET", "/api/admin/orgs/{id}/instructions/{slug}", _OID_SLUG),
    ),
    Capability(
        key="org.guide.admin_list", handler=_list_guides, Input=AdminGuideListInput,
        authz=ORG_MEMBER_OF("org_id"),
        description="[ADMIN] List another org's named guides by id (incl. base guide).",
        rest=RestBinding("GET", "/api/admin/orgs/{id}/instructions", _OID),
    ),
    Capability(
        key="org.instruction.admin_set", handler=_set_instruction, Input=AdminInstrSetInput,
        authz=ORG_ADMIN_OF("org_id"),
        description="[ADMIN] Write another org's guide by id (cross-org = platform admin).",
        rest=RestBinding("PUT", "/api/admin/orgs/{id}/instructions/{slug}", _OID_SLUG),
    ),
    Capability(
        key="org.instruction.admin_delete", handler=_delete_instruction, Input=AdminSlugInput,
        authz=ORG_ADMIN_OF("org_id"),
        description="[ADMIN] Delete another org's guide by id and its history.",
        rest=RestBinding("DELETE", "/api/admin/orgs/{id}/instructions/{slug}", _OID_SLUG),
    ),
]
