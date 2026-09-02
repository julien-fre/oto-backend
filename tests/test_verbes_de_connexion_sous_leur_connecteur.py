"""Un verbe de CONNEXION vit sous son connecteur, pas sous le préfixe transverse.

Alexis, le 2026-09-02, en trouvant dans ses outils un connecteur qu'il n'utilise pas :
**« oui il faut qu'ils soient dans le connecteur »**.

La cause est mécanique et sans rapport avec l'autorisation : **le gate par connecteur
résout au namespace du NOM**. Un verbe appelé `oto_<service>_connect` tombe donc dans le
namespace transverse, qu'aucun gate de connecteur ne touche — il était servi aux 87
comptes, y compris ceux qui n'ont ni Salesforce ni Zoho. Leur autorisation, elle, était
correcte : c'est la VISIBILITÉ qui débordait, pas le droit.

⚠️ **Ce banc garde le NOM, pas le comportement d'un handler.** C'est voulu : le défaut
ne vivait pas dans le code du verbe, il vivait dans son nom. Un test qui exercerait la
connexion ne l'aurait jamais vu.

⚠️ **Et l'ancien nom reste servi** — une procédure d'org le référence encore (mesuré en
base le 2026-09-02, pas supposé). Il est déclaré déprécié : le middleware d'alias le
sert À CÔTÉ du canonique, en héritant de sa visibilité. Un compte sans le connecteur ne
le voit donc réapparaître ni sous l'un ni sous l'autre nom.

Éprouvé rouge le 2026-09-02 : `mcp="oto_salesforce_connect"` rétabli ⟹ le premier test
nomme le namespace fautif.
"""
from __future__ import annotations

import pytest

from oto_mcp import deprecations, tool_visibility
from oto_mcp.capabilities.registry import CAPABILITIES

# clé de capacité → connecteur auquel le verbe appartient
VERBES = {"me.salesforce_connect": "salesforce", "me.zoho_connect": "zoho"}


def _mcp_de(cle: str) -> str:
    trouvees = [c for c in CAPABILITIES if c.key == cle]
    assert trouvees, f"capacité {cle} introuvable"
    return trouvees[0].mcp


@pytest.mark.parametrize("cle, connecteur", sorted(VERBES.items()))
def test_le_verbe_est_rattache_a_SON_connecteur(cle, connecteur):
    """Le rattachement se lit sur le nom : c'est lui qui décide du gate, pas la
    déclaration de la capacité."""
    nom = _mcp_de(cle)
    assert tool_visibility.namespace_of(nom) == connecteur, (
        f"`{nom}` tombe dans le namespace « {tool_visibility.namespace_of(nom)} » : "
        f"aucun gate de connecteur ne le touche, il est servi à tout le monde")


@pytest.mark.parametrize("cle, connecteur", sorted(VERBES.items()))
def test_l_ancien_nom_reste_servi_et_pointe_vers_le_bon(cle, connecteur):
    """Une procédure d'org référence encore l'ancien nom. Le retirer sec la casserait —
    et une procédure cassée ne se manifeste qu'à son prochain déroulé, chez quelqu'un
    d'autre."""
    nom = _mcp_de(cle)
    ancien = f"oto_{connecteur}_connect"
    assert deprecations.tool_canonique(ancien) == nom
    assert ancien in deprecations.tools_deprecies_de(nom)


def test_aucun_autre_verbe_de_connexion_ne_traine_sous_le_prefixe_transverse():
    """Le contre-test de CLASSE : c'est l'axe qui compte, pas les deux cas trouvés.
    Un troisième verbe de connexion nommé `oto_…_connect` demain retomberait dans le
    même trou, et personne ne le verrait — la faute n'a aucun symptôme à l'exécution."""
    # ⚠️ Le motif vise une TERMINAISON, pas une sous-chaîne : « connect » est contenu
    # dans « connector », et `oto_connector` / `oto_connector_access` /
    # `oto_connector_activation` sont de la GOUVERNANCE transverse — leur place est
    # bien sous le préfixe commun. Ma première version les accusait tous les trois.
    fautifs = [
        c.mcp for c in CAPABILITIES
        if c.mcp and c.mcp.startswith("oto_")
        and (c.mcp.endswith("_connect") or c.mcp.endswith("_connect_start"))
        and c.mcp not in deprecations.TOOLS
    ]
    assert not fautifs, (
        f"verbes de connexion encore sous le préfixe transverse : {sorted(fautifs)} — "
        "ils seront servis à tous les comptes, connecteur installé ou non")
