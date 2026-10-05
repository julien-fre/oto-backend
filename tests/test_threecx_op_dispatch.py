"""Tools `threecx_*` et gardes du connecteur.

Ce que ce fichier verrouille :
- la SURFACE (2 tools) et le routage vers la bonne méthode du client ;
- le credential : `base_url` + la paire du mode `auth_mode` (client API ou
  compte), seule passée au client ; mode inconnu, paire incomplète ou sans
  `base_url` → refusé avant de construire le client, dans l'outil ET la sonde ;
- la garde d'egress sur `base_url` à chaque construction du client ;
- la vue de tri du journal (corps rendus en taille) et le rendu de l'audio par
  `file_content.render_for_agent` ;
- un 401/403 de la sonde → `NonAutorise`, classé sur `status_code`.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError

_USER = {"base_url": "https://pbx.exemple.fr", "auth_mode": "user", "username": "u@exemple.fr",
         "password": "p", "client_id": "", "client_secret": ""}
_API = {"base_url": "https://pbx.exemple.fr", "auth_mode": "api_client", "client_id": "cid",
        "client_secret": "s", "username": "restes", "password": "d-un-autre-mode"}


@pytest.fixture
def construits(monkeypatch):
    """Les kwargs de chaque construction de `ThreeCXClient`, et le faux client."""
    inst, calls = MagicMock(), []

    def fabrique(**kw):
        calls.append(kw)
        return inst

    monkeypatch.setattr("oto.tools.threecx.ThreeCXClient", fabrique)
    return inst, calls


@pytest.fixture
def egress_vu(monkeypatch):
    vus = []
    monkeypatch.setattr("oto_mcp.egress.check_url",
                        lambda url, connector=None: vus.append((url, connector)))
    return vus


def _creds(monkeypatch, creds):
    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider: dict(creds))


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import threecx as X

    m = FastMCP("t")
    X.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_mcp().get_tool(name)).fn


def test_surface(construits):
    names = {t.name for t in asyncio.run(_mcp().list_tools())}
    assert names == {"threecx_call", "threecx_recording"}


@pytest.mark.parametrize("creds, attendu", [
    (_USER, {"base_url": "https://pbx.exemple.fr", "username": "u@exemple.fr", "password": "p"}),
    (_API, {"base_url": "https://pbx.exemple.fr", "client_id": "cid", "client_secret": "s"}),
])
def test_credential_passe_tel_quel(construits, egress_vu, monkeypatch, creds, attendu):
    inst, calls = construits
    _creds(monkeypatch, creds)
    inst.list_calls.return_value = {"calls": [], "next_skip": None}
    _tool("threecx_call")("2026-09-01", "2026-09-02")
    assert calls == [attendu]
    assert egress_vu == [("https://pbx.exemple.fr", "threecx")]


@pytest.mark.parametrize("creds", [
    {**_USER, "base_url": ""},
    {**_USER, "password": " "},
    {**_API, "client_secret": ""},
    {**_API, "auth_mode": ""},
    {**_API, "auth_mode": "basic"},
])
def test_credential_incomplet_ou_mode_inconnu_refuse(construits, egress_vu, monkeypatch, creds):
    _creds(monkeypatch, creds)
    with pytest.raises(McpError):
        _tool("threecx_call")("2026-09-01", "2026-09-02")
    assert construits[1] == [] and egress_vu == []


def test_journal_routage_et_vue_de_tri(construits, egress_vu, monkeypatch):
    inst, _ = construits
    _creds(monkeypatch, _USER)
    inst.list_calls.return_value = {"calls": [
        {"SegmentId": 1, "SrcRecId": 7, "Transcription": "bonjour", "Summary": None}],
        "next_skip": 100}
    out = _tool("threecx_call")("2026-09-01", "2026-09-02", recorded_only=True, top=50, skip=100)
    inst.list_calls.assert_called_once_with("2026-09-01", "2026-09-02", top=50, skip=100,
                                            recorded_only=True)
    row = out["calls"][0]
    assert "Transcription" not in row and row["Transcription_length"] == 7
    assert out["next_skip"] == 100 and out["projection"]["omitted"]


def test_journal_brut(construits, egress_vu, monkeypatch):
    inst, _ = construits
    _creds(monkeypatch, _USER)
    inst.list_calls.return_value = {"calls": [{"SegmentId": 1, "Transcription": "x"}],
                                    "next_skip": None}
    out = _tool("threecx_call")("2026-09-01", "2026-09-02", fields=["*"])
    assert out == {"calls": [{"SegmentId": 1, "Transcription": "x"}], "next_skip": None}


def test_enregistrement_rendu_par_render_for_agent(construits, egress_vu, monkeypatch):
    inst, _ = construits
    _creds(monkeypatch, _USER)
    inst.download_recording.return_value = {
        "content": b"RIFF", "content_type": "audio/x-wav", "filename": "a.wav"}
    vus = []

    def render(data, filename, mime, *, sub, prefix, **kw):
        vus.append((data, filename, mime, sub, prefix))
        return {"encoding": "url", "url": "https://signed"}

    monkeypatch.setattr("oto_mcp.file_content.render_for_agent", render)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    out = _tool("threecx_recording")(42)
    inst.download_recording.assert_called_once_with(42)
    assert vus == [(b"RIFF", "a.wav", "audio/x-wav", "sub-1", "threecx-files")]
    assert out["url"] == "https://signed"


def test_stockage_indisponible_refus_nomme(construits, egress_vu, monkeypatch):
    from oto_mcp import file_content

    inst, _ = construits
    _creds(monkeypatch, _USER)
    inst.download_recording.return_value = {
        "content": b"RIFF", "content_type": "audio/x-wav", "filename": "a.wav"}

    def render(*a, **kw):
        raise file_content.MediaUnavailable("stockage indisponible")

    monkeypatch.setattr("oto_mcp.file_content.render_for_agent", render)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    with pytest.raises(McpError, match="stockage indisponible"):
        _tool("threecx_recording")(42)


def test_erreur_amont_classee_sur_status(construits, egress_vu, monkeypatch):
    from oto.tools.common.errors import UpstreamHTTPError

    inst, _ = construits
    _creds(monkeypatch, _USER)
    inst.download_recording.side_effect = UpstreamHTTPError(403, "texte libre", service="3cx")
    with pytest.raises(McpError, match="accès refusé"):
        _tool("threecx_recording")(42)


# --- sonde -------------------------------------------------------------------

def test_sonde_refuse_credential_incomplet(construits, egress_vu):
    from oto_mcp.connectors import verify as connector_verify
    from oto_mcp.tools import threecx as X

    with pytest.raises(connector_verify.NonAutorise):
        X._verify({"base_url": "https://pbx.exemple.fr", "auth_mode": "user", "username": "u"})
    assert construits[1] == [] and egress_vu == []


@pytest.mark.parametrize("status, attendu", [(401, "NonAutorise"), (403, "NonAutorise"),
                                             (500, "RuntimeError")])
def test_sonde_classe_les_refus(construits, egress_vu, status, attendu):
    from oto.tools.common.errors import UpstreamHTTPError
    from oto_mcp.connectors import verify as connector_verify
    from oto_mcp.tools import threecx as X

    construits[0].list_calls.side_effect = UpstreamHTTPError(status, "x", service="3cx")
    exc = connector_verify.NonAutorise if attendu == "NonAutorise" else RuntimeError
    with pytest.raises(exc):
        X._verify(dict(_API))
    assert egress_vu == [("https://pbx.exemple.fr", "threecx")]
    assert construits[0].list_calls.call_args.kwargs == {"top": 1}


# --- export ------------------------------------------------------------------

def _render_capture(monkeypatch):
    vus = []

    def render(data, filename, mime, *, sub, prefix, **kw):
        vus.append({"data": data, "filename": filename, "mime": mime, "prefix": prefix})
        return {"encoding": "url", "url": "https://signed"}

    monkeypatch.setattr("oto_mcp.file_content.render_for_agent", render)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    return vus


def test_export_lit_toute_la_periode_et_rend_un_csv(construits, egress_vu, monkeypatch):
    inst, _ = construits
    _creds(monkeypatch, _USER)
    inst.list_calls.side_effect = [
        {"calls": [{"StartTime": "2026-09-30T09:00:00+02:00", "SourceDn": "1001",
                    "TalkingDuration": "PT1M16.46S", "RingingDuration": "PT0.03S",
                    "SegmentId": 1, "SrcRecId": 7}], "next_skip": 500},
        {"calls": [{"StartTime": "2026-09-30T10:00:00+02:00", "SourceDn": "1002",
                    "TalkingDuration": "PT1H2M3S"}], "next_skip": None},
    ]
    vus = _render_capture(monkeypatch)
    out = _tool("threecx_call")("2026-09-30", "2026-10-01", op="export", recorded_only=True)
    skips = [c.kwargs["skip"] for c in inst.list_calls.call_args_list]
    assert skips == [0, 500]
    assert all(c.kwargs["top"] == 500 and c.kwargs["recorded_only"] for c in
               inst.list_calls.call_args_list)
    assert out == {"encoding": "url", "url": "https://signed", "rows": 2}
    (f,) = vus
    assert f["filename"] == "appels-3cx-2026-09-30-2026-10-01.csv"
    assert f["mime"] == "text/csv" and f["prefix"] == "threecx-files"
    texte = f["data"].decode("utf-8-sig").splitlines()
    entete = texte[0].split(";")
    assert entete[0] == "StartTime" and "SegmentId" not in entete
    ligne = dict(zip(entete, texte[1].split(";")))
    assert ligne["TalkingDuration"] == "76.5" and ligne["RingingDuration"] == "0.0"
    assert ligne["SrcRecId"] == "7"
    assert dict(zip(entete, texte[2].split(";")))["TalkingDuration"] == "3723.0"


@pytest.mark.parametrize("kw", [{"top": 10}, {"skip": 0}, {"fields": ["*"]}])
def test_export_refuse_les_arguments_de_page(construits, egress_vu, monkeypatch, kw):
    _creds(monkeypatch, _USER)
    with pytest.raises(McpError, match="n'utilise pas"):
        _tool("threecx_call")("2026-09-30", "2026-10-01", op="export", **kw)
    construits[0].list_calls.assert_not_called()


def test_export_borne(construits, egress_vu, monkeypatch):
    from oto_mcp.tools import threecx as X

    inst, _ = construits
    _creds(monkeypatch, _USER)
    monkeypatch.setattr(X, "_EXPORT_PAGES_MAX", 2)
    inst.list_calls.return_value = {"calls": [{}], "next_skip": 1}
    _render_capture(monkeypatch)
    with pytest.raises(McpError, match="resserre"):
        _tool("threecx_call")("2026-09-30", "2026-10-01", op="export")
    assert inst.list_calls.call_count == 2


def test_export_ne_rend_aucune_cellule_texte_en_formule():
    """Un champ venu d'un tiers (nom affiché d'un appelant) qui commence par
    `= + - @`, une tabulation ou un retour chariot est préfixé d'une apostrophe ;
    un numéro `+33…` l'est aussi (texte, `+` gardé) ; une durée reste un nombre."""
    import csv
    import io

    from oto_mcp.tools import threecx as X

    lignes = [
        {"SourceDisplayName": '=HYPERLINK("http://exemple.invalid";"x")',
         "SourceCallerId": "+33612345678", "DestinationDisplayName": "-2+3",
         "Reason": "@SUM(A1)", "DestinationCallerId": "\t=1", "Status": "\r=1",
         "Direction": "Inbound", "SourceDn": "1001", "CallCost": 0.5,
         "TalkingDuration": "PT1M16.46S", "RingingDuration": "-PT1S"},
    ]
    entete, ligne = list(csv.reader(io.StringIO(
        X._csv(lignes).decode("utf-8-sig")), delimiter=";"))
    cell = dict(zip(entete, ligne))
    assert cell["SourceDisplayName"] == '\'=HYPERLINK("http://exemple.invalid";"x")'
    assert cell["SourceCallerId"] == "'+33612345678"
    assert cell["DestinationDisplayName"] == "'-2+3"
    assert cell["Reason"] == "'@SUM(A1)"
    assert cell["DestinationCallerId"] == "'\t=1" and cell["Status"] == "'\r=1"
    assert cell["Direction"] == "Inbound" and cell["SourceDn"] == "1001"
    assert cell["CallCost"] == "0.5" and cell["TalkingDuration"] == "76.5"
    # Une durée illisible reste du texte, donc neutralisée comme tout texte.
    assert cell["RingingDuration"] == "'-PT1S"


def test_duree_illisible_rendue_telle_quelle():
    from oto_mcp.tools import threecx as X

    assert X._secondes("PT41.037054S") == 41.0
    assert X._secondes("n/a") == "n/a" and X._secondes(None) is None


def test_carte_chaque_paire_requise_dans_son_mode():
    from oto_mcp import providers

    c = providers.REGISTRY["threecx"]
    assert c.field_discriminator == "auth_mode"
    for mode, paire in (("api_client", {"client_id", "client_secret"}),
                        ("user", {"username", "password"})):
        champs = {f.name: f for f in c.fields_for({"auth_mode": mode})}
        assert set(champs) == {"base_url", "auth_mode"} | paire
        assert all(champs[n].required for n in paire)
