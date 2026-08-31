"""Les deux empreintes courtes d'un secret — celle du journal, celle du coffre.

Un secret n'apparaît hors du serveur que sous une forme courte et non inversible.
Ce module en porte les DEUX, et la clé unique qui les signe :

- `mask()` — ce que le **journal** écrit à la place d'un jeton (`tool`, `args`).
  Volontairement **corrélable** : deux lignes portant le même masque disent « le même
  jeton, rejoué », sans jamais dire lequel.
- `fingerprint()` — ce que la **lecture d'un credential** rend à la place de la valeur
  (oto-backend#671, 2026-08-31). Volontairement **NON corrélable** : elle est liée à
  la ligne du coffre, donc la même clé posée à deux endroits n'y donne pas la même
  empreinte. C'est ce qui empêche un lecteur d'empreintes de confirmer une clé devinée
  par ailleurs en la posant sur une ligne qu'il contrôle.

Une seule clé pour les deux : un second secret à gérer n'apporterait rien, et les deux
propriétés se lisent mieux côte à côte que dans deux modules.

## Ce que le journal d'appels n'écrit jamais en clair — et pourquoi par PROPRIÉTÉ

Le journal (`tool_calls`, ADR 0017) porte deux colonnes alimentées par des données
d'appelant : `tool` (pour un geste REST : `MÉTHODE /route`) et `args`. Jusqu'au
2026-08-29, la réduction de `tool` (`api/routes._normalize_route`) était une
**allowlist de FORMES** — numérique ou UUID → `:id`, tout le reste passe. Or quatre
routes servies portent leur secret DANS le chemin (`/api/upload/{token}`,
`/api/public/docs/{token}`, `/api/invitations/{token}`, `/api/invitations/code/{code}`),
et aucun de ces secrets n'a la forme d'un identifiant : ils partaient donc en clair
dans une table lue par les surfaces de supervision (#558). Pour l'invitation, le
modèle de données refuse explicitement de persister le jeton en clair
(`org_store/invitations.py` n'enregistre que son empreinte) — un middleware
transverse défaisait cette précaution.

**La propriété qui remplace la forme** : un segment lié à un PARAMÈTRE DE ROUTE dont
le nom est déclaré secret ici est réduit, quelle que soit son allure. La liste des
routes concernées n'est écrite nulle part : elle est DÉRIVÉE de la table servie
(`api/routes.make_routes` appelle `declare_routes`), donc une route future qui
déclare `{token}` est couverte le jour où elle est montée.

La même propriété vaut sur l'autre face : le jeton d'invitation passe aussi par
l'outil `oto_org op=accept_invite`, dont l'`Input` déclare les mêmes noms. Un
argument de CAPACITÉ portant un de ces noms est masqué ; un argument de CONNECTEUR
qui s'appelle pareil ne l'est pas (`droit_article(code='CT')` n'est pas un secret,
et un journal qui le cache coûte une lecture sans rien protéger).

⚠️ **Le masque est un HMAC, pas « les 8 derniers » ni un sha256 nu.** Un code
d'invitation fait 7 caractères sur un alphabet de 30 (~34 bits) : garder ses 8
derniers caractères le rendrait ENTIER, et un sha256 nu se retrouve par force brute
en quelques secondes pour qui lit le journal. La clé est celle qui signe déjà les
jetons d'upload (`OTO_MCP_OAUTH_STATE_SECRET`) — le masque reste donc stable d'un
boot à l'autre, ce qui est tout son intérêt : deux lignes portant le même masque
disent « le même jeton, rejoué », sans jamais dire lequel.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets as _secrets
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Les NOMS de paramètre qui portent un secret. C'est la seule liste écrite à la
# main de ce module, et elle est volontairement courte : tout le reste (quelles
# routes, quels outils) en est dérivé.
SECRET_PARAM_NAMES = frozenset({"token", "code"})

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Forme d'une route déclarant un secret : (gabarit, {index → nom du paramètre}).
# Le gabarit est la découpe du patron, un paramètre valant None (joker).
_SECRET_ROUTES: list[tuple[tuple[Optional[str], ...], dict[int, str]]] = []

_KEY: Optional[bytes] = None


# --------------------------------------------------------------------------- #
# Le masque
# --------------------------------------------------------------------------- #

def _key() -> bytes:
    """Clé du masque. La même que celle des jetons signés — un secret de plus à
    gérer n'ajouterait rien, et un masque non clé serait inversible (cf. module).
    Sans aucun secret d'environnement (dev, tests), une clé de processus : le
    masque reste correct, il cesse seulement d'être corrélable entre deux boots."""
    global _KEY
    if _KEY is None:
        brut = (os.environ.get("OTO_MCP_OAUTH_STATE_SECRET")
                or os.environ.get("OTO_MCP_MASTER_KEY") or "")
        _KEY = brut.encode() if brut else _secrets.token_bytes(32)
    return _KEY


def mask(value) -> str:
    """Empreinte courte et NON INVERSIBLE d'un secret — corrélable, jamais lisible."""
    digest = hmac.new(_key(), str(value).encode("utf-8", "replace"),
                      hashlib.sha256).hexdigest()
    return "#" + digest[:12]


def fingerprint(*parts: object) -> str:
    """Empreinte de RECONNAISSANCE d'un secret posé : quatre caractères hexadécimaux,
    non inversibles, et **liés à l'endroit** où le secret est rangé.

    Elle répond à « est-ce toujours la même clé qu'hier ? » et « celle-ci ou l'autre ? »
    sans rendre un seul caractère du secret. Le front l'affiche `•••• 3f7a`.

    ⚠️ **Ce ne sont pas les derniers caractères de la clé.** Un suffixe DE LA CLÉ est un
    morceau de secret : il identifie un compte chez le fournisseur, et il confirme une
    clé devinée par ailleurs. Ici, quatre caractères d'un HMAC dont la clé ne sort pas
    du serveur — indevinable hors ligne.

    ⚠️ **Passer la LIGNE du coffre en premier, la valeur en dernier.** Sans les
    coordonnées de la ligne, la même clé donnerait la même empreinte partout : qui lit
    l'empreinte d'un palier pourrait poser un candidat sur une ligne à lui et comparer —
    un oracle de confirmation à 1/65536. Liée à sa ligne, la seule façon de comparer est
    d'écraser la clé qu'on cherchait à confirmer.

    Résiduel assumé et borné : sur une MÊME ligne, quatre caractères laissent une chance
    sur 65536 de collision — un lecteur peut donc croire inchangée une clé qui a été
    rotée. C'est un défaut d'affichage, pas de confidentialité ; la source de vérité de
    « quand a-t-elle changé » reste la date de pose servie à côté.
    """
    charge = "\x1f".join(str(p) for p in parts)
    return hmac.new(_key(), charge.encode("utf-8", "replace"),
                    hashlib.sha256).hexdigest()[-4:]


# --------------------------------------------------------------------------- #
# Les routes : la déclaration, dérivée de la table servie
# --------------------------------------------------------------------------- #

def declare_routes(routes: Iterable) -> int:
    """Recense les routes dont un paramètre est déclaré secret. Appelée par
    `api/routes.make_routes` sur la table qu'elle sert — jamais sur une liste
    tenue à la main. Rend le nombre de routes retenues.

    Accepte des routes Starlette (attribut `path`) ou des patrons bruts.
    """
    trouve: list[tuple[tuple[Optional[str], ...], dict[int, str]]] = []
    vus: set[tuple] = set()
    for route in routes:
        patron = getattr(route, "path", route)
        if not isinstance(patron, str):
            continue
        gabarit: list[Optional[str]] = []
        secrets_a: dict[int, str] = {}
        for i, seg in enumerate(patron.split("/")):
            if seg.startswith("{") and seg.endswith("}"):
                nom = seg[1:-1].split(":", 1)[0]
                gabarit.append(None)
                if nom in SECRET_PARAM_NAMES:
                    secrets_a[i] = nom
            else:
                gabarit.append(seg)
        if secrets_a and tuple(gabarit) not in vus:
            vus.add(tuple(gabarit))
            trouve.append((tuple(gabarit), secrets_a))
    # Le plus SPÉCIFIQUE d'abord : `/api/invitations/code/{code}` doit primer sur
    # `/api/invitations/{token}`, sinon le code court serait lu comme un jeton et
    # la route perdrait son nom dans l'agrégation.
    trouve.sort(key=lambda e: len(e[0]), reverse=True)
    _SECRET_ROUTES[:] = trouve
    return len(trouve)


def declared_secret_routes() -> list[tuple[tuple[Optional[str], ...], dict[int, str]]]:
    """Ce qui est déclaré, pour les tests et la maintenance."""
    return list(_SECRET_ROUTES)


def _secret_indices(segments: list[str]) -> dict[int, str]:
    """Indices de segments porteurs d'un secret, d'après la route la plus spécifique.

    Le gabarit peut être PLUS COURT que le chemin : un 404 sur
    `/api/upload/<jeton>/x` n'atteint aucun handler mais est journalisé comme tout
    `/api/*` — son jeton doit tomber aussi."""
    for gabarit, secrets_a in _SECRET_ROUTES:   # déjà triés du plus long au plus court
        if len(segments) < len(gabarit):
            continue
        if all(g is None or g == segments[i] for i, g in enumerate(gabarit)):
            return secrets_a
    return {}


def route_and_secrets(path: str) -> tuple[str, Optional[dict[str, str]]]:
    """`(route réduite, {nom: masque})` — ce que le journal écrit d'un chemin REST.

    La route réduite ne porte JAMAIS le masque : `tool` sert l'agrégation du
    monitoring (un `GROUP BY tool`), et y mettre une empreinte par jeton ferait
    exploser sa cardinalité. Le masque part dans `args`, où il répond à la seule
    question qu'on se pose vraiment : « le même jeton a-t-il été rejoué ? »"""
    segments = path.split("/")
    secrets_a = _secret_indices(segments)
    reduit, masques = [], {}
    for i, seg in enumerate(segments):
        nom = secrets_a.get(i)
        if nom:
            reduit.append(":" + nom)
            if seg:
                masques[nom] = mask(seg)
        elif seg.isdigit() or _UUID_RE.match(seg):
            # La réduction PAR FORME d'avant #558 : conservée telle quelle, sans
            # quoi l'agrégation du monitoring changerait de vocabulaire et les
            # séries seraient coupées en deux.
            reduit.append(":id")
        else:
            reduit.append(seg)
    # Un chemin plus long que le gabarit : la queue est de la donnée d'appelant
    # sans route, on la coupe plutôt que de la recopier.
    if secrets_a:
        fin = max(secrets_a) + 1
        reduit = reduit[:fin]
    return "/".join(reduit), (masques or None)


# --------------------------------------------------------------------------- #
# Les arguments d'outil : la même propriété, sur l'autre face
# --------------------------------------------------------------------------- #

_ARG_NAMES: Optional[dict[str, frozenset]] = None


def _arg_names() -> dict[str, frozenset]:
    """`{outil MCP → noms d'arguments secrets}`, dérivé du registre de capacités.

    Seules les CAPACITÉS sont couvertes : ce sont les surfaces de la plateforme,
    et `{token}`/`{code}` y désignent toujours le même objet que dans les routes.
    Un connecteur qui expose un `code` métier (`droit_article`, `stripe_*`) n'est
    pas concerné — masquer par le nom seul y coûterait une lecture pour rien."""
    global _ARG_NAMES
    if _ARG_NAMES is None:
        table: dict[str, frozenset] = {}
        try:
            import oto_mcp.capabilities  # noqa: F401 — peuple le registre
            from oto_mcp.capabilities.registry import caps_with_mcp
            for cap in caps_with_mcp():
                champs = set(getattr(cap.Input, "model_fields", {}) or {})
                secrets_ = champs & SECRET_PARAM_NAMES
                if secrets_:
                    table[cap.mcp] = frozenset(secrets_)
        except Exception:  # noqa: BLE001 — le journal ne casse jamais le service
            # Bruyant, et une seule fois : sans registre, le masquage des arguments
            # est INERTE. Un journal qui cesse de masquer sans le dire est
            # exactement le mode d'échec que ce module ferme.
            logger.warning("registre de capacités illisible : le masquage des "
                           "arguments du journal est inactif", exc_info=True)
            _ARG_NAMES = {}
            return _ARG_NAMES
        _ARG_NAMES = table
    return _ARG_NAMES


def secret_arg_names_by_tool() -> dict:
    """La table complète `{outil → champs secrets}` — pour la purge rétroactive."""
    return dict(_arg_names())


def secret_arg_names(tool: Optional[str]) -> frozenset:
    """Noms d'arguments qu'un outil ne doit pas laisser passer en clair."""
    if not tool:
        return frozenset()
    return _arg_names().get(tool, frozenset())


# --------------------------------------------------------------------------- #
# La purge rétroactive (ADR 0065 — un travail de maintenance, pas un boot)
# --------------------------------------------------------------------------- #

def journal_purge_plans() -> list[tuple[str, str, list[str]]]:
    """Ce qu'il y a à réparer dans les lignes DÉJÀ écrites, une entrée par route :
    `(préfixe littéral, route réduite, préfixes plus spécifiques à exclure)`.

    Dérivé de la même déclaration que le masquage à l'écriture — pas d'une seconde
    liste qui divergerait. L'exclusion est ce qui empêche la passe générique
    (`/api/invitations/`) d'écraser ce que la passe spécifique
    (`/api/invitations/code/`) vient de réduire."""
    plans = []
    prefixes = []
    for gabarit, secrets_a in _SECRET_ROUTES:
        premier = min(secrets_a)
        if any(g is None for g in gabarit[:premier]):
            continue  # secret précédé d'un joker : pas de préfixe littéral (aucun cas ce jour)
        prefixe = "/".join(gabarit[:premier]) + "/"
        reduit = prefixe + ":" + secrets_a[premier]
        prefixes.append(prefixe)
        plans.append((prefixe, reduit))
    return [(p, r, [q for q in prefixes if q != p and q.startswith(p)])
            for p, r in plans]
