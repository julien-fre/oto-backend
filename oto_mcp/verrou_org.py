"""Le verrou d'org d'un jeton de délégation : un travail du runner agit dans l'org de
son travail, et dans aucune autre.

Un travail programmé s'exécute au nom de son porteur (`capabilities/runner_jobs._delegue`
émet un jeton `kind="delegation"`). Le porteur peut être membre de plusieurs orgs ; son
travail, lui, appartient à UNE org. Le jeton porte désormais cette org, posée à
l'émission (`user_api_tokens.verrou_org*`), et toute résolution qui viserait une autre org
pour le porteur est refusée :

- `roles.effective_org_role` (le seam unique derrière `is_org_member`, `is_org_admin` et
  `ownership.can_access`) ne rend aucun rôle hors de l'org du travail — AVANT l'escalade
  plateforme ;
- `session_org.set_call_org`, où aboutit tout jeton d'appel qui pose une org (`_org`,
  `_project`, `_group`, `_instance`), refuse une autre org avec un code nommé ;
- `access.current_org` rend l'org du travail en premier, jamais la maison du porteur ;
- `ownership.accessor_scope` borne les partages reçus à l'org du travail.

**Le porteur seul est concerné** (`sub` = celui du jeton) : une lecture sur un TIERS
(membres d'une org, rôle d'un autre compte) garde son chemin d'avant.

**Une seule source par requête** : une ContextVar posée au bord — l'authentification
REST (`api/base._authenticate`) et l'`on_request` MCP (`middleware/verrou_org.py`). Sans
elle, rien n'est verrouillé : un jeton humain ou d'API n'en porte jamais.

`OTO_VERROU_ORG_DELEGATION` : `enforce` (défaut), `report` (journalise ce qui serait
refusé et laisse passer) ou `off` — un retour arrière sans déploiement.

**Les rôles PLATEFORME du porteur** (10/10/2026) : le verrou d'org ne bornait que le
rôle d'ORG ; `super_admin`/`admin` restaient les siens sous le jeton, et avec eux toute
garde plateforme (`PLATFORM_ADMIN`, `SUPER_ADMIN`, l'escalade `roles.is_platform_admin`,
la visibilité des `oto_admin_*`, la gouvernance de `ownership`). Sous un jeton de
délégation, un agent hébergé n'est pas l'opérateur : `role_plateforme` ramène ces rôles
à `member` — UN seul point, `access.scope.role_effectif`, d'où tous les prédicats
plateforme dérivent. Réglage PROPRE, `OTO_VERROU_ROLES_PLATEFORME` : `enforce`,
`report` (DÉFAUT : journalise ce qui serait retiré, ne retire rien — des agents
hébergés de comptes admin servent peut-être des outils d'admin, à relever avant de
mordre) ou `off`. Indépendant du verrou d'org : couper l'un ne coupe pas l'autre.
`OTO_VERROU_ROLES_PLATEFORME_ORGS` (ids d'org, virgules) EXEMPTE de ce verrou les travaux
de ces orgs — celle de l'opérateur, dont les agents hébergés servent des `oto_admin_*` :
leur porteur garde son rôle plateforme, quel que soit le mode. Vide = aucune exemption.
"""
from __future__ import annotations

import contextvars
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

#: Le code nommé d'un refus. Testé par les appelants, jamais la phrase.
CODE = "org_out_of_job"
_ENV = "OTO_VERROU_ORG_DELEGATION"
_MODES = ("enforce", "report", "off")


@dataclass(frozen=True)
class Verrou:
    """Le porteur du jeton, l'org de son travail (None = aucune org : portée
    personnelle seule) et le travail, pour le journal."""
    sub: str
    org_id: Optional[int]
    job_id: Optional[int] = None


class HorsVerrou(Exception):
    """Une résolution vise une org hors du travail. Les adaptateurs traduisent."""

    def __init__(self, org_id: object, verrou: Verrou):
        self.org_id, self.verrou = org_id, verrou
        ici = (f"org {verrou.org_id}" if verrou.org_id is not None
               else "no org (personal scope only)")
        super().__init__(
            f"This hosted agent runs for its job's {ici}; org {org_id} is out of its "
            "scope. Its calls cannot target another organization.")


_COURANT: contextvars.ContextVar[Optional[Verrou]] = contextvars.ContextVar(
    "oto_verrou_org", default=None)

_ENV_ROLES = "OTO_VERROU_ROLES_PLATEFORME"
_ENV_ROLES_ORGS = "OTO_VERROU_ROLES_PLATEFORME_ORGS"
#: Les rôles plateforme qu'un jeton de délégation ne porte pas (`access.scope`).
_ROLES_PLATEFORME = ("admin", "super_admin")
#: Le verrou dont le rôle plateforme a déjà été signalé en `report` : une ligne par
#: requête, pas une par prédicat — `get_user_role` est lu des dizaines de fois.
_ROLE_SIGNALE: contextvars.ContextVar[Optional[Verrou]] = contextvars.ContextVar(
    "oto_verrou_role_signale", default=None)


def poser(verrou: Optional[Verrou]) -> contextvars.Token:
    """Pose le verrou de la requête — y compris à None (jeton humain, JWT) pour
    qu'aucun verrou ne survive à sa requête."""
    return _COURANT.set(verrou)


def lever(jeton: contextvars.Token) -> None:
    _COURANT.reset(jeton)


def depuis_ligne(row: Optional[dict]) -> Optional[Verrou]:
    """Le verrou porté par une ligne de jeton (`db.verify_api_token`), ou None."""
    if not row or not row.get("verrou_org"):
        return None
    org = row.get("verrou_org_id")
    job = row.get("job_id")
    return Verrou(sub=str(row["sub"]), org_id=int(org) if org is not None else None,
                  job_id=int(job) if job is not None else None)


def mode() -> str:
    """Lu à chaque appel : un basculement d'environnement mord au redémarrage suivant
    sans code. Une valeur inconnue vaut `enforce` — le mode sûr."""
    m = (os.environ.get(_ENV) or "enforce").strip().lower()
    return m if m in _MODES else "enforce"


def courant() -> Optional[Verrou]:
    """Le verrou actif, ou None (aucun, ou désactivé)."""
    v = _COURANT.get()
    if v is None or mode() == "off":
        return None
    return v


def travail_de_la_requete() -> Optional[Verrou]:
    """Le verrou posé pour la requête, QUEL QUE SOIT le mode du verrou d'org : il dit quel
    travail porte le jeton. La liste d'outils de ce travail (`outils_du_travail`) n'est pas
    le verrou d'org et ne se coupe pas avec lui (`OTO_VERROU_ORG_DELEGATION=off`)."""
    return _COURANT.get()


def borne(sub: object, *, route: str, ecart: bool) -> Optional[Verrou]:
    """Le verrou à APPLIQUER pour ce porteur — None pour un tiers, sans verrou, ou hors
    `enforce`. Pour les points qui bornent au lieu de refuser (`current_org`,
    `accessor_scope`) : en `report`, `ecart` dit si le résultat aurait changé, et
    seul ce cas est journalisé."""
    v = courant()
    if v is None or sub is None or str(sub) != v.sub:
        return None
    if mode() == "report":
        if ecart:
            logger.warning("verrou d'org (report) : %s aurait été borné à l'org %s "
                           "(travail %s)", route, v.org_id, v.job_id)
        return None
    return v


def hors(sub: Optional[str], org_id: object, *, route: str) -> bool:
    """`org_id` est-elle hors de l'org du travail, pour CE porteur ? En `report`, le dit
    au journal et rend False. Un tiers (`sub` ≠ porteur) n'est jamais concerné."""
    v = courant()
    if v is None or sub is None or str(sub) != v.sub:
        return False
    try:
        cible = int(org_id) if org_id is not None else None
    except (TypeError, ValueError):
        cible = None
    if cible is not None and cible == v.org_id:
        return False
    if cible is None and v.org_id is None:
        return False
    if mode() == "report":
        logger.warning("verrou d'org (report) : %s viserait l'org %s hors du travail %s "
                       "(org %s)", route, cible, v.job_id, v.org_id)
        return False
    logger.warning("verrou d'org : %s refusé, org %s hors du travail %s (org %s)",
                   route, cible, v.job_id, v.org_id)
    return True


def interdit(sub: Optional[str], *, route: str) -> bool:
    """Un geste interdit au porteur verrouillé QUELLE QUE SOIT l'org visée : changer
    l'org maison de l'humain (`org.set_home`, `org.clear`, `group.set_home`, la face REST
    de `group.use`) ou le faire quitter une org (`me.leave_org`). Le jeton porte une org de
    travail ; il ne déplace pas le défaut de la personne. En `report`, le dit au journal
    et rend False. Un tiers n'est jamais concerné."""
    v = courant()
    if v is None or sub is None or str(sub) != v.sub:
        return False
    if mode() == "report":
        logger.warning("verrou d'org (report) : %s aurait été refusé (travail %s)",
                       route, v.job_id)
        return False
    logger.warning("verrou d'org : %s refusé sous le travail %s (org %s)",
                   route, v.job_id, v.org_id)
    return True


def exiger(sub: Optional[str], org_id: object, *, route: str) -> None:
    """Lève `HorsVerrou` si `org_id` est hors de l'org du travail du porteur."""
    if hors(sub, org_id, route=route):
        raise HorsVerrou(org_id, courant())  # type: ignore[arg-type]


def mode_roles() -> str:
    """Le mode du verrou des rôles plateforme, lu à chaque appel. Une valeur inconnue
    vaut `report` — le défaut : on n'a pas encore relevé ce qu'`enforce` couperait."""
    m = (os.environ.get(_ENV_ROLES) or "report").strip().lower()
    return m if m in _MODES else "report"


def orgs_exemptees() -> frozenset[int]:
    """Les orgs dont les travaux gardent le rôle plateforme de leur porteur, lues à
    chaque appel. Un élément qui n'est pas un entier est ignoré et journalisé : il
    n'exempte rien — fail-closed."""
    orgs: set[int] = set()
    for brut in (os.environ.get(_ENV_ROLES_ORGS) or "").split(","):
        brut = brut.strip()
        if not brut:
            continue
        try:
            orgs.add(int(brut))
        except ValueError:
            logger.warning("%s : %r n'est pas un id d'org, ignoré", _ENV_ROLES_ORGS, brut)
    return frozenset(orgs)


def role_plateforme(sub: Optional[str], role: str) -> str:
    """Le rôle plateforme de `sub` sous la requête courante : `member` à la place
    d'`admin`/`super_admin` pour le PORTEUR d'un jeton de délégation, en `enforce` —
    sauf si l'org de son travail est exemptée (`orgs_exemptees`), ni retiré ni journalisé.

    Lit la ContextVar BRUTE, pas `courant()` : `OTO_VERROU_ORG_DELEGATION=off` ne doit
    pas rendre au porteur ses rôles plateforme — chaque réglage coupe son verrou. Un
    tiers (`sub` ≠ porteur) n'est jamais concerné : lister les opérateurs d'une org
    reste exact."""
    if role not in _ROLES_PLATEFORME:
        return role
    v = _COURANT.get()
    if v is None or sub is None or str(sub) != v.sub:
        return role
    if v.org_id is not None and v.org_id in orgs_exemptees():
        return role
    m = mode_roles()
    if m == "off":
        return role
    if m == "report":
        if _ROLE_SIGNALE.get() is not v:
            _ROLE_SIGNALE.set(v)
            logger.warning("verrou des rôles plateforme (report) : le porteur du travail "
                           "%s (org %s) agit avec le rôle `%s` — `enforce` le lirait "
                           "`member`", v.job_id, v.org_id, role)
        return role
    return "member"
