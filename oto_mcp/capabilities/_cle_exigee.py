"""Un travail hébergé tourne sur la clé de modèle de SON org — ou ne tourne pas.

Sans cette garde, une org qui n'a pas déposé de clé ne se voit rien refuser : le
worker retombe sur la clé de SON environnement, c'est-à-dire la nôtre (oto-runner,
`agent_llm.complete` : `api_key or resolve_key()`). « Chaque client paie avec sa
propre clé » était donc une POLITIQUE que le système n'appliquait pas — et le
défaut est silencieux par construction : les runs aboutissent, seule la facture
change de destinataire.

⚠️ **Un réglage, pas une constante, et éteint par défaut.** Le poser en dur aurait
arrêté, le jour du déploiement, tous les agents programmés des orgs qui n'ont pas
encore déposé de clé — les nôtres comprises. L'ordre qui ne casse personne est :
déposer la clé dans nos orgs, relever les orgs clientes qui ont des agents vivants
sans clé, PUIS allumer. Le réglage vit dans `connector_settings`, que la console
admin écrit déjà (`oto_admin_connector_setting`) :

```
connector=anthropic  key=runner.org_key_required  value=true             plateforme
connector=anthropic  key=runner.org_key_required  value=false  org_id=N  exemption d'une org
```

L'org l'emporte sur la plateforme. Par CONNECTEUR, parce que la clé exigée est
celle du fournisseur que le worker sert : exiger une clé Mistral d'une org servie
par des workers Anthropic n'aurait aucun sens.

⚠️ **Deux moments, un seul verdict.** À la POSE d'un agent (création, rallumage,
armement d'un passage) : refus lisible, `model_key_required`, au moment où l'on
peut encore déposer la clé. À la RÉSERVATION : arrêt DÉFINITIF du travail, avec sa
raison écrite. Le premier est un confort ; le second est la garantie — une clé
retirée après la pose, ou un agent posé avant l'allumage, n'échappe pas au second.
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import credentials_store, providers
from ..db import connector_settings
from ._types import AuthzDenied

logger = logging.getLogger(__name__)

#: La clé du réglage dans `connector_settings`.
CLE_REGLAGE = "runner.org_key_required"
VRAI, FAUX = "true", "false"


def fournisseurs_de_modele() -> list[str]:
    """Les connecteurs qui portent une clé de MODÈLE — ceux du type `credential`.
    Dérivés du registre, jamais recopiés : un fournisseur ajouté demain est couvert
    sans toucher à cette garde."""
    return sorted(c.name for c in providers.REGISTRY.values()
                  if getattr(c, "kind", None) == "credential")


def cle_exigee(org_id: int, fournisseur: str) -> bool:
    """L'org doit-elle fournir SA clé `fournisseur` pour qu'un agent tourne ?

    L'org d'abord (une exemption, ou une exigence posée avant la plateforme), puis
    la plateforme, puis FAUX — le comportement d'avant."""
    for portee, ident in (("org", str(org_id)), ("platform", "platform")):
        # Par le MODULE : `connector_settings` n'est pas aplati dans la façade `db`.
        v = connector_settings.get_connector_setting(portee, ident, fournisseur, CLE_REGLAGE)
        if v is not None:
            return v == VRAI
    return False


def cle_deposee(org_id: int, fournisseur: str) -> bool:
    """Présence du dépôt — sans déchiffrer (la ligne du coffre existe), sur le
    mono-compte, exactement ce que la remise au worker lit. Une lecture qui LÈVE
    se lit « non déposée » et le dit : pour une garde d'argent, le doute arrête le
    travail plutôt que de le faire payer par nous."""
    try:
        return credentials_store.has_credential("org", str(org_id), fournisseur,
                                                account="")
    except Exception:
        logger.warning("présence de la clé `%s` illisible pour l'org %s — lue comme "
                       "absente", fournisseur, org_id, exc_info=True)
        return False


#: Les rangs d'une clé PARTAGÉE : payée par le tenant ou la plateforme, jamais par
#: l'org — donc refacturée (`runner_jobs._mesurer_cle_partagee`).
RANGS_PARTAGES = (credentials_store.TENANT, credentials_store.PLATFORM)


class CleIllisible(RuntimeError):
    """Le coffre n'a pas répondu : on ne SAIT pas si une clé partagée sert cette org.
    Levée seulement à qui la demande (`strict=True`) — la réservation, qui ne doit ni
    servir ni arrêter un travail sur un doute."""


def cle_partagee(org_id: int, fournisseur: str, sub: Optional[str] = None, *,
                 strict: bool = False):
    """La clé PARTAGÉE (tenant ou plateforme) qui sert `fournisseur` à cette org, ou
    None. Rend le `ResolvedCredential` gagnant : sa clé et son rang (`mode`).

    ⚠️ **Seulement un fournisseur qui DÉCLARE une clé de plateforme** (`platform` dans
    ses `auth_modes`) : c'est la déclaration qu'une clé partagée de ce fournisseur se
    sert, et se refacture. Sans elle, une clé posée au tenant pour un autre usage
    partirait payer des travaux que rien ne mesure.

    ⚠️ **Fermée** (`platform_key_open=False`) : la clé de plateforme ne sert que les
    orgs à qui elle est accordée — c'est le DROIT. Le retirer (le grant de l'org)
    coupe l'org dès la réservation suivante, ses agents compris.

    `sub` = le demandeur du travail : la cascade complète, à son nom et dans l'org du
    travail (son tenant compris). Sans lui — un écran, la pose d'un agent, ou un
    travail d'avant le 02/09/2026 —, la cascade de l'ORG seule (l'arête tenant→org,
    puis la plateforme), qui ne lit le tenant que par une arête vivante.

    Le doute se lit « pas de clé partagée » et le dit : pour une garde d'argent, un
    refus vaut mieux qu'une facture qui change de destinataire. Avec `strict`, il
    LÈVE `CleIllisible` : la réservation rend alors le travail à la file plutôt que
    de l'arrêter sur une raison fausse."""
    from .. import access, session_org
    from ..auth import hooks as auth_hooks
    from ..mcp_errors import McpError
    c = providers.connector_for_provider(fournisseur)
    if c is None or "platform" not in c.auth_modes:
        return None
    try:
        if sub:
            with auth_hooks.sub_override(sub):
                jeton = session_org.set_call_org(int(org_id))
                try:
                    rc = access.resolve_credential(fournisseur, sub=sub,
                                                   emit_on_failure=False,
                                                   check_usage=False)
                finally:
                    session_org.reset_call_org(jeton)
        else:
            rc = access._resolve_credential_anon(fournisseur, "auto", int(org_id),
                                                 check_usage=False)
    except McpError:
        return None
    except Exception as e:
        logger.warning("clé partagée `%s` illisible pour l'org %s — %s", fournisseur,
                       org_id, "doute rendu à l'appelant" if strict else "lue comme absente",
                       exc_info=True)
        if strict:
            raise CleIllisible(str(e)) from e
        return None
    return rc if rc.mode in RANGS_PARTAGES else None


def cle_de_plateforme_posee(fournisseur: str) -> Optional[bool]:
    """L'instance a-t-elle POSÉ une clé partagée de ce fournisseur au rang plateforme ?
    C'est le signe qu'elle paie ce fournisseur, et qu'un travail sans clé ne doit
    plus tourner sur celle de l'environnement d'un worker (`runner_jobs._avec_cle`).

    `None` = on ne sait pas (le coffre n'a pas répondu), et le dit : la réservation
    ne sert alors rien et n'arrête rien — le travail retourne à la file."""
    c = providers.connector_for_provider(fournisseur)
    if c is None or "platform" not in c.auth_modes:
        return False
    try:
        return bool(credentials_store.list_platform_credentials(fournisseur))
    except Exception:
        logger.warning("clés de plateforme `%s` illisibles — doute rendu à l'appelant",
                       fournisseur, exc_info=True)
        return None


def raison_sans_acces(fournisseur: str) -> str:
    return (f"cette organisation n'a pas déposé de clé `{fournisseur}`, et la clé partagée "
            "de la plateforme ne lui est pas ouverte — jamais accordée, ou retirée. Deux "
            "issues : déposer sa propre clé (Connecteurs), ou demander l'accès à qui "
            "administre la clé partagée de cette instance. L'agent repartira à sa "
            "prochaine échéance.")


def manquantes(org_id: int, fournisseurs: Optional[list[str]] = None) -> list[str]:
    """Les fournisseurs dont une clé est EXIGÉE et que l'org n'a ni déposée, ni reçue
    en partage (`cle_partagee` : une clé de plateforme accordée paie, et se
    refacture — l'org n'a rien à déposer)."""
    return [f for f in (fournisseurs or fournisseurs_de_modele())
            if cle_exigee(org_id, f) and not cle_deposee(org_id, f)
            and cle_partagee(org_id, f) is None]


def raison_du_refus(fournisseurs: list[str]) -> str:
    noms = ", ".join(f"`{f}`" for f in fournisseurs)
    return (f"cette organisation n'a pas déposé sa clé de modèle ({noms}). Les agents "
            "hébergés tournent sur la clé de l'organisation qui les demande ; sans "
            "elle, ils ne tournent pas. Dépose la clé (Connecteurs, ou la fiche de "
            "l'agent), puis rallume l'agent. Si le besoin est une tâche planifiée de "
            "l'utilisateur, ce n'est pas le bon outil : voir le guide "
            "`procedure-en-routine`.")


def exiger_a_la_pose(org_id: int, famille: Optional[str] = None) -> None:
    """Le refus LISIBLE, au moment de poser un agent. Lève `model_key_required`.

    `famille` = celle du modèle DÉCLARÉ sur cet agent, telle que
    `runner_models.famille`/`_modele.famille_declaree` la rend. **Seule elle est
    vérifiée** — jamais toutes les familles exigées à la fois.

    ⚠️ **Corrigé le 14/09/2026 — la lecture d'avant regardait TOUTES les familles
    exigées, pas celle du modèle posé.** Le raisonnement tenu alors (« à la pose,
    on ne sait pas encore quel worker réservera le travail ») ne tenait déjà plus
    depuis que le modèle se DÉCLARE sur l'agent (12/09/2026, oto#81) : quand il est
    posé, sa famille est CONNUE, et c'est la seule que la réservation demandera.
    Mesuré en production le 14/09 : `runner.org_key_required=true` posé pour
    `anthropic` a refusé la pose de flottes déclarées `mistral-large-2512`, dans
    une org qui n'avait — à raison — déposé aucune clé Anthropic.

    `famille=None` (modèle hors catalogue) : **rien n'est exigé ici**. Un agent
    SANS modèle n'arrive plus jusqu'ici : il est refusé à la pose
    (`_modele.exige_un_modele`, 24/09/2026) et arrêté à la réservation
    (`runner_jobs._avec_cle`) — sans cela il tournait sur le modèle du worker,
    donc sur notre clé, sans qu'aucune clé d'org ne soit en jeu.

    ⚠️ La garde à la RÉSERVATION (`refuser_pour_identite`, arrêt définitif) reste
    la vraie garantie et n'est PAS touchée par ce lot : elle juge le travail
    produit, avec sa famille déjà écrite dedans — jamais un ensemble de familles
    « peut-être demandées »."""
    if not famille:
        return
    absentes = manquantes(org_id, [famille])
    if absentes:
        raise AuthzDenied(400, "model_key_required", raison_du_refus(absentes))
