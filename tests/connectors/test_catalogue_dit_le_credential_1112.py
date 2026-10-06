"""Le catalogue ne fait plus dire « non connecté » à l'agent (oto-backend#1112).

Le fait : l'agent d'un utilisateur lui a dit que son LinkedIn n'était pas connecté
alors qu'il l'était (`linkedin_unipile_account op=status` : `connected`, `alive`). Il
n'avait pas appelé l'outil de statut, il avait conclu du catalogue. Deux fois, chez
deux utilisateurs. Trois causes, une par section, chacune rejouée ici sur un FAUX
MAGASIN (aucune base) :

1. `name="linkedin"` répondait « inconnu ou indisponible » — le nom est
   `linkedin_unipile`, le libellé « LinkedIn » ;
2. une ligne `not_selected` ne disait rien de la clé ou du compte existants ;
3. `oto_list_my_tools` déclarait TOUT `installed` en cours de session, contredisant
   `oto_connector` — ses couches partaient de la liste déjà filtrée par la session.

Ce qu'on remplace : les ENTRÉES des fonctions (le snapshot `access.status_for`, la
sélection, l'exposition, les transformations de session de fastmcp), jamais la règle
qui les lit.
"""
from __future__ import annotations

import fastmcp as _fc
import pytest
from _mcp_app import static_mcp as _test_mcp
from fastmcp.server.transforms import visibility as fmv

import oto_mcp.tools.unipile  # noqa: F401 — déclare le geste de vérification LinkedIn
from oto_mcp import providers, session_visibility as sv
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.connectors import selection as CS
from oto_mcp.connectors import credential_presence as CP
from oto_mcp.tools import meta as _meta

_SUB = "u-catalogue-credential"
_ORG = 7


def _ligne(name: str) -> dict:
    """Une ligne du catalogue servi, prise au VRAI registre (libellé, namespaces)."""
    return next(c for c in providers.public_catalog() if c["name"] == name)


# Le faux magasin : ce que `/api/me` dirait de ce compte. Une clé Unipile de palier
# plateforme avec le compte LinkedIn lié (aucune étape en attente), une clé PayFit
# perso, rien pour AI Ark.
_SNAPSHOT = {
    "unipile": {"mode": "platform"},
    "linkedin_unipile": {"mode": "platform", "pending_action": None},
    "payfit": {"mode": "user"},
    "aiark": {"mode": "forbidden"},
}


@pytest.fixture()
def magasin(monkeypatch):
    """Rien de sélectionné (tout est `not_selected`), quatre connecteurs exposés."""
    cat = [_ligne(n) for n in ("linkedin_unipile", "aiark", "payfit", "unipile")]
    monkeypatch.setattr(CS, "_visible_catalog", lambda ctx: list(cat))
    monkeypatch.setattr(CS.connector_selection, "list_selection_detail", lambda s, o: {})
    monkeypatch.setattr(CS.connector_selection, "list_removed", lambda s, o: {})
    monkeypatch.setattr(CS.org_store, "get_org_default_connectors", lambda o: [])
    monkeypatch.setattr(CS, "_guide_refs_by_ns", lambda o: {})
    monkeypatch.setattr(CS, "_toolbox_scope", lambda sub: None)
    monkeypatch.setattr(CS.access, "reachable_instances_map", lambda s, o: {})
    monkeypatch.setattr(CS.access, "option_open", lambda s, n, org=None: True)
    monkeypatch.setattr(CS.access, "paid_option_for", lambda n: None)
    monkeypatch.setattr(CS.access, "current_group", lambda s: None)
    monkeypatch.setattr(CS.connector_readiness, "diagnose",
                        lambda sub, name, org, group: None)
    snapshot = {k: dict(v) for k, v in _SNAPSHOT.items()}
    monkeypatch.setattr(CP.access, "status_for",
                        lambda sub, org, group: {"providers": snapshot})
    return snapshot


def _me(**kw) -> dict:
    return CS._me(ResolvedCtx(sub=_SUB, org_id=_ORG), CS.MyConnectorsInput(**kw))


# ── 1. le nom : libellé, namespace, et un refus qui PROPOSE ───────────────────

def test_linkedin_trouve_les_deux_connecteurs_linkedin(magasin):
    """LE cas : « linkedin » n'est pas un nom, c'est un libellé et un namespace."""
    out = _me(name="linkedin")
    assert [c["name"] for c in out["connectors"]] == ["linkedin_unipile", "aiark"]
    assert out["name_match"]["candidates"] == ["linkedin_unipile", "aiark"]
    assert "none is chosen for you" in out["name_match"]["note"]
    # Chaque candidat reçoit son verdict : la lecture reste ciblée.
    assert out["readiness"] == "computed"
    assert all(c["ready"] is True for c in out["connectors"])


def test_un_namespace_d_outils_designe_son_connecteur(magasin):
    """`linkedin_aiark` est le préfixe des outils du connecteur `aiark`."""
    out = _me(name="linkedin_aiark")
    assert [c["name"] for c in out["connectors"]] == ["aiark"]
    assert out["name_match"]["candidates"] == ["aiark"]


def test_un_nom_exact_ne_porte_pas_de_name_match(magasin):
    out = _me(name="payfit")
    assert [c["name"] for c in out["connectors"]] == ["payfit"]
    assert "name_match" not in out


def test_un_nom_qui_ne_designe_rien_est_refuse_en_proposant(magasin):
    """Jamais un « inconnu » sec : il a été relu « pas connecté »."""
    with pytest.raises(AuthzDenied) as e:
        _me(name="linkdin")
    assert e.value.code == "unknown_connector"
    assert "linkedin_unipile" in e.value.details["suggestions"]
    assert "`linkedin_unipile`" in e.value.message
    assert "says NOTHING about your connections" in e.value.message


def test_un_geste_exige_le_nom_exact_mais_son_refus_propose(magasin, monkeypatch):
    """`select` écrit : il ne se résout pas par libellé, il refuse — en proposant."""
    monkeypatch.setattr(CS.connector_activation, "exposed_connectors",
                        lambda org: {"linkedin_unipile", "aiark", "payfit", "unipile"})
    with pytest.raises(AuthzDenied) as e:
        CS._select(ResolvedCtx(sub=_SUB, org_id=_ORG),
                   CS.ConnectorActionInput(name="linkedin"))
    assert e.value.code == "unknown_connector"
    assert e.value.details["suggestions"][:2] == ["linkedin_unipile", "aiark"]


# ── 2. la ligne dit qu'un credential EXISTE, même `not_selected` ──────────────

def test_un_linkedin_lie_se_dit_sur_une_ligne_not_selected(magasin):
    rows = {c["name"]: c for c in _me()["connectors"]}
    li = rows["linkedin_unipile"]
    assert li["state"] == "not_selected"
    assert li["credential"]["status"] == "connected"
    assert li["credential"]["level"] == "platform"
    assert li["credential"]["nature"] == "hosted_account"
    # « vérifié vivant » n'est PAS dit : on nomme l'outil qui le vérifie.
    assert "linkedin_unipile_account(op='status')" in li["credential"]["next_step"]


def test_une_cle_perso_se_dit_sur_une_ligne_not_selected(magasin):
    rows = {c["name"]: c for c in _me()["connectors"]}
    pf = rows["payfit"]
    assert pf["state"] == "not_selected"
    assert pf["credential"]["status"] == "connected" and pf["credential"]["level"] == "user"
    assert pf["credential"]["nature"] == providers.REGISTRY["payfit"].secret_kind
    assert pf["credential"]["next_step"]


def test_rien_ne_resout_rien_n_est_dit_et_l_enveloppe_dit_le_calcul(magasin):
    out = _me()
    assert "credential" not in {c["name"]: c for c in out["connectors"]}["aiark"]
    assert out["credentials"] == "computed"
    hint = out["readiness_hint"]
    assert "`not_selected` does NOT mean not connected" in hint


def test_un_compte_a_lier_est_une_etape_pas_une_connexion(magasin):
    magasin["linkedin_unipile"]["pending_action"] = "Connecte ton compte LinkedIn"
    li = {c["name"]: c for c in _me()["connectors"]}["linkedin_unipile"]
    assert li["credential"]["status"] == "pending_step"
    assert li["credential"]["next_step"] == "Connecte ton compte LinkedIn"


def test_un_snapshot_illisible_se_dit_au_lieu_de_se_taire(magasin, monkeypatch):
    def _casse(sub, org, group):
        raise RuntimeError("base indisponible")
    monkeypatch.setattr(CP.access, "status_for", _casse)
    out = _me()
    assert out["credentials"] == "unavailable"
    assert all("credential" not in c for c in out["connectors"])


def test_la_ligne_servie_est_declaree(magasin):
    """Le modèle de sortie DÉCRIT la ligne : `credential` et `name_match` y sont."""
    CS.MyConnectors.model_validate(_me(name="linkedin"))
    assert "credential" in CS.MyConnectorRow.model_fields
    assert "name_match" in CS.MyConnectors.model_fields


# ── 3. une seule vérité : oto_list_my_tools en cours de session ──────────────

_INSTALLE, _NON_SELECTIONNE = "apollo", "linkedin_unipile"


@pytest.fixture()
def session_montee(monkeypatch, magasin):
    """Une session dont le HANDSHAKE a déjà masqué les outils non sélectionnés : c'est
    ce que fait `apply_session_visibility`, et c'est ce que le banc d'oto#170 (sans
    session) ne voyait pas."""
    monkeypatch.setattr(_meta, "current_user_sub_from_token", lambda: _SUB)
    from oto_mcp.capabilities import _mcp_adapter
    monkeypatch.setattr(_mcp_adapter, "current_user_sub_from_token", lambda: _SUB)
    monkeypatch.setattr(_meta.access, "get_user_role", lambda s: "member")
    monkeypatch.setattr(sv.db, "list_user_disabled_tools", lambda s, o=None: [])
    monkeypatch.setattr(sv.db, "list_user_enabled_tools", lambda s, o=None: [])
    monkeypatch.setattr(sv.access, "current_org", lambda s: _ORG)
    monkeypatch.setattr(sv.access, "get_user_role", lambda s: "member")
    monkeypatch.setattr(sv.access, "org_admin_hidden_tools", lambda o: set())
    monkeypatch.setattr(sv.access, "group_admin_hidden_tools", lambda g: set())
    monkeypatch.setattr(sv.access, "has_option", lambda s, opt, org=None: True)
    exposes = {c.name for c in providers._REGISTRY_LIST}
    monkeypatch.setattr(sv.connector_activation, "exposed_connectors", lambda org: exposes)
    monkeypatch.setattr(sv.connector_selection, "is_seeded", lambda s, o: True)
    monkeypatch.setattr(sv.connector_selection, "list_selection",
                        lambda s, o: {_INSTALLE: sv.connector_selection.ACTIVE})
    # La session : ce que le handshake a masqué, tel que fastmcp le rejoue à chaque
    # `list_tools` — tout outil de connecteur qui n'est pas d'`apollo`.
    from oto_mcp.tool_visibility import namespace_of
    masques = set()

    async def _transformations(ctx):
        if not masques:
            for t in await _brut(ctx.fastmcp):
                con = providers.connector_for_namespace(namespace_of(t.name))
                if con is not None and con.name != _INSTALLE:
                    masques.add(t.name)
        return [fmv.Visibility(False, names=set(masques), components={"tool"})]
    monkeypatch.setattr(fmv, "get_session_transforms", _transformations)
    return masques


async def _brut(fastmcp):
    from fastmcp.server.providers.base import Provider
    return await Provider.list_tools(fastmcp)


async def _list_my_tools(args: dict) -> dict:
    tool = await _test_mcp().get_tool("oto_list_my_tools")
    async with _fc.Context(fastmcp=_test_mcp()):
        return (await tool.run(args)).structured_content


@pytest.mark.asyncio
async def test_en_session_un_outil_non_selectionne_n_est_pas_installe(session_montee):
    """LE défaut 3 : les couches partaient de la liste DÉJÀ filtrée par la session —
    un outil masqué au handshake n'entrait dans aucune couche et sortait `installed`.
    Tout le catalogue l'était, contre `not_selected` côté `oto_connector`."""
    async with _fc.Context(fastmcp=_test_mcp()):
        vus = {t.name for t in await _test_mcp().list_tools(run_middleware=False)}
    assert "linkedin_unipile_search" not in vus and "apollo_search_people" in vus, \
        "le banc doit monter une session qui masque, comme le handshake"
    out = await _list_my_tools({"full": True})
    etats = {e["name"]: e["state"] for e in out["tools"]}
    assert etats["linkedin_unipile_search"] == "installable"
    assert etats["apollo_search_people"] == "installed"
    assert out["catalog_by_state"]["installable"] > out["catalog_by_state"]["installed"]


@pytest.mark.asyncio
async def test_les_deux_surfaces_disent_le_meme_credential(session_montee):
    """Une seule vérité : le groupe d'`oto_list_my_tools` et la ligne d'`oto_connector`
    sortent de la MÊME fonction, et disent la même chose du même connecteur."""
    outils = await _list_my_tools({})
    groupes = {g["connector"]: g for g in outils["connectors"]}
    ligne = {c["name"]: c for c in _me()["connectors"]}
    assert outils["credentials"] == "computed"
    assert groupes[_NON_SELECTIONNE]["state"] == "installable"
    assert groupes[_NON_SELECTIONNE]["credential"] == ligne[_NON_SELECTIONNE]["credential"]
    assert ligne[_NON_SELECTIONNE]["state"] == "not_selected"
    assert "credential" not in groupes["aiark"]


@pytest.mark.asyncio
async def test_une_recherche_rend_le_credential_de_ses_connecteurs(session_montee):
    out = await _list_my_tools({"query": "linkedin profile"})
    assert "linkedin_unipile" in out["connector_credentials"]
    assert out["connector_credentials"]["linkedin_unipile"]["status"] == "connected"
    assert "credential" in out["legend"]


# ── le texte servi ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_la_description_d_oto_connector_dit_que_not_selected_n_est_pas_non_connecte():
    prose = " ".join(((await _test_mcp().get_tool("oto_connector")).description or "").split())
    assert "`not_selected` does NOT mean not connected" in prose
    assert "linkedin_unipile_account op=status" in prose
    assert "BEFORE concluding" in prose
