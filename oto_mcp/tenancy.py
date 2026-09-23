"""Tenant : registre d'émetteurs et qualification du sub (ADR 0052, lot L2).

Le tenant est l'étage d'identité entre la plateforme et l'org : il porte un
**émetteur** (son Logto dédié), des domaines, des orgs. Ce module tient les deux
seams que ça demande, et rien d'autre :

1. **Le registre `issuer → (tenant, verifier)`.** Le verifier était mono-émetteur,
   avec un `LOGTO_ENDPOINT_ALT` que le code décrivait lui-même comme une fenêtre de
   drain (« puis on retire l'env »). Le drain est ABSORBÉ ici : c'est une seconde
   entrée pour le MÊME tenant `oto` — exactement le cas général dont il était le
   cas particulier. Plus de mécanisme à côté du registre.

2. **La qualification du sub.** Un sub du tenant `oto` reste **NU** (`abc123`) ;
   un sub d'un tenant tiers devient `"<slug>:<sub>"` (ex. `acme:abc123`). C'est ce
   qui rend le chantier additif : aucune ligne existante n'est retouchée et **rien
   n'est rechiffré** — l'AAD du coffre dérive du sub (`credentials_store._aad`), donc
   qualifier le sub du tenant `oto` rendrait TOUS les credentials indéchiffrables.

**En aval, le sub est une chaîne opaque** : jamais désassemblé, jamais parsé.
`users`, coffre, ownership, calllog, quotas, RBAC ne changent pas d'une ligne. Deux
conséquences pratiques, gardées par `tests/test_tenant_l2_sub_opaque.py` :

- l'énoncé naïf « aucun call-site ne parse `:` » est FAUX — `entity_id` vaut
  `{org}:{sub}` au scope membre et se découpe légitimement à son PREMIER `:`. Ce
  qui tient : *le sub n'est jamais découpé ; `entity_id` ne l'est qu'à son premier
  `:`* — et jamais quand `entity_type='user'`, où `entity_id` EST le sub ;
- un ref d'instance (`instance_refs`) contient un sub, mais **percent-encodé** :
  son `split(":")` reste non-ambigu sur un sub qualifié (roundtrip testé).

Collision impossible : les subs Logto ne contiennent pas de `:`, donc un sub
qualifié ne peut jamais désigner la ligne d'un sub nu. C'est le cloisonnement
cryptographique par tenant, obtenu gratuitement via l'AAD.

Hors périmètre de L2, volontairement : l'audience stricte par tenant et le PRM
Host-aware (lot L3, d'où la colonne `hosts` que personne ne lit encore), et la
migration des comptes existants vers un tenant (L3bis — un compte migré reçoit un
sub neuf, donc une AAD neuve, donc des secrets illisibles).
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Le tenant de la plateforme elle-même (id 1, semé par L1). Son sub reste NU :
# l'existant est NOMMÉ, pas déplacé.
PRIMARY_SLUG = "oto"

# Un slug entre DANS le sub : il doit être un jeton sans ambiguïté. Pas de `:` (il
# rendrait la qualification indécidable), rien qu'un agent ou une UI puisse
# confondre avec autre chose.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _refus(message: str, *args) -> None:
    """Un refus du registre : au journal **et** au suivi d'erreurs (Sentry).

    Chacun des trois refus du registre laisse une déclaration de tenant NON
    CHARGÉE : les jetons du tenant routent vers le verifier primaire, qui les rejette — le
    partenaire est injoignable sans qu'aucune requête n'échoue de notre côté, donc
    sans rien à voir nulle part. Le journal système ne suffit pas : un tel
    avertissement est resté sans destinataire en préprod le 2026-09-07, et le tenant
    est resté non chargé toute la journée. Il manquait un DESTINATAIRE, pas un
    message — d'où l'alerte, avec exactement le texte du journal.

    Sentry est le canal déjà câblé (`sentry_setup`, gaté `OTO_SENTRY_DSN`, no-op
    sans lui), et déjà employé pour une anomalie d'exploitation qui n'est pas une
    exception (`loop_watch`, gel de l'event loop). Fail-open : le suivi d'erreurs ne
    doit jamais empêcher un registre de se construire.
    """
    logger.warning(message, *args)
    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            # Clé de recherche stable (`has:oto.registre_tenants`) ; le TEXTE porte
            # le slug et l'émetteur, donc une issue par conflit, pas un fourre-tout.
            scope.set_tag("oto.registre_tenants", "refus")
            sentry_sdk.capture_message(message % args if args else message,
                                       level="error")
    # noqa: SILENT — un boot ne casse pas parce que le suivi d'erreurs est indisponible
    except Exception:
        pass


@dataclass(frozen=True)
class TenantIssuer:
    """Une entrée du registre : un émetteur, le tenant qu'il désigne, son JWKS.

    `name` et `hosts` servent la DÉCOUVERTE (lot L3) et rien d'autre : le nom part
    en `resource_name` du PRM, les hosts font le binding `host → tenant`. Ils sont
    sans effet sur la vérification d'un jeton, qui ne connaît que l'émetteur.
    """
    slug: str
    issuer: str
    jwks_uri: str
    name: str = ""
    hosts: tuple = ()
    # Client OAuth servi par la façade d'enregistrement sur les hosts de ce tenant
    # (cf. `oauth_facade`). Sans lui, un host déclaré enverrait le client demander un
    # enregistrement automatique à un annuaire qui n'en fait pas.
    oauth_client_id: str = ""
    # Adresse du tableau de bord de ce tenant. Vide = la nôtre (cf. `config`).
    dashboard_url: str = ""
    # Accès d'ADMINISTRATION de l'annuaire de ce tenant, quand c'est nous qui
    # l'hébergeons : `{"token_endpoint", "api_endpoint", "credential"}`, ou None.
    # ⚠️ **Jamais le secret** — `credential` est le NOM d'un couple de variables
    # d'environnement (`<credential>_ID` / `<credential>_SECRET`), lu au moment de
    # l'appel. La base dit OÙ frapper et SOUS QUEL nom, le process détient la clé.
    # Absent = annuaire non administrable par la plateforme, et c'est le défaut :
    # authentifier un tenant ne donne aucun droit d'écrire dans son annuaire.
    logto_mgmt: Any = None
    # Chemins par TYPE de lien (`links.DEFAULT_PATHS` pour les types connus).
    # Type absent = ce tenant n'a pas cette vue ⟹ on ne rend AUCUN lien.
    link_paths: Any = None
    # Préfixe des outils de la plateforme MONTRÉS à ses comptes (`oto_doc` →
    # `acme_doc`, cf. `tool_alias`). Vide = les noms canoniques, l'état d'avant.
    # DÉCLARÉ, jamais dérivé du slug : renommer les outils rompt les procédures et la
    # prose déjà écrites du tenant, donc ça se décide.
    tool_prefix: str = ""
    # PALETTE de ce tenant pour ce qu'on lui dessine — aujourd'hui les emails
    # (`email_brand`). Vide/absente = notre charte. Même raison d'être que
    # `dashboard_url` et `link_paths` : ce qui appartient au partenaire se DÉCLARE,
    # il ne s'écrit pas dans notre code, sinon le second partenaire nous coûte un
    # déploiement. Les teintes sont validées à la LECTURE, pas ici : le registre
    # transporte ce qui est déclaré, il ne juge pas des couleurs.
    brand: Any = None
    # Rappels OAuth EXACTS que la façade d'enregistrement (`auth/facade.py`) accepte EN
    # PLUS de sa liste globale, **sur les hosts de ce tenant et nulle part ailleurs** :
    # l'URL complète d'un client hébergé que son exploitant a choisi, jamais un motif.
    # Déclarés dans `logto_mgmt.redirect_uris` : ils n'ont d'effet que si la façade sait
    # les poser dans l'annuaire du tenant, donc avec ces accès. Vide = la liste globale
    # seule, l'état d'avant à l'octet près.
    dcr_redirects: tuple = ()
    # OPT-IN de ce tenant aux jetons de rafraîchissement pour les clients qui passent par le
    # RELAIS de la façade (`auth/relay.py`) : déclaré dans `logto_mgmt.refresh_tokens`, éteint
    # par défaut. Allumé, l'autorisation relayée sur les hosts de CE tenant ajoute `consent` à
    # `prompt` quand `offline_access` est demandé (`authorize_consent`, oto#202) — sans quoi
    # Logto ne délivre aucun jeton de rafraîchissement et un client comme Codex, qui n'envoie
    # pas `prompt`, perd sa connexion à chaque expiration du jeton d'accès. C'est SA décision,
    # jamais la nôtre : durée et rotation se règlent dans SON annuaire. Ne vaut que sur un
    # host relayé ; sur un autre, le client s'autorise chez l'annuaire du tenant et la
    # plateforme ne voit pas la demande.
    refresh_tokens: bool = False


def qualify(slug: Optional[str], sub: Optional[str]) -> Optional[str]:
    """Le sub tel que TOUTE la plateforme le verra — l'unique qualificateur.

    Tenant `oto` (ou slug absent) : le sub est rendu **inchangé, byte pour byte**.
    Ce n'est pas une optimisation, c'est l'invariant : l'AAD du coffre en dérive.
    """
    if not sub or not slug or slug == PRIMARY_SLUG:
        return sub
    return f"{slug}:{sub}"


def unverified_issuer(token: Optional[str]) -> Optional[str]:
    """Claim `iss` du jeton, **sans vérifier la signature** — il ne sert qu'à CHOISIR
    le verifier, qui revalidera l'émetteur pour de vrai (signature + `iss`).

    Décodage à la main plutôt qu'avec une lib : ça garde visible le fait que rien
    n'est vérifié ici, et n'ajoute pas une dépendance sur un chemin d'auth.
    """
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pad))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    iss = claims.get("iss") if isinstance(claims, dict) else None
    return normalize_issuer(iss)


def normalize_issuer(issuer) -> Optional[str]:
    """Forme de comparaison d'un émetteur (espaces, slash final).

    N'élargit que la SÉLECTION : le verifier choisi revalide l'`iss` byte-à-byte,
    donc une forme non canonique route mais ne passe pas.
    """
    if not issuer or not isinstance(issuer, str):
        return None
    return issuer.strip().rstrip("/") or None


def build(primary_issuer: str, drain_issuers: Iterable[str] = (),
          tenants: Iterable[Mapping] = ()) -> dict:
    """`{issuer: TenantIssuer}` — le registre.

    `primary_issuer` et `drain_issuers` viennent de l'ENV et portent tous deux le
    tenant `oto` : le drain est un second émetteur du même tenant, pas un tenant.
    `tenants` vient de la base (lignes `slug`/`issuer`/`jwks_uri`).

    Trois refus, tous loggés ET alertés (`_refus`) — chacun ferait qualifier un
    sub sous le mauvais tenant, c'est-à-dire pointer la mauvaise serrure du coffre :

    - une ligne qui réclame l'émetteur primaire (ou un drain) : **l'env gagne
      toujours**, sinon une écriture en base re-tenanterait les comptes existants ;
    - un slug invalide (vide, majuscules, `:`…) : il entre dans le sub ;
    - un émetteur déjà tenu : la sélection par `iss` deviendrait ambiguë.
    """
    entries: dict[str, TenantIssuer] = {}

    def _put(slug: str, issuer, jwks_uri=None, name="", hosts=(),
             oauth_client_id="", dashboard_url="", link_paths=None,
             tool_prefix="", brand=None, logto_mgmt=None) -> None:
        iss = normalize_issuer(issuer)
        if not iss:
            return
        jwks = (jwks_uri or "").strip() if isinstance(jwks_uri, str) else ""
        entries[iss] = TenantIssuer(slug=slug, issuer=iss,
                                    jwks_uri=jwks or f"{iss}/jwks",
                                    name=name or "", hosts=tuple(hosts or ()),
                                    oauth_client_id=str(oauth_client_id or ""),
                                    dashboard_url=str(dashboard_url or "").rstrip("/"),
                                    link_paths=_normalize_paths(link_paths),
                                    # Tel que DÉCLARÉ (cf. le slug juste au-dessus) :
                                    # `tool_alias.normalize_prefix` juge, il ne répare pas.
                                    tool_prefix=str(tool_prefix or ""),
                                    brand=brand if isinstance(brand, dict) else None,
                                    logto_mgmt=_normalize_mgmt(logto_mgmt, slug),
                                    dcr_redirects=_normalize_redirects(logto_mgmt, slug),
                                    refresh_tokens=_normalize_refresh_tokens(logto_mgmt, slug))

    _put(PRIMARY_SLUG, primary_issuer)
    for drain in drain_issuers or ():
        _put(PRIMARY_SLUG, drain)

    for row in tenants or ():
        # Pas de `.strip()` : le slug entre dans le sub, donc il vaut EXACTEMENT ce
        # qui est déclaré. Un espace parasite doit se voir (ligne refusée + log),
        # pas se faire absorber en une identité voisine de celle qu'on lit en base.
        slug = str((row or {}).get("slug") or "")
        iss = normalize_issuer((row or {}).get("issuer"))
        if not iss:
            continue
        if not _SLUG_RE.match(slug) or slug == PRIMARY_SLUG:
            _refus(
                "registre d'émetteurs : slug %r refusé (invalide, ou réservé au "
                "tenant de la plateforme dont l'émetteur vient de l'env) — %s ignoré",
                slug, iss)
            continue
        if iss in entries:
            _refus(
                "registre d'émetteurs : %s réclamé par le tenant %r alors qu'il est "
                "déjà tenu par %r — ligne ignorée", iss, slug, entries[iss].slug)
            continue
        _put(slug, iss, (row or {}).get("jwks_uri"),
             name=str((row or {}).get("name") or ""),
             hosts=normalize_hosts((row or {}).get("hosts")),
             oauth_client_id=str((row or {}).get("oauth_client_id") or ""),
             dashboard_url=str((row or {}).get("dashboard_url") or ""),
             link_paths=(row or {}).get("link_paths"),
             tool_prefix=(row or {}).get("tool_prefix"),
             brand=(row or {}).get("brand"),
             logto_mgmt=(row or {}).get("logto_mgmt"))
    return entries


# Le NOM d'un couple de variables d'environnement, pas une valeur. Contraint à la
# forme d'un identifiant d'env en majuscules : une chaîne libre irait chercher
# n'importe quelle variable du process, et ce qu'elle rapporterait servirait à
# s'authentifier quelque part.
_ENV_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,60}$")


def _normalize_mgmt(valeur, slug: str = "") -> Optional[dict]:
    """Les accès d'administration d'un annuaire, ou **None** — jamais à moitié.

    ⚠️ Les DEUX endpoints sont DÉCLARÉS, jamais dérivés l'un de l'autre : chez Logto
    le jeton de management s'obtient sur l'endpoint d'ADMINISTRATION et les appels
    `/api` vont sur l'endpoint PRINCIPAL — l'inverse rend `401 aud check_failed`,
    très loin de sa cause. Deviner le second à partir du premier, c'est choisir un
    endpoint à la place du partenaire.

    Une déclaration incomplète ou illisible est REFUSÉE bruyamment (`_refus`) plutôt
    que rabotée : silencieuse, elle ferait retomber la façade d'enregistrement sur
    « annuaire non administrable » — c'est-à-dire exactement le défaut qu'elle vient
    corriger (oto-backend#909), et sans destinataire.
    """
    if valeur in (None, "", {}):
        return None
    if isinstance(valeur, str):
        try:
            valeur = json.loads(valeur)
        except ValueError:
            valeur = None
    if not isinstance(valeur, dict):
        _refus("registre d'émetteurs : accès d'annuaire du tenant %r illisible — "
               "son annuaire reste non administrable", slug)
        return None
    tok = str(valeur.get("token_endpoint") or "").strip().rstrip("/")
    api = str(valeur.get("api_endpoint") or "").strip().rstrip("/")
    cred = str(valeur.get("credential") or "").strip()
    manque = [nom for nom, ok in (("token_endpoint", tok.startswith("https://")),
                                  ("api_endpoint", api.startswith("https://")),
                                  ("credential", bool(_ENV_REF_RE.match(cred))))
              if not ok]
    if manque:
        _refus("registre d'émetteurs : accès d'annuaire du tenant %r refusé "
               "(%s absent ou invalide) — son annuaire reste non administrable",
               slug, ", ".join(manque))
        return None
    return {"token_endpoint": tok, "api_endpoint": api, "credential": cred}


_REDIRECT_MAX = 512


def _rappel_declarable(uri) -> bool:
    """Une URL de rappel qu'un tenant peut DÉCLARER : https, complète, et rien qui
    ressemble à un motif. Ce n'est pas la garde d'acceptation (`facade`, qui exige en
    plus l'ÉGALITÉ avec une déclaration et la lecture canonique de l'URL demandée) : ici on
    refuse ce qu'aucune requête légitime n'écrirait, pour que la faute se voie au
    chargement plutôt qu'à l'autorisation d'un client."""
    if not isinstance(uri, str) or not (len(uri) <= _REDIRECT_MAX
                                        and uri.startswith("https://")):
        return False
    if any(c <= " " or c > "~" or c in "\\*?#" for c in uri):
        return False
    try:
        p = urlsplit(uri)
        p.port  # un port hors bornes lève
    # noqa: SILENT — fail-closed : une déclaration douteuse est refusée par l'appelant
    except ValueError:
        return False
    return bool(p.hostname) and "@" not in p.netloc and p.path.startswith("/")


def _normalize_redirects(valeur, slug: str = "") -> tuple:
    """Les rappels EXACTS déclarés par un tenant (`logto_mgmt.redirect_uris`), en tuple.

    ⚠️ **Fail-closed, entrée par entrée** : une entrée qui n'est pas une URL https
    complète (joker, requête, identité, fragment…) est ÉCARTÉE et alertée, les autres
    restent. Contrairement aux accès d'annuaire (`_normalize_mgmt`, tout ou rien), ici
    une ligne écartée ne peut que RÉDUIRE ce qui est accepté — jamais l'élargir."""
    if isinstance(valeur, str):
        try:
            valeur = json.loads(valeur)
        except ValueError:
            return ()
    if not isinstance(valeur, dict):
        return ()
    brut = valeur.get("redirect_uris")
    if brut in (None, [], ()):
        return ()
    if not isinstance(brut, (list, tuple)):
        _refus("registre d'émetteurs : `logto_mgmt.redirect_uris` du tenant %r n'est pas "
               "une liste — aucun rappel déclaré", slug)
        return ()
    retenus: list = []
    for uri in brut:
        if not _rappel_declarable(uri):
            _refus("registre d'émetteurs : rappel %r du tenant %r refusé (une URL https "
                   "EXACTE est attendue : ni joker, ni requête, ni fragment, ni identité) "
                   "— écarté", uri, slug)
        elif uri not in retenus:
            retenus.append(uri)
    return tuple(retenus)


def _normalize_refresh_tokens(valeur, slug: str = "") -> bool:
    """L'opt-in d'un tenant aux jetons de rafraîchissement (`logto_mgmt.refresh_tokens`).

    ⚠️ **Fail-closed** : seul le booléen JSON `true` allume. `"true"`, `"yes"`, `1`, une
    liste, un objet, une chaîne vide : écartés en alertant, drapeau ÉTEINT — une faute de
    frappe ne doit jamais délivrer des jetons de longue durée à des clients publics. Absent,
    `null` et `false` sont l'état d'avant, sans alerte. Le drapeau ne se lit qu'ici, dans la
    déclaration posée par l'administrateur de la plateforme : aucune requête, aucun en-tête,
    aucun corps de DCR n'y touche."""
    if isinstance(valeur, str):
        try:
            valeur = json.loads(valeur)
        except ValueError:
            return False
    if not isinstance(valeur, dict):
        return False
    brut = valeur.get("refresh_tokens")
    if brut is None or brut is False:
        return False
    if brut is True:
        return True
    _refus("registre d'émetteurs : `logto_mgmt.refresh_tokens` du tenant %r vaut %r — le "
           "booléen JSON `true` est seul accepté ; drapeau ÉTEINT (aucun jeton de "
           "rafraîchissement délivré)", slug, brut)
    return False


def _normalize_paths(valeur) -> dict:
    """`{type: chemin}` en forme utilisable. Une valeur illisible devient un dict VIDE,
    donc « aucun lien » — jamais un lien construit sur une donnée qu'on n'a pas su lire."""
    if isinstance(valeur, str):
        try:
            valeur = json.loads(valeur)
        except ValueError:
            return {}
    if not isinstance(valeur, dict):
        return {}
    return {str(k): str(v) for k, v in valeur.items()
            if isinstance(k, str) and isinstance(v, str) and v.strip()}


def normalize_hosts(hosts) -> tuple:
    """Hosts d'un tenant, en forme de comparaison (minuscule, sans port).

    Un `Host:` d'entrée arrive tel que le client l'a écrit — casse libre, port
    éventuel. On compare donc les deux côtés sous la même forme, sinon un tenant
    déclaré `MCP.Acme.ai` ne serait jamais reconnu et le défaut (notre émetteur)
    s'appliquerait en silence : exactement le symptôme qu'on corrige.
    """
    if isinstance(hosts, str):
        try:
            hosts = json.loads(hosts)
        except ValueError:
            hosts = [hosts]
    if not isinstance(hosts, (list, tuple, set)):
        return ()
    out = []
    for h in hosts:
        if not isinstance(h, str):
            continue
        h = h.strip().lower().split("/")[0].split(":")[0]
        if h and h not in out:
            out.append(h)
    return tuple(out)


class IssuerRegistry:
    """Le registre en lecture : sélection par `iss`, classement d'un sub par tenant.

    Vide = tout est le tenant `oto` — l'état d'avant ce lot, et le défaut tant que
    `_build_verifier` n'a pas tourné (imports de test, scripts hors serveur).
    """

    def __init__(self, entries: Optional[Mapping[str, TenantIssuer]] = None) -> None:
        self._by_issuer: dict = dict(entries or {})
        # Préfixes des tenants NON primaires — servent à CLASSER un sub sans le
        # découper (cf. `tenant_of`). Triés pour un log/diagnostic stable.
        self._prefixes = tuple(sorted(
            f"{e.slug}:" for e in self._by_issuer.values() if e.slug != PRIMARY_SLUG))
        # Binding `host → tenant` (lot L3). Un host déclaré par DEUX tenants est
        # refusé comme l'est un émetteur en double : il déciderait vers quel
        # annuaire on envoie l'utilisateur, et se tromper l'envoie chez le mauvais
        # partenaire. Le PREMIER déclarant garde le host, l'autre est loggé.
        self._by_host: dict = {}
        for entry in self._by_issuer.values():
            for host in entry.hosts:
                held = self._by_host.get(host)
                if held is not None and held.slug != entry.slug:
                    _refus(
                        "registre d'émetteurs : le host %r est réclamé par le tenant "
                        "%r alors qu'il est déjà tenu par %r — réclamation ignorée",
                        host, entry.slug, held.slug)
                    continue
                self._by_host[host] = entry

    def entries(self) -> tuple:
        return tuple(self._by_issuer.values())

    def get(self, issuer) -> Optional[TenantIssuer]:
        return self._by_issuer.get(normalize_issuer(issuer) or "")

    def slug_for(self, issuer) -> str:
        """Tenant d'un émetteur. Inconnu ⟹ `oto` : le verifier primaire tranchera,
        et il rejettera (l'`iss` ne correspond pas au sien)."""
        entry = self.get(issuer)
        return entry.slug if entry else PRIMARY_SLUG

    def entry_for_slug(self, slug: Optional[str]) -> Optional[TenantIssuer]:
        """L'entrée d'un tenant par son SLUG — ce que ce tenant DÉCLARE (son adresse,
        ses chemins, sa palette).

        `None` pour le tenant primaire comme pour un slug inconnu, et c'est la même
        réponse à dessein : dans les deux cas il n'y a rien de déclaré à appliquer, et
        l'appelant sert ce qui est à nous. Rendre une entrée pour `oto` ouvrirait la
        porte à repeindre la plateforme depuis une ligne en base.

        ⚠️ Cherche par balayage : le registre est indexé par ÉMETTEUR (sa clé d'usage,
        celle du chemin d'authentification), et il compte quelques entrées. Un second
        index à tenir se désynchroniserait pour économiser une comparaison.
        """
        s = (slug or "").strip()
        if not s or s == PRIMARY_SLUG:
            return None
        return next((e for e in self.entries() if e.slug == s), None)

    def qualify_claims(self, claims: Optional[Mapping]) -> Optional[str]:
        """Sub qualifié depuis un jeu de claims — le qualificateur, appelé partout où
        un jeton devient un sub (vérification du verifier, attribution du journal)."""
        if not claims:
            return None
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            return None
        return qualify(self.slug_for(claims.get("iss")), sub)

    def tenant_of(self, sub: Optional[str]) -> str:
        """Tenant d'un sub, par **classification de préfixe** — jamais par découpe.

        `startswith` teste une appartenance et laisse le sub entier ; un `split`
        fabriquerait deux moitiés dont l'une ressemble à un sub sans en être un.
        Un sub qui ne porte aucun préfixe connu est du tenant `oto` (sub nu).
        """
        if not sub:
            return PRIMARY_SLUG
        for prefix in self._prefixes:
            if sub.startswith(prefix):
                return prefix[:-1]
        return PRIMARY_SLUG

    def for_host(self, host) -> Optional[TenantIssuer]:
        """Tenant servi par ce host, ou **None** si le host n'est réclamé par aucun.

        `None` est le cas nominal aujourd'hui — aucune ligne ne porte de host — et
        c'est ce qui rend ce lot inerte : tout appelant doit lire `None` comme
        « garde le comportement d'avant », jamais comme une erreur.
        """
        if not host or not isinstance(host, str):
            return None
        return self._by_host.get(host.strip().lower().split(":")[0])

    def hosts(self) -> tuple:
        """Les hosts liés à un tenant, pour diagnostic (ordre stable)."""
        return tuple(sorted(self._by_host))

    def callback_host(self, slug: Optional[str]) -> Optional[str]:
        """Le host où poser le rappel OAuth de l'app d'un tenant : son PREMIER host
        déclaré qu'il TIENT réellement — ou `None` (le rappel reste le nôtre).

        `entry.hosts` garde un host même quand sa réclamation a été ignorée parce qu'un
        autre tenant le tenait déjà (cf. `__init__`) : y poser un rappel enverrait le
        code d'autorisation et le state signé chez l'autre. Seul `_by_host` dit qui
        tient quoi. ⚠️ Ce qui ne se vérifie pas ici : que ce host ROUTE vers ce
        backend — c'est la condition de la déclaration du tenant (`docs/tenants.md`)."""
        entry = self.entry_for_slug(slug)
        if entry is None:
            return None
        return next((h for h in entry.hosts
                     if (held := self._by_host.get(h)) is not None and held.slug == entry.slug),
                    None)

    def same_tenant(self, a: Optional[str], b: Optional[str]) -> bool:
        """Deux subs relèvent-ils du même tenant ? (garde d'alias, ADR 0052 §6 :
        pas de fédération d'identités entre tenants.)"""
        return self.tenant_of(a) == self.tenant_of(b)


_INSTALLED = IssuerRegistry()


def install(registry: IssuerRegistry) -> None:
    """Pose le registre du process (au boot, depuis `server._build_verifier`)."""
    global _INSTALLED
    _INSTALLED = registry


def current() -> IssuerRegistry:
    return _INSTALLED


# ── Frontière d'annuaire : authentifier ≠ administrer ─────────────────────────
class ForeignTenantDirectory(RuntimeError):
    """Un acte d'**administration d'annuaire** a été demandé sur un sub qui ne relève
    pas du tenant `oto` — donc sur un annuaire qui n'est pas le nôtre.

    Connaître le tenant d'un sub suffit à l'**authentifier** (c'est ce que fait le
    registre ci-dessus, et ça marche), mais pas à **agir dans son annuaire** : ce qui
    manque n'est pas l'information du tenant, ce sont les clés de la maison du
    partenaire (oto-backend#274).

    ⚠️ **`logto_mgmt` n'ouvre pas cette porte-là.** Un tenant dont NOUS hébergeons
    l'annuaire peut déclarer des accès d'administration, et la façade
    d'enregistrement s'en sert — mais pour poser un rappel sur une APPLICATION, un
    objet qui n'appartient à aucun compte. Router un acte qui vise un UTILISATEUR
    (email autoritatif, MFA) vers l'annuaire d'un tenant est une autre décision, que
    personne n'a prise : ces chemins restent fermés, et disent pourquoi.

    Cette exception existe pour que l'échec DISE ça, au lieu de laisser notre Logto
    répondre « utilisateur inconnu » très loin de la cause.
    """


def require_primary_tenant(sub: Optional[str], action: str) -> Optional[str]:
    """Garde d'un appel qui écrit dans **notre** annuaire Logto : lève
    `ForeignTenantDirectory` si `sub` relève d'un autre tenant.

    À poser au plus près du fil (le helper qui met le sub dans un corps ou une URL
    Management API), pas chez l'appelant : c'est là que l'hypothèse « ce sub désigne
    un utilisateur de notre Logto » est faite.
    """
    slug = current().tenant_of(sub)
    if slug != PRIMARY_SLUG:
        raise ForeignTenantDirectory(
            f"{action} : le compte {sub!r} relève du tenant {slug!r}, pas de "
            f"{PRIMARY_SLUG!r}. Il n'existe pas dans notre annuaire Logto, et aucun "
            f"acte de management visant un UTILISATEUR n'est routé vers l'émetteur "
            f"de {slug!r} — cet acte doit être porté par le tenant propriétaire du "
            f"compte.")
    return sub


def load_tenants() -> list:
    """Tenants porteurs d'un émetteur, depuis la base.

    Une base indisponible rend une liste VIDE, loggée : l'authentification canonique
    est DB-indépendante et continue (c'est la promesse du tenant `oto`), pendant que
    les tenants tiers deviennent injoignables — leurs jetons routent vers le verifier
    primaire, qui les rejette. Dégradation fail-CLOSED côté tiers, sans jamais
    couper la plateforme.

    Le registre est construit **au boot** : déclarer un tenant demande un restart —
    c'est déjà le cas du provisioning (une instance Logto par tenant, B4).
    """
    try:
        from . import db
        return list(db.list_tenant_issuers())
    except Exception:
        # Pas d'alerte ici, contrairement aux refus de `build` : une base illisible
        # ne reste pas silencieuse longtemps — tout ce qui la touche ensuite lève, et
        # le suivi d'erreurs le voit. Ce qui n'avait AUCUN destinataire, c'est le refus
        # d'une LIGNE au milieu d'un boot par ailleurs sain.
        logger.warning("registre d'émetteurs : lecture des tenants impossible — "
                       "seul l'émetteur de l'env est accepté", exc_info=True)
        return []
