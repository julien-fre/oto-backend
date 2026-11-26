"""Tools `twilio` / `twilio_act` et sonde du connecteur Twilio (clé propre de l'org).

Ce que ce fichier verrouille :
- la paire `<ns>` / `<ns>_act` et le routage vers la bonne méthode du client, avec le
  credential à deux champs passé tel quel ;
- l'appel MIS EN RELATION : le commercial sonne d'abord, le TwiML compose le contact
  avec le numéro de l'org en présentation, enregistrement des deux côtés par défaut ;
- l'achat et la libération réservés à un admin de l'org ;
- `dry_run` : aucune méthode qui agit n'est appelée ;
- l'envoi de SMS en lot : un refus par destinataire ne coupe pas le lot, un 401 si ;
- l'audio d'un enregistrement déposé en transit ;
- les refus d'intention et la sonde.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from oto_mcp.connectors import verify as cv
from oto_mcp.mcp_errors import McpError

_CREDS = {"account_sid": "AC" + "0" * 32, "auth_token": "jeton-de-test"}


@pytest.fixture
def client(monkeypatch):
    inst, built = MagicMock(), []

    def fabrique(**kw):
        built.append(kw)
        return inst

    monkeypatch.setattr("oto.tools.twilio.TwilioClient", fabrique)
    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider, account=None: dict(_CREDS))
    inst.built = built
    return inst


@pytest.fixture
def admin(monkeypatch):
    etat = {"admin": True}
    monkeypatch.setattr("oto_mcp.tools.twilio._caller_is_org_admin", lambda: etat["admin"])
    return etat


def _fn(name):
    from fastmcp import FastMCP
    from oto_mcp.tools import twilio as T

    m = FastMCP("t")
    T.register(m)
    return asyncio.run(m.get_tool(name)).fn


def test_lecture_des_numeros_resserree(client):
    client.list_numbers.return_value = {"incoming_phone_numbers": [
        {"sid": "PN1", "phone_number": "+33939030001", "uri": "/x", "subresource_uris": {}}],
        "next_page_uri": None}
    out = _fn("twilio")(op="numbers")
    assert client.built == [_CREDS]
    assert out["incoming_phone_numbers"] == [{"sid": "PN1", "phone_number": "+33939030001"}]
    assert "next_page_uri" in out


def test_un_appel_par_sid(client):
    client.get_call.return_value = {"sid": "CA1", "status": "completed"}
    assert _fn("twilio")(op="calls", sid="CA1")["status"] == "completed"
    client.list_calls.assert_not_called()


def test_audio_en_transit(client, monkeypatch):
    client.recording_media.return_value = b"ID3"
    vu = {}
    monkeypatch.setattr("oto_mcp.transit.deposer",
                        lambda prefix, data, mime, name: vu.update(p=prefix, d=data) or {"source": 1})
    assert _fn("twilio")(op="recording_audio", sid="RE1") == {"source": 1}
    assert vu == {"p": "twilio-recordings", "d": b"ID3"}


def test_refus_d_intention(client):
    with pytest.raises(McpError):
        _fn("twilio")(op="numbers", country="FR")
    with pytest.raises(McpError):
        _fn("twilio")(op="available_numbers")


def test_appel_mis_en_relation(client, admin):
    client.create_call.return_value = {"sid": "CA9", "status": "queued", "uri": "/x"}
    out = _fn("twilio_act")(op="call", rep="+33600000001", to=["+33600000002"],
                            from_="+33939030001", announce="Appel vers le prospect")
    args, kw = client.create_call.call_args
    assert args == ("+33600000001", "+33939030001")
    assert "<Number>+33600000002</Number>" in kw["twiml"]
    assert 'callerId="+33939030001"' in kw["twiml"]
    assert "record-from-answer-dual" in kw["twiml"]
    assert out == {"sid": "CA9", "status": "queued"}


def test_appel_un_seul_contact_et_sans_enregistrement(client, admin):
    with pytest.raises(McpError):
        _fn("twilio_act")(op="call", rep="+33600000001", to=["+33600000002", "+33600000003"],
                          from_="+33939030001")
    _fn("twilio_act")(op="call", rep="+33600000001", to=["+33600000002"],
                      from_="+33939030001", record=False)
    assert "record=" not in client.create_call.call_args.kwargs["twiml"]


def test_achat_et_liberation_reserves_aux_admins(client, admin):
    admin["admin"] = False
    for op, kw in (("add_number", {"phone_number": "+33939030001"}),
                   ("remove_number", {"sid": "PN1"})):
        with pytest.raises(McpError) as e:
            _fn("twilio_act")(op=op, **kw)
        assert "admin" in str(e.value)
    client.buy_number.assert_not_called()
    client.release_number.assert_not_called()
    admin["admin"] = True
    _fn("twilio_act")(op="add_number", phone_number="+33939030001", bundle_sid="BU1")
    client.buy_number.assert_called_once()


def test_dry_run_n_agit_jamais(client, admin):
    client.get_number.return_value = {"phone_number": "+33939030001", "sms_url": "https://a"}
    act = _fn("twilio_act")
    act(op="add_number", phone_number="+33939030001", dry_run=True)
    out = act(op="update_number", sid="PN1", sms_url="https://b", dry_run=True)
    assert out["changes"] == {"sms_url": {"from": "https://a", "to": "https://b"}}
    act(op="remove_number", sid="PN1", dry_run=True)
    act(op="call", rep="+33600000001", to=["+33600000002"], from_="+33939030001", dry_run=True)
    out = act(op="send_sms", from_="TULINA", to=["+33600000002"], body="Salut", dry_run=True)
    assert out["to"] == ["+33600000002"]
    for m in ("buy_number", "update_number", "release_number", "create_call", "send_message"):
        getattr(client, m).assert_not_called()


def test_sms_en_lot_continue_apres_un_refus(client, admin):
    from oto.tools.common.errors import UpstreamHTTPError

    client.send_message.side_effect = [
        {"sid": "SM1", "status": "queued", "num_segments": "1"},
        UpstreamHTTPError(400, {"code": 21211, "message": "Invalid 'To'"}),
    ]
    out = _fn("twilio_act")(op="send_sms", from_="+33939030001",
                            to=["+33600000001", "+33600000002"], body="Bonjour")
    assert out["total"] == 2 and out["sent"][0]["sid"] == "SM1"
    assert out["failed"][0]["index"] == 1 and "21211" in out["failed"][0]["error"]


def test_sms_un_401_coupe_le_lot(client, admin):
    from oto.tools.common.errors import UpstreamHTTPError

    client.send_message.side_effect = UpstreamHTTPError(401, {"code": 20003})
    with pytest.raises(McpError):
        _fn("twilio_act")(op="send_sms", from_="+33939030001",
                          to=["+33600000001", "+33600000002"], body="x")
    assert client.send_message.call_count == 1


def test_sms_borne_du_lot(client, admin):
    with pytest.raises(McpError):
        _fn("twilio_act")(op="send_sms", from_="+33939030001",
                          to=["+336000000%02d" % i for i in range(51)], body="x")


def test_sonde(monkeypatch):
    from oto.tools.common.errors import UpstreamHTTPError
    from oto_mcp.tools import twilio as T

    inst = MagicMock()
    monkeypatch.setattr("oto.tools.twilio.TwilioClient", lambda **kw: inst)
    inst.get_account.side_effect = UpstreamHTTPError(401, {"code": 20003})
    with pytest.raises(cv.NonAutorise):
        T._verify(dict(_CREDS))
    inst.get_account.side_effect = None
    inst.get_account.return_value = {"status": "active"}
    T._verify(dict(_CREDS))
