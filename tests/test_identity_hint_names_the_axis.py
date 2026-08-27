"""L'aide d'`oto_identity` doit nommer l'axe TEL QU'IL EST ACCEPTÉ.

`oto_identity` est la porte d'entrée du choix de compte : c'est là qu'un agent
apprend comment viser un compte précis pour un appel. Sa description a dit
`account=<id>` alors que les jetons de contexte ont été préfixés (`_account`) —
justement parce que le nom NU entrait en collision avec les arguments métier de
certains outils (un `account` avalé en silence, 28/07). Un agent qui suivait
l'aide à la lettre voyait donc son appel rejeté, sans rien pour comprendre.

Le contrat gardé ici est étroit et vérifiable : ce que l'aide dit de passer est
ce que l'appel accepte réellement. La source du nom est `call_axes.ACCOUNT`, pas
une constante recopiée — sinon le test dirait seulement qu'il est d'accord avec
lui-même.
"""
from __future__ import annotations

import re

from oto_mcp import call_axes
from oto_mcp.capabilities.registry import CAPABILITIES


def _identity_description() -> str:
    cap = next(c for c in CAPABILITIES if c.key == "connectors.console.identity")
    return cap.description


def test_l_aide_nomme_l_axe_reellement_accepte():
    param = call_axes.ACCOUNT.param            # `_account`
    desc = _identity_description()
    assert param in desc, f"l'aide ne nomme pas `{param}`"
    # …et jamais la forme NUE en position d'argument (`account=`), qui est refusée
    # sur un outil sans argument métier de ce nom, et avalée sur les autres.
    nu = re.findall(r"(?<![_\w])account=", desc)
    assert not nu, "l'aide propose la forme nue `account=` — celle qui a causé le renommage"


def test_l_exemple_de_l_aide_vise_un_outil_qui_accepte_l_axe():
    """Un exemple qui ne marcherait pas est pire que pas d'exemple."""
    desc = _identity_description()
    outils = re.findall(r"\b([a-z][a-z0-9_]+)\(_account=", desc)
    assert outils, "l'aide ne donne aucun exemple d'appel"
    for nom in outils:
        assert call_axes.accepts_account_axis(nom), \
            f"l'aide cite `{nom}` en exemple, mais cet outil n'accepte pas l'axe compte"
