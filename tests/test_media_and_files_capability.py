"""Images et fichiers de projet, en capacités : et le silence destructeur qu'on ferme.

Cinq routes de forme JSON ont quitté `api_routes_media.py` / `api_routes_projects.py`
pour `capabilities/media_and_files.py` (27/08). Leurs quatre voisines — trois `POST`
multipart et l'export ZIP — sont reclassées **NATURE** : l'adaptateur lit du JSON et
répond en JSON, un corps binaire et une réponse `application/zip` sont hors du moule par
CONSTRUCTION. C'est la FORME qui tranche, pas le domaine.

Trois choses gardées ici :

1. **`public` est REQUIS, et c'est un durcissement voulu.** Le handler d'origine faisait
   `bool(isinstance(body, dict) and body.get("public"))` : un corps SANS `public` valait
   « rendre privé », en silence. Combiné à l'adaptateur — qui avale un corps illisible et
   le traite comme absent — un JSON malformé aurait DÉPARTAGÉ le fichier en rendant 200,
   là où la route rendait un 400 franc. On refuse plutôt que d'agir.
2. **L'ordre des refus du logo d'org** : id illisible (400) → org inconnue (404) →
   non-admin (403). `ORG_ADMIN_OF` rendrait 403 sur une org inconnue.
3. **`s3_key` ne sort jamais** : la clé de stockage est remplacée par une `download_url`
   signée et temporaire.
"""
from __future__ import annotations

import pytest

from _datastore_rest import call, stub_authz

from oto_mcp import media_store, ownership
from oto_mcp.capabilities import media_and_files as mf

_LIGNE = {"id": 3, "project_id": 12, "s3_key": "k/3", "filename": "cr.pdf",
          "mime": "application/pdf", "size_bytes": 42, "title": "CR",
          "description": None, "summary": None, "public": False, "public_url": None,
          "created_by": "u-1", "created_at": "2026-08-01"}


@pytest.fixture()
def socle(monkeypatch):
    vus: list = []
    monkeypatch.setattr(mf.db, "get_user", lambda sub: {"avatar_url": "https://x/a.png"})
    monkeypatch.setattr(mf.db, "set_avatar_url",
                        lambda sub, url: vus.append(("avatar", sub, url)))
    monkeypatch.setattr(mf.org_store, "get_org",
                        lambda oid: {"id": oid, "logo_url": "https://x/l.png"} if oid == 35 else None)
    monkeypatch.setattr(mf.org_store, "set_org_logo",
                        lambda oid, url: vus.append(("logo", oid, url)))
    from oto_mcp import roles
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, oid: True)
    monkeypatch.setattr(mf.access, "current_org", lambda sub: 35)
    monkeypatch.setattr(ownership, "visible_in_org", lambda sub, org, t, i: True)
    monkeypatch.setattr(ownership, "can_access", lambda sub, t, i, mode: True)
    monkeypatch.setattr(mf.db, "get_project_by_id", lambda pid: {"id": pid})
    monkeypatch.setattr(mf.db, "list_project_files", lambda pid: [dict(_LIGNE)])
    monkeypatch.setattr(mf.db, "get_project_file", lambda fid: dict(_LIGNE))
    monkeypatch.setattr(mf.db, "delete_project_file",
                        lambda fid: vus.append(("suppr", fid)))
    monkeypatch.setattr(mf.db, "set_project_file_public",
                        lambda fid, pub, url: dict(_LIGNE, public=pub, public_url=url))
    monkeypatch.setattr(mf.db, "log_project_activity",
                        lambda *a: vus.append(("journal",) + a[2:]))
    monkeypatch.setattr(media_store, "presign_get", lambda k: f"https://signed/{k}")
    monkeypatch.setattr(media_store, "delete_by_url",
                        lambda u: vus.append(("purge_url", u)))
    monkeypatch.setattr(media_store, "delete_by_key",
                        lambda k: vus.append(("purge_key", k)))
    monkeypatch.setattr(media_store, "make_public", lambda k: f"https://public/{k}")
    monkeypatch.setattr(media_store, "make_private", lambda k: vus.append(("prive", k)))
    return vus


# --- Images -----------------------------------------------------------------

def test_effacer_l_avatar_purge_aussi_l_objet_stocke(monkeypatch, socle):
    """Sans la purge, le stockage garderait un orphelin que plus rien ne référence."""
    stub_authz(monkeypatch)
    code, out = call("me.avatar.clear")
    assert (code, out) == (200, {"ok": True})
    assert socle == [("avatar", "u-1", None), ("purge_url", "https://x/a.png")]


def test_effacer_un_avatar_absent_ne_purge_rien(monkeypatch, socle):
    stub_authz(monkeypatch)
    monkeypatch.setattr(mf.db, "get_user", lambda sub: {"avatar_url": None})
    assert call("me.avatar.clear")[0] == 200
    assert socle == [("avatar", "u-1", None)]


def test_l_ordre_des_refus_du_logo_est_preserve(monkeypatch, socle):
    """⚠️ `ORG_ADMIN_OF` rendrait 403 sur une org INCONNUE, là où cette route rend 404
    depuis toujours — et 400 sur un id illisible, là où pydantic dirait `invalid_input`.
    D'où `SUB_ONLY` + escalade au handler, comme `me_credentials._clear`."""
    stub_authz(monkeypatch)
    code, out = call("org.logo.clear", path_params={"id": "zz"})
    assert (code, out["error"]) == (400, "invalid_id")
    code, out = call("org.logo.clear", path_params={"id": "999"})
    assert (code, out["error"]) == (404, "unknown_org")
    from oto_mcp import roles
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, oid: False)
    code, out = call("org.logo.clear", path_params={"id": "35"})
    assert (code, out["error"]) == (403, "forbidden")
    assert socle == [], "aucune écriture quand le geste est refusé"


def test_effacer_le_logo_uploade(monkeypatch, socle):
    stub_authz(monkeypatch)
    code, out = call("org.logo.clear", path_params={"id": "35"})
    assert (code, out) == (200, {"ok": True})
    assert socle == [("logo", 35, None), ("purge_url", "https://x/l.png")]


# --- Fichiers d'un projet ---------------------------------------------------

def test_la_liste_signe_les_liens_et_ne_rend_JAMAIS_la_cle_de_stockage(monkeypatch, socle):
    """`s3_key` sortirait l'adresse interne de l'objet. Elle est retirée de la ligne et
    remplacée par une URL signée, temporaire — d'où l'absence de `s3_key` dans `Output`."""
    stub_authz(monkeypatch)
    code, out = call("me.project_file.list", path_params={"project_id": 12})
    assert code == 200, out
    f = out["files"][0]
    assert "s3_key" not in f
    assert f["download_url"] == "https://signed/k/3"
    assert set(f) == set(mf.ProjectFile.model_fields)


def test_un_stockage_muet_rend_la_ligne_sans_lien(monkeypatch, socle):
    """La ligne reste servie : perdre le lien de téléchargement ne doit pas faire
    disparaître le fichier de la liste."""
    stub_authz(monkeypatch)

    def _boum(k):
        raise media_store.MediaError(503, "storage_unavailable")

    monkeypatch.setattr(media_store, "presign_get", _boum)
    _, out = call("me.project_file.list", path_params={"project_id": 12})
    assert out["files"][0]["download_url"] is None


def test_un_projet_inconnu_et_un_projet_hors_org_rendent_le_meme_404(monkeypatch, socle):
    """404 non-disclosante : un projet accessible via une AUTRE de mes orgs ne doit pas
    se distinguer d'un projet inexistant, sinon la 404 devient un oracle d'existence."""
    stub_authz(monkeypatch)
    monkeypatch.setattr(mf.db, "get_project_by_id", lambda pid: None)
    assert call("me.project_file.list",
                path_params={"project_id": 12})[1]["error"] == "unknown_project"
    monkeypatch.setattr(mf.db, "get_project_by_id", lambda pid: {"id": pid})
    monkeypatch.setattr(ownership, "visible_in_org", lambda *a: False)
    code, out = call("me.project_file.list", path_params={"project_id": 12})
    assert (code, out["error"]) == (404, "unknown_project")


def test_supprimer_un_fichier_purge_l_objet_et_journalise(monkeypatch, socle):
    stub_authz(monkeypatch)
    code, out = call("me.project_file.delete",
                     path_params={"project_id": 12, "file_id": 3})
    assert (code, out) == (200, {"ok": True})
    assert ("suppr", 3) in socle and ("purge_key", "k/3") in socle
    assert any(v[0] == "journal" for v in socle)


def test_un_fichier_d_un_AUTRE_projet_est_un_404(monkeypatch, socle):
    """L'appartenance est vérifiée : sinon `DELETE /projects/12/files/3` supprimerait un
    fichier du projet 99 pour peu qu'on en connaisse l'id."""
    stub_authz(monkeypatch)
    monkeypatch.setattr(mf.db, "get_project_file", lambda fid: dict(_LIGNE, project_id=99))
    code, out = call("me.project_file.delete",
                     path_params={"project_id": 12, "file_id": 3})
    assert (code, out["error"]) == (404, "unknown_file")
    assert socle == []


def test_l_ecriture_exige_la_permission(monkeypatch, socle):
    stub_authz(monkeypatch)
    monkeypatch.setattr(ownership, "can_access", lambda *a: False)
    for cle, corps in (("me.project_file.delete", None),
                       ("me.project_file.set_public", {"public": True})):
        code, out = call(cle, path_params={"project_id": 12, "file_id": 3}, body=corps)
        assert (code, out["error"]) == (403, "forbidden")
    assert socle == []


# --- Le partage : le silence destructeur qu'on ferme ------------------------

@pytest.mark.parametrize("public,url", [(True, "https://public/k/3"), (False, None)])
def test_la_bascule_de_partage_pose_l_acl_et_l_url(monkeypatch, socle, public, url):
    stub_authz(monkeypatch)
    code, out = call("me.project_file.set_public",
                     path_params={"project_id": 12, "file_id": 3},
                     body={"public": public})
    assert code == 200 and out["ok"] is True
    assert out["file"]["public"] is public and out["file"]["public_url"] == url
    assert ("prive", "k/3") in socle if not public else True


@pytest.mark.parametrize("corps,erreur", [
    ({}, "invalid_input"),                    # objet valide, `public` manquant
    (None, "invalid_input"),                  # aucun corps du tout
    (b"{pas du json", "invalid_json"),        # illisible (seam json_body)
    ([1, 2], "invalid_body"),                 # JSON valide mais pas un objet
])
def test_un_corps_SANS_public_est_refuse_au_lieu_de_departager(monkeypatch, socle,
                                                               corps, erreur):
    """**Écart visible, et c'est le point du lot.** Avant, un corps sans `public` valait
    « rendre privé » et rendait **200** : un client mal formé départageait un fichier
    SANS LE SAVOIR. Désormais `public` est requis — on refuse plutôt que d'agir.

    Les quatre cas rendent 400 mais **pas le même code**, et c'est voulu : le seam
    `json_body` distingue le corps illisible (`invalid_json`) du corps valide mais
    non-objet (`invalid_body`), et pydantic prend le relais sur l'objet incomplet
    (`invalid_input`). Un refus qui ne dit pas LEQUEL des trois oblige à deviner."""
    stub_authz(monkeypatch)
    code, out = call("me.project_file.set_public",
                     path_params={"project_id": 12, "file_id": 3}, body=corps)
    assert (code, out["error"]) == (400, erreur)
    assert socle == [], "aucune ACL touchée, aucune ligne écrite"


def test_un_champ_inconnu_est_refuse(monkeypatch, socle):
    stub_authz(monkeypatch)
    code, out = call("me.project_file.set_public",
                     path_params={"project_id": 12, "file_id": 3},
                     body={"public": True, "isPublic": True})
    assert code == 400
    assert out["error"] == "unknown_fields" and "isPublic" in out["detail"]


# --- Ce qui reste écrit à la main, et pourquoi ------------------------------

def test_les_quatre_voisines_sont_de_NATURE_pas_de_la_dette():
    """Le lot ne se termine pas en « il reste quatre routes » mais en « ces quatre-là ne
    peuvent pas être des capacités, voici pourquoi ». Le figer empêche qu'on les
    reclasse en dette par réflexe, ou qu'on déforme l'adaptateur pour elles."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_garde", "tests/test_rest_modules_are_capabilities.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    for chemin in ("/api/me/avatar", "/api/orgs/{id}/logo",
                   "/api/me/projects/{project_id:int}/files",
                   "/api/me/projects/{id}/export"):
        assert g._KNOWN[chemin] == g.NATURE, f"{chemin} devrait être classé NATURE"
    assert not [p for p, k in g._KNOWN.items() if k == g.DEBT], (
        "la dette REST doit être VIDE après ce lot")
