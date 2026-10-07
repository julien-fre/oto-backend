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

from .. import ownership
from ._types import AuthzDenied, DeclaredError, ResolvedCtx

#: Le refus de `exiger_appartenance` — déclaré tel quel par chaque capacité qui publie.
REFUS_HORS_ORG = DeclaredError(
    403, "publish_requires_membership",
    "publier (endpoint MCP sans login, page ou fichier public) exige d'être membre de "
    "l'org propriétaire du projet — de l'org parente pour un projet d'équipe, le "
    "propriétaire lui-même pour un projet personnel ; un gérant extérieur ne publie pas")


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


def exiger_appartenance(sub: str, project_id: int) -> None:
    """Publier exige d'appartenir au propriétaire du projet (#1176) — LE point de décision
    de toute publication : endpoint MCP (`oto_project op=publish_mcp`, `oto_resource`
    audience public/secret, les deux faces), page publique (`oto_doc op=set_public`),
    fichier public (`me.project_file.set_public`). La règle elle-même est
    `ownership.can_publish` ; ce qui s'ajoute ici, c'est le refus NOMMÉ.

    Elle s'ajoute au droit du geste, elle ne le remplace pas : le caller a déjà gaté
    `can_govern` (endpoint) ou `write` (page, fichier).

    **Ce qu'elle tient, à la différence de `refuser_si_agent`.** C'est un contrôle
    d'accès, sur les deux faces et pour tout appelant : l'endpoint publié résout clés et
    quota SOUS L'ORG PROPRIÉTAIRE, et un gérant (`manager`, grantable à quiconque) qui
    n'en est pas membre ouvrait au public l'usage de clés qui ne sont pas les siennes.

    Seule l'OUVERTURE est gardée : dépublier, refermer une page ou un fichier réduit la
    portée et reste ouvert à qui gouverne."""
    if not ownership.can_publish(sub, "project", str(project_id)):
        raise AuthzDenied(
            403, "publish_requires_membership",
            "Publier ce projet (endpoint sans login, page ou fichier public) est réservé "
            "aux membres de l'org qui le possède — de l'org parente pour un projet "
            "d'équipe, à son propriétaire pour un projet personnel. Tu le gouvernes sans "
            "en être membre : tu peux le modifier et le partager à des personnes, pas "
            "l'ouvrir au web sous les clés et le quota de cette org. Demande à l'un de "
            "ses membres de publier.")
