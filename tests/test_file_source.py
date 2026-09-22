"""Résolveur « fichier côté oto » (oto-backend#60).

Teste le dispatch par `kind` + les gardes (kind inconnu, dépassement de taille)
sans I/O réseau : les clients Drive/Gmail et la résolution de credentials sont
monkeypatchés.
"""
from __future__ import annotations

import sys
import types

import pytest

from oto_mcp import file_source as fs


def _inject(monkeypatch, module_path: str, attr: str, value):
    """Injecte un faux module dans sys.modules pour que le `from … import` lazy de
    file_source résolve un stub — sans importer le vrai client (qui tire l'extra
    `google`, absent du venv de test mais présent en prod)."""
    mod = types.ModuleType(module_path)
    setattr(mod, attr, value)
    monkeypatch.setitem(sys.modules, module_path, mod)


@pytest.fixture(autouse=True)
def _no_google(monkeypatch):
    # Pas de credentials réels : les résolveurs drive/gmail instancient un client
    # stub, on n'atteint jamais Google.
    monkeypatch.setattr(fs, "_google_creds", lambda account: object())


def test_unknown_kind_raises():
    with pytest.raises(fs.FileSourceError):
        fs.resolve({"kind": "ftp", "path": "x"})


def test_non_dict_raises():
    with pytest.raises(fs.FileSourceError):
        fs.resolve("drive:123")


def test_drive_dispatch(monkeypatch):
    class FakeDrive:
        def __init__(self, **kw): pass
        def get_file_bytes(self, file_id):
            return {"filename": "Contrat.pdf", "mimeType": "application/pdf",
                    "size": 4, "data": b"%PDF"}
    _inject(monkeypatch, "oto.tools.google.drive.lib.drive_client", "DriveClient", FakeDrive)
    rf = fs.resolve({"kind": "drive", "file_id": "1AbC"})
    assert rf.data == b"%PDF" and rf.filename == "Contrat.pdf" and rf.mime == "application/pdf"


def test_drive_missing_file_id():
    with pytest.raises(fs.FileSourceError):
        fs.resolve({"kind": "drive"})


def test_gmail_dispatch(monkeypatch):
    class FakeGmail:
        def __init__(self, **kw): pass
        def get_attachment(self, mid, filename, index):
            assert (mid, filename, index) == ("m1", "facture.pdf", 0)
            return {"filename": "facture.pdf", "mimeType": "application/pdf",
                    "size": 3, "data": b"abc"}
    _inject(monkeypatch, "oto.tools.google.gmail.lib.gmail_client", "GmailClient", FakeGmail)
    rf = fs.resolve({"kind": "gmail", "message_id": "m1", "filename": "facture.pdf"})
    assert rf.data == b"abc"


def test_gmail_missing_args():
    with pytest.raises(fs.FileSourceError):
        fs.resolve({"kind": "gmail", "message_id": "m1"})


def test_size_cap_enforced(monkeypatch):
    class FakeDrive:
        def __init__(self, **kw): pass
        def get_file_bytes(self, file_id):
            return {"filename": "big.bin", "mimeType": "application/octet-stream",
                    "size": 10, "data": b"x" * 10}
    _inject(monkeypatch, "oto.tools.google.drive.lib.drive_client", "DriveClient", FakeDrive)
    with pytest.raises(fs.FileSourceError):
        fs.resolve({"kind": "drive", "file_id": "1"}, max_bytes=5)


def test_url_requires_http():
    with pytest.raises(fs.FileSourceError):
        fs.resolve({"kind": "url", "url": "file:///etc/passwd"})


@pytest.mark.parametrize("host", [
    "http://127.0.0.1/x",            # loopback
    "http://localhost/x",            # loopback (résolu)
    "http://169.254.169.254/latest", # IMDS cloud
    "http://10.0.0.5/x",             # privé
    "http://192.168.1.1/x",          # privé
])
def test_url_ssrf_blocked(host):
    # Anti-SSRF : une cible interne/réservée est refusée AVANT toute requête.
    with pytest.raises(fs.FileSourceError):
        fs.resolve({"kind": "url", "url": host})


def test_assert_public_host_passes_for_global():
    # Un host public ne lève pas (résolution réelle d'une IP globale).
    fs._assert_public_host("example.com")


# ── kind="project_file" : un fichier déposé sur un projet (ADR 0074) ──────────

class _S3:
    """Faux client S3 : le VRAI `media_store.fetch_object` s'exerce dessus."""

    def __init__(self, objets):
        self.objets = objets

    def head_object(self, *, Bucket, Key):
        return {"ContentLength": len(self.objets[Key])}

    def get_object(self, *, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objets[Key])}


TROIS_MO = b"ID3" + b"a" * (3 * 1024 * 1024)
FICHIER = {"id": 7, "project_id": 42, "s3_key": "project-files/42/abc/visite.m4a",
           "filename": "visite.m4a", "mime": "audio/mp4"}


@pytest.fixture
def projet(monkeypatch):
    """Le monde par défaut : le projet 42 est visible dans l'org active et lisible,
    il porte le fichier 7. Chaque test ne débranche que ce qu'il éprouve."""
    from oto_mcp import db, media_store, ownership

    etat = {"visible": [], "acces": []}
    monkeypatch.setattr(fs.access, "current_user_sub_or_raise", lambda: "u1")
    monkeypatch.setattr(fs.access, "current_org", lambda sub: 3)

    def _visible(sub, org_id, rtype, rid):
        etat["visible"].append((sub, org_id, rtype, rid))
        return True

    def _acces(sub, rtype, rid, want="read"):
        etat["acces"].append((sub, rtype, rid, want))
        return True

    monkeypatch.setattr(ownership, "visible_in_org", _visible)
    monkeypatch.setattr(ownership, "can_access", _acces)
    monkeypatch.setattr(db, "get_project_file",
                        lambda fid: dict(FICHIER) if fid == FICHIER["id"] else None)
    monkeypatch.setattr(media_store, "_get_client",
                        lambda: _S3({FICHIER["s3_key"]: TROIS_MO}))
    monkeypatch.setattr(media_store, "_bucket", lambda: "media-test")
    monkeypatch.delenv("OTO_MCP_S3_MAX_IMAGE_BYTES", raising=False)
    return etat


def _pf(pid=42, fid=7):
    return {"kind": "project_file", "project_id": pid, "file_id": fid}


def test_project_file_rend_les_octets_nom_et_type(projet):
    """Un fichier de 3 Mo passe : la lecture est bornée par le plafond de
    `file_source` (25 Mo), pas par celui d'une image (2 Mo)."""
    rf = fs.resolve(_pf())
    assert rf.data == TROIS_MO
    assert (rf.filename, rf.mime) == ("visite.m4a", "audio/mp4")


def test_project_file_garde_d_une_lecture_par_id_dans_l_org_active(projet):
    fs.resolve(_pf())
    assert projet["visible"] == [("u1", 3, "project", "42")]
    assert projet["acces"] == [("u1", "project", "42", "read")]


def _message_introuvable(**source):
    with pytest.raises(fs.FileSourceError) as e:
        fs.resolve(_pf(**source))
    return str(e.value)


def test_un_refus_repond_comme_un_fichier_inconnu(projet, monkeypatch):
    """Distinguer « refusé » de « inconnu » dirait à qui n'a pas le droit que le
    fichier existe. Hors de l'org active, sans lecture, fichier absent ou rangé sur
    un AUTRE projet : un seul et même message."""
    from oto_mcp import db, ownership

    attendu = _message_introuvable(fid=999)                    # fichier inconnu
    assert "introuvable" in attendu

    monkeypatch.setattr(ownership, "visible_in_org", lambda *a: False)
    assert _message_introuvable(fid=999) == attendu            # hors de l'org active
    monkeypatch.setattr(ownership, "visible_in_org", lambda *a: True)

    monkeypatch.setattr(ownership, "can_access", lambda *a, **k: False)
    assert _message_introuvable(fid=999) == attendu            # sans droit de lecture
    monkeypatch.setattr(ownership, "can_access", lambda *a, **k: True)

    monkeypatch.setattr(db, "get_project_file",
                        lambda fid: dict(FICHIER, id=999, project_id=41))
    assert _message_introuvable(fid=999) == attendu            # fichier d'un autre projet


def test_un_refus_ne_lit_rien_dans_le_stockage(projet, monkeypatch):
    from oto_mcp import media_store, ownership

    monkeypatch.setattr(ownership, "visible_in_org", lambda *a: False)
    monkeypatch.setattr(media_store, "_get_client",
                        lambda: pytest.fail("le stockage ne doit pas être lu sur un refus"))
    with pytest.raises(fs.FileSourceError):
        fs.resolve(_pf())


def test_project_file_au_dela_du_plafond_est_refuse_avant_lecture(projet):
    with pytest.raises(fs.FileSourceError, match="object_too_large"):
        fs.resolve(_pf(), max_bytes=1024 * 1024)


@pytest.mark.parametrize("source", [
    {"kind": "project_file", "file_id": 7},
    {"kind": "project_file", "project_id": 42},
    {"kind": "project_file", "project_id": "abc", "file_id": 7},
])
def test_project_file_exige_deux_ids_entiers(projet, source):
    with pytest.raises(fs.FileSourceError, match="entiers requis"):
        fs.resolve(source)


def test_le_kind_inconnu_nomme_project_file():
    with pytest.raises(fs.FileSourceError, match="project_file"):
        fs.resolve({"kind": "ftp"})
