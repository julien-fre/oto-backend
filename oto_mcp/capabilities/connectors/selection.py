"""Capacités « sélection de connecteurs » — marketplace (ADR 0019).

Per-membre, scopé à l'org active (`SUB_ONLY` injecte `ctx.org_id`). Trois faits
distincts (cf. `connector_selection`) : exposition (`connector_activation`, plafond),
proposition (`orgs.default_connectors`), sélection (`user_selected_connectors`).

- `connectors.me` (lecture) = catalogue exposé pour l'org active, fusionné avec
  l'état per-membre (`not_selected` | `active` | `paused`) + `recommended` (baseline org).
  Source unique consommée par le dashboard (library + « mes connecteurs »).
- `connectors.select` / `.pause` / `.unselect` (mutation) = installe / met en pause /
  retire un connecteur. Garde : refuser un connecteur non-exposé pour l'org active
  (le plafond d'exposition `connector_activation` n'est jamais relâché).

Handlers SYNC (les adaptateurs n'awaitent pas). Régime NOMINAL (ADR 0050) :
« non-sélectionné = masqué » — le seed d'un nouveau (sub, org) installe le socle
curé `default_active` ; sélectionner/mettre en pause a un effet de visibilité à la
session suivante (`session_visibility`).
"""
from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from typing import Literal, Optional

from pydantic import BaseModel

from ... import access, db, org_store, providers, session_org, tool_registry
from ...connectors import activation as connector_activation
from ...connectors import cardinality as connector_cardinality
from ...connectors import credential_presence
from ...connectors.credential_presence import CredentialPresence
from ...connectors import readiness as connector_readiness
from ...connectors import selection as connector_selection
from .._authz import ORG_ADMIN_OF, SUB_ONLY
from .._types import (AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding)
from ..registry import CAPABILITIES
from .kit import (BulkSelectInput, BulkSelectResult,  # noqa: F401 — ré-exportés
                  OrgRecommendedConnectors, RecommendInput, UnsetDefaultInput,
                  UnsetDefaultResult, _bulk_select, _recommend, _unset_default)
from .catalog_card import (AuthDescriptor, ConnectFlow, CredentialField,
                           DocSection, FreeTier)

logger = logging.getLogger(__name__)

# Mapping placeholder de route {id} → champ Input `org_id` (routes réelles en {id}).
_ID = {"id": "org_id"}


class MyConnectorsInput(BaseModel):
    """Filtre/projection de `connectors.me`. Défaut = **compact** (identité + état) :
    la vue pleine (doc_sections/auth/credential_fields/…) gonfle le payload à ~90 KB
    pour 55 connecteurs et dépasse le plafond de tokens MCP (oto-backend#109).

    `name` = lecture d'état d'UN connecteur. Il était déclaré sur `oto_connector`
    (pour select/pause/…) mais **ignoré en silence** sur op=list → l'agent qui le
    passait recevait le catalogue entier (~30k tokens en verbose) sans le moindre
    warning (feedback #326). Un `name` qui ne matche rien lève, jamais une liste
    vide : un filtre muet est ce qui a coûté le contexte."""
    verbose: bool = False                # True = payload complet (dashboard / setup credential)
    state: Optional[str] = None          # filtre : not_selected | active | paused
    # filtre : UN connecteur (lecture d'état ciblée). Nom exact, ou libellé / namespace /
    # mot du nom (#1112 : « linkedin » trouve `linkedin_unipile` ET `aiark`, dont les
    # outils sont `linkedin_aiark_*`) — plusieurs candidats sont TOUS rendus.
    name: Optional[str] = None


class ConnectorActionInput(BaseModel):
    name: str                            # nom de connecteur (registre providers/)


class ReachableInstance(BaseModel):
    """Une clé du connecteur qui existe à portée du membre SANS être la sienne
    (équipe dont il est membre, autre org) — de la découvrabilité, pas un droit :
    l'usage passe par un pin (`_group=`/`_org=`/`_instance=`) et reste re-gardé à
    l'appel."""
    kind: Literal["group", "org"]
    id: int
    name: str


class MyConnectorRow(BaseModel):
    """Un connecteur du catalogue vu par le membre : la carte + SON état à lui.

    **Deux projections sortent du même champ**, selon `verbose` (l'enveloppe l'écho) :

    - `verbose=false` (défaut) — identité + axes de tri + état : les huit clés de
      `_COMPACT_KEYS`, plus l'état per-membre. C'est ce que lit une LISTE.
    - `verbose=true` — la même ligne PLUS toute la carte publique du catalogue,
      section « carte » ci-dessous. C'est ce que lit une CARTE, et ce dont dépend le
      formulaire de credential.

    Un champ de la carte est donc `Optional` parce qu'il est absent en compact, pas
    parce qu'il serait parfois nul en verbeux : en `verbose=true` les treize sont
    toujours présents (`base = c`, la ligne entière — cf. `_me`). `null` y garde son
    sens propre, énoncé champ par champ.

    ⚠️ Le docstring disait jusqu'au 2026-09-01 que « la forme large est celle du
    dashboard, elle n'est pas figée ici » — et l'ouverture aux champs additionnels en
    découlait. Levé (#667) : dès qu'un intégrateur hors du dépôt en dépend, « pas
    figée » veut dire « cassable sans préavis, et sans qu'on sache qui casse ». Les
    treize clés de premier niveau que servait le mode verbeux sans les déclarer sont
    désormais nommées (cf. `catalog_card.py` pour les objets).

    `extra="allow"` est retiré (oto-backend#742) : le cliquet
    `tests/connectors/test_carte_connecteur_declaree.py`, posé par #738, a vécu —
    c'est désormais lui le garde-fou mécanique contre le prochain champ non déclaré.
    Un champ oublié disparaîtrait du payload plutôt que de rester toléré en silence."""

    name: str
    label: Optional[str] = None
    help: Optional[str] = None
    family: Optional[str] = None            # axe builder (dérivé)
    category: Optional[str] = None          # axe utilisateur (curé)
    availability: Optional[str] = None
    # None = absence DÉCLARÉE de logo de marque (générique/maison) → monogramme
    # côté UI, pas un chargement raté.
    logo_url: Optional[str] = None
    # NATURE du credential attendu — api_key|basic_auth|fields|oauth|cookie|none
    # — pas son état : `none` dit « ce connecteur marche sans qu'on apporte quoi
    # que ce soit », et ne dit rien de la clé posée (ça, c'est `providers[name]`
    # de `/api/me`). Le seul champ d'auth du mode compact ; cf. `_COMPACT_KEYS`.
    secret_kind: Optional[str] = None
    state: Literal["not_selected", "active", "paused"]
    # QUI a posé l'installation (ADR 0050 §E7) — présent seulement quand `state` ≠
    # `not_selected`. `kit` = installé par ton organisation (un retrait du kit le
    # retire) ; `membre` = par toi ; `admin` = poussé à toi par un admin ; `socle` =
    # d'office par la plateforme ; `inconnue` = posé avant que la plateforme ne le
    # trace — ce dernier n'est jamais retiré par un geste d'org.
    origin: Optional[Literal["socle", "kit", "admin", "membre", "inconnue"]] = None
    # Date (« AAAA-MM-JJ HH:MM:SS », UTC) à laquelle TU as retiré ce connecteur — présent seulement quand
    # `state` = `not_selected` et que le retrait vient de toi. Aucun geste d'org (kit,
    # poussée) ne le réinstalle tant qu'il est posé ; le réinstaller toi-même l'efface.
    removed_at: Optional[str] = None
    # Baseline proposée par l'ORG (ADR 0019), jamais l'état du membre : un
    # connecteur `recommended` peut très bien être `not_selected`.
    recommended: bool
    # Nombre de procédures d'org qui citent un namespace du connecteur — dérivé
    # des bodies de guide, best-effort (0 sur incident de lecture, pas d'erreur).
    guide_ref_count: int
    doctrine_ref_count: int                # ALIAS déprécié (retrait 29/10/2026, #519)
    paid_option: Optional[str] = None       # option payante requise (couche 3), None = aucune
    # `true` = l'option est levée OU aucune n'est requise. Ne dit RIEN du credential :
    # un connecteur `option_ok` reste inutilisable sans clé posée.
    option_ok: bool
    # Présent SEULEMENT si une clé est à portée sans être installée (la ligne se
    # distingue au lieu d'ajouter un champ vide sur 40 lignes). Son ABSENCE ne
    # prouve pas qu'il n'y en a aucune : le batch ne couvre pas « ma clé membre
    # dans une autre org » (limite assumée d'`access.reachable_instances_map`).
    reachable_instances: Optional[list[ReachableInstance]] = None
    # ── Aptitude EFFECTIVE (#476) — présents SEULEMENT sur une lecture ciblée
    # (`name=`), cf. `readiness` sur l'enveloppe. `state` répond « l'ai-je installé ? »,
    # `ready` répond « est-ce que ça marche ? » : ORTHOGONAUX, et le signal est né de
    # leur confusion. Détail des couches et du coût : `connectors/readiness.py`.
    ready: Optional[bool] = None
    # La PREMIÈRE couche qui manque : paid_option_off | no_credential | over_quota |
    # credential_rejected | pending_step. Absent quand `ready` est vrai.
    not_ready: Optional[str] = None
    next_step: Optional[str] = None         # le geste, rendu tel quel (jamais reformulé)
    # ── Credential DISPONIBLE (#1112) — sur TOUT le catalogue, pas seulement en
    # lecture ciblée. Présent quand une clé ou un compte existe pour toi à un palier
    # de la cascade, MÊME si `state` vaut `not_selected` : c'est la ligne qui manquait
    # quand deux agents ont lu `not_selected` comme « non connecté ». Troisième axe,
    # « vérifié vivant », jamais calculé ici : `credential.next_step` nomme l'outil.
    credential: Optional[CredentialPresence] = None

    # ── La CARTE (`verbose=true` seulement) ──────────────────────────────────────
    # Les treize clés que `providers.public_catalog()` pose sur la ligne entière.
    # Servies depuis toujours, déclarées depuis le 2026-09-01 (#667) : sans elles au
    # contrat, un front tiers ne pouvait pas rendre un formulaire de credential, alors
    # que la donnée arrivait. L'ordre suit celui du producteur, pour que les deux
    # listes se relisent côte à côte.
    #
    # Description curée 2-3 phrases (carte catalogue). `""` si non rédigée — le front
    # retombe alors sur `help`, ce que `null` ne dirait pas.
    description: Optional[str] = None
    doc_sections: Optional[list[DocSection]] = None
    href: Optional[str] = None              # site du connecteur ; `null` = aucun
    publisher: Optional[str] = None         # éditeur (curé)
    auth_modes: Optional[list[str]] = None  # ⊆ {byo_user, byo_org, platform}
    # Catégorie « session navigateur » (le credential est une session, pas une clé).
    personal_session: Optional[bool] = None
    # Le descripteur d'auth unifié (ADR 0024) — c'est LUI qui pilote le widget de
    # credential, `secret_kind` n'en étant que le scalaire de tri gardé en compact.
    auth: Optional[AuthDescriptor] = None
    namespaces: Optional[list[str]] = None  # préfixes des outils possédés
    # DÉRIVÉ de `auth.fields`, pas recopié — donc toujours identique. Les deux clés
    # sont servies, les deux sont déclarées : dépréciera qui voudra, mais pas en
    # silence (la forme d'une dépréciation de nom servi est #519, et elle se date).
    credential_fields: Optional[list[CredentialField]] = None
    free_tier: Optional[FreeTier] = None
    # Le connecteur propose-t-il de choisir une identité/cible par défaut (ADR 0024) ?
    identities: Optional[bool] = None
    # Le connecteur a-t-il enregistré une sonde de credential sans effet de bord ? Si
    # oui, la carte affiche « tester la connexion » à côté de l'état « clé posée ».
    verifiable: Optional[bool] = None
    connect: Optional[ConnectFlow] = None


class ToolboxScope(BaseModel):
    """L'écart entre l'org pour laquelle la SESSION a été montée et celle que l'appel
    ÉPINGLE (#577). Présent seulement quand les deux diffèrent — cf. `_toolbox_scope`."""
    mounted_for_org: Optional[int] = None
    listing_for_org: Optional[int] = None
    note: str


class NameMatch(BaseModel):
    """`name` n'était pas un nom exact : ce qu'il a trouvé, et par où (#1112). Absent
    sur un nom exact. Plusieurs candidats = tous rendus en lignes, aucun n'est choisi
    à la place de l'appelant."""
    query: str
    candidates: list[str]
    note: str


class MyConnectors(BaseModel):
    """Le catalogue exposé à l'org active, fusionné avec l'état per-membre.
    Source UNIQUE de la library ET de « mes connecteurs » du dashboard."""
    connectors: list[MyConnectorRow]
    # Écho de la projection demandée — dit au client si les lignes portent la carte
    # complète ou la vue compacte (les deux formes sortent du MÊME champ).
    verbose: bool
    # `computed` (lecture ciblée `name=`) | `not_computed` (catalogue : trop cher,
    # cf. `connectors/readiness.py`) | `unavailable` (la lecture des couches a échoué).
    # DIT toujours quelque chose : une absence muette de `ready` se lisait « rien à
    # signaler », et c'est ce raccourci qui a coûté cinq jours (#476).
    readiness: str = "not_computed"
    readiness_hint: Optional[str] = None    # le geste pour l'obtenir, quand on ne l'a pas
    # `computed` | `unavailable` (#1112) — le calcul de `credential` sur les lignes.
    # `unavailable` = le snapshot ne s'est pas lu : une ligne SANS `credential` ne dit
    # alors RIEN (elle ne veut pas dire « rien n'est connecté »).
    credentials: str = "computed"
    name_match: Optional[NameMatch] = None
    toolbox_scope: Optional[ToolboxScope] = None


class ConnectorSelectionState(BaseModel):
    """État de sélection d'UN connecteur pour le membre, après mutation. Forme
    commune à `select` / `pause` / `unselect` — même objet (l'appartenance du
    connecteur à la toolbox), les extras diffèrent selon le verbe."""
    connector: str
    state: Literal["active", "paused", "not_selected"]
    # `select` seulement : les outils du connecteur, lus du registre BOOT. Vide si
    # le registre n'est pas réchauffé (script hors serveur) — pas un connecteur
    # sans outils.
    tools: Optional[list[str]] = None
    # `select` seulement : le mode d'emploi de l'instant — les outils ne sont PAS
    # montés dans la conversation courante (registre figé à l'ouverture), il faut
    # passer par `oto_call` ou rouvrir une conversation.
    hint: Optional[str] = None
    # `unselect` seulement, toujours `True` sur un succès (oto#42/oto-backend#868) :
    # un retrait qui n'a rien trouvé REFUSE désormais (`connector_not_selected`, 404)
    # au lieu de rendre `removed: false` sur un 200 — un succès qui n'a rien fait est
    # pire qu'un refus, même pattern que l'unlink de projet (`d3c5de40`).
    removed: Optional[bool] = None


def _visible_catalog(ctx: ResolvedCtx) -> list[dict]:
    """Catalogue exposé pour l'org active du caller — miroir du filtrage de
    `api_routes_public.connectors_catalog` : activation (plafond).

    ⚠️ **La ligne servie sort d'ici avec sa cardinalité EFFECTIVE**, pas celle du code
    (oto-backend#732). `providers.public_catalog()` pose `auth.cardinality` depuis le
    registre, qui est pur et ne peut donc pas lire une surcharge d'org — or c'est cette
    clé que le panneau de connexion du dashboard lit pour décider s'il propose un second
    compte. Le seam est ICI et pas au call-site : c'est le point de passage unique de la
    carte vers `connectors.me` ET `oto_search`, donc le seul endroit où l'on ne peut pas
    en oublier un."""
    exposed = connector_activation.exposed_connectors(ctx.org_id)
    out = [c for c in providers.public_catalog() if c["name"] in exposed]
    return connector_cardinality.overlay_for_org(out, ctx.org_id)


def _guide_refs_by_ns(org_id: int | None) -> dict[str, set]:
    """namespace → ensemble des guides de l'org qui le référencent (`<tool:slug>`).
    Vide si pas d'org. Dérivation pure depuis les bodies de guide (posture
    « guide-only », ADR 0024) — best-effort, ne fait jamais échouer la lecture."""
    if not org_id:
        return {}
    try:
        refs: dict[str, set[str]] = {}
        for d in org_store.list_instruction_bodies("org", org_id):
            slug = d.get("slug") or ""
            for ns in tool_registry.namespaces_in(d.get("body_md") or ""):
                refs.setdefault(ns, set()).add(slug)
        return refs
    # noqa: SILENT — refs de guide illisibles ⇒ overlay vide, catalogue servi
    except Exception:
        return {}


# Champs conservés en mode COMPACT (défaut MCP) : identité + axes de tri + état.
# Les gros champs (doc_sections/auth/credential_fields/description/namespaces) ne
# reviennent qu'en `verbose=True` (dashboard, setup credential). Cf. #109.
# `secret_kind` est dans le COMPACT, et pas seulement dans la carte verbeuse :
# c'est le seul scalaire qui distingue « ce connecteur ne demande aucune clé »
# (open data — `none`) de « il en demande une que tu n'as pas encore ». Sans lui
# au chargement, un tableau ne peut pas le dire, et l'absence d'entrée dans
# `providers` ne suffit PAS à trancher : `scaleway` et `http` en sont absents eux
# aussi tout en exigeant un credential. Le front
# affichait donc « Not connected » sur OpenStreetMap jusqu'à ce qu'on ouvre la
# carte, où le verbeux disait « Included » (constaté en prod le 2026-08-29).
# Scalaire déjà calculé, aucun coût : le compact évite `auth`/`credential_fields`
# pour leurs listes, pas pour un mot.
_COMPACT_KEYS = ("name", "label", "help", "family", "category", "availability",
                 "logo_url", "secret_kind")


def _toolbox_scope(sub: str) -> Optional[dict]:
    """L'écart entre l'org pour laquelle la SESSION a été montée et celle que l'appel
    épingle — ou None s'il n'y en a pas (signal #577).

    Prouvé par différentiel sur la prod le 28/08/2026. La boîte à outils d'une session
    MCP est calculée AU HANDSHAKE (`session_visibility.compute_hidden_tools`, appelé à
    `on_initialize`) : à cet instant aucun jeton `_org=` n'existe, donc `current_org`
    retombe sur l'org MAISON. Une session planifiée épingle ensuite `_org=` à CHAQUE
    appel — mais le registre d'outils, lui, est figé pour la maison. Le sub qui fait
    tourner la procédure de #577 a pour maison l'org 42 (`folk`, `grain` sélectionnés)
    et travaille sur l'org 196 (treize connecteurs, dont `granola`, `slack`, `linear`).
    D'où « aucun outil de connecteur ne remonte » — alors que les sept cités ont tous
    répondu du premier coup via `oto_call`.

    C'est un défaut de VISIBILITÉ, jamais d'accès ni de credential. Nulle part la carte
    ne le disait : trois matinées (20-22/08) de faux rapports « Linear est en panne ».

    Ne se dit QUE sur écart réel : un champ toujours présent devient du bruit qu'on
    cesse de lire. Pas de jeton d'appel (face REST du dashboard) ⟹ pas de session MCP
    dont la boîte pourrait diverger ⟹ rien à annoncer.

    ⚠️ DEUX consommateurs depuis le 03/09 : cette carte, et `oto_list_my_tools`
    (`tools/meta.py`). Posé ici seul, l'aveu n'était lu que par qui appelait déjà
    `oto_connector` — or un agent qui cherche un outil appelle `oto_list_my_tools`, et
    y lisait une liste vide sans un mot. Ne prend qu'un `sub` pour cette raison : le
    fait est une propriété de la SESSION, pas des connecteurs."""
    call_org = session_org.current_call_org()
    if call_org is None:
        return None
    home = org_store.get_active_org(sub)
    if home == call_org:
        return None
    return {
        "mounted_for_org": home,
        "listing_for_org": call_org,
        "note": (
            f"La boîte à outils de cette session a été montée pour l'org {home} (ton "
            f"org maison au moment du handshake), pas pour l'org {call_org} que cet "
            f"appel épingle : les outils des connecteurs actifs ici peuvent ne PAS "
            f"être listés. Ils restent appelables par "
            f"`oto_call(name=..., arguments={{...}})` — un outil absent de la liste "
            f"n'est PAS un connecteur en panne."),
    }


# ── Retrouver un connecteur par ce que l'appelant en SAIT (#1112) ─────────────────
# L'agent ne connaît pas `linkedin_unipile` : il sait « LinkedIn », le libellé de la
# carte. `name="linkedin"` répondait « inconnu ou indisponible » — un refus sec, que
# l'agent a relu « pas de LinkedIn » et rendu à l'utilisateur. Même chose pour
# `linkedin_aiark`, qui est le NAMESPACE des outils du connecteur `aiark`.
#
# Le registre `providers/` est la seule source (le catalogue n'est pas une table,
# #905) : aucun alias persisté, on lit ce que chaque connecteur déclare déjà — son nom,
# son libellé, ses namespaces.

# Au-delà, la lecture ciblée cesse d'en être une : le verdict d'aptitude (~244 ms
# l'unité, `connectors/readiness.py`) n'est pas calculé, et on le dit.
_CANDIDATS_DIAGNOSTIQUES = 5


def _normalise(texte: str) -> str:
    """Casse, accents et séparateurs neutralisés : « LinkedIn », `linkedin`,
    `linked-in` ne diffèrent pas pour qui cherche."""
    plat = unicodedata.normalize("NFKD", texte or "")
    plat = "".join(ch for ch in plat if not unicodedata.combining(ch)).lower()
    return " ".join(re.split(r"[^a-z0-9]+", plat)).strip()


def _formes(c: dict) -> set[str]:
    """Les noms sous lesquels on peut DÉSIGNER ce connecteur : nom, libellé,
    namespaces — normalisés."""
    return {f for f in (_normalise(c.get("name") or ""), _normalise(c.get("label") or ""),
                        *(_normalise(ns) for ns in c.get("namespaces") or [])) if f}


def _resoudre_nom(catalog: list[dict], demande: str) -> list[dict]:
    """Les lignes que `demande` désigne : le nom exact d'abord (seul), sinon toute
    ligne dont une forme (nom, libellé, namespace) ÉGALE la demande ou en contient
    chaque mot. « linkedin » → `linkedin_unipile` (libellé) et `aiark` (namespace
    `linkedin_aiark`). Vide = rien ne correspond ; c'est à l'appelant de refuser."""
    exact = [c for c in catalog if c["name"] == demande]
    if exact:
        return exact
    q = _normalise(demande)
    if not q:
        return []
    mots = set(q.split())
    egal, contient = [], []
    for c in catalog:
        formes = _formes(c)
        if q in formes:
            egal.append(c)
        elif any(mots <= set(f.split()) for f in formes):
            contient.append(c)
    return egal + contient


def _suggestions(catalog: list[dict], demande: str, n: int = 5) -> list[str]:
    """Les noms PROCHES de `demande` (orthographe, préfixe) — ce qu'un refus propose
    au lieu d'un « inconnu » sec. Nom exact du connecteur, jamais son libellé : c'est
    lui que l'appel suivant doit porter."""
    q = _normalise(demande)
    if not q:
        return []
    par_forme: dict[str, str] = {}
    for c in catalog:
        for f in _formes(c):
            par_forme.setdefault(f, c["name"])
    proches = [par_forme[f] for f in difflib.get_close_matches(q, par_forme, n=n * 2,
                                                               cutoff=0.6)]
    proches += [nom for f, nom in par_forme.items() if q in f or f in q]
    return list(dict.fromkeys(proches))[:n]


def _refus_nom_inconnu(catalog: list[dict], demande: str) -> AuthzDenied:
    """Le refus d'un nom qui ne désigne rien — NOMMÉ et qui propose (#1112)."""
    proches = _suggestions(catalog, demande)
    if proches:
        suite = (f" Noms proches : {', '.join(f'`{n}`' for n in proches)} — relance avec "
                 f"l'un d'eux (`oto_connector(op='list', name='{proches[0]}')`).")
    else:
        suite = (" Aucun nom proche : `oto_connector(op='list')` sans `name` rend le "
                 "catalogue compact avec les noms exacts.")
    return AuthzDenied(
        404, "unknown_connector",
        f"Aucun connecteur disponible pour ton org active ne s'appelle `{demande}` ni ne "
        f"porte ce libellé (ou il n'est pas ouvert à ton org).{suite} Ce refus ne dit "
        f"RIEN de tes connexions : il porte sur le nom.",
        details={"query": demande, "suggestions": proches})


def _with_readiness(ctx: ResolvedCtx, row: dict) -> dict:
    """Pose `ready` / `not_ready` / `next_step` sur LA ligne demandée, et renvoie ce
    que l'enveloppe doit dire du calcul.

    Fail-VISIBLE et non fail-open : si les couches ne se lisent pas, on rend
    `readiness:"unavailable"` au lieu d'omettre `ready` en silence. Omettre serait
    reproduire le défaut même de #476 — une absence que l'appelant lit « rien à
    signaler »."""
    try:
        diag = connector_readiness.diagnose(
            ctx.sub, row["name"], org=ctx.org_id,
            # Explicite : `credential_mode_for` le re-dériverait sinon (73 % du temps
            # d'une carte mesurée), et surtout le contexte doit être celui du SUJET.
            group=access.current_group(ctx.sub))
    except Exception:
        logger.warning("readiness indisponible pour %s (fail-visible)", row["name"],
                       exc_info=True)
        return {"readiness": "unavailable",
                "readiness_hint": ("L'état réel n'a pas pu être lu (couches "
                                   "clé/option indisponibles) — `state` ci-dessus ne "
                                   "dit QUE ta sélection, pas si le connecteur marche.")}
    if diag is None:
        row["ready"] = True
    else:
        row["ready"] = False
        row["not_ready"] = diag.reason
        row["next_step"] = diag.next_step
    return {"readiness": "computed"}


def _me(ctx: ResolvedCtx, inp: MyConnectorsInput) -> dict:
    """`GET /api/me/connectors` : UNE connexion du pool pour toute la lecture
    (oto-backend#1148). Le chemin fait ~160 lectures unitaires (sélection, coffre,
    cascade, options, apps OAuth) ; chacune empruntait sa connexion et payait son
    `BEGIN`/`COMMIT` — 3 allers-retours par lecture, et autant d'attentes au pool sous
    charge : médiane 3,0 s, p95 15,7 s en production les 03-04/10/2026. Même enveloppe
    que `access.status_for`, et même condition : le chemin ne fait QUE lire (un banc
    relève chaque requête, `tests/test_connecteurs_me_une_connexion_1148.py`)."""
    with db.reuse_connection():
        return _me_projection(ctx, inp)


def _me_projection(ctx: ResolvedCtx, inp: MyConnectorsInput) -> dict:
    org_id = ctx.org_id or 0
    detail = connector_selection.list_selection_detail(ctx.sub, org_id)
    selection = {name: d["state"] for name, d in detail.items()}
    removed = connector_selection.list_removed(ctx.sub, org_id)
    recommended = set(org_store.get_org_default_connectors(ctx.org_id) or []) if ctx.org_id else set()
    doc_refs = _guide_refs_by_ns(ctx.org_id)
    # Découvrabilité : une clé peut exister à portée (équipe dont je suis membre,
    # autre org) sans que la cascade la lise — le connecteur paraît alors « vide »
    # dans la library alors qu'il est utilisable en épinglant. Jusqu'ici l'info
    # n'existait qu'APRÈS un appel raté (erreur « rien ne résout »), donc jamais si
    # le connecteur n'est pas installé (l'appel meurt au dispatch). Une passe
    # BATCHÉE, hors boucle, pour ne pas payer N×M requêtes.
    reach = access.reachable_instances_map(ctx.sub, ctx.org_id)
    catalog = _visible_catalog(ctx)
    name_match: Optional[dict] = None
    if inp.name:
        trouves = _resoudre_nom(catalog, inp.name)
        if not trouves:
            # Non exposé pour l'org active, restreint par le RBAC d'org, ou nom inconnu
            # — indistinguables côté membre (même verdict que `_require_exposed`). Mais
            # le refus PROPOSE : un « inconnu » sec a été relu « pas connecté » (#1112).
            raise _refus_nom_inconnu(catalog, inp.name)
        if [c["name"] for c in trouves] != [inp.name]:
            name_match = {
                "query": inp.name,
                "candidates": [c["name"] for c in trouves],
                "note": (f"`{inp.name}` n'est pas un nom exact : "
                         + (f"il désigne `{trouves[0]['name']}`." if len(trouves) == 1 else
                            f"{len(trouves)} connecteurs y répondent, tous rendus — "
                            f"aucun n'est choisi à ta place.")
                         + " Les gestes (select/pause/unselect) prennent le nom exact."),
            }
        catalog = trouves
    # Credential DISPONIBLE (#1112), sur toutes les lignes — la même fonction que
    # `oto_list_my_tools`. Fail-VISIBLE : un snapshot illisible se DIT dans
    # l'enveloppe, sinon des lignes sans `credential` se reliraient « rien n'est
    # connecté », la conclusion même qu'on répare.
    presence, credentials = credential_presence.lire(ctx.sub, org=ctx.org_id)
    # L'option couche 3 se juge par (option, porteur du credential) : les canaux d'un
    # compte hébergé partagent l'option ET la clé de leur porteur, donc le même verdict.
    # Une marche de cascade par couple, pas une par ligne (#1148).
    options_ouvertes: dict[tuple, bool] = {}

    def _option_ok(nom: str) -> bool:
        cle = (access.paid_option_for(nom), providers.credential_provider(nom))
        if cle not in options_ouvertes:
            options_ouvertes[cle] = access.option_open(ctx.sub, nom, org=ctx.org_id)
        return options_ouvertes[cle]

    connectors = []
    for c in catalog:
        state = selection.get(c["name"], "not_selected")
        if inp.state and state != inp.state:
            continue
        refset: set = set()
        for ns in c.get("namespaces") or []:
            refset |= doc_refs.get(ns, set())
        base = c if inp.verbose else {k: c.get(k) for k in _COMPACT_KEYS}
        # URL de retour de consentement — sur la projection AUTHENTIFIÉE seulement.
        # `providers.public_catalog()` alimente aussi `/api/connectors`, servie sans
        # auth : le descripteur public reste sans URL (cf. `connector_flow.describe`).
        # Elle est DÉRIVÉE de l'environnement, jamais écrite : c'est la valeur que le
        # client doit enregistrer chez son fournisseur, et une prose codée en dur ment
        # dès qu'on la lit depuis la preprod (vécu : un `redirect_uri_mismatch`
        # incompréhensible côté client).
        # `app_ready` suit la même règle (réponse propre au DEMANDEUR, donc jamais au
        # catalogue public) : il dit au front s'il reste une app à poser avant de
        # pouvoir consentir. Sans lui, l'écran de connexion promettait le pire cas à
        # tout le monde — « pose d'abord les identifiants de l'application », y compris
        # à qui n'a plus rien à poser depuis qu'oto publie la sienne.
        if inp.verbose and base.get("connect"):
            from ...connectors import flow as connector_flow
            base = {**base, "connect": {
                **base["connect"],
                "callback_url": connector_flow.callback_url(c["name"]),
                "app_ready": connector_flow.app_ready(c["name"], ctx.sub)}}
        # Couche 3 (option payante) sur la surface USER — sans ça le front ne peut pas
        # dire LAQUELLE des 3 conditions manque (le bandeau « État pour toi », ADR 0044) :
        # `mode==forbidden` conflate option/activation/RBAC. `option_ok=True` si aucune
        # option requise. Verbose seulement (compact = catalogue).
        opt = access.paid_option_for(c["name"])
        # `option_ok` = SOURCE UNIQUE `access.option_open` (partagée avec status_for) :
        # pas d'option ⟹ ok ; sinon BYO (clé propre) OU has_option. Un seul endroit
        # décide « utilisable » → plus de divergence carte « clé d'org » + « Bloqué ».
        row = {
            **base,
            "state": state,
            "recommended": c["name"] in recommended,
            "guide_ref_count": len(refset),
            "doctrine_ref_count": len(refset),   # ALIAS déprécié (retrait 29/10/2026)
            "paid_option": opt,
            "option_ok": _option_ok(c["name"]),
        }
        # Présent SEULEMENT si une instance est à portée → la ligne se distingue au
        # lieu d'ajouter un champ vide sur 40 lignes. Volontairement distinct de
        # `recommended` (= baseline de l'org) : surcharger ce dernier ferait mentir
        # le réglage d'org. C'est de la VISIBILITÉ : l'accès se juge à l'appel.
        if reach.get(c["name"]):
            row["reachable_instances"] = reach[c["name"]]
        if c["name"] in presence:
            row["credential"] = presence[c["name"]]
        # Provenance et retrait (ADR 0050 §E7) : posés seulement quand ils disent
        # quelque chose, pour la même raison que `reachable_instances`.
        if c["name"] in detail:
            row["origin"] = detail[c["name"]]["origin"]
        elif c["name"] in removed:
            # Déjà une chaîne : la fabrique de lignes de `db` rend les horodatages en
            # « AAAA-MM-JJ HH:MM:SS » (UTC, sans fuseau) — servie telle quelle.
            row["removed_at"] = str(removed[c["name"]])
        connectors.append(row)
    out: dict = {"connectors": connectors, "verbose": inp.verbose,
                 "credentials": credentials}
    if name_match is not None:
        out["name_match"] = name_match
    # Verdict d'aptitude (#476) — sur une lecture CIBLÉE seulement. Mesuré sur la prod
    # le 28/08/2026 : le rendre sur tout le catalogue coûte 1 993 ms pour 90
    # connecteurs, sur un serveur MONO-LOOP. On ne le calcule donc pas — mais on le
    # DIT, sinon l'absence de `ready` se relit « rien à signaler », qui est
    # précisément le raccourci qu'on répare. Une recherche par libellé peut rendre
    # quelques candidats (#1112) : chacun reçoit son verdict, dans une borne.
    if inp.name and connectors and len(connectors) <= _CANDIDATS_DIAGNOSTIQUES:
        verdicts = [_with_readiness(ctx, row) for row in connectors]
        out.update(next((v for v in verdicts if v["readiness"] == "unavailable"),
                        verdicts[0]))
    else:
        out["readiness"] = "not_computed"
        out["readiness_hint"] = (
            "Aptitude non calculée sur un catalogue (trop cher pour un serveur "
            "mono-loop). TROIS axes, à ne pas confondre : `state` = ta SÉLECTION dans "
            "la toolbox (`not_selected` ne veut PAS dire non connecté) ; `credential` "
            "= une clé ou un compte existe pour toi (absent = aucun ne résout) ; "
            "vivant = jamais vérifié ici, `credential.next_step` nomme l'outil qui le "
            "vérifie. Pour l'aptitude d'un connecteur, redemande-le seul — "
            "`oto_connector(op='list', name='<connecteur>')` rend `ready` et, s'il "
            "ne l'est pas, l'étape qui manque.")
    tb = _toolbox_scope(ctx.sub)
    if tb is not None:
        out["toolbox_scope"] = tb
    return out


def _require_exposed(ctx: ResolvedCtx, name: str) -> None:
    """Plafond : un connecteur non-exposé pour l'org active ne peut être ni
    sélectionné ni mis en pause (deny-by-default jamais relâché)."""
    if name not in connector_activation.exposed_connectors(ctx.org_id):
        # Un geste ne se résout PAS par libellé (il écrit) : il exige le nom exact, mais
        # son refus propose les noms proches, comme la lecture (#1112).
        raise _refus_nom_inconnu(_visible_catalog(ctx), name)


# Guidage post-activation (oto-backend#111). Le registre d'outils d'une session MCP est
# FIGÉ à l'ouverture de la conversation : un connecteur activé en cours de session n'y
# monte pas ses outils (le hot-reload `tools/list_changed` n'est pas appliqué par
# claude.ai). Le pont fiable = `oto_call` (dispatch universel, ADR 0036) ; sinon, nouvelle
# conversation. On le DIT à l'agent au moment où il installe, pour qu'il enchaîne sans
# conclure « la capacité n'existe pas ».
def _connector_tools(name: str) -> list[str]:
    """Noms des tools du connecteur, depuis le registre BOOT (immunisé à la
    visibilité de session) — la moitié « découverte » de #186 : le hint ordonnait
    « appelle via oto_call » sans donner UN SEUL nom, et l'introspection de session
    ne voyait pas les tools d'un connecteur fraîchement activé."""
    from ... import providers, tool_registry
    from ...tool_visibility import namespace_of
    con = providers.REGISTRY.get(name)
    ns = set(con.namespaces) if con else {name}
    return [t for t in tool_registry.boot_tool_names() if namespace_of(t) in ns]


def _activation_hint(name: str, tools: list[str]) -> str:
    listing = (f" Ses outils : {', '.join(tools[:12])}." if tools
               else " (noms indisponibles — oto_tool_schema les décrit à la demande).")
    return (f"`{name}` est actif. Ses outils ne sont pas encore montés dans CETTE conversation "
            f"(le registre d'outils est figé à l'ouverture) —{listing} Appelle-les DÈS "
            f"MAINTENANT via `oto_call(name=…, arguments={{…}})` (schéma d'un outil : "
            f"`oto_tool_schema`), ou ouvre une NOUVELLE conversation pour les voir listés.")


def _org_du_geste(ctx: ResolvedCtx) -> int:
    """L'org sous laquelle un geste du membre range sa sélection — une org RÉELLE,
    jamais l'ancienne sentinelle `0` (#959). Ce `ctx.org_id or 0` écrivait des lignes
    qu'aucune lecture ne rend plus depuis la fin du « perso sans org » (ADR 0030 §8) :
    sans org active, le geste est REFUSÉ par son nom plutôt que rangé hors de vue."""
    if not ctx.org_id:
        raise AuthzDenied(400, "no_active_org",
                          "Aucune org active : une sélection de connecteur se range sous "
                          "une org — choisis-en une avec oto_use_org.")
    return ctx.org_id


_REFUS_SANS_ORG = DeclaredError(400, "no_active_org",
                                "aucune org active : une sélection se range sous une org "
                                "réelle, jamais hors de vue")


def _select(ctx: ResolvedCtx, inp: ConnectorActionInput) -> dict:
    org_id = _org_du_geste(ctx)
    _require_exposed(ctx, inp.name)
    connector_selection.set_state(ctx.sub, inp.name, connector_selection.ACTIVE, org_id)
    tools = _connector_tools(inp.name)
    return {"connector": inp.name, "state": "active", "tools": tools,
            "hint": _activation_hint(inp.name, tools)}


def _pause(ctx: ResolvedCtx, inp: ConnectorActionInput) -> dict:
    org_id = _org_du_geste(ctx)
    _require_exposed(ctx, inp.name)
    connector_selection.set_state(ctx.sub, inp.name, connector_selection.PAUSED, org_id)
    return {"connector": inp.name, "state": "paused"}


def _unselect(ctx: ResolvedCtx, inp: ConnectorActionInput) -> dict:
    # oto#42/oto-backend#868 — un retrait qui ne retire rien ne répond plus `ok`
    # (`removed: false` par-dessus un 200 se lisait comme un succès idempotent ;
    # `setExposure` côté dashboard ne lit d'ailleurs pas ce champ et pose l'état
    # local dès que l'appel n'a pas levé). REFUSE nommément, sur le même patron
    # que l'unlink de projet (`d3c5de40`) : un succès qui n'a rien fait est pire
    # qu'un refus.
    if not connector_selection.unselect(ctx.sub, inp.name, _org_du_geste(ctx)):
        raise AuthzDenied(404, "connector_not_selected",
                          f"`{inp.name}` n'est pas dans ta sélection active pour cette org — "
                          "rien n'a été retiré (déjà désinstallé, ou jamais installé ici). "
                          "`connectors.me` te dit ce qui l'est.")
    return {"connector": inp.name, "state": "not_selected", "removed": True}


CAPABILITIES += [
    Capability(
        key="connectors.me", handler=_me, Input=MyConnectorsInput, authz=SUB_ONLY,
        Output=MyConnectors,
        description="List every connector available to you (the marketplace catalog) with your "
                    "per-workspace state: not_selected (in the library) / active / paused, plus "
                    "`recommended` when your org proposes it. Source for both the connector "
                    "library and your installed connectors. Returns a COMPACT row per connector "
                    "by default (name/label/family/category/state/…) — pass verbose=true for the "
                    "full card (doc, auth descriptor, credential fields). Filter with state="
                    "active|paused|not_selected, or with name=<connector> to read the state of "
                    "a SINGLE connector (pair it with verbose=true instead of pulling the whole "
                    "catalog). `name` also accepts a label or a tool namespace (\"linkedin\" "
                    "finds every LinkedIn connector, all returned); an unmatched name is refused "
                    "with the closest names. ⚠️ `state` is only YOUR SELECTION in the toolbox — "
                    "`not_selected` does NOT mean not connected. `credential` (on every row, even "
                    "not_selected) says a key or an account exists for you, at which level, and "
                    "its `next_step` names the tool that checks it is ALIVE (e.g. "
                    "linkedin_unipile_account op=status) — call it before telling anyone a "
                    "connector is not connected. A name=<connector> lookup also returns `ready` "
                    "(key resolves, paid option open, no step left) plus `not_ready`/`next_step` "
                    "when it doesn't; the whole catalog returns readiness:not_computed.",
        errors=(DeclaredError(404, "unknown_connector",
                              "nom inconnu du registre, connecteur non exposé "
                              "pour l'org active, ou restreint par une règle — "
                              "les trois sont indistinguables côté membre, et "
                              "tous se règlent par la même demande à un admin"),),
        rest=RestBinding("GET", "/api/me/connectors"),
    ),
    Capability(
        key="connectors.select", handler=_select, Input=ConnectorActionInput, authz=SUB_ONLY,
        Output=ConnectorSelectionState,
        description="Install a connector into your active workspace (state=active). name = "
                    "connector name from the catalog. Its tools do NOT mount in the current "
                    "conversation (the tool registry is frozen at open) — the response `hint` "
                    "tells you to reach them right away via oto_call, or open a new conversation.",
        errors=(DeclaredError(404, "unknown_connector",
                              "nom inconnu du registre, connecteur non exposé "
                              "pour l'org active, ou restreint par une règle — "
                              "les trois sont indistinguables côté membre, et "
                              "tous se règlent par la même demande à un admin"),
                _REFUS_SANS_ORG),
        rest=RestBinding("POST", "/api/me/connectors/{name}/select"),
    ),
    Capability(
        key="connectors.pause", handler=_pause, Input=ConnectorActionInput, authz=SUB_ONLY,
        Output=ConnectorSelectionState,
        description="Pause an installed connector (state=paused): kept installed but its tools "
                    "are hidden. Resume by selecting it again.",
        errors=(DeclaredError(404, "unknown_connector",
                              "nom inconnu du registre, connecteur non exposé "
                              "pour l'org active, ou restreint par une règle — "
                              "les trois sont indistinguables côté membre, et "
                              "tous se règlent par la même demande à un admin"),
                _REFUS_SANS_ORG),
        rest=RestBinding("POST", "/api/me/connectors/{name}/pause"),
    ),
    Capability(
        key="connectors.unselect", handler=_unselect, Input=ConnectorActionInput, authz=SUB_ONLY,
        Output=ConnectorSelectionState,
        description="Remove a connector from your workspace (back to the library). Does not touch "
                    "credentials, only your selection. REFUSES (connector_not_selected, 404) if "
                    "it wasn't in your active selection for this org — it never answers ok on a "
                    "removal that found nothing.",
        errors=(DeclaredError(404, "connector_not_selected",
                              "le connecteur n'est pas dans ta sélection active pour "
                              "cette org : déjà retiré, jamais installé ici, ou "
                              "installé sous une autre org active"),
                _REFUS_SANS_ORG),
        rest=RestBinding("DELETE", "/api/me/connectors/{name}"),
    ),
    Capability(
        key="connectors.recommend", handler=_recommend, Input=RecommendInput,
        Output=OrgRecommendedConnectors,
        authz=ORG_ADMIN_OF("org_id"),
        description="[org admin] Set your org's KIT as a whole list — the connectors your org "
                    "installs into its members' toolboxes. Only the DIFFERENCE with the current "
                    "kit is applied, to current members AND to members who join later: each "
                    "added connector is installed for every member who doesn't have it (never "
                    "over a member's own choice — a connector they paused or removed themselves "
                    "stays that way); a connector taken out of the kit is uninstalled where the "
                    "kit installed it, and nowhere else. Connectors already in the kit are not "
                    "replayed. Returns, per changed "
                    "connector, installed / already_active / paused / removed_by_member. "
                    "Members' agents see it at their NEXT conversation. "
                    "ADDING a connector unknown to the catalog or not available for your org "
                    "is REFUSED, naming why — nothing is written. A connector already in the "
                    "kit that your org has since cut stays in it: installed, hidden for "
                    "everyone, back on its own when reopened (listed in `cut`). "
                    "connectors = connector names ([] empties the kit).",
        errors=(DeclaredError(404, "unknown_org", "org inconnue"),
                DeclaredError(404, "unknown_connector",
                              "un connecteur AJOUTÉ au kit est inconnu du registre — rien "
                              "n'est écrit"),
                DeclaredError(409, "org_disabled",
                              "un connecteur AJOUTÉ au kit n'est pas disponible pour les "
                              "membres de l'org (l'org l'a coupé) — rien n'est écrit"),
                DeclaredError(409, "platform_disabled",
                              "un connecteur AJOUTÉ au kit est coupé par la plateforme — "
                              "rien n'est écrit"),),
        rest=RestBinding("PUT", "/api/orgs/{id}/default-connectors", _ID),
    ),
    Capability(
        key="connectors.bulk_select", handler=_bulk_select, Input=BulkSelectInput,
        Output=BulkSelectResult,
        authz=ORG_ADMIN_OF("org_id"),
        description="[org admin] Add a connector to your org's KIT: it is installed right away "
                    "for every current member who doesn't have it, and for every member who "
                    "joins later. Never over a member's own choice — a member who paused it or "
                    "removed it themselves keeps it that way. If it is ALREADY in the kit, "
                    "nothing is replayed: the response says why and how to apply it anyway. "
                    "Requires the org to expose the connector. Returns activated (installed "
                    "now), skipped, and the per-population detail in `changes`. Members' "
                    "agents see it at their NEXT conversation.",
        errors=(DeclaredError(404, "unknown_connector",
                              "nom inconnu du registre"),
                DeclaredError(409, "org_disabled",
                              "l'org a désactivé ce connecteur : l'activer pour "
                              "tous contredirait sa propre gouvernance"),
                DeclaredError(409, "platform_disabled",
                              "la plateforme a coupé ce connecteur : l'org ne peut pas "
                              "l'installer"),),
        rest=RestBinding("POST", "/api/orgs/{id}/connectors/{name}/bulk-select", _ID),
    ),
    Capability(
        key="connectors.unset_default", handler=_unset_default, Input=UnsetDefaultInput,
        Output=UnsetDefaultResult,
        authz=ORG_ADMIN_OF("org_id"),
        description="[org admin] Take a connector out of your org's KIT: it is uninstalled "
                    "from every member the KIT installed it for (active or paused), and kept "
                    "where the member installed or resumed it themselves, where an admin pushed "
                    "it to them, or where it predates tracking — `uninstalled` and `kept` (by "
                    "provenance) say so. Members who join later no longer get it. It never "
                    "hides the connector from search/the library: that is the availability "
                    "switch, a different lever.",
        rest=RestBinding("DELETE", "/api/orgs/{id}/connectors/{name}/bulk-select", _ID),
    ),
]
