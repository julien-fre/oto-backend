"""Le type SERVI d'un fichier de projet se décide sur son contenu (oto-backend#562).

⚠️ **Constat de la revue de sécurité du 29/08** : pour un fichier de projet, le type
déclaré — au mint du jeton, sinon l'en-tête de la requête — repartait tel quel dans le
`ContentType` de l'objet, donc dans ce que servent son URL présignée puis son URL
publique. Un HTML déclaré `text/plain` ou `image/png` était servi comme tel, et un
`text/html` avoué comme une page active. La voie image du même module, elle, reniflait
déjà les octets.

Décision (Alexis, 23/09) : rien n'est refusé ; ce qui est actif ou non reconnu est
écrit en `application/octet-stream` et servi en téléchargement.
"""
from __future__ import annotations

import pytest

from oto_mcp import media_store, upload_tokens

_HTML = b"<!DOCTYPE html><html><body><script>fetch('/api/me')</script></body></html>"


class _S3:
    def __init__(self):
        self.puts: list[dict] = []

    def put_object(self, **kw):
        self.puts.append(kw)


@pytest.fixture
def s3(monkeypatch):
    faux = _S3()
    monkeypatch.setattr(media_store, "_get_client", lambda: faux)
    monkeypatch.setattr(media_store, "_bucket", lambda: "media-test")
    return faux


@pytest.mark.parametrize("contenu, declare", [
    (_HTML, "text/plain"),
    (_HTML, "image/png"),
    (_HTML, "text/html"),
    (b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>", "image/svg+xml"),
    (b"  <?xml version='1.0'?><x/>", "text/csv"),
    (b"alert(document.cookie)", "application/javascript"),
    (b"\x00\x01\x02 inconnu", "application/x-quelconque"),
    (b"sans type", None),
])
def test_un_contenu_actif_ou_inconnu_est_ecrit_neutre_et_en_telechargement(s3, contenu,
                                                                           declare):
    """⚠️ LE test : c'est ce que l'objet SERVIRA, pas ce qu'on a déclaré."""
    media_store.upload_object("project-files", "5", contenu, declare, "piege.txt",
                              max_bytes=10_000)
    [put] = s3.puts
    assert put["ContentType"] == "application/octet-stream", put["ContentType"]
    assert put["ContentDisposition"].startswith("attachment"), put


@pytest.mark.parametrize("contenu, declare, servi", [
    (b"%PDF-1.7 ...", "application/octet-stream", "application/pdf"),
    (b"%PDF-1.7 ...", "text/html", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n...", "text/html", "image/png"),
    (b"a,b\n1,2\n", "text/csv", "text/csv"),
    (b"# Titre\n\nDu texte.", "text/markdown; charset=utf-8", "text/markdown"),
    (b"PK\x03\x04...", "application/vnd.openxmlformats-officedocument."
                       "wordprocessingml.document",
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    (b"PK\x03\x04...", "text/html", "application/zip"),
])
def test_un_contenu_reconnu_et_passif_garde_un_type_affichable(s3, contenu, declare,
                                                              servi):
    """Aucun fichier légitime n'est refusé, et un format reconnu reste affichable :
    la signature fait foi, le déclaré ne départage qu'une famille de conteneurs."""
    media_store.upload_object("project-files", "5", contenu, declare, "f",
                              max_bytes=10_000)
    [put] = s3.puts
    assert put["ContentType"] == servi
    assert "ContentDisposition" not in put


def test_le_lien_signe_enregistre_le_type_SERVI_pas_le_declare(s3, monkeypatch):
    """Bout en bout sur la voie de l'issue : le jeton déclare `text/plain`, le corps
    est du HTML. L'objet ET la ligne du fichier portent le type neutre."""
    import oto_mcp.db as db
    lignes = []
    monkeypatch.setattr(db, "add_project_file",
                        lambda pid, key, filename, **k: lignes.append(k) or
                        {"id": 1, "s3_key": key, "filename": filename})
    monkeypatch.setattr(db, "log_project_activity", lambda *a, **k: None)
    cible = {"kind": "project_file", "project_id": 5, "filename": "note.txt",
             "title": None, "description": None, "content_type": "text/plain"}
    upload_tokens.materialize("u1", cible, _HTML, None)
    [put] = s3.puts
    assert put["ContentType"] == "application/octet-stream"
    assert put["ContentDisposition"].startswith("attachment")
    assert lignes[0]["mime"] == "application/octet-stream"
