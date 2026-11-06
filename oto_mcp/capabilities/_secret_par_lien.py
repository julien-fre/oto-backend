"""Le porteur d'un webhook, remis à un AGENT : un lien, jamais le secret.

`oto_trigger op=create` (un webhook) et `op=rotate_secret` frappent un porteur neuf et
le rendaient EN CLAIR dans la réponse — donc dans le contexte du modèle, l'historique
du client et ses journaux. C'est le miroir, en SORTIE, de la règle « un secret brut ne
passe jamais en argument MCP » : un secret frappé par le serveur ne passe pas non plus
par la réponse d'un outil.

Sur la face MCP (`ctx.channel == "mcp"`, EXPLICITE — jamais « pas rest »), le porteur
frappé est JETÉ : seul son haché est posé, et personne ne le détient. La porte est donc
FERMÉE, pas ouverte — toute livraison est refusée tant qu'une personne n'a pas tiré un
porteur neuf depuis l'écran de l'agent, où le lien rendu mène (type de lien `webhook`,
déclaré par instance). Rien n'est stocké en clair, rien n'expire, rien n'est à purger :
le serveur sait refrapper un porteur à la demande, il n'a pas à en garder un.

La face REST (l'écran de l'agent) reçoit le porteur comme avant : c'est la personne
qui le lit, une fois.
"""
from __future__ import annotations

from .. import links
from ._types import ResolvedCtx

NOTE = ("The token was NOT returned to you: an AI never holds a webhook's token. Send "
        "`hook_secret_url` to the person who sets up the sending app — they generate "
        "the token on that page and paste it there, never in the chat. Until they do, "
        "the webhook refuses every delivery.")


def remettre(ctx: ResolvedCtx, trigger_id: int, secret: str) -> dict:
    """Les clés à fusionner dans la réponse : le porteur à une personne (REST), un lien
    à un agent (MCP). Le secret n'est jamais journalisé ici."""
    if ctx.channel != "mcp":
        return {"hook_secret": secret}
    url = links.link_for("webhook", sub=ctx.sub, org=ctx.org_id, id=trigger_id)
    out: dict = {"hook_secret": None, "hook_secret_url": url, "hook_secret_note": NOTE}
    if url is None:
        raison = links.raison_sans_lien("webhook", sub=ctx.sub, org=ctx.org_id,
                                        id=trigger_id)
        out["hook_secret_missing"] = (
            f"{raison}. The agent's owner (or an org admin) gets the token from the "
            "agent's own screen in the product: Webhook ▸ Regenerate.")
    return out

