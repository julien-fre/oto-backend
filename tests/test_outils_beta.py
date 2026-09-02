"""Les verbes du nouvel univers ne se proposent qu'aux comptes bêta.

Ils étaient exposés à TOUT LE MONDE depuis leur création — mesuré le 2026-09-01, et
c'est l'inverse de ce qu'on croyait : on pensait qu'ils n'apparaissaient pas. Cette
surface part de vide (la recopie depuis l'ancien monde est arrêtée) et son contrat est
provisoire ; la servir à tous, c'est proposer à chaque agent une lecture qui ne trouve
rien et une écriture dont l'utilisateur ignore la destination.

Trois choses se vérifient ici, et la deuxième est celle qui compte :

1. **sans l'option, les trois verbes sont masqués** ;
2. **sur une ERREUR, ils sont masqués AUSSI** — fail-closed, à contre-courant de tous
   les autres blocs de ce module. Eux sont fail-open parce qu'un hoquet de base ne doit
   pas priver quelqu'un de ses outils ; ici le pire est l'inverse, une surface non finie
   qui réapparaît à tout le monde sans que personne ne le voie ;
3. **avec l'option, ils reviennent** — sans quoi le premier test serait satisfait par un
   masquage définitif, et le flag ne servirait à rien.

⚠️ Ce que ce fichier NE prétend pas : que le masquage empêche l'appel. Il ne l'empêche
pas, et aucune règle de visibilité de ce module ne le fait (ADR 0031). Ce qui protège
est l'autorisation de la capacité elle-même.
"""
from __future__ import annotations

import pytest

from oto_mcp import session_visibility as SV
from oto_mcp.tool_visibility import BETA_OPTION, BETA_TOOLS


class _Ctx:
    """Le minimum que `compute_hidden_tools` demande : la liste des tools montés."""

    class _FastMCP:
        def __init__(self, noms):
            self._noms = noms

        async def list_tools(self, run_middleware=False):
            return [type("T", (), {"name": n})() for n in self._noms]

    def __init__(self, noms):
        self.fastmcp = self._FastMCP(noms)


_TOUS = set(BETA_TOOLS) | {"oto_whoami", "oto_doc"}


@pytest.fixture
def socle(monkeypatch):
    """Un compte ordinaire : rien de désactivé, aucun connecteur, rôle membre.

    On neutralise les blocs voisins pour que le seul écart observable soit celui du
    gate bêta — un test qui laisserait la sélection de connecteurs jouer masquerait
    `oto_doc` aussi, et ne prouverait plus rien sur `BETA_TOOLS`.
    """
    monkeypatch.setattr(SV.access, "current_org", lambda sub: 1)
    monkeypatch.setattr(SV.access, "current_group", lambda sub: None)
    monkeypatch.setattr(SV.access, "get_user_role", lambda sub: "member")
    monkeypatch.setattr(SV.access, "org_admin_hidden_tools", lambda org: set())
    monkeypatch.setattr(SV.access, "group_admin_hidden_tools", lambda g: set())
    monkeypatch.setattr(SV.access, "rbac_denied_connectors", lambda s, o: set())
    monkeypatch.setattr(SV.access, "group_rbac_denied_connectors", lambda s, g: set())
    monkeypatch.setattr(SV.db, "list_user_disabled_tools", lambda s, o: [])
    monkeypatch.setattr(SV.db, "list_user_enabled_tools", lambda s, o: [])
    monkeypatch.setattr(SV.connector_activation, "exposed_connectors", lambda o: set())
    monkeypatch.setattr(SV.connector_selection, "is_seeded", lambda s, o: True)
    monkeypatch.setattr(SV.connector_selection, "list_selection", lambda s, o: {})
    return _Ctx(_TOUS)


async def _masques(ctx, monkeypatch, option):
    monkeypatch.setattr(SV.access, "has_option", option)
    return await SV.compute_hidden_tools(ctx, "sub-1")


@pytest.mark.asyncio
async def test_sans_l_option_les_verbes_sont_masques(socle, monkeypatch):
    caches = await _masques(socle, monkeypatch, lambda sub, opt, org=None: False)
    assert BETA_TOOLS <= caches, sorted(BETA_TOOLS - caches)


@pytest.mark.asyncio
async def test_avec_l_option_les_verbes_reviennent(socle, monkeypatch):
    """Sans ce test, un masquage DÉFINITIF satisferait le précédent."""
    vu = {}

    def option(sub, opt, org=None):
        vu["option"], vu["org"] = opt, org
        return True

    caches = await _masques(socle, monkeypatch, option)
    assert not (BETA_TOOLS & caches), sorted(BETA_TOOLS & caches)
    assert vu["option"] == BETA_OPTION
    # L'option se juge sur l'org ACTIVE de la session, pas sur une org devinée
    # ailleurs : c'est ce qui permet de l'ouvrir à une org entière.
    assert vu["org"] == 1


@pytest.mark.asyncio
async def test_sur_une_ERREUR_ils_restent_masques(socle, monkeypatch):
    """Fail-CLOSED, et c'est l'inverse des blocs voisins.

    Eux sont fail-open : une toolbox trop pauvre pendant une seconde est le pire
    qu'ils risquent. Ici le pire est qu'une surface non finie réapparaisse pour tout
    le monde sur un hoquet de base — silencieusement.
    """
    def casse(sub, opt, org=None):
        raise RuntimeError("base injoignable")

    caches = await _masques(socle, monkeypatch, casse)
    assert BETA_TOOLS <= caches, (
        "un hoquet a rouvert la surface bêta à tout le monde")


@pytest.mark.asyncio
async def test_le_gate_ne_masque_QUE_les_verbes_beta(socle, monkeypatch):
    """Le contre-test de portée : un gate trop large est invisible en production —
    il se manifeste par des outils qui manquent, jamais par une erreur."""
    caches = await _masques(socle, monkeypatch, lambda sub, opt, org=None: False)
    assert "oto_doc" not in caches
    assert "oto_whoami" not in caches


# ── le seul nom VIVANT du bloc, et ce qui le décharge ─────────────────────────

def test_oto_trigger_est_dans_le_bloc_beta():
    """Le nom vivant entré sous décharge mesurée (02/09/2026).

    Les tests génériques ci-dessus le couvrent déjà — masqué sans l'option,
    rendu avec, masqué sur erreur. Celui-ci grave la DÉCISION : sans lui, un
    retrait « pour faire propre » passerait pour un nettoyage, alors qu'il
    rouvrirait à tout le monde un verbe qui promet une exécution que personne
    n'assure."""
    assert "oto_trigger" in BETA_TOOLS


def test_oto_trigger_nest_pas_anti_lockout():
    """⚠️ Le piège de ce lot : une entrée VERTE et INERTE.

    `compute_hidden_tools` retire du masque, en tout dernier, tout tool
    `is_protected` — un nom protégé posé dans `BETA_TOOLS` n'y ferait donc
    RIEN, et les assertions génériques d'appartenance passeraient quand même.
    C'est exactement le défaut que ce chantier dénonce ailleurs : une garde
    verte qui ne garde pas."""
    from oto_mcp.tool_visibility import is_protected

    assert not is_protected("oto_trigger")


def test_la_face_REST_nest_pas_gatee_par_le_bloc_beta():
    """Ce qui BORNE les dégâts, et qui doit rester vrai.

    Le masquage est une règle de VISIBILITÉ MCP (ADR 0031), jamais une autz : la
    route servie n'en sait rien. C'est elle qui laisse au dashboard — et à qui
    hérite d'un déclencheur mort — de quoi lire, corriger et supprimer sans
    l'option. Si un jour le gate descendait dans la capacité, ce test rougirait,
    et c'est le moment où il faudrait en reparler."""
    from oto_mcp.capabilities.registry import CAPABILITIES

    cap = next(c for c in CAPABILITIES if c.key == "runner.triggers")
    assert cap.rest is not None and cap.rest.path == "/api/me/runner/triggers"
    assert cap.mcp == "oto_trigger"
