"""Tools `sharepoint_excel` / `sharepoint_excel_act` : les cellules d'un classeur Excel,
lues et écrites en place.

Ce que ce fichier verrouille :
- la SURFACE : la lecture en `LECTURE`, l'écriture dans son propre outil en `ACTE` — un
  client MCP autorise des outils, pas des arguments ;
- le dispatch vers les VRAIES méthodes de `WorkbookClient` (autospec), le jeton du service
  `sharepoint` ;
- le classeur adressé comme un fichier : OneDrive par défaut, `drive_id`, `item_id` ou
  `path` ; sans classeur, refus avant tout appel ;
- la plage lue bornée en lignes et dite tronquée ; la plage écrite rendue telle quelle.
"""
import asyncio
from unittest.mock import create_autospec

import pytest
from oto_mcp.mcp_errors import McpError

pytestmark = pytest.mark.exige_pin_oto_core


@pytest.fixture
def classeur(monkeypatch):
    import oto.tools.microsoft as coeur

    w = create_autospec(coeur.WorkbookClient, instance=True)
    f = create_autospec(coeur.FilesClient, instance=True)
    f.get_my_drive.return_value = {"id": "me-drive"}
    jetons = []
    monkeypatch.setattr(coeur, "WorkbookClient",
                        lambda jeton, *, session: jetons.append(jeton) or w)
    monkeypatch.setattr(coeur, "FilesClient", lambda jeton, *, session: f)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")

    def jeton(sub, service):
        assert service == "sharepoint"
        return f"AT-{sub}"

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", jeton)
    w.jetons = jetons
    return w


def _tool(name):
    from fastmcp import FastMCP
    from oto_mcp.tools import sharepoint as X

    m = FastMCP("t")
    X.register(m)
    return asyncio.run(m.get_tool(name)).fn


def test_lecture_et_ecriture_separees():
    from fastmcp import FastMCP
    from oto_mcp.tools import sharepoint as X
    from oto_mcp.tools.lecture import ACTE, LECTURE

    m = FastMCP("t")
    X.register(m)
    outils = {t.name: t for t in asyncio.run(m.list_tools())}
    assert outils["sharepoint_excel"].annotations == LECTURE
    assert outils["sharepoint_excel_act"].annotations == ACTE


def test_feuilles_du_classeur_dans_une_bibliotheque(classeur):
    classeur.list_worksheets.return_value = [
        {"id": "s1", "name": "Octobre", "position": 0, "visibility": "Visible"}]
    out = _tool("sharepoint_excel")(drive_id="d1", path="RH/Centre 1.xlsx")
    assert out == {"sheets": [{"id": "s1", "name": "Octobre", "position": 0,
                               "visibility": "Visible"}], "count": 1}
    classeur.list_worksheets.assert_called_once_with("d1", item_id=None,
                                                     path="RH/Centre 1.xlsx")
    assert classeur.jetons == ["AT-sub-1"]


def test_plage_utilisee_bornee_et_dite_tronquee(classeur):
    classeur.get_range.return_value = {"address": "Octobre!A1:C300", "rowCount": 300,
                                       "columnCount": 3,
                                       "values": [[i, 0, 0] for i in range(300)]}
    out = _tool("sharepoint_excel")(op="range", item_id="i1", sheet="Octobre",
                                    max_rows=10)
    assert len(out["values"]) == 10 and out["truncated"] is True
    classeur.get_range.assert_called_once_with("me-drive", "Octobre", None,
                                               item_id="i1", path=None)


def test_plage_telle_qu_affichee(classeur):
    classeur.get_range.return_value = {"address": "S!A1", "values": [[0.25]],
                                       "text": [["25 %"]]}
    out = _tool("sharepoint_excel")(op="range", item_id="i1", sheet="S", address="A1",
                                    displayed=True)
    assert out["values"] == [["25 %"]] and "truncated" not in out


def test_ecrire_une_plage(classeur):
    classeur.update_range.return_value = {"address": "Octobre!H2:I3", "rowCount": 2,
                                          "columnCount": 2, "values": [[3.5, 1], [0, 2]]}
    out = _tool("sharepoint_excel_act")(op="update_range", drive_id="d1", item_id="i1",
                                        sheet="Octobre", address="H2:I3",
                                        values=[[3.5, 1], [0, 2]])
    assert out == {"address": "Octobre!H2:I3", "rowCount": 2, "columnCount": 2,
                   "values": [[3.5, 1], [0, 2]]}
    classeur.update_range.assert_called_once_with("d1", "Octobre", "H2:I3",
                                                  [[3.5, 1], [0, 2]], item_id="i1",
                                                  path=None)


@pytest.mark.parametrize("nom,args,motif", [
    ("sharepoint_excel", {"op": "range", "sheet": "S"}, "item_id"),
    ("sharepoint_excel", {"op": "range", "item_id": "i1"}, "sheet"),
    ("sharepoint_excel", {"item_id": "i1", "address": "A1"}, "address"),
    ("sharepoint_excel_act", {"op": "update_range", "sheet": "S", "address": "A1",
                              "values": [[1]]}, "item_id"),
])
def test_refus_avant_tout_appel(classeur, nom, args, motif):
    with pytest.raises(McpError, match=motif):
        _tool(nom)(**args)
    classeur.get_range.assert_not_called()
    classeur.update_range.assert_not_called()


# --- le refus WAC : l'API Excel refuse sa session, pas le fichier ---------------------

def _wac():
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(403, {"error": {"code": "accessDenied", "message": (
        "Could not obtain a WAC access token.")}}, service="microsoft")


def test_un_refus_wac_en_lecture_renvoie_au_telechargement(classeur):
    classeur.list_worksheets.side_effect = _wac()
    with pytest.raises(McpError) as exc:
        _tool("sharepoint_excel")(op="sheets", user="collegue@exemple.fr", item_id="it-1")
    msg = str(exc.value)
    assert "Excel for the web" in msg and 'sharepoint_file(op="download")' in msg
    assert "does not have rights" not in msg


def test_un_refus_wac_en_ecriture_dit_comment_faire(classeur):
    classeur.update_range.side_effect = _wac()
    with pytest.raises(McpError, match='conflict="replace"'):
        _tool("sharepoint_excel_act")(op="update_range", item_id="it-1", sheet="S",
                                      address="A1", values=[[1]])


def test_un_autre_403_reste_un_refus_de_droits(classeur):
    from oto.tools.common.errors import UpstreamHTTPError
    classeur.get_range.side_effect = UpstreamHTTPError(
        403, {"error": {"message": "Access denied"}}, service="microsoft")
    with pytest.raises(McpError, match="does not have rights"):
        _tool("sharepoint_excel")(op="range", item_id="it-1", sheet="S")
