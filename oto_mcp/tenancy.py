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
   un sub d'un tenant tiers devient `"<slug>:<sub>"` (`tulina:abc123`). C'est ce
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
from typing import Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

# Le tenant de la plateforme elle-même (id 1, semé par L1). Son sub reste NU :
# l'existant est NOMMÉ, pas déplacé.
PRIMARY_SLUG = "oto"

# Un slug entre DANS le sub : il doit être un jeton sans ambiguïté. Pas de `:` (il
# rendrait la qualification indécidable), rien qu'un agent ou une UI puisse
# confondre avec autre chose.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


@dataclass(frozen=True)
class TenantIssuer:
    """Une entrée du registre : un émetteur, le tenant qu'il désigne, son JWKS."""
    slug: str
    issuer: str
    jwks_uri: str


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

    Trois refus, tous loggés — chacun ferait qualifier un sub sous le mauvais
    tenant, c'est-à-dire pointer la mauvaise serrure du coffre :

    - une ligne qui réclame l'émetteur primaire (ou un drain) : **l'env gagne
      toujours**, sinon une écriture en base re-tenanterait les comptes existants ;
    - un slug invalide (vide, majuscules, `:`…) : il entre dans le sub ;
    - un émetteur déjà tenu : la sélection par `iss` deviendrait ambiguë.
    """
    entries: dict[str, TenantIssuer] = {}

    def _put(slug: str, issuer, jwks_uri=None) -> None:
        iss = normalize_issuer(issuer)
        if not iss:
            return
        jwks = (jwks_uri or "").strip() if isinstance(jwks_uri, str) else ""
        entries[iss] = TenantIssuer(slug=slug, issuer=iss,
                                    jwks_uri=jwks or f"{iss}/jwks")

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
            logger.warning(
                "registre d'émetteurs : slug %r refusé (invalide, ou réservé au "
                "tenant de la plateforme dont l'émetteur vient de l'env) — %s ignoré",
                slug, iss)
            continue
        if iss in entries:
            logger.warning(
                "registre d'émetteurs : %s réclamé par le tenant %r alors qu'il est "
                "déjà tenu par %r — ligne ignorée", iss, slug, entries[iss].slug)
            continue
        _put(slug, iss, (row or {}).get("jwks_uri"))
    return entries


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

    def entries(self) -> tuple:
        return tuple(self._by_issuer.values())

    def get(self, issuer) -> Optional[TenantIssuer]:
        return self._by_issuer.get(normalize_issuer(issuer) or "")

    def slug_for(self, issuer) -> str:
        """Tenant d'un émetteur. Inconnu ⟹ `oto` : le verifier primaire tranchera,
        et il rejettera (l'`iss` ne correspond pas au sien)."""
        entry = self.get(issuer)
        return entry.slug if entry else PRIMARY_SLUG

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
    registre ci-dessus, et ça marche), mais pas à **agir dans son annuaire** : la
    table `tenants` porte `slug`/`issuer`/`jwks_uri`/`hosts` et **aucun credential de
    management**. Ce qui manque n'est pas l'information du tenant, ce sont les clés de
    la maison du partenaire — question ouverte, à trancher au lot de provisioning
    quand un partenaire en aura besoin (oto-backend#274).

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
            f"{PRIMARY_SLUG!r}. Il n'existe pas dans notre annuaire Logto, et nous "
            f"n'avons aucun credential de management sur l'émetteur de {slug!r} — "
            f"cet acte doit être porté par le tenant propriétaire du compte.")
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
        logger.warning("registre d'émetteurs : lecture des tenants impossible — "
                       "seul l'émetteur de l'env est accepté", exc_info=True)
        return []
