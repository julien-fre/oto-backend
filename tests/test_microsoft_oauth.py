"""La connexion Microsoft du connecteur `sharepoint` — OAuth délégué, par personne,
plusieurs comptes par personne (oto-backend#23).

⚠️ Le cœur est MOQUÉ à sa frontière (`oto.tools.microsoft` posé dans
`sys.modules`) et le coffre est un faux en mémoire : aucun appel réel à Microsoft
ni à la base n'est joué ici. La RÉSOLUTION du compte, elle, est la vraie
(`access.resolve_credential`, celle de tout connecteur multi-compte). Vérifié : le
registre, le flux hébergé (state, URL de retour, coordonnées de l'instance), le
retour de connexion (un second compte s'AJOUTE, le même se remplace), le choix du
compte à l'appel (`_account=`, défaut, ambiguïté), le renouvellement (cache,
rotation du refresh token sur la ligne de CE compte) et l'autorisation morte,
isolée à son compte.
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

from oto_mcp import providers
from oto_mcp.mcp_errors import McpError

CONNECTEUR = "sharepoint"
ORG = 42
SUB = "user-de-test"
MEMBRE = f"{ORG}:{SUB}"
_COORDONNEES = {"client_id": "app-id-fictif", "client_secret": "secret-fictif"}
_RETOUR = "https://mcp.exemple.test/api/microsoft/oauth/callback"
JANE = {"id": "id-jane", "displayName": "Jane Doe", "mail": "Jane@Contoso.example",
        "userPrincipalName": "jane@contoso.example"}
JOHN = {"id": "id-john", "displayName": "John Roe", "mail": None,
        "userPrincipalName": "john@fabrikam.example"}


def _grant(access="AT1", refresh="RT1", expires_in=3600):
    return types.SimpleNamespace(access_token=access, refresh_token=refresh,
                                 expires_in=expires_in, scope="Files.ReadWrite.All")


def _faux_coeur():
    mod = types.ModuleType("oto.tools.microsoft")
    auth = types.ModuleType("oto.tools.microsoft.auth")

    class MicrosoftAuthError(ValueError):
        status_code = 401
        code = None

    class MicrosoftGrantExpired(MicrosoftAuthError):
        pass

    auth.authorize_url = MagicMock(return_value="https://login.example/authorize?x=1")
    auth.exchange_code = MagicMock(return_value=_grant())
    auth.refresh = MagicMock(return_value=_grant("AT2", "RT2"))
    auth.MicrosoftAuthError = MicrosoftAuthError
    auth.MicrosoftGrantExpired = MicrosoftGrantExpired
    mod.auth = auth
    mod.MicrosoftAuthError = MicrosoftAuthError
    mod.MicrosoftGrantExpired = MicrosoftGrantExpired
    client = MagicMock(name="GraphClient")
    client.return_value.get_me.return_value = dict(JANE)
    mod.GraphClient = client
    return mod


class _Coffre:
    """Le coffre, en mémoire : une ligne par (entité, compte), le secret à part du
    meta — mêmes signatures que `credentials_store` pour ce que le connecteur lit."""

    def __init__(self):
        self.lignes: dict[tuple, dict] = {}

    def poser(self, account, secret, meta=None):
        self.lignes[("member", MEMBRE, account)] = {
            "secret": secret, "meta": dict(meta or {}), "set_by": SUB,
            "set_at": "2026-10-05T00:00:00Z"}

    def meta(self, account):
        return self.lignes[("member", MEMBRE, account)]["meta"]

    def get_with_meta(self, entity_type, entity_id, connector, account=""):
        assert connector == CONNECTEUR
        ligne = self.lignes.get((entity_type, entity_id, account))
        return {**ligne, "meta": dict(ligne["meta"])} if ligne else None

    def get(self, entity_type, entity_id, connector, account=""):
        ligne = self.get_with_meta(entity_type, entity_id, connector, account)
        return ligne["secret"] if ligne else None

    def set(self, entity_type, entity_id, connector, secret, set_by=None,
            meta=None, conn=None, account="", expected_version=None):
        assert connector == CONNECTEUR
        self.lignes[(entity_type, entity_id, account)] = {
            "secret": secret, "meta": dict(meta or {}), "set_by": set_by,
            "set_at": "2026-10-05T01:00:00Z"}

    def list_accounts(self, entity_type, entity_id, connector):
        assert connector == CONNECTEUR
        return [{"account": a, "meta": dict(l["meta"]), "set_at": l["set_at"]}
                for (et, eid, a), l in sorted(self.lignes.items())
                if (et, eid) == (entity_type, entity_id)]

    def update_meta(self, entity_type, entity_id, connector, account, patch, conn=None):
        ligne = self.lignes.get((entity_type, entity_id, account))
        if ligne is None:
            return False
        ligne["meta"].update(patch)
        return True


@pytest.fixture
def env(monkeypatch):
    from oto_mcp import access, credentials_store, db, session_org
    from oto_mcp.auth import microsoft as ms_auth
    from oto_mcp.connectors import cardinality
    from oto_mcp.db import connector_settings as store

    mod = _faux_coeur()
    monkeypatch.setitem(sys.modules, "oto.tools.microsoft", mod)
    monkeypatch.setitem(sys.modules, "oto.tools.microsoft.auth", mod.auth)
    monkeypatch.setattr(store, "list_connector_settings",
                        lambda key=None, conn=None: [
                            {"scope_type": "platform", "scope_id": "platform",
                             "connector": CONNECTEUR, "key": k, "value": v}
                            for k, v in _COORDONNEES.items()])
    coffre = _Coffre()
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", coffre.get_with_meta)
    monkeypatch.setattr(credentials_store, "get_credential", coffre.get)
    monkeypatch.setattr(credentials_store, "set_credential", coffre.set)
    monkeypatch.setattr(credentials_store, "list_accounts", coffre.list_accounts)
    monkeypatch.setattr(credentials_store, "update_meta", coffre.update_meta)
    monkeypatch.setattr(db, "member_instance_suspended", lambda *a, **k: False)
    monkeypatch.setattr(db, "insert_tool_call", lambda *a, **k: None)
    # La mesure à côté de la résolution (L7) lit la base : hors sujet ici.
    monkeypatch.setenv("OTO_L7_SHADOW", "0")
    monkeypatch.setattr(access, "current_org", lambda sub: ORG)
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    monkeypatch.setattr(access, "project_pinned_identity", lambda p, project_id=None: None)
    # La cardinalité vient du REGISTRE (aucune surcharge en base dans ce banc).
    monkeypatch.setattr(cardinality, "_OVERRIDES", {})
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.exemple.test")
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "secret-de-signature-de-test")
    monkeypatch.setattr(ms_auth, "_JETONS", {})

    def sous_compte(account):
        """Exécute un appel comme le ferait l'axe `_account=` posé par le middleware."""
        def appel(fn, *a):
            jeton = session_org.set_call_account(account)
            try:
                return fn(*a)
            finally:
                session_org.reset_call_account(jeton)
        return appel

    return types.SimpleNamespace(coeur=mod, coffre=coffre, auth=ms_auth,
                                 sous_compte=sous_compte)


# ── Registre ────────────────────────────────────────────────────────────────

def test_registre():
    c = providers.REGISTRY[CONNECTEUR]
    assert c.secret_kind == "oauth" and c.auth_modes == frozenset({"byo_user"})
    # Multi-compte DÉCLARÉ (OAuth ⟹ la dérivation dirait mono) : c'est ce qui branche
    # la mécanique commune — axe `_account=`, `oto_identity`, refus d'ambiguïté.
    assert c.cardinality == "multi" and c.auth_multi_account
    assert c.publisher_name == "Microsoft"
    assert not c.credential_fields
    assert c.doc_sections, "la fiche doit être servie depuis son markdown"


# ── Flux hébergé ────────────────────────────────────────────────────────────

def test_url_de_retour_derivee_de_l_environnement(env):
    from oto_mcp.connectors import flow as connector_flow

    assert connector_flow.supports(CONNECTEUR)
    assert connector_flow.callback_url(CONNECTEUR) == _RETOUR


def test_state_ne_vaut_que_pour_ce_flux(env):
    from oto_mcp.auth import flow as oauth_flow

    etat = env.auth.make_state(SUB, ORG, "")
    assert env.auth.verify_state(etat) == (SUB, ORG, "")
    assert env.auth.verify_state(oauth_flow.sign_state(
        "meta_ads", {"sub": SUB, "org": ORG})) is None


def test_sans_reglages_le_refus_nomme_la_cle(env, monkeypatch):
    from oto_mcp.db import connector_settings as store

    monkeypatch.setattr(store, "list_connector_settings",
                        lambda key=None, conn=None: [])
    assert env.auth.coordonnees_manquantes() == ["client_id", "client_secret"]
    assert env.auth.app_disponible(SUB) is False
    with pytest.raises(RuntimeError) as e:
        env.auth.app()
    assert "client_secret" in str(e.value) and "oto_admin_connector_setting" in str(e.value)


def test_le_dialogue_part_avec_l_application_de_l_instance(env):
    env.auth.build_auth_url(SUB, "")
    client_id, retour, etat = env.coeur.auth.authorize_url.call_args.args
    assert client_id == "app-id-fictif" and retour == _RETOUR
    assert env.auth.verify_state(etat) == (SUB, ORG, "")


def test_seul_client_secret_est_secret_et_suit_la_convention():
    from oto_mcp.auth import microsoft as ms_auth
    from oto_mcp.capabilities import platform_connectors as pc

    assert [k for k in ms_auth._REGLAGES if "secret" in k] == ["client_secret"]
    lignes = pc._sans_les_secrets([
        {"connector": CONNECTEUR, "key": k, "value": v} for k, v in _COORDONNEES.items()])
    assert "secret-fictif" not in str(lignes)


# ── Retour de connexion ─────────────────────────────────────────────────────

def _callback(query: dict):
    from starlette.requests import Request

    from oto_mcp.api import microsoft as api_ms

    route = api_ms.make_routes(None, None, None, None, None)[0]
    qs = "&".join(f"{k}={v}" for k, v in query.items()).encode()
    req = Request({"type": "http", "method": "GET", "path": route.path,
                   "query_string": qs, "headers": []})
    return asyncio.run(route.endpoint(req))


def test_retour_echange_le_code_et_range_le_refresh_token(env):
    etat = env.auth.make_state(SUB, ORG, "")
    resp = _callback({"code": "le-code", "state": etat})
    assert resp.status_code == 302 and "connected" in resp.headers["location"]
    args = env.coeur.auth.exchange_code.call_args.args
    assert args == ("app-id-fictif", "secret-fictif", "le-code", _RETOUR)
    ligne = env.coffre.lignes[("member", MEMBRE, "jane@contoso.example")]
    assert ligne["secret"] == "RT1"
    assert ligne["meta"]["email"] == "Jane@Contoso.example"
    assert ligne["meta"]["microsoft_id"] == "id-jane"
    assert ligne["meta"]["is_default"] is True, "le premier compte lié est le défaut"
    assert "AT1" not in str(ligne), "le jeton d'accès ne va jamais en base"


def test_retour_refuse_sans_state_ou_sur_refus(env):
    assert "error" in _callback({"code": "c", "state": "faux"}).headers["location"]
    etat = env.auth.make_state(SUB, ORG, "")
    resp = _callback({"error": "access_denied", "state": etat})
    assert "forbidden" in resp.headers["location"]
    env.coeur.auth.exchange_code.assert_not_called()
    assert not env.coffre.lignes


# ── Plusieurs comptes : se connecter AJOUTE ─────────────────────────────────

def _connecter(env, me, refresh):
    env.coeur.GraphClient.return_value.get_me.return_value = dict(me)
    return env.auth.persist_grant(SUB, ORG, _grant("AT-" + refresh, refresh))


def test_un_second_compte_s_ajoute_sans_toucher_au_premier(env):
    _connecter(env, JANE, "RT-JANE")
    out = _connecter(env, JOHN, "RT-JOHN")
    assert out["account"] == "john@fabrikam.example", "sans `mail`, l'UPN nomme le compte"
    assert set(a for (_, _, a) in env.coffre.lignes) == {
        "jane@contoso.example", "john@fabrikam.example"}
    assert env.coffre.lignes[("member", MEMBRE, "jane@contoso.example")]["secret"] == "RT-JANE"
    assert env.coffre.meta("jane@contoso.example")["is_default"] is True
    assert env.coffre.meta("john@fabrikam.example")["is_default"] is False


def test_reconnecter_le_meme_compte_remplace_sa_ligne_meme_renommee(env):
    _connecter(env, JANE, "RT-JANE")
    _connecter(env, JOHN, "RT-JOHN")
    # Renommé depuis (`oto_identity op='rename'`) : le compte se reconnaît à son id.
    env.coffre.lignes[("member", MEMBRE, "client-a")] = env.coffre.lignes.pop(
        ("member", MEMBRE, "john@fabrikam.example"))
    env.coffre.meta("client-a").update(health_ko=True, health_reason="expiré")
    _connecter(env, JOHN, "RT-JOHN-2")
    assert set(a for (_, _, a) in env.coffre.lignes) == {"jane@contoso.example", "client-a"}
    assert env.coffre.lignes[("member", MEMBRE, "client-a")]["secret"] == "RT-JOHN-2"
    assert "health_ko" not in env.coffre.meta("client-a"), "la reconnexion démarque"
    assert env.coffre.meta("client-a")["is_default"] is False, "le défaut ne bouge pas"


def test_un_nom_pris_par_un_autre_compte_n_est_pas_ecrase(env):
    env.coffre.poser("jane@contoso.example", "RT-AUTRE", {"microsoft_id": "id-autre"})
    with pytest.raises(RuntimeError, match="rename"):
        _connecter(env, JANE, "RT-JANE")
    assert env.coffre.lignes[("member", MEMBRE, "jane@contoso.example")]["secret"] == "RT-AUTRE"


def test_me_sans_identite_rien_n_est_range(env):
    with pytest.raises(RuntimeError, match="rien n'a été enregistré"):
        _connecter(env, {"displayName": "x"}, "RT")
    assert not env.coffre.lignes


# ── Choix du compte à l'appel ───────────────────────────────────────────────

def _deux_comptes(env, defaut="jane@contoso.example"):
    for compte, rt in (("jane@contoso.example", "RT-JANE"),
                       ("john@fabrikam.example", "RT-JOHN")):
        env.coffre.poser(compte, rt, {"is_default": compte == defaut})
    env.coeur.auth.refresh.side_effect = lambda cid, cs, rt: _grant("AT:" + rt, rt)


def test_account_choisit_le_compte(env):
    _deux_comptes(env)
    jeton = env.sous_compte("john@fabrikam.example")(env.auth.access_token_for, SUB)
    assert jeton == "AT:RT-JOHN"


def test_sans_account_le_compte_par_defaut(env):
    _deux_comptes(env)
    assert env.auth.access_token_for(SUB) == "AT:RT-JANE"


def test_un_seul_compte_sert_sans_defaut(env):
    env.coffre.poser("john@fabrikam.example", "RT-JOHN")
    env.coeur.auth.refresh.side_effect = lambda cid, cs, rt: _grant("AT:" + rt, rt)
    assert env.auth.access_token_for(SUB) == "AT:RT-JOHN"


def test_plusieurs_comptes_sans_defaut_refus_qui_les_nomme(env):
    _deux_comptes(env, defaut=None)
    with pytest.raises(McpError) as e:
        env.auth.access_token_for(SUB)
    message = str(e.value)
    assert "jane@contoso.example" in message and "john@fabrikam.example" in message
    assert "_account" in message
    env.coeur.auth.refresh.assert_not_called()


def test_compte_inconnu_refuse_jamais_un_autre(env):
    _deux_comptes(env)
    with pytest.raises(McpError, match="not found"):
        env.sous_compte("inconnu@exemple.test")(env.auth.access_token_for, SUB)
    env.coeur.auth.refresh.assert_not_called()


def test_sans_compte_le_refus_dit_le_geste(env, monkeypatch):
    from oto_mcp import access

    # Les indices du refus générique lisent la base (révocations, instances à
    # portée) : hors sujet ici, c'est le refus lui-même qu'on vérifie.
    for indice in ("_revoked_hint", "_reachable_hint"):
        monkeypatch.setattr(access, indice, lambda *a, **k: "")
    with pytest.raises(McpError, match="sharepoint"):
        env.auth.access_token_for(SUB)
    env.coeur.auth.refresh.assert_not_called()


# ── Renouvellement ──────────────────────────────────────────────────────────

def test_rotation_rangee_sur_le_bon_compte_puis_cache(env):
    _deux_comptes(env)
    env.coffre.meta("john@fabrikam.example")["email"] = "john@fabrikam.example"
    env.coeur.auth.refresh.side_effect = None
    env.coeur.auth.refresh.return_value = _grant("AT2", "RT-JOHN-TOURNE")
    appel = env.sous_compte("john@fabrikam.example")
    assert appel(env.auth.access_token_for, SUB) == "AT2"
    assert env.coffre.lignes[("member", MEMBRE, "john@fabrikam.example")]["secret"] \
        == "RT-JOHN-TOURNE"
    assert env.coffre.meta("john@fabrikam.example")["email"] == "john@fabrikam.example", \
        "l'identité du compte reste"
    assert env.coffre.lignes[("member", MEMBRE, "jane@contoso.example")]["secret"] \
        == "RT-JANE", "l'autre compte ne bouge pas"
    assert appel(env.auth.access_token_for, SUB) == "AT2"
    assert env.coeur.auth.refresh.call_count == 1, "le second appel sert du cache"


def test_le_cache_d_un_compte_ne_sert_pas_l_autre(env):
    _deux_comptes(env)
    assert env.auth.access_token_for(SUB) == "AT:RT-JANE"
    jeton = env.sous_compte("john@fabrikam.example")(env.auth.access_token_for, SUB)
    assert jeton == "AT:RT-JOHN"


def test_un_renouvellement_reussi_demarque_le_compte(env):
    env.coffre.poser("jane@contoso.example", "RT1",
                     {"is_default": True, "health_ko": True, "health_reason": "expiré"})
    assert env.auth.access_token_for(SUB) == "AT2"
    assert env.coffre.meta("jane@contoso.example")["health_ko"] is False


def test_autorisation_morte_marque_ce_compte_seulement(env):
    _deux_comptes(env)
    expire = env.coeur.MicrosoftGrantExpired("expiré")

    def refresh(cid, cs, rt):
        if rt == "RT-JOHN":
            raise expire
        return _grant("AT:" + rt, rt)

    env.coeur.auth.refresh.side_effect = refresh
    with pytest.raises(env.auth.MicrosoftReauthRequired, match="john@fabrikam.example"):
        env.sous_compte("john@fabrikam.example")(env.auth.access_token_for, SUB)
    assert env.coffre.meta("john@fabrikam.example")["health_ko"] is True
    assert not env.coffre.meta("jane@contoso.example").get("health_ko")
    assert env.coffre.lignes[("member", MEMBRE, "john@fabrikam.example")]["secret"] \
        == "RT-JOHN", "marquer n'efface rien"
    # L'autre compte continue de servir.
    assert env.auth.access_token_for(SUB) == "AT:RT-JANE"
    assert "john@fabrikam.example" in env.auth._etape_manquante(SUB, None, None, {})
    etat = env.auth._link_state(SUB)
    assert etat.linked and etat.accounts == 2 and etat.health_ko
    assert "john@fabrikam.example" in etat.health_reason
    assert "jane@contoso.example" not in etat.health_reason


def test_secret_d_application_faux_ne_marque_pas_la_personne(env):
    env.coffre.poser("jane@contoso.example", "RT1")
    env.coeur.auth.refresh.side_effect = env.coeur.MicrosoftAuthError("invalid_client")
    with pytest.raises(env.coeur.MicrosoftAuthError):
        env.auth.access_token_for(SUB)
    assert not env.coffre.meta("jane@contoso.example").get("health_ko")


def test_statut_de_la_fiche(env):
    assert env.auth._etape_manquante(SUB, None, None, {}) == "Sign in with Microsoft"
    assert env.auth._link_state(SUB).linked is False
    env.coffre.poser("jane@contoso.example", "RT1")
    assert env.auth._etape_manquante(SUB, None, None, {}) is None
    env.coffre.meta("jane@contoso.example")["health_ko"] = True
    assert "reconnect" in env.auth._etape_manquante(SUB, None, None, {})


# ── La mécanique commune, branchée ──────────────────────────────────────────

def test_oto_identity_liste_et_fixe_le_defaut(env):
    from oto_mcp.connectors import identities

    _deux_comptes(env)
    assert identities.supports(CONNECTEUR)
    liste = identities.list_identities(SUB, CONNECTEUR)
    assert [(i["id"], i["is_default"]) for i in liste] == [
        ("jane@contoso.example", True), ("john@fabrikam.example", False)]
    identities.select_identity(SUB, CONNECTEUR, "john@fabrikam.example")
    assert env.auth.access_token_for(SUB) == "AT:RT-JOHN"


def test_l_axe_account_est_accepte_sur_les_outils(env):
    from oto_mcp import call_axes

    for outil in ("sharepoint_file", "sharepoint_site"):
        assert "_account" in {a.param for a in call_axes.axes_for_call(outil)}, outil


def test_la_fiche_dit_la_regle_des_comptes_par_connexion():
    sections = providers.REGISTRY[CONNECTEUR].doc_sections
    multi = next(s for s in sections if "multiple" in s.title)
    assert "_account" in multi.body_md and "principal" not in multi.body_md
