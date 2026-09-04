"""Observer les élargissements de portée faits par un agent (ADR 0068 §4).

Le §2 de l'ADR pose des gardes ; celui-ci pose le second volet, **savoir**. Une garde
ne couvrira jamais tous les chemins, et un agent peut légitimement passer le paramètre
d'élargissement parce que la demande était ambiguë. Il faut donc qu'un élargissement
laisse une trace que son propriétaire reçoit **sans avoir à la chercher**.

`tool_calls` journalise déjà tout — et personne ne le regarde. **Journaliser n'est pas
avertir** : c'est exactement la différence que ce module existe pour payer.

## Rien ne part encore

⚠️ **Période d'OBSERVATION, décision d'Alexis (04/09/2026) : on n'envoie aucun message.**
On enregistre ce qui SERAIT parti — à qui, avec quelle urgence — et on lira le volume
après quelques jours. Ouvrir un canal en devinant son débit, c'est le refermer une
semaine plus tard, après avoir appris à ses destinataires à l'ignorer.

## Les trois choix qui décident du contenu d'une ligne

**Qui déclenche.** Un élargissement fait par un AGENT (`ResolvedCtx.channel == "mcp"`).
Un geste fait à la main dans le dashboard n'enregistre rien : la personne vient de le
faire, l'en avertir serait un accusé de réception — et ce qu'on reçoit toujours, on
cesse de le lire.
⚠️ Le canal est le meilleur proxy dont on dispose, pas la vérité : un porteur de jeton
`oto_` sur la face REST est lui aussi une machine, et il n'est pas compté ici. C'est une
limite CONNUE de l'observation, pas un oubli — la corriger demande de distinguer, sur
REST, un JWT Logto (un humain qui a cliqué) d'un jeton porté.

**Qui reçoit.** Le propriétaire du contenu ET l'auteur du geste (choix d'Alexis). Ils se
confondent dans le cas nominal — c'est celui de l'incident fondateur, où la personne
avait demandé et son agent avait fait plus. Ils diffèrent quand un tiers partage ce
qu'on lui avait confié, et là les deux ont besoin de le savoir : l'un parce qu'il subit,
l'autre parce qu'il ignore peut-être ce que son agent vient de faire en son nom.

**Quand.** Immédiat pour ce qui devient lisible SANS LOGIN, groupé pour le reste. Un
agent qui partage trente lignes doit produire un message, pas trente ; mais une page
publiée sur le web n'attend pas la fin d'une fenêtre de regroupement. `immediat` porte
cette distinction dès l'enregistrement, pour que la période d'observation mesure les
deux volumes séparément — c'est le second qui décide s'il faut regrouper, et le premier
s'il faut alerter.
"""
from __future__ import annotations

from typing import Optional

from ..db import portee as db_portee
from ._types import ResolvedCtx

#: Ce qui devient lisible SANS COMPTE. Seule catégorie qui n'attend pas.
_SANS_LOGIN = frozenset({"public", "secret"})


def observer(
    ctx: ResolvedCtx,
    *,
    ressource_type: str,
    ressource_id,
    vers: str,
    geste: str,
    ressource_nom: Optional[str] = None,
    proprietaire_sub: Optional[str] = None,
    cible: Optional[str] = None,
) -> None:
    """Enregistre un élargissement de portée fait par un agent. N'envoie RIEN.

    No-op hors face MCP : `channel` vaut `"rest"` (le dashboard, un humain qui clique)
    ou `None` (appel interne, banc). On n'observe que ce qui sort d'une conversation.

    `vers` ∈ {org, group, person, public, secret} — ce vers quoi le contenu s'est
    ouvert, pas le geste. `public`/`secret` déclenchent l'urgence.

    ⚠️ Aucune exception ne remonte : l'appelant est un handler en train de RÉUSSIR un
    partage légitime, et une panne d'observation n'a pas à faire échouer le produit
    qu'elle observe (`db.portee` journalise et rend None).
    """
    if ctx.channel != "mcp" or not ctx.sub:
        return
    proprio = proprietaire_sub or ctx.sub
    # Le propriétaire ET l'auteur — dédoublonnés en base, où ils se confondent souvent.
    destinataires = [d for d in (proprio, ctx.sub) if d]
    db_portee.enregistrer_elargissement(
        acteur_sub=ctx.sub,
        org_id=ctx.org_id,
        ressource_type=ressource_type,
        ressource_id=str(ressource_id),
        ressource_nom=ressource_nom,
        proprietaire_sub=proprio,
        vers=vers,
        cible=cible,
        geste=geste,
        destinataires=destinataires,
        immediat=vers in _SANS_LOGIN,
    )
