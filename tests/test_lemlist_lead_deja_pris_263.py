"""Un lead refusé comme DOUBLON par lemlist est un résultat, pas une panne
(otomata-tech/oto#263).

Relevé en prod (tool_calls 25/08 → 10/09) : « Lead already in other campaign »
en HTTP 500 le 31/08 (23 appels), le même en 409 le 09/09 (71 appels), « Lead
already in the campaign » en 400. Corps en texte brut. Le 500 partait en panne
backend et le contact était perdu au lieu d'être compté. On reconnaît le
MESSAGE, quel que soit le statut ; tout autre refus garde son chemin d'erreur.

Doublure : la réponse HTTP passe par le vrai `raise_for_upstream` d'oto-core
(corps non-JSON → texte), aucun appel réel à lemlist."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from oto.tools.common.errors import UpstreamHTTPError, raise_for_upstream


class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def json(self):
        return json.loads(self.text)


class _FauxClient:
    reponse = None
    retour = None
    appels = 0

    def __init__(self, api_key=None):
        pass

    def create_lead(self, campaign_id, lead, **flags):
        _FauxClient.appels += 1
        if self.reponse is None:
            if self.retour is not None:
                return self.retour
            return {"_id": "lea_1", "campaignId": campaign_id, **lead}
        raise_for_upstream(self.reponse, service="lemlist")


def _creer(reponse, *, campaign_id="cam_1", retour=None, **kw):
    from fastmcp import FastMCP
    from oto_mcp.tools import lemlist

    _FauxClient.reponse, _FauxClient.retour, _FauxClient.appels = reponse, retour, 0
    m = FastMCP("t")
    with patch("oto_mcp.access.resolve_api_key", return_value=("k", False)), \
            patch("oto.tools.lemlist.LemlistClient", _FauxClient):
        lemlist.register(m)
        fn = asyncio.run(m.get_tool("lemlist_create_lead")).fn
        return fn(campaign_id=campaign_id, email="a@b.co", **kw)


@pytest.mark.parametrize("status, message, reason", [
    (500, "Lead already in other campaign", "already_in_other_campaign"),
    (409, "Lead already in other campaign", "already_in_other_campaign"),
    (400, "Lead already in the campaign", "already_in_campaign"),
])
def test_le_doublon_est_un_resultat_quel_que_soit_le_statut(status, message, reason):
    r = _creer(_Resp(status, message))
    assert r == {"created": False, "reason": reason, "message": message,
                 "campaign_id": "cam_1", "lead": {"email": "a@b.co"}}


def test_un_autre_500_reste_une_panne():
    with pytest.raises(UpstreamHTTPError) as exc:
        _creer(_Resp(500, "Internal Server Error"))
    assert exc.value.status_code == 500


def test_un_autre_400_garde_son_erreur():
    with pytest.raises(UpstreamHTTPError) as exc:
        _creer(_Resp(400, '{"message": "Invalid email"}'))
    assert exc.value.status_code == 400


def test_la_creation_normale_reste_intacte():
    assert _creer(None) == {"_id": "lea_1", "campaignId": "cam_1", "email": "a@b.co"}


# --- oto#1071, #1072 : une campagne mal nommée ou introuvable ne passe jamais en silence ---
# lemlist nomme ses campagnes `cam_…` (doc API, `POST /campaigns/{campaignId}/leads/`).
# Relevé en prod : sans le préfixe, l'amont répondait 200 SANS créer de lead — un
# succès vide ; avec, un 404 « Campaign not found » générique.

def _code(exc):
    return exc.value.error.data["code"]


def test_un_id_sans_prefixe_cam_est_refuse_avant_tout_appel():
    from oto_mcp.mcp_errors import McpError
    with pytest.raises(McpError) as exc:
        _creer(None, campaign_id="GfAhCXe2YTvTQFu9b")
    assert _code(exc) == "lemlist_campaign_id_format"
    assert "cam_GfAhCXe2YTvTQFu9b" in exc.value.error.message
    assert _FauxClient.appels == 0


def test_une_reponse_sans_id_de_lead_n_est_pas_un_succes():
    from oto_mcp.mcp_errors import McpError
    with pytest.raises(McpError) as exc:
        _creer(None, retour={})
    assert _code(exc) == "lemlist_lead_not_created"
    assert exc.value.error.data["retryable"] is False


def test_une_campagne_introuvable_est_un_refus_nomme():
    from oto_mcp.mcp_errors import McpError
    with pytest.raises(McpError) as exc:
        _creer(_Resp(404, "Campaign not found"))
    assert _code(exc) == "lemlist_campaign_not_found"
    assert "cam_1" in exc.value.error.message


def test_un_autre_404_garde_son_erreur():
    with pytest.raises(UpstreamHTTPError) as exc:
        _creer(_Resp(404, "No user found for this API key"))
    assert exc.value.status_code == 404
