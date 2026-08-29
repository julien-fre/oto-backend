"""`oto_upload_url(target="image")` — une image publiée à une URL publique et permanente.

Avant ce lot, aucun chemin MCP ne menait une image à une URL publique stable :
`project_file` dépose un blob PRIVÉ (URL signée qui expire), et la bascule publique
d'un fichier de projet est REST-only. `media_store.upload_image` (public-read, clé par
hash, 2 Mo, type par magic bytes) n'était branchée que sur l'avatar et le logo, en
multipart. La cible `image` est la plus petite exposition de cette fonction : un
upload, une URL, réutilisée d'envoi en envoi (`email_send(image_url=…)`).

Les gardes décrites ici sont celles du SYSTÈME : ce que le seam refuse (taille, type,
octets vides), ce que la clé contient (le hash du contenu, jamais un nom choisi), et
ce que l'accusé rend (l'URL — sinon l'upload est un succès dont personne ne peut rien
faire).
"""
from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("OTO_MCP_OAUTH_STATE_SECRET", "test-secret")

from oto_mcp import media_store, upload_tokens as ut
from oto_mcp.api import uploads as api_uploads
from oto_mcp.capabilities import uploads as U
from oto_mcp.capabilities._types import ResolvedCtx

CTX = ResolvedCtx(sub="u1", org_id=42)

# Un PNG minimal reconnu par le sniff (l'en-tête suffit : on ne décode pas l'image).
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_GIF = b"GIF89a" + b"\x00" * 64
_PDF = b"%PDF-1.7 pas une image"


class _S3:
    """Client S3 qui capture le `put_object` — pas de réseau."""

    def __init__(self):
        self.puts: list[dict] = []

    def put_object(self, **kw):
        self.puts.append(kw)


@pytest.fixture
def s3(monkeypatch):
    client = _S3()
    monkeypatch.setattr(media_store, "_get_client", lambda: client)
    monkeypatch.setattr(media_store, "_bucket", lambda: "media-test")
    monkeypatch.setenv("OTO_MCP_S3_ENDPOINT", "https://s3.fr-par.scw.cloud")
    monkeypatch.delenv("OTO_MCP_S3_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("OTO_MCP_S3_MAX_IMAGE_BYTES", raising=False)
    return client


# --- mint -------------------------------------------------------------------

def test_le_mint_scelle_une_cible_sans_parametre_et_annonce_la_borne_image():
    """Ni projet ni nom de fichier : la clé dérive du contenu. Et la borne annoncée est
    celle qui MORD (2 Mo, `upload_image`), pas le plafond générique de 25 Mo."""
    out = U._upload_url(CTX, U.UploadUrlInput(target="image"))
    p = ut.verify(out["url"].rsplit("/", 1)[1])
    assert p["target"] == {"kind": "image"} and p["sub"] == "u1"
    assert out["max_bytes"] == media_store.max_image_bytes() == 2 * 1024 * 1024
    assert out["headers"] == {"Content-Type": "application/octet-stream"}


def test_la_garde_d_acces_de_l_image_est_le_porteur_seul():
    """Aucune ressource cible à vérifier : la fonction rend sans toucher la base (un
    accès DB lèverait ici, `DATABASE_URL` absent)."""
    assert ut.check_target_access("u1", {"kind": "image"}) is None


def test_le_libelle_humain_dit_ce_qu_on_attend():
    assert "image" in ut.target_label({"kind": "image"})


# --- matérialisation --------------------------------------------------------

def test_la_materialisation_publie_sous_le_sub_et_rend_l_url(monkeypatch):
    vus = {}

    def fake_upload(prefix, owner, data, ct):
        vus.update(prefix=prefix, owner=owner, n=len(data), ct=ct)
        return "https://media-test.s3.fr-par.scw.cloud/images/u1/abc.png"
    monkeypatch.setattr(media_store, "upload_image", fake_upload)
    res = ut.materialize("u1", {"kind": "image"}, _PNG, "application/x-www-form-urlencoded")
    assert res == {"ok": True, "kind": "image", "bytes": len(_PNG),
                   "url": "https://media-test.s3.fr-par.scw.cloud/images/u1/abc.png"}
    # Le type déclaré par curl n'est PAS transmis : `upload_image` sniffe les octets.
    assert vus == {"prefix": "images", "owner": "u1", "n": len(_PNG), "ct": ""}


def test_un_refus_du_seam_image_garde_son_code(monkeypatch):
    def refuse(prefix, owner, data, ct):
        raise media_store.MediaError(400, "unsupported_type", "Formats acceptés : …")
    monkeypatch.setattr(media_store, "upload_image", refuse)
    with pytest.raises(ut.UploadError) as e:
        ut.materialize("u1", {"kind": "image"}, _PDF, None)
    assert (e.value.status, e.value.code) == (400, "unsupported_type")


# --- les gardes d'`upload_image` (le seam réel, client S3 factice) -----------

def test_trop_gros_est_refuse_avant_tout_put(s3):
    with pytest.raises(media_store.MediaError) as e:
        media_store.upload_image("images", "u1", _PNG + b"\x00" * (2 * 1024 * 1024), "")
    assert (e.value.status, e.value.code) == (413, "image_too_large")
    assert s3.puts == []


def test_ce_qui_n_est_pas_une_image_est_refuse_quel_que_soit_le_type_declare(s3):
    with pytest.raises(media_store.MediaError) as e:
        media_store.upload_image("images", "u1", _PDF, "image/png")
    assert (e.value.status, e.value.code) == (400, "unsupported_type")
    with pytest.raises(media_store.MediaError) as vide:
        media_store.upload_image("images", "u1", b"", "image/png")
    assert vide.value.code == "missing_file"
    assert s3.puts == []


def test_gif_accepte_type_derive_des_octets_pas_du_declare(s3):
    url = media_store.upload_image("images", "u1", _GIF, "image/png")
    put = s3.puts[0]
    assert put["ContentType"] == "image/gif" and put["Key"].endswith(".gif")
    assert put["ACL"] == "public-read"
    assert url == f"https://media-test.s3.fr-par.scw.cloud/{put['Key']}"


def test_la_cle_est_le_hash_du_contenu_jamais_un_nom_choisi(s3):
    """Non devinable (128 bits de SHA-256) et idempotente : le même visuel déposé deux
    fois rend la même URL — c'est ce qui autorise « un upload, une URL réutilisée »."""
    a = media_store.upload_image("images", "u1", _PNG, "")
    b = media_store.upload_image("images", "u1", _PNG, "")
    assert a == b
    assert re.fullmatch(r"images/u1/[0-9a-f]{32}\.png", s3.puts[0]["Key"])
    assert s3.puts[0]["CacheControl"].startswith("public, max-age=")


# --- la page humaine (claude.ai sans shell) ---------------------------------

def test_la_page_d_upload_affiche_l_url_rendue_par_l_accuse():
    """Sans ça, le dépôt par formulaire serait un succès inerte : l'URL n'arriverait
    nulle part, et personne ne pourrait la donner à `email_send`."""
    page = api_uploads._upload_page_html(ut.target_label({"kind": "image"}))
    assert "j.url" in page and "image publique" in page
