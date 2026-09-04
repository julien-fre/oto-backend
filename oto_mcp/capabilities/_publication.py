"""Rendre un contenu lisible SANS LOGIN ne sort pas d'une conversation (04/09/2026).

Décision d'Alexis, après l'inventaire des chemins d'élargissement de portée : « org =
explicite, public = interdit à l'agent ». Élargir vers l'org reste possible à un agent,
mais jamais par défaut ; ouvrir au web ne lui est plus possible du tout.

**Pourquoi cette asymétrie.** Un contenu d'org reste dans une population nommée, dont
les membres ont un compte, un contrat et un administrateur : l'élargissement se répare.
Un contenu servi sans login est indexable, recopiable, et ne se reprend pas — le
retirer n'efface pas ce qui a été lu. Le geste n'a pas la même réversibilité, il n'a
donc pas le même régime.

**Ce que cette garde tient, et ce qu'elle ne tient pas.** Elle empêche qu'une
CONVERSATION publie — le chemin par lequel un document présenté comme personnel s'est
retrouvé partagé, parce qu'un verbe faisait plus que son nom ne disait. Elle
n'empêche pas un porteur de jeton d'appeler la face REST : ce n'est pas un contrôle
d'accès, c'est un cran d'intention. L'appeler « sécurité » serait promettre une
garantie qu'elle ne tient pas, et personne ne poserait le vrai contrôle ensuite.

⚠️ Le refus se déclenche sur `channel == "mcp"` EXPLICITE, jamais sur « pas rest » :
un contexte sans canal (appel interne, banc) passe. Le prix de ce choix est qu'un
adaptateur qui oublierait de poser le canal rendrait la garde inerte SANS rougir —
d'où `tests/test_canal_d_appel.py`, qui lit le canal sur le montage réel des deux
faces et non sur la fonction qui le pose.
"""
from __future__ import annotations

from ._types import AuthzDenied, ResolvedCtx


def refuser_si_agent(ctx: ResolvedCtx, quoi: str, ou_le_faire: str) -> None:
    """Refuse une ouverture SANS LOGIN demandée depuis une conversation d'agent.

    `quoi` = ce qui deviendrait lisible, en clair et au concret (« cette page »,
    « ce projet et les tableaux qui y sont liés ») — pas le nom de l'op.
    `ou_le_faire` = le geste humain équivalent. Un refus qui ne dit pas par où passer
    n'arrête pas la demande, il la déplace : l'agent réessaie autrement, et c'est là
    qu'on perd le contrôle qu'on croyait poser.
    """
    if ctx.channel != "mcp":
        return
    raise AuthzDenied(
        403, "publication_reservee_a_l_humain",
        f"Un agent ne peut pas rendre {quoi} lisible SANS LOGIN. Ce qui est servi "
        "sans compte est indexable et recopiable : le retirer n'efface pas ce qui a "
        f"été lu, donc le geste demande une personne. {ou_le_faire} "
        "Dis-le à qui te parle plutôt que de chercher un autre chemin — élargir à "
        "l'org reste possible, c'est l'ouverture au web qui ne l'est pas.")
