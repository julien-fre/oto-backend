"""Capacités « bibliothèque publique de doctrines » (marketplace de skills).

Un catalogue cherchable et partageable de doctrines PUBLIÉES, chaque entrée
portant un AUTEUR : **Otomata** (la plateforme) ou un **créateur privé** (une
org). Co-déclarées MCP + REST (ADR 0009) :

- lecture (`library.list`/`library.get`) = tout user authentifié (`SUB_ONLY`) ;
  la surface ANONYME pour la vitrine est servie à part par des routes écrites à
  la main dans `api.routes` (l'adaptateur REst des capacités authentifie toujours).
- publication / fork = org_admin de l'**org active** (injectée par `ORG_MEMBER`,
  jamais d'un param client → verrou IDOR ; l'org est REQUISE même pour un
  platform-operator, cf. `_require_org_admin`) ; un publieur **platform-operator**
  publie au nom d'**Otomata**.
- publier est **borné à l'auteur** : le slug public est unique (c'est l'adresse de
  l'entrée, toute l'API adresse par slug) et POSSÉDÉ — republier le sien
  incrémente sa version, viser celui d'une autre org est refusé (409 `slug_taken`,
  message non-disclosant : un slug `unlisted` est un lien secret).
- dépublication = l'auteur (org_admin de l'org auteur) ou un admin plateforme.

Handlers SYNC (les adaptateurs n'awaitent pas). Le fork réutilise
`org_store.set_instruction` → la doctrine forkée devient un skill d'org versionné.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .. import access, org_store, procedure_diagram, procedure_digest, roles
from ._authz import ORG_MEMBER, SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class LibraryListInput(BaseModel):
    query: Optional[str] = None
    category: Optional[str] = None
    author_kind: Optional[str] = None    # 'otomata' | 'org'
    limit: int = 100

    @field_validator("limit")
    @classmethod
    def _cap(cls, v):
        # Patron `SearchInput._cap` : la valeur part telle quelle en `LIMIT %s`. Sans
        # borne, un `limit` énorme rend toute la bibliothèque d'un coup (avec un ILIKE
        # sur `body_md` quand il y a une requête) et un NÉGATIF fait échouer Postgres
        # (« LIMIT must not be negative ») en 500 opaque.
        return max(1, min(int(v), 200))


class LibraryGetInput(BaseModel):
    slug: str


class PublishInput(BaseModel):
    slug: str                            # le slug du skill d'org à publier
    public_slug: Optional[str] = None    # slug public (défaut = slug source)
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list] = None
    visibility: str = "public"           # 'public' | 'unlisted'


class ForkInput(BaseModel):
    slug: str                            # slug public de l'entrée à forker
    new_slug: Optional[str] = None


class UnpublishInput(BaseModel):
    id: int


# ── formes de réponse (ADR 0009 : `Output` DÉCRIT la 200, ne la valide pas) ───
# Ce qui n'était écrit nulle part et qu'un intégrateur découvrait autrement en
# production : le catalogue ne porte PAS le corps, `snippet` dépend de la requête,
# un slug rendu peut différer de celui demandé, et un `version` ne dit pas la même
# chose selon qu'on publie ou qu'on forke.

class LibraryEntrySummary(BaseModel):
    """Une entrée du CATALOGUE : métadonnées seules. Le corps markdown n'y est
    jamais — c'est library.get qui le sert, par slug."""
    id: int = Field(description="Identifiant de l'entrée publiée — la clé qu'attend "
                                "library.unpublish (≠ le slug).")
    slug: str = Field(description="Slug PUBLIC de l'entrée (normalisé à la "
                                  "publication) : la clé de library.get et library.fork. "
                                  "Il peut différer du slug du skill d'org d'origine.")
    title: str = Field(description="Titre affiché ; chaîne vide si l'auteur n'en a "
                                   "jamais posé (jamais null).")
    description: str = Field(description="Résumé d'une ligne ; chaîne vide par défaut.")
    author_kind: str = Field(description="'otomata' (publiée par la plateforme) | 'org' "
                                         "(créateur privé). C'est le seul axe de "
                                         "confiance affichable.")
    author_org_id: Optional[int] = Field(
        default=None,
        description="Org autrice quand author_kind='org' ; `null` pour une entrée "
                    "Otomata. Une entrée 'org' sans author_org_id n'est plus "
                    "dépubliable par son auteur (seul un admin plateforme le peut).")
    author_display: str = Field(description="Nom d'auteur affichable ('Otomata' ou le "
                                            "nom de l'org). Figé à la publication : il "
                                            "ne suit pas un renommage d'org ultérieur.")
    category: str = Field(description="Catégorie de rangement, libre ; chaîne vide si "
                                      "non classée (et non filtrable par category alors).")
    tags: list = Field(default_factory=list, description="Étiquettes libres, [] par défaut.")
    visibility: str = Field(
        description="'public' (listée) | 'unlisted' (partage par LIEN : servie par slug "
                    "exact à tout compte authentifié, mais jamais listée ici). Sur cette "
                    "liste la valeur vaut donc toujours 'public'.")
    version: int = Field(description="Numéro de publication : incrémenté à chaque "
                                     "re-publication du même slug. 1 = jamais republiée.")
    created_at: str = Field(description="Première publication ('YYYY-MM-DD HH:MM:SS' UTC).")
    updated_at: str = Field(description="Dernière re-publication — c'est la clé de tri "
                                        "du catalogue (plus récentes d'abord).")
    snippet: Optional[str] = Field(
        default=None,
        description="Extrait du corps autour de la 1ʳᵉ occurrence de `query`. Présent "
                    "UNIQUEMENT quand une `query` a été passée : son absence signifie "
                    "« pas de recherche », jamais « corps vide ».")


class LibraryList(BaseModel):
    """Catalogue filtré, plus récemment publiées d'abord, borné par `limit`.

    ⚠️ Ne contient QUE les entrées `public` : une entrée `unlisted` existe et reste
    lisible par son slug exact (library.get), mais n'apparaît jamais ici. Une liste
    vide ne prouve donc pas qu'une doctrine n'existe pas."""
    doctrines: list[LibraryEntrySummary]


class LibraryEntry(BaseModel):
    """Une entrée COMPLÈTE, corps inclus. ⚠️ Rendue à plat, sans enveloppe : les
    champs sont à la racine de la réponse (pas de clé `doctrine`)."""
    id: int = Field(description="Identifiant de l'entrée publiée.")
    slug: str = Field(description="Slug public.")
    title: str = Field(description="Titre affiché ('' si absent).")
    description: str = Field(description="Résumé ('' si absent).")
    body_md: str = Field(description="Le corps markdown intégral de la doctrine — la "
                                     "matière à lire avant de forker. Jamais vide : une "
                                     "publication au corps vide est refusée.")
    slots: list = Field(default_factory=list,
                        description="Entités requises déclarées par la procédure (ADR "
                                    "0035) : ce qu'il faudra brancher APRÈS le fork pour "
                                    "qu'elle tourne. [] = rien à brancher.")
    author_kind: str = Field(description="'otomata' | 'org'.")
    author_org_id: Optional[int] = Field(default=None, description="Org autrice, `null` "
                                                                   "pour Otomata.")
    author_display: str = Field(description="Nom d'auteur figé à la publication.")
    category: str = Field(description="Catégorie ('' si non classée).")
    tags: list = Field(default_factory=list, description="Étiquettes libres.")
    visibility: str = Field(
        description="'public' | 'unlisted'. Ici la valeur PEUT être 'unlisted' : cette "
                    "lecture sert le partage par lien. « Non listé » ≠ secret d'org — "
                    "tout compte authentifié qui connaît le slug lit l'entrée.")
    source_org_id: Optional[int] = Field(
        default=None,
        description="Org d'où le skill a été publié (traçabilité interne). `null` pour "
                    "une publication au nom d'Otomata.")
    source_slug: Optional[str] = Field(default=None, description="Slug du skill d'org "
                                                                 "d'origine, quand il "
                                                                 "diffère du slug public.")
    forked_from: Optional[int] = Field(
        default=None,
        description="Entrée de bibliothèque dont celle-ci descend, si elle a elle-même "
                    "été forkée avant d'être republiée. `null` = publication d'origine.")
    version: int = Field(description="Numéro de publication (incrémenté à chaque "
                                     "re-publication du même slug).")
    published_by: Optional[str] = Field(default=None, description="Compte (sub) auteur de "
                                                                  "la dernière publication.")
    created_at: str = Field(description="Première publication ('YYYY-MM-DD HH:MM:SS' UTC).")
    updated_at: str = Field(description="Dernière re-publication.")


class PublishResult(BaseModel):
    """Accusé de publication. Une entrée déjà publiée sous le même slug public par
    TON org est REMPLACÉE (corps, titre) et sa version incrémentée — publier n'est
    donc pas toujours une création. Le slug d'une entrée appartenant à une AUTRE
    org (ou à la plateforme) est refusé, jamais repris : 409 `slug_taken`."""
    published: bool = Field(description="Toujours `true` : un échec ne rend pas "
                                        "`published:false`, il lève (404 doctrine "
                                        "absente, 403 sans org_admin, 409 nom déjà "
                                        "pris par une autre org). Ne pas le tester "
                                        "comme un booléen d'issue.")
    id: int = Field(description="Identifiant de l'entrée publiée — à conserver, c'est "
                                "ce qu'attend library.unpublish.")
    slug: str = Field(description="Slug public RÉELLEMENT retenu, après normalisation : "
                                  "il peut différer du `public_slug` demandé.")
    version: int = Field(description="Version de publication après l'opération. `1` = "
                                     "création ; ≥2 = le slug existait et vient d'être "
                                     "écrasé.")
    visibility: str = Field(description="'public' | 'unlisted' tel qu'enregistré.")
    diagram_warning: Optional[str] = Field(
        default=None,
        description="Le SCHÉMA manquant du corps publié (cf. `procedure_diagram`). "
                    "`null` = rien à signaler. Non bloquant : la publication a eu lieu.")
    digest_warning: Optional[str] = Field(
        default=None,
        description="Le DIGEST d'ouverture manquant (cf. `procedure_digest`).")


class ForkResult(BaseModel):
    """Accusé de fork : la doctrine publique a été COPIÉE dans l'org active comme
    nouveau skill versionné. Copie ponctuelle, sans lien vivant — republier la
    source ne mettra jamais à jour le fork."""
    forked: bool = Field(description="Toujours `true` (un échec lève : 404 entrée "
                                     "inconnue, 403 sans org_admin).")
    org_id: int = Field(description="Org d'accueil = l'org ACTIVE de l'appelant, jamais "
                                    "un paramètre — c'est le verrou anti-IDOR.")
    slug: str = Field(description="Slug du skill créé dans l'org. Peut différer du "
                                  "`new_slug` demandé : en cas de collision avec un "
                                  "skill existant, il est suffixé -2, -3… plutôt que "
                                  "d'écraser.")
    version: int = Field(description="Version du skill d'org créé — vaut toujours 1, le "
                                     "slug étant dédoublonné avant écriture (un fork "
                                     "n'écrase jamais une procédure existante).")
    forked_from: int = Field(description="Identifiant de l'entrée de bibliothèque "
                                         "source, conservé pour la traçabilité.")
    source_title: str = Field(description="Titre de l'entrée source au moment du fork "
                                          "('' si elle n'en portait pas).")
    diagram_warning: Optional[str] = Field(
        default=None,
        description="Le SCHÉMA manquant du corps forké (cf. `procedure_diagram`). "
                    "`null` = rien à signaler. Non bloquant : le fork a eu lieu.")
    digest_warning: Optional[str] = Field(
        default=None,
        description="Le DIGEST d'ouverture manquant (cf. `procedure_digest`).")


class UnpublishResult(BaseModel):
    """Retrait du catalogue. Le retrait supprime l'entrée PUBLIÉE, jamais le skill
    d'org d'origine ni les forks déjà faits par d'autres."""
    unpublished: bool = Field(
        description="`false` n'est ni un refus (403) ni un identifiant inconnu (404) — "
                    "les deux lèvent avant. C'est le cas de course : l'entrée a disparu "
                    "entre la vérification d'auteur et la suppression, donc le résultat "
                    "voulu est déjà atteint.")


def _require_org_admin(ctx: ResolvedCtx, what: str) -> int:
    """Gate org_admin de l'org active (escalade platform_admin incluse) — et rend
    l'org active, dont ces deux opérations ont besoin.

    L'org est exigée AVANT l'escalade plateforme : publier lit un skill DANS une
    org (`get_instruction(org_id, slug)`) et forker écrit DANS une org
    (`org_instructions.org_id` NOT NULL). `author_kind='otomata'` ne nomme qu'un
    AUTEUR — il ne fournit ni la source à publier ni la destination du fork. Sans
    ce garde, un opérateur plateforme sans org active passait le gate avec
    `org_id=None` : 500 (violation NOT NULL) au fork, 404 au message faux
    (« absente de ton org active ») à la publication."""
    if ctx.org_id is None:
        raise AuthzDenied(400, "no_active_org",
                          f"{what} demande une org active — choisis-en une avec oto_use_org.")
    if access.is_platform_operator(ctx.sub):
        return ctx.org_id
    if not roles.is_org_admin(ctx.sub, ctx.org_id):
        raise AuthzDenied(403, "forbidden", f"{what} requiert org_admin de ton org active.")
    return ctx.org_id


def _author_for(ctx: ResolvedCtx) -> tuple[str, Optional[int], str]:
    """Auteur affiché : platform-operator → Otomata ; sinon l'org active.

    Lève plutôt que de publier un auteur vide : `author_display` est le seul axe
    de confiance affiché au catalogue, une entrée anonyme n'y a rien à faire."""
    if access.is_platform_operator(ctx.sub):
        return "otomata", None, "Otomata"
    o = org_store.get_org(ctx.org_id)
    if not o:
        raise AuthzDenied(404, "unknown_org", f"Org #{ctx.org_id} introuvable.")
    name = (o.get("name") or "").strip()
    if not name:
        raise AuthzDenied(400, "unnamed_org",
                          "Ton org n'a pas de nom : nomme-la avant de publier "
                          "(elle signe l'entrée au catalogue).")
    return "org", ctx.org_id, name


def _list(ctx: ResolvedCtx, inp: LibraryListInput) -> dict:
    return {"doctrines": org_store.list_library(
        query=inp.query, category=inp.category, author_kind=inp.author_kind,
        include_unlisted=False, limit=inp.limit)}


def _get(ctx: ResolvedCtx, inp: LibraryGetInput) -> dict:
    # Sémantique `unlisted` = **lien non listé** (style YouTube), choix assumé :
    # une entrée `unlisted` est servie par SLUG EXACT à tout user authentifié
    # (`include_unlisted=True`), mais n'apparaît JAMAIS dans le catalogue
    # (`_list` force `include_unlisted=False`) ni sur la surface anonyme. C'est un
    # partage par lien — pas un secret d'org. Une doctrine vraiment sensible ne se
    # publie pas (reste un skill d'org privé). Cf. CLAUDE.md §Bibliothèque.
    entry = org_store.get_library_entry(slug=inp.slug, include_unlisted=True)
    if not entry:
        raise AuthzDenied(404, "unknown_entry", f"Doctrine publique `{inp.slug}` inconnue.")
    return entry


def _publish(ctx: ResolvedCtx, inp: PublishInput) -> dict:
    org_id = _require_org_admin(ctx, "Publier")
    src = org_store.get_instruction(org_id, inp.slug)
    if not src:
        raise AuthzDenied(404, "unknown_doctrine",
                          f"Doctrine `{inp.slug}` absente de ton org active.")
    kind, author_org_id, display = _author_for(ctx)
    try:
        row = org_store.publish_guide(
            slug=inp.public_slug or inp.slug,
            title=inp.title if inp.title is not None else (src.get("title") or ""),
            description=inp.description if inp.description is not None else (src.get("description") or ""),
            body_md=src["body_md"], author_kind=kind, author_org_id=author_org_id,
            author_display=display, category=inp.category or "", tags=inp.tags or [],
            visibility=inp.visibility, source_org_id=org_id, source_slug=inp.slug,
            published_by=ctx.sub, slots=src.get("slots") or [],
        )
    except org_store.LibrarySlugTaken:
        # NON-DISCLOSANT, à dessein : ne jamais dire À QUI est l'entrée, ni même
        # qu'elle existe sous cette forme — le slug d'une entrée `unlisted` est un
        # lien secret (cf. `_get`), un refus précis le confirmerait à qui devine.
        raise AuthzDenied(409, "slug_taken",
                          f"Le nom `{org_store.normalize_slug(inp.public_slug or inp.slug)}` "
                          "n'est pas disponible — publie sous un autre `public_slug`.")
    # Publier une procédure sans schéma propage le manque à tous ses forks : le
    # signal part ici aussi, au même régime non bloquant (tulina-app-front#108).
    return {"published": True, "id": row["id"], "slug": row["slug"],
            "version": row["version"], "visibility": row["visibility"],
            **procedure_diagram.diagram_check(src.get("body_md") or ""),
            **procedure_digest.digest_check(src.get("body_md") or "")}


def _fork(ctx: ResolvedCtx, inp: ForkInput) -> dict:
    org_id = _require_org_admin(ctx, "Forker")
    entry = org_store.get_library_entry(slug=inp.slug, include_unlisted=True)
    if not entry:
        raise AuthzDenied(404, "unknown_entry", f"Doctrine publique `{inp.slug}` inconnue.")
    res = org_store.fork_into_org(entry_id=entry["id"], org_id=org_id,
                                  new_slug=inp.new_slug, set_by=ctx.sub)
    # Le fork est une écriture de procédure comme une autre : l'org repart avec un
    # corps qu'elle n'a pas écrit, et c'est elle qui devra lui dessiner son schéma.
    return {"forked": True, **res,
            **procedure_diagram.diagram_check(entry.get("body_md") or ""),
            **procedure_digest.digest_check(entry.get("body_md") or "")}


def _unpublish(ctx: ResolvedCtx, inp: UnpublishInput) -> dict:
    entry = org_store.get_library_entry(entry_id=inp.id, include_unlisted=True)
    if not entry:
        raise AuthzDenied(404, "unknown_entry", "Entrée inconnue.")
    is_author = (entry["author_kind"] == "org" and entry.get("author_org_id") is not None
                 and roles.is_org_admin(ctx.sub, entry["author_org_id"]))
    if not (is_author or access.is_platform_operator(ctx.sub)):
        raise AuthzDenied(403, "forbidden", "Réservé à l'auteur ou à un admin plateforme.")
    return {"unpublished": org_store.unpublish_guide(inp.id)}


CAPABILITIES += [
    Capability(
        key="library.list", handler=_list, Input=LibraryListInput, authz=SUB_ONLY,
        description="Browse/search the PUBLIC doctrine library (a marketplace of skills/"
                    "templates). Each entry has an author (Otomata or a private creator). "
                    "Filter by query / category / author_kind (otomata|org). Returns metadata "
                    "+ snippet, not the full body — use oto_procedure op=library_get for that.",
        Output=LibraryList,
        rest=RestBinding("GET", "/api/me/doctrines/library"),
    ),
    Capability(
        key="library.get", handler=_get, Input=LibraryGetInput, authz=SUB_ONLY,
        description="Read one public-library doctrine in full (markdown body) by its public "
                    "slug — preview before forking it into your org with oto_procedure op=fork. "
                    "Also serves `unlisted` entries by exact slug (unlisted = shared by link, "
                    "never in the catalog), not a private-org secret.",
        Output=LibraryEntry,
        rest=RestBinding("GET", "/api/me/doctrines/library/{slug}"),
    ),
    Capability(
        key="library.publish", handler=_publish, Input=PublishInput, authz=ORG_MEMBER,
        description="Publish one of your org's named doctrines (skills) to the PUBLIC library "
                    "so others can find and fork it. Requires org_admin of your active org. "
                    "slug = the org skill to publish ; visibility = public | unlisted. "
                    "Public names are unique and OWNED: re-publishing your own entry bumps its "
                    "version, a name held by someone else is refused (409) — pick another "
                    "public_slug.",
        Output=PublishResult,
        rest=RestBinding("POST", "/api/me/doctrines/publish"),
    ),
    Capability(
        key="library.fork", handler=_fork, Input=ForkInput, authz=ORG_MEMBER,
        description="Fork (copy) a public-library doctrine into your active org as a new "
                    "versioned skill. Requires org_admin of your active org. slug = the public "
                    "entry ; new_slug optional (defaults to source slug, de-duplicated).",
        Output=ForkResult,
        rest=RestBinding("POST", "/api/me/doctrines/fork"),
    ),
    Capability(
        key="library.unpublish", handler=_unpublish, Input=UnpublishInput, authz=SUB_ONLY,
        description="Remove a doctrine you published from the public library (author org_admin "
                    "or platform admin). id = the library entry id.",
        Output=UnpublishResult,
        rest=RestBinding("DELETE", "/api/me/doctrines/library/{id}"),
    ),
]
