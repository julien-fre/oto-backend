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

from oto_mcp import deprecations, providers, tool_visibility
from oto_mcp.capabilities import _authz
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


def _connecteur_porte_par(nom: str) -> str | None:
    """Le nom d'un connecteur RÉEL du registre, s'il apparaît comme TOKEN(S) exact(s)
    de `nom` (après split sur `_`) — jamais une sous-chaîne (« connect » ⊂
    « connector » avait produit le faux positif du test précédent, dans sa toute
    première version)."""
    tokens = nom.split("_")
    for conn in sorted(providers.REGISTRY, key=len, reverse=True):
        conn_tokens = conn.split("_")
        largeur = len(conn_tokens)
        if any(tokens[i:i + largeur] == conn_tokens
               for i in range(len(tokens) - largeur + 1)):
            return conn
    return None


def test_aucun_verbe_a_plancher_ouvert_ne_porte_un_connecteur_non_rattache():
    """oto-backend#822, point 2 — le contre-test au bon GRAIN, généralisé le 04/09/2026.

    Le test précédent garde une TERMINAISON de nom (`_connect`/`_connect_start`) : il
    n'aurait rien dit d'un futur `oto_hubspot_import` ou `oto_notion_sync`. Ce qui fait
    RÉELLEMENT le défaut n'est pas la terminaison, c'est une PROPRIÉTÉ : `oto_salesforce_
    connect` était joignable par n'importe quel membre parce que son autz `ORG_MEMBER` ne
    pose AUCUN plancher de rôle plateforme (`_authz.platform_floor` ne rend `operator`/
    `super` que pour `PLATFORM_ADMIN`/`SUPER_ADMIN` — tout le reste, y compris
    `ORG_MEMBER`/`ORG_ADMIN`, rend `None`, cf. `_authz.py` §Plancher). Rien, côté rôle,
    ne le protégeait donc : SEUL le gate connecteur (résolu sur le nom) aurait pu le
    faire, et il ne le voyait pas.

    ⚠️ **Un verbe à plancher `operator`/`super` est hors de cette classe PAR
    CONSTRUCTION** — pas une exception ad hoc. `oto_admin_unipile_seat` (plancher
    `super`) gère les sièges de TOUTE la plateforme, pas les siens : le rôle le masque
    déjà à qui n'est pas super admin, connecteur installé ou non, et le gater EN PLUS
    casserait le geste pour un super admin qui n'a personnellement rien connecté.
    Vérifié le 2026-09-04 : c'est le SEUL nom du registre réel (675 outils montés,
    `scripts/empreinte_servie.py`) qui porte un connecteur hors de son namespace, et
    il tombe dans cette exception — zéro troisième verbe fautif.

    Éprouvé rouge en repassant `mcp="oto_salesforce_connect"` sur
    `capabilities/salesforce_connect.py` (2026-09-04) : ce test nomme
    `('oto_salesforce_connect', 'salesforce')` avant le premier test du fichier
    (qui, lui, connaît déjà les DEUX noms fautifs et ne dirait rien d'un troisième).
    """
    fautifs = []
    for c in CAPABILITIES:
        if not c.mcp or _authz.platform_floor(c.authz) is not None:
            continue  # masqué par le rôle plateforme, peu importe le connecteur
        ns = tool_visibility.namespace_of(c.mcp)
        if providers.connector_for_namespace(ns) is not None:
            continue  # déjà rattaché à son connecteur
        conn = _connecteur_porte_par(c.mcp)
        if conn:
            fautifs.append((c.mcp, conn))

    assert not fautifs, (
        f"verbes nommés d'après un connecteur, à plancher de rôle OUVERT, hors de son "
        f"namespace : {sorted(fautifs)} — servis à tout le monde, connecteur installé "
        "ou non, et aucun rôle ne les protège")
