"""Un tableur `.xlsx` rendu à l'agent en CSV, feuille par feuille (oto#181).

Arbitrage du 23/09 : un `.xlsx` reçu par Drive, par mail ou par Slack est rendu **en
CSV par feuille**, borné comme les autres fichiers, avec `sheet` (la feuille voulue) et
`max_rows` (la borne de lignes) — sur le modèle de l'export d'une Google Sheet en CSV.

Avant, le fichier partait en URL signée : l'agent recevait des octets ZIP qu'il ne
sait pas lire. Et l'extracteur des fichiers de projet sortait une cellule par ligne :
la structure en lignes et colonnes, qui EST l'information d'un tableur, était perdue.

Le classeur de test est fabriqué ici (plusieurs feuilles, une date, une formule avec
sa valeur calculée, une feuille vide, une feuille longue) : pas de fichier du disque,
le test doit tourner en CI.
"""
from __future__ import annotations

import datetime as dt
import io
import zipfile

import pytest

from oto_mcp import file_content, file_extract as fe, media_store

openpyxl = pytest.importorskip("openpyxl")

LONGUE = 450          # lignes de données de la feuille longue (> borne par défaut)


def _classeur() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Devis"
    ws.append(["Date", "Article", "Montant", "Payé"])
    ws.append([dt.datetime(2026, 9, 1), "Vis, inox", 12.5, True])
    ws.append([dt.datetime(2026, 9, 2, 14, 30), 'Écrou "M6"', 3.0, False])
    ws["C4"] = "=SUM(C2:C3)"
    wb.create_sheet("Vide")
    longue = wb.create_sheet("Articles")
    longue.append(["ref", "libellé"])
    for i in range(LONGUE):
        longue.append([i, f"article {i}"])
    buf = io.BytesIO()
    wb.save(buf)
    return _avec_valeur_calculee(buf.getvalue(), "C4", "15.5")


def _avec_valeur_calculee(data: bytes, cellule: str, valeur: str) -> bytes:
    """openpyxl écrit la formule SANS sa valeur en cache (il ne calcule pas) ; Excel et
    LibreOffice, eux, écrivent les deux. On pose la valeur à la main pour avoir le cas
    réel : une formule dont le résultat est connu."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for it in src.infolist():
            contenu = src.read(it.filename)
            if it.filename == "xl/worksheets/sheet1.xml":
                texte = contenu.decode()
                debut = texte.index(f'<c r="{cellule}"')
                fin = texte.index("</c>", debut)
                bloc = texte[debut:fin]
                assert "<f>" in bloc and bloc.endswith("<v></v>"), bloc
                texte = texte[:fin - len("<v></v>")] + f"<v>{valeur}</v>" + texte[fin:]
                contenu = texte.encode()
            dst.writestr(it, contenu)
    return out.getvalue()


XLSX = _classeur()
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ── le rendu pur ─────────────────────────────────────────────────────────────

def test_chaque_feuille_est_un_csv_avec_son_en_tete():
    r = fe.render_xlsx_csv(XLSX)
    assert [s.name for s in r.sheets] == ["Devis", "Vide", "Articles"]
    assert r.sheet_names == ("Devis", "Vide", "Articles")
    blocs = r.text.split("\n\n")
    assert blocs[0].splitlines()[0] == (
        '# sheet=0 name="Devis" rows_total=4 rows_rendered=4 truncated=false')
    assert blocs[0].splitlines()[1:] == [
        "Date,Article,Montant,Payé",
        '2026-09-01,"Vis, inox",12.5,TRUE',
        '2026-09-02T14:30:00,"Écrou ""M6""",3,FALSE',
        ",,15.5",
    ]


def test_la_formule_rend_sa_valeur_jamais_son_texte():
    r = fe.render_xlsx_csv(XLSX, sheet="Devis")
    assert "SUM" not in r.text and "=" not in r.text.split("\n", 1)[1]
    assert ",,15.5" in r.text


def test_une_feuille_vide_se_dit_vide():
    r = fe.render_xlsx_csv(XLSX, sheet="Vide")
    assert r.text == '# sheet=1 name="Vide" rows_total=0 rows_rendered=0 truncated=false'
    assert r.sheets[0].rows_total == 0 and not r.sheets[0].truncated


def test_la_feuille_longue_est_bornee_et_le_dit():
    r = fe.render_xlsx_csv(XLSX, sheet=2)
    s = r.sheets[0]
    assert (s.name, s.rows_total, s.rows_rendered, s.truncated) == (
        "Articles", LONGUE + 1, fe.DEFAULT_SHEET_ROWS, True)
    lignes = r.text.splitlines()
    assert lignes[0] == (f'# sheet=2 name="Articles" rows_total={LONGUE + 1} '
                         f'rows_rendered={fe.DEFAULT_SHEET_ROWS} truncated=true')
    assert len(lignes) == 1 + fe.DEFAULT_SHEET_ROWS


def test_max_rows_leve_la_borne():
    r = fe.render_xlsx_csv(XLSX, sheet="Articles", max_rows=1000)
    assert r.sheets[0].rows_rendered == LONGUE + 1 and not r.sheets[0].truncated
    assert r.text.splitlines()[-1] == f"{LONGUE - 1},article {LONGUE - 1}"


def test_la_borne_de_caracteres_coupe_a_la_ligne_et_le_dit():
    r = fe.render_xlsx_csv(XLSX, max_rows=1000, max_chars=400)
    assert len(r.text) <= 400 + 200            # un en-tête + la ligne des omises
    assert r.truncated
    assert any(s.truncated for s in r.sheets)


def test_les_feuilles_hors_budget_sont_omises_et_c_est_dit():
    r = fe.render_xlsx_csv(XLSX, max_chars=150)
    assert [s.name for s in r.sheets] == ["Devis"]
    assert r.sheet_names == ("Devis", "Vide", "Articles")
    assert r.truncated
    assert r.text.splitlines()[-1] == ("# 2 more sheet(s) not rendered (size limit) — "
                                       "ask one with sheet=<index or name>, see sheet_names")
    # jamais une ligne CSV coupée en deux
    for ligne in r.text.splitlines():
        assert ligne.startswith("#") or ligne.count(",") >= 1 or ligne == ""


def test_la_feuille_se_choisit_par_nom_ou_par_index():
    assert fe.render_xlsx_csv(XLSX, sheet="Articles").sheets[0].index == 2
    assert fe.render_xlsx_csv(XLSX, sheet=2).sheets[0].name == "Articles"
    assert fe.render_xlsx_csv(XLSX, sheet="2").sheets[0].name == "Articles"


def test_une_feuille_inconnue_nomme_les_feuilles_existantes():
    with pytest.raises(fe.SpreadsheetError) as e:
        fe.render_xlsx_csv(XLSX, sheet="Budget")
    assert e.value.status == "sheet_not_found"
    assert "Devis" in str(e.value) and "Articles" in str(e.value)
    with pytest.raises(fe.SpreadsheetError):
        fe.render_xlsx_csv(XLSX, sheet=7)


@pytest.mark.parametrize("n", [0, -1, fe.MAX_SHEET_ROWS + 1])
def test_max_rows_hors_bornes_est_refuse(n):
    with pytest.raises(fe.SpreadsheetError) as e:
        fe.render_xlsx_csv(XLSX, max_rows=n)
    assert e.value.status == "invalid_argument"


def test_un_fichier_corrompu_leve_une_erreur_nommee():
    with pytest.raises(fe.SpreadsheetError) as e:
        fe.render_xlsx_csv(b"PK\x03\x04 pas vraiment un zip")
    assert e.value.status == fe.FAILED


def test_une_archive_demesuree_est_refusee_avant_lecture():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/gros.xml", "a" * (fe.MAX_UNCOMPRESSED_BYTES + 1024))
    with pytest.raises(fe.SpreadsheetError) as e:
        fe.render_xlsx_csv(buf.getvalue())
    assert e.value.status == fe.TOO_LARGE


# ── le branchement : render_for_agent (Drive, Gmail, Slack) ───────────────────

def _pas_de_s3(monkeypatch):
    def _interdit(*a, **k):
        raise AssertionError("un tableur ne part plus en URL signée")
    monkeypatch.setattr(media_store, "upload_private", _interdit)


def _s3_qui_note(monkeypatch) -> list:
    deposes = []

    def _depot(prefix, sub, data, mime, filename):
        deposes.append((prefix, sub, data, mime, filename))
        return "https://signed/brut"
    monkeypatch.setattr(media_store, "upload_private", _depot)
    monkeypatch.setattr(media_store, "presign_expiry", lambda: 3600)
    return deposes


def test_render_for_agent_rend_un_xlsx_en_csv_inline(monkeypatch):
    _s3_qui_note(monkeypatch)
    out = file_content.render_for_agent(XLSX, "devis.xlsx", MIME, sub="s", prefix="drive-files")
    assert out["encoding"] == "text" and out["format"] == "csv"
    assert out["sheet_names"] == ["Devis", "Vide", "Articles"]
    assert out["sheets"][2] == {"index": 2, "name": "Articles", "rows_total": LONGUE + 1,
                                "rows_rendered": fe.DEFAULT_SHEET_ROWS, "truncated": True}
    assert out["truncated"] is True
    assert '2026-09-01,"Vis, inox",12.5,TRUE' in out["content"]
    assert out["size"] == len(XLSX)


def test_render_for_agent_transmet_sheet_et_max_rows(monkeypatch):
    _pas_de_s3(monkeypatch)
    out = file_content.render_for_agent(XLSX, "devis.xlsx", MIME, sub="s", prefix="p",
                                        sheet="Articles", max_rows=1000)
    assert [s["name"] for s in out["sheets"]] == ["Articles"]
    assert out["truncated"] is False


def test_le_mime_suffit_quand_le_nom_n_a_pas_d_extension(monkeypatch):
    _s3_qui_note(monkeypatch)
    out = file_content.render_for_agent(XLSX, "export", MIME, sub="s", prefix="p")
    assert out["format"] == "csv"


def test_un_tableur_tronque_donne_aussi_le_fichier_brut(monkeypatch):
    """Signal #1152 : rendu à 200 lignes sur 2 414, sans chemin vers les données. Le
    CSV inline reste (l'agent le lit) ; le fichier ORIGINAL part en URL signée."""
    deposes = _s3_qui_note(monkeypatch)
    out = file_content.render_for_agent(XLSX, "devis.xlsx", MIME, sub="s",
                                        prefix="gmail-attachments")
    assert out["truncated"] is True and out["encoding"] == "text"
    assert out["raw_url"] == "https://signed/brut" and out["raw_expires_in"] == 3600
    assert deposes == [("gmail-attachments", "s", XLSX, MIME, "devis.xlsx")]


def test_un_tableur_entier_ne_depose_rien(monkeypatch):
    _pas_de_s3(monkeypatch)
    out = file_content.render_for_agent(XLSX, "devis.xlsx", MIME, sub="s", prefix="p",
                                        sheet="Articles", max_rows=1000)
    assert out["truncated"] is False and "raw_url" not in out


def test_sans_stockage_le_tronque_le_dit_sans_echouer(monkeypatch):
    def _pas_de_bucket(*a, **k):
        raise media_store.MediaError(503, "storage_unavailable", "S3 non configuré")
    monkeypatch.setattr(media_store, "upload_private", _pas_de_bucket)
    out = file_content.render_for_agent(XLSX, "devis.xlsx", MIME, sub="s", prefix="p")
    assert out["truncated"] is True and "raw_url" not in out
    assert "S3 non configuré" in out["raw_unavailable"]


def test_sheet_sur_un_fichier_qui_n_est_pas_un_tableur_est_refuse():
    with pytest.raises(fe.SpreadsheetError) as e:
        file_content.render_for_agent(b"a,b\n", "x.csv", "text/csv", sub="s", prefix="p",
                                      sheet=1)
    assert e.value.status == "not_a_spreadsheet"


def test_render_for_agent_ne_masque_pas_un_tableur_corrompu(monkeypatch):
    _pas_de_s3(monkeypatch)
    with pytest.raises(fe.SpreadsheetError):
        file_content.render_for_agent(b"PK\x03\x04 casse", "cassé.xlsx", MIME,
                                      sub="s", prefix="p")


# ── l'index de recherche des fichiers de projet : même lecture, même forme ────

def test_l_index_garde_une_cellule_par_ligne_mais_lit_les_valeurs_calculees():
    """Le worker d'extraction partage la LECTURE (valeurs calculées, dates ISO) mais
    garde sa forme d'index — une cellule par ligne —, pour ne pas changer ce que la
    recherche plein texte trouve (cf. docstring de `_extract_xlsx`)."""
    out = fe.extract(XLSX, "devis.xlsx")
    assert out.ok
    lignes = out.text.splitlines()
    assert "Vis, inox" in lignes and "2026-09-01" in lignes and "15.5" in lignes
    assert not any("SUM" in l for l in lignes)
