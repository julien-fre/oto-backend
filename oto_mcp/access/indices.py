"""Les INDICES des refus « rien ne résout » — ce qu'un refus de credential ajoute
pour que l'agent sache quoi faire, plutôt qu'un « pas de clé » sec.

Extrait de `rbac` le 24/09/2026 (oto-backend#499), quand le second indice de projet
l'a fait passer la borne de taille du package : `rbac` dit qui a le DROIT et énumère
ce qui est à portée ; ce module-ci en fait du TEXTE pour un refus déjà levé.

Deux propriétés communes, qui sont la raison de les tenir ensemble :

- **lecture seule, jamais une résolution** : on nomme un geste (`group=`, `_project=`),
  l'agent le pose — rien ne bascule un appel en silence sur les frais d'autrui ;
- **fail-soft** : un indice s'ajoute à un refus, un hoquet DB rend le refus sans lui,
  jamais une 500 à sa place.

Dépend de `rbac` (instances à portée) ; `resolve` l'appelle au moment du refus.
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import credentials_store, db, links, providers
from . import rbac

logger = logging.getLogger(__name__)


_REVOKED_REASON_LABELS = {
    db.REVOKED_CREDENTIAL_REMOVED: "clé retirée",
    db.REVOKED_RENAMED_ONTO_EXISTING: "compte renommé vers un autre déjà posé",
    db.REVOKED_VAULT_ROW_MISSING: "ligne de coffre disparue (maintenance)",
}


def _revoked_hint(sub: str, org: Optional[int], provider: str) -> str:
    """Suffixe actionnable des erreurs « aucun credential configuré » : dit si CE
    connecteur a existé ici puis a été RETIRÉ, plutôt que de laisser croire qu'il n'a
    jamais été posé. Chaîne vide si rien n'a jamais existé, ou sans org (pas de scope
    membre à interroger).

    oto#42, entrée 11 du lot 1 — quatre signalements le même jour (03/09) pour cette
    seule cause : chacun a mené sa propre enquête pour retrouver une info déjà en
    base. Lecture seule (`connector_instances.most_recent_revocation`), jamais un
    critère d'aiguillage — voir son docstring.

    Fail-soft PAR CONSTRUCTION (comme `chain_shadow.observe`) : c'est un hint EN
    PLUS d'un refus déjà levé, jamais un chemin dont la résolution dépend — un
    hoquet DB ici doit rendre le refus normal (sans second indice), pas remplacer
    le refus par une 500."""
    if org is None:
        return ""
    try:
        rev = db.most_recent_revocation("member", credentials_store.member_id(org, sub), provider)
    # noqa: SILENT — hint best-effort : un hoquet DB laisse le refus SANS second indice
    except Exception:
        logger.warning("hint de révocation indisponible pour %s (fail-soft)", provider,
                       exc_info=True)
        return ""
    if not rev:
        return ""
    motif = _REVOKED_REASON_LABELS.get(rev["revoked_reason"], rev["revoked_reason"])
    quand = str(rev["revoked_at"])[:10]
    return f"\n(un `{provider}` a existé ici et a été retiré le {quand} — {motif})"


def _projects_pinning(sub: str, org: Optional[int], provider: str) -> list[dict]:
    """Projets LISIBLES par `sub` dans `org` qui épinglent déjà une instance
    `provider` (oto-backend#499). Lisibles = le scoping ensembliste d'`op=list`
    (`ownership.accessible_project_ids`) : le hint est un objet de visibilité, il ne
    nomme jamais le projet d'une autre entité. Lecture seule — on NOMME le geste
    (`_project=`), on ne résout rien. Fail-soft : hoquet ⇒ [] (hint sans cette ligne)."""
    from .. import ownership
    try:
        return db.projects_pinning_instance(
            ownership.accessible_project_ids(sub, org), provider)
    # noqa: SILENT — hint best-effort : un hoquet DB laisse le refus avec le hint générique
    except Exception:
        logger.warning("hint de projet épinglant %s indisponible (fail-soft)", provider,
                       exc_info=True)
        return []


def _reachable_hint(sub: str, org: Optional[int], provider: str) -> str:
    """Suffixe actionnable des erreurs « rien ne résout » : remonte les instances
    à portée avec le GESTE de pin pour chacune — jeton d'appel d'abord (`_group=`/
    `_org=`, per-call, sans état), `_instance=` pour le grain fin. Chaîne vide si
    rien à portée.

    Le conseil durable dépend de ce qui EXISTE (#499) : si un projet lisible épingle
    déjà une instance de ce provider, on nomme CE projet et le geste qui l'active
    (`_project=<id>` sur l'appel) — « lie l'instance à ton projet » renvoyait refaire
    un lien déjà posé, et l'agent tâtonnait. Le conseil de lier ne sort qu'à défaut."""
    items = rbac.reachable_instances(sub, org, provider)
    # Le binding de projet se lit sous le PORTEUR, comme la résolution le lit
    # (`scope.project_pinned_instance(porteur)`) ; le texte garde le nom appelé.
    pins = _projects_pinning(sub, org, providers.credential_provider(provider))
    if not items and not pins:
        return ""
    out = ""
    if items:
        lines = []
        for it in items[:4]:
            if it["kind"] == "group":
                lines.append(
                    f"· équipe « {it['name']} » → passe group={it['id']} sur l'appel "
                    f"(ou instance=group:{it['id']}:{provider})")
            else:
                lines.append(
                    f"· org « {it['name']} » → passe org={it['id']} sur l'appel")
        more = len(items) - 4
        if more > 0:
            lines.append(f"· … +{more} (oto_instance op=list pour tout voir)")
        # Le hint nomme des clés qui appartiennent à l'entité citée, et un prestataire
        # est membre des orgs de SES clients : sans cette réserve, il se lit comme un
        # libre-service et l'agent bascule l'appel sur les crédits d'un client pour un
        # travail qui n'est pas le sien (signalé le 12/08 sur une prospection maison
        # renvoyée vers la clé d'une org cliente).
        out += (f"\nNB — des clés `{provider}` existent à portée, aux frais de l'entité "
                f"citée — n'y bascule un appel QUE s'il est fait pour elle :\n"
                + "\n".join(lines))
    if pins:
        # Même réserve : l'instance épinglée a un payeur ; on nomme le geste, l'agent
        # le pose s'il travaille pour ce projet — rien ne se résout tout seul.
        if not items:
            out += (f"\nNB — un projet que tu peux lire épingle une instance `{provider}`, "
                    f"aux frais de l'entité qui la possède — ne le passe QUE si l'appel "
                    f"est fait pour ce projet :")
        out += "\n" + "\n".join(
            f"· le projet #{p['id']} « {p['name']} » épingle déjà une instance "
            f"`{provider}` → passe _project={p['id']} sur l'appel"
            for p in pins[:4])
        if len(pins) > 4:
            out += f"\n· … +{len(pins) - 4} projet(s) (oto_project op=list)"
    else:
        out += "\nDurable : lie l'instance à ton projet (oto_project op=link)."
    return out


def _poser_ou_accorder(sub: str, lien_org, porteur: str) -> str:
    """Le geste proposé quand aucune clé `porteur` ne résout. Poser sa clé, toujours ;
    le prêt d'une clé PLATEFORME seulement si oto en détient une pour ce connecteur.
    Sans elle, « demande à un admin de te grant une clé plateforme » renvoyait vers
    un geste impossible — signalé par un org_admin qui avait fait exactement ça
    (#1156). Le prêt relève des admins d'oto, pas de ceux de l'org : on le dit.

    Fail-soft comme les autres indices du refus (`_revoked_hint`) : un hoquet
    DB ici rend le refus sans la seconde proposition, jamais une 500 à sa place."""
    poser = f"Pose ta propre clé{links.ou_poser_la_cle(sub, org=lien_org, connecteur=porteur)}"
    try:
        pretable = bool(credentials_store.list_platform_instances(porteur))
    # noqa: SILENT — indice best-effort : un hoquet DB laisse le refus sans proposer le prêt
    except Exception:
        logger.warning("clés plateforme `%s` illisibles pour le refus (fail-soft)", porteur,
                       exc_info=True)
        return f"{poser}."
    if not pretable:
        return f"{poser} — oto ne fournit pas de clé plateforme `{porteur}`."
    return (f"{poser}, ou demande aux admins d'oto de prêter à ton org la clé "
            f"plateforme `{porteur}`.")
