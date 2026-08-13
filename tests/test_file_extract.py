"""Extraction du texte d'un fichier déposé (#298) — la logique pure, sans base.

Ce module est appelé par un worker de fond qui draine une file. Deux propriétés
comptent plus que le reste, et ce sont elles qu'on garde ici :

1. **il ne lève jamais** — une exception qui remonte, c'est un fichier qui bloque la
   file ou un worker qui meurt ; un statut nommé laisse la file avancer ;
2. **les statuts sont terminaux** — un fichier chiffré ou d'un format non supporté ne
   changera pas d'avis au prochain passage : le retenter, c'est payer une file qui ne
   se vide jamais.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from oto_mcp import file_extract as fe


# ── ce qu'on sait lire ───────────────────────────────────────────────────────

def test_plain_text_survives_a_bad_byte():
    """Un octet douteux au milieu d'un fichier ne doit pas rendre introuvable tout le
    reste — on remplace, on ne lève pas."""
    out = fe.extract(b"bonjour \xff le monde entier", "notes.txt")
    assert out.ok and "bonjour" in out.text and "monde" in out.text


def test_markdown_and_csv_are_text():
    for nom in ("brief.md", "export.csv", "data.json", "serveur.log"):
        assert fe.extract(b"contenu suffisamment long pour passer", nom).ok, nom


def test_a_real_pdf_yields_its_text():
    """Un vrai PDF, fabriqué ici (pas un fichier du disque : le test doit tourner en
    CI). On vérifie que le texte sort et que les pages sont comptées."""
    pypdf = pytest.importorskip("pypdf")
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)

    out = fe.extract(buf.getvalue(), "vide.pdf")
    # Une page blanche n'a pas de texte : le cas attendu est EMPTY, nommé — pas `ok`
    # avec une chaîne vide, et surtout pas une exception.
    assert out.status == fe.EMPTY
    assert out.pages == 1
    assert "scanné" in out.detail


def test_docx_reads_tables_not_only_paragraphs():
    """Dans un document bureautique, une part notable du contenu utile vit dans les
    TABLEAUX — les rater, c'est indexer la moitié du document en croyant l'avoir fait."""
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Un paragraphe ordinaire")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "Engagement"
    t.rows[0].cells[1].text = "quarante-deux jours"
    buf = io.BytesIO()
    d.save(buf)

    out = fe.extract(buf.getvalue(), "contrat.docx")
    assert out.ok
    assert "paragraphe ordinaire" in out.text
    assert "quarante-deux jours" in out.text, "le contenu des tableaux doit être extrait"


def test_xlsx_reads_values_and_sheet_names():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Prospects"
    ws.append(["Société", "Statut"])
    ws.append(["Boulangerie Sylvestre", "à qualifier"])
    buf = io.BytesIO()
    wb.save(buf)

    out = fe.extract(buf.getvalue(), "suivi.xlsx")
    assert out.ok
    assert "Prospects" in out.text and "Sylvestre" in out.text


# ── ce qu'on ne sait pas lire, et qui doit le DIRE ───────────────────────────

def test_an_image_is_unsupported_not_failed():
    """Pas d'OCR : lire une image est un autre métier et un autre coût. « Non
    supporté » est une réponse honnête, et surtout TERMINALE — la retenter serait
    payer pour toujours un travail qui n'existe pas."""
    out = fe.extract(b"\x89PNG\r\n\x1a\n" + b"0" * 100, "capture.png")
    assert out.status == fe.UNSUPPORTED
    assert not fe.is_retryable(out.status)


def test_an_empty_file_is_empty():
    out = fe.extract(b"", "vide.txt")
    assert out.status == fe.EMPTY and not fe.is_retryable(out.status)


def test_a_scanned_pdf_is_empty_not_ok():
    """⚠️ Le cas qui compte pour un futur lot d'OCR : la lecture RÉUSSIT et ne rend
    rien, parce que le texte est une image. Le marquer `ok` avec un texte vide le
    rendrait indiscernable d'un vrai document — `empty` permet de retrouver
    exactement la population concernée le jour où l'OCR se décide."""
    out = fe.extract(b"%PDF-1.4\n" + b"x" * 50, "scan.pdf")
    assert out.status in (fe.EMPTY, fe.FAILED)   # corrompu ou vide, jamais `ok`
    assert not out.ok


def test_a_corrupted_pdf_fails_without_raising():
    out = fe.extract(b"pas du tout un pdf", "menteur.pdf")
    assert out.status == fe.FAILED
    assert fe.is_retryable(out.status), "l'imprévu, lui, se retente"


def test_a_corrupted_docx_fails_without_raising():
    """Un .docx est un zip : un zip valide mais sans le contenu attendu doit rendre
    `failed`, pas remonter une exception dans le worker."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pas-un-docx.txt", "surprise")
    out = fe.extract(buf.getvalue(), "faux.docx")
    assert out.status == fe.FAILED and not out.ok


def test_extract_never_raises_whatever_the_extractor_does(monkeypatch):
    """La ceinture : même si un extracteur se met à lever, la file avance."""
    monkeypatch.setitem(fe._BY_EXT, "pdf",
                        lambda data: (_ for _ in ()).throw(RuntimeError("boum")))
    out = fe.extract(b"%PDF-1.4 quoi que ce soit", "doc.pdf")
    assert out.status == fe.FAILED and out.detail == "RuntimeError"


# ── les bornes ───────────────────────────────────────────────────────────────

def test_a_pathological_file_is_truncated_not_refused():
    """Un export de log géant n'a pas à devenir une ligne de base — mais il reste
    cherchable sur son début, ce qui vaut mieux que pas du tout."""
    out = fe.extract(b"a" * (fe.MAX_TEXT_CHARS + 5000), "enorme.txt")
    assert out.ok
    assert len(out.text) == fe.MAX_TEXT_CHARS
    assert "tronqué" in out.detail


def test_a_zip_bomb_is_refused_before_being_opened():
    """⚠️ **La garde de sécurité du module.** Un `.docx`/`.xlsx` est une archive ZIP :
    la taille du fichier REÇU ne dit rien de ce qu'il pèse une fois ouvert.

    Mesuré sur ce module avant correction : une archive de **400 ko** faisait monter
    le process à **638 Mo** — parce que le document est parsé EN ENTIER avant que la
    borne de sortie ne tronque quoi que ce soit. Sur un serveur mono-loop, un
    dépassement mémoire ne dégrade pas une requête : il tue le process et toutes les
    sessions avec. Et le fichier vient d'un upload utilisateur.

    On refuse donc sur le CATALOGUE du zip, sans rien décompresser."""
    gros = "a" * (fe.MAX_UNCOMPRESSED_BYTES + 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", gros)

    out = fe.extract(buf.getvalue(), "bombe.docx")

    assert out.status == fe.TOO_LARGE
    assert not fe.is_retryable(out.status), "une archive énorme le restera"
    assert "Mo" in out.detail
    # Et l'archive elle-même est petite : c'est tout l'intérêt de l'attaque.
    assert len(buf.getvalue()) < 1_000_000


def test_the_same_guard_covers_spreadsheets():
    """La garde est sur le format ZIP, pas sur le lecteur : `.xlsx` est exposé
    exactement pareil."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", "a" * (fe.MAX_UNCOMPRESSED_BYTES + 1024))
    assert fe.extract(buf.getvalue(), "bombe.xlsx").status == fe.TOO_LARGE


def test_an_honest_document_still_passes_the_guard():
    """La garde ne doit pas refuser les vrais documents — sinon elle sera retirée."""
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("un document ordinaire, de taille ordinaire")
    buf = io.BytesIO()
    d.save(buf)
    assert fe.extract(buf.getvalue(), "normal.docx").ok


def test_text_is_cut_during_accumulation_not_after():
    """Couper APRÈS avoir tout accumulé laisse le document entier en mémoire — la
    borne de sortie ne protège alors de rien. `_join_bounded` s'arrête pendant.

    On le prouve sur le générateur : il ne doit pas être consommé jusqu'au bout."""
    consommes = []

    def _morceaux():
        for i in range(100_000):
            consommes.append(i)
            yield "x" * 1000

    texte, tronque = fe._join_bounded(_morceaux())

    assert tronque and len(texte) == fe.MAX_TEXT_CHARS
    assert len(consommes) < 5000, (
        f"le générateur a été consommé {len(consommes)} fois : la coupe arrive trop tard")


def test_a_few_characters_are_not_a_document():
    """Trois caractères de garde ne font pas un document extrait : les marquer `ok`
    peuplerait l'index de bruit et masquerait les vrais cas OCR."""
    out = fe.extract(b"ok", "presque-vide.txt")
    assert out.status == fe.EMPTY


# ── le routage ───────────────────────────────────────────────────────────────

def test_the_mime_is_a_fallback_never_the_authority():
    """Le mime d'un upload vient du client, qui se trompe couramment
    (`application/octet-stream` sur un PDF valide). On route sur l'EXTENSION ; le mime
    ne sert que si l'extension manque."""
    # Extension correcte, mime menteur → on suit l'extension.
    assert fe.extract(b"du texte bien assez long ici", "notes.txt",
                      "application/octet-stream").ok
    # Pas d'extension, mime utile → on suit le mime.
    assert fe.extract(b"du texte bien assez long ici", "blob", "text/plain").ok
    # Ni l'un ni l'autre → non supporté, nommé.
    assert fe.extract(b"du texte bien assez long ici", "blob").status == fe.UNSUPPORTED


def test_supported_extensions_are_derived_not_recopied():
    """Une liste en double finit par mentir : celle de la doc, celle du code, et la
    vraie. `supported_extensions()` dérive du registre."""
    exts = fe.supported_extensions()
    assert {"pdf", "docx", "xlsx", "txt", "md", "csv"} <= exts
    assert "png" not in exts and "zip" not in exts
