"""Sonde `_verify` Slack (signal #217) : un token posé peut authentifier mais
manquer les scopes de lecture → on veut un diagnostic actionnable, pas un
`missing_scope` opaque au premier appel réel. Deux étages : auth.test (token
vivant ?) puis lecture channels (scope `channels:read` ?)."""
from __future__ import annotations

import pytest
from oto.tools.slack.client import SlackError

from oto_mcp.tools.slack import _verify


#: Ce que Slack rend vraiment sur `auth.test`, par jeton. Deux corps DISTINCTS :
#: c'est tout l'objet du lot — le jeton bot et le jeton utilisateur n'identifient pas
#: la même chose, et c'est l'app du bot qui porte les appartenances de canaux.
AUTH_BOT = {"ok": True, "url": "https://acme.slack.com/", "team": "Acme",
            "team_id": "T0FAKETEAM1", "user": "oto", "user_id": "U0FAKEBOTU1",
            "bot_id": "B0FAKEBOT01", "app_id": "A0FAKEAPP01",
            "is_enterprise_install": False}
AUTH_USER = {"ok": True, "url": "https://acme.slack.com/", "team": "Acme",
             "team_id": "T0FAKETEAM1", "user": "membre", "user_id": "U0FAKEUSER1"}


class _FakeClient:
    """Client Slack stubé : `calls` pilote ce que chaque appel lève."""

    calls: dict = {}

    def __init__(self, bot_token=None, user_token=None, default_as_user=False):
        _FakeClient.calls["init"] = {"bot": bot_token, "user": user_token}
        self._bot, self._user = bot_token, user_token
        self._default_as_user = default_as_user

    def _request(self, method, endpoint, as_user=None, **kw):
        mode = self._default_as_user if as_user is None else as_user
        _FakeClient.calls.setdefault("auth_calls", []).append(
            "user" if mode else "bot")
        exc = _FakeClient.calls.get("auth")
        if exc:
            raise exc
        return dict(AUTH_USER if mode else AUTH_BOT)

    def list_channels(self, types="public_channel", as_user=None):
        exc = _FakeClient.calls.get("channels")
        if exc:
            raise exc
        return []


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    _FakeClient.calls = {}
    monkeypatch.setattr("oto.tools.slack.client.SlackClient", _FakeClient)


def test_no_token_raises():
    with pytest.raises(ValueError, match="aucun token Slack"):
        _verify({})


def test_dead_token_flagged_as_invalid():
    _FakeClient.calls = {"auth": SlackError("invalid_auth")}
    with pytest.raises(ValueError, match="token Slack invalide"):
        _verify({"user_token": "xoxp-dead"})


def test_valid_token_missing_scope_names_channels_read():
    # auth.test passe (token vivant) mais la lecture channels manque le scope :
    # LE cas du signal #217 → message qui nomme channels:read + la ré-install.
    _FakeClient.calls = {"channels": SlackError("missing_scope")}
    with pytest.raises(ValueError, match="channels:read"):
        _verify({"bot_token": "xoxb-ok"})


def test_all_ok_does_not_raise():
    _verify({"bot_token": "xoxb-ok", "user_token": "xoxp-ok"})
    assert _FakeClient.calls["init"] == {"bot": "xoxb-ok", "user": "xoxp-ok"}


def test_legacy_raw_bot_token_routed_by_prefix():
    # credential mono-champ legacy (token brut, pas de bot_token/user_token nommé).
    _verify({"value": "xoxb-legacy"})
    assert _FakeClient.calls["init"] == {"bot": "xoxb-legacy", "user": None}


# --------------------------------------------------------------------------
# L'identité de l'app — signaux 802/814 (deux orgs, 3→8 septembre 2026).
#
# `auth.test` était appelée puis JETÉE. Conséquence mesurée : un credential
# remplacé par les jetons d'une AUTRE app Slack perd toutes ses appartenances de
# canaux, et rien nulle part ne le dit. Six jours d'illisibilité sur quatre canaux
# clients privés, et un « a rejoint le canal » écrit six fois par jour dans le
# canal d'un client, parce que le seul remède connu était de re-rejoindre.
#
# Ce que ces épreuves tiennent : la sonde RÉPOND « quelle app ? », pour que la
# question « est-ce toujours la même qu'hier ? » ait une réponse quelque part.
# --------------------------------------------------------------------------


def test_la_sonde_rend_l_identite_de_l_app_du_bot():
    """Le corps d'`auth.test` n'est plus jeté : app, bot, équipe, compte."""
    mesures = _verify({"bot_token": "xoxb-ok"})
    assert mesures["identity"]["bot"] == {
        "app_id": "A0FAKEAPP01", "bot_id": "B0FAKEBOT01", "team": "Acme",
        "team_id": "T0FAKETEAM1", "url": "https://acme.slack.com/",
        "user": "oto", "user_id": "U0FAKEBOTU1"}


def test_chaque_jeton_pose_est_identifie_par_LE_SIEN():
    """Deux jetons = deux identités, et c'est le point : l'ancien appel unique
    partait sur le jeton UTILISATEUR dès qu'il y en avait un, donc n'identifiait
    JAMAIS l'app du bot — celle qui porte les appartenances de canaux."""
    mesures = _verify({"bot_token": "xoxb-ok", "user_token": "xoxp-ok"})
    assert _FakeClient.calls["auth_calls"] == ["bot", "user"], (
        "les deux jetons doivent être sondés, chacun avec le sien")
    assert mesures["identity"]["bot"]["bot_id"] == "B0FAKEBOT01"
    assert mesures["identity"]["user"]["user_id"] == "U0FAKEUSER1"
    assert "bot_id" not in mesures["identity"]["user"]


def test_l_identite_ne_porte_que_ce_que_Slack_a_REELLEMENT_rendu():
    """Pas de clé fabriquée à vide : un champ absent du corps est absent de la
    mesure. Une valeur qu'on n'a pas ne se rend jamais par son défaut."""
    mesures = _verify({"user_token": "xoxp-ok"})
    assert set(mesures["identity"]) == {"user"}
    assert "app_id" not in mesures["identity"]["user"]


def test_l_identite_TRAVERSE_le_seam_des_mesures():
    """La destination, pas l'intention : `executer` réduit ce que rend une sonde
    aux clés connues. Sans `identity` dans cette liste, la sonde la rendrait et le
    seam la jetterait — le défaut d'origine, déplacé d'un cran."""
    import asyncio

    from oto_mcp.connectors import verify as V

    mesures = asyncio.run(V.executer(_verify, {"bot_token": "xoxb-ok"}, {}, None))
    assert mesures["identity"]["bot"]["app_id"] == "A0FAKEAPP01"


# --------------------------------------------------------------------------
# La DESTINATION : ce que reçoit vraiment celui qui appelle la sonde.
#
# Une sonde qui rend l'identité et une surface qui la jette, c'est le défaut
# d'origine déplacé d'un cran. Ces deux épreuves suivent la valeur jusqu'au
# payload servi et jusqu'au contrat publié — pas jusqu'à la fonction voisine.
# --------------------------------------------------------------------------


def test_l_identite_arrive_dans_le_PAYLOAD_de_la_capacite(monkeypatch):
    """`oto_instance op=verify` / `POST /api/me/connectors/slack/verify`."""
    import asyncio

    from oto_mcp import access
    from oto_mcp.capabilities.connectors import verify as cv
    from oto_mcp.connectors import health as connector_health
    from oto_mcp.connectors import verify as V

    class _RC:
        mode, entity_type, entity_id = "org", "org", "9249"
        fields, config = {"bot_token": "xoxb-ok"}, {}

    monkeypatch.setattr(access, "resolve_credential", lambda *a, **k: _RC())
    # Le marquage de santé écrit en base : hors sujet ici, et il ne doit pas
    # décider du verdict de cette épreuve.
    monkeypatch.setattr(connector_health, "record_health", lambda *a, **k: None)
    V.register("slack", _verify)

    class _Ctx:
        sub, org_id = "acme:abc123def456", 9249

    class _Inp:
        provider, level = "slack", "auto"

    out = asyncio.run(cv._verify(_Ctx(), _Inp()))
    assert out["ok"] is True
    assert out["identity"]["bot"]["app_id"] == "A0FAKEAPP01", (
        "la sonde la rend, la capacité doit la servir")


def test_le_contrat_SERVI_declare_identity():
    """`Output` DÉCRIT la 200 dans `/api/openapi.json` : un champ servi mais non
    déclaré est un champ que personne ne sait chercher."""
    from oto_mcp.capabilities.connectors.verify import VerifyResult

    assert "identity" in VerifyResult.model_json_schema()["properties"]
