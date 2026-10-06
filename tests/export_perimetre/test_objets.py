"""L'archive des objets d'un périmètre : scellée pour la cible, vérifiée, reprenable (#1088).

Aucun vrai seau : `FauxS3` (`perimetre_banc`) parle l'API de boto3, en mémoire.
"""
from __future__ import annotations

import hashlib
import os

import pytest

from oto_mcp.export_perimetre import objets as module_objets
from oto_mcp.export_perimetre.classement import CLASSEMENT
from oto_mcp.export_perimetre.objets import (
    COLONNES_D_URL, COLONNES_DE_CLES, ObjetsRefuses, StockageS3, archiver, cles_dans,
    controler_archive, verser)
from perimetre_banc import BASE_SOURCE, PNG, FauxS3, url_source

OBJETS = {"project-files/12/abc/rapport.pdf": b"%PDF-1.7 rapport",
          "images/t1%3Aalice/def.png": PNG + b" image" * 50}
CLE = os.urandom(32)
_CACHE = "public, max-age=31536000, immutable"


def _archive(tmp_path, objets=OBJETS, cle=CLE, entetes=None):
    chemin = tmp_path / "x.objets.tar"
    liste, empreinte = archiver(objets, StockageS3(FauxS3(objets, entetes=entetes), "src"),
                                chemin, cle)
    return chemin, liste, empreinte


def test_l_archive_porte_chaque_objet_scelle_et_se_verse_a_l_identique(tmp_path):
    chemin, liste, empreinte = _archive(tmp_path)
    assert liste == {c: {"taille": len(d), "sha256": hashlib.sha256(d).hexdigest(),
                         "content_type": "", "public": False}
                     for c, d in OBJETS.items()}
    brut = chemin.read_bytes()
    assert all(d not in brut for d in OBJETS.values())       # rien en clair
    controler_archive(chemin, empreinte)
    cible = FauxS3()
    rapport = verser(chemin, liste, StockageS3(cible, "dst"), CLE)
    assert sorted(rapport.copies) == sorted(OBJETS)
    assert {c: v[0] for c, v in cible.objets.items()} == OBJETS


def test_un_import_interrompu_se_reprend_sans_rien_recopier(tmp_path):
    chemin, liste, _ = _archive(tmp_path)
    cible = FauxS3()
    verser(chemin, liste, StockageS3(cible, "dst"), CLE)
    reprise = verser(chemin, liste, StockageS3(cible, "dst"), CLE)
    assert reprise.copies == [] and sorted(reprise.deja_la) == sorted(OBJETS)
    assert cible.ecritures == len(OBJETS)


def test_une_autre_cle_ne_verse_rien(tmp_path):
    chemin, liste, _ = _archive(tmp_path)
    cible = FauxS3()
    with pytest.raises(ObjetsRefuses, match="ne se déchiffre pas"):
        verser(chemin, liste, StockageS3(cible, "dst"), os.urandom(32))
    assert cible.ecritures == 0


def test_une_archive_modifiee_refuse(tmp_path):
    chemin, _, empreinte = _archive(tmp_path)
    brut = bytearray(chemin.read_bytes())
    brut[-600] ^= 1
    chemin.write_bytes(bytes(brut))
    with pytest.raises(ObjetsRefuses, match="tronquée ou modifiée"):
        controler_archive(chemin, empreinte)


def test_une_cible_qui_abime_ce_qu_on_y_ecrit_refuse(tmp_path):
    chemin, liste, _ = _archive(tmp_path)
    with pytest.raises(ObjetsRefuses, match="ne rend pas"):
        verser(chemin, liste, StockageS3(FauxS3(alterer=True), "dst"), CLE)


def test_l_import_repose_l_acl_et_les_en_tetes_de_l_ecriture_native(tmp_path):
    """Une image publique arrivée privée et sans en-têtes (le défaut constaté : 403 sur
    les logos, avatars et images) redevient publique, typée sur ses octets, en cache
    immuable ; un fichier de projet reste privé, sauf partagé à la source."""
    objets = {"avatars/u1/a.png": PNG + b" avatar",
              "org-logos/7/l.png": PNG + b" logo",
              "images/u1/i.png": PNG + b" image",
              "project-files/12/abc/prive.pdf": b"%PDF-1.7 prive",
              "project-files/12/def/partage.txt": b"texte partage",
              "transcription-jobs/12/ghi/a.mp3": b"ID3 audio"}
    source = {"project-files/12/abc/prive.pdf": {"ContentType": "application/pdf"},
              "project-files/12/def/partage.txt": {"ContentType": "text/plain",
                                                   "ACL": "public-read"},
              "transcription-jobs/12/ghi/a.mp3": {"ContentType": "audio/mpeg"}}
    chemin, liste, _ = _archive(tmp_path, objets, entetes=source)
    cible = FauxS3()
    verser(chemin, liste, StockageS3(cible, "dst"), CLE)
    publique = {"ContentType": "image/png", "ACL": "public-read", "CacheControl": _CACHE}
    assert {c: v[2] for c, v in cible.objets.items()} == {
        "avatars/u1/a.png": publique, "org-logos/7/l.png": publique,
        "images/u1/i.png": publique,
        "project-files/12/abc/prive.pdf": {"ContentType": "application/pdf"},
        "project-files/12/def/partage.txt": {"ContentType": "text/plain",
                                             "ACL": "public-read"},
        "transcription-jobs/12/ghi/a.mp3": {"ContentType": "audio/mpeg"}}


def test_un_objet_deja_la_avec_d_autres_en_tetes_est_reecrit(tmp_path):
    """Une cible où les objets sont là, octets justes : le fichier de projet avec ses
    en-têtes, l'image privée et sans en-têtes (un import d'avant ce correctif). Un nouvel
    import saute le premier et réécrit la seconde, publique."""
    chemin, liste, _ = _archive(tmp_path)
    cible = FauxS3()
    for cle, donnees in OBJETS.items():
        entetes = {"ContentType": "application/pdf"} if cle.startswith("project-") else {}
        cible.put_object(Bucket="dst", Key=cle, Body=donnees,
                         Metadata={"sha256": hashlib.sha256(donnees).hexdigest()}, **entetes)
    rapport = verser(chemin, liste, StockageS3(cible, "dst"), CLE)
    assert rapport.copies == ["images/t1%3Aalice/def.png"]
    assert rapport.deja_la == ["project-files/12/abc/rapport.pdf"]
    assert cible.objets["images/t1%3Aalice/def.png"][2]["ACL"] == "public-read"


def test_un_prefixe_sans_regle_refuse_a_l_export_comme_a_l_import(tmp_path, monkeypatch):
    objets = {**OBJETS, "tmp/x/y": b"z"}
    with pytest.raises(ObjetsRefuses, match="sans règle"):
        _archive(tmp_path, objets)
    # Une archive écrite par un export qui ne refusait pas encore : l'import refuse
    # à son tour, avant d'écrire quoi que ce soit.
    with monkeypatch.context() as m:
        m.setattr(module_objets, "_refuser_sans_regle", lambda cles: None)
        chemin, liste, _ = _archive(tmp_path, objets)
    cible = FauxS3()
    with pytest.raises(ObjetsRefuses, match="sans règle"):
        verser(chemin, liste, StockageS3(cible, "dst"), CLE)
    assert cible.ecritures == 0


def test_un_manifeste_qui_ne_dit_pas_les_en_tetes_refuse_sans_rien_ecrire(tmp_path):
    chemin, liste, _ = _archive(tmp_path)
    ancien = {c: {"taille": v["taille"], "sha256": v["sha256"]} for c, v in liste.items()}
    cible = FauxS3()
    with pytest.raises(ObjetsRefuses, match="refaire l'export"):
        verser(chemin, ancien, StockageS3(cible, "dst"), CLE)
    assert cible.ecritures == 0


def test_une_image_publique_qui_n_en_est_pas_une_refuse(tmp_path):
    chemin, liste, _ = _archive(tmp_path, {"avatars/u1/a.png": b"pas une image"})
    with pytest.raises(ObjetsRefuses, match="avatars/u1/a.png"):
        verser(chemin, liste, StockageS3(FauxS3(), "dst"), CLE)


def test_un_objet_absent_de_la_source_refuse_sans_archive(tmp_path):
    chemin = tmp_path / "x.objets.tar"
    with pytest.raises(ObjetsRefuses, match="project-files/99/perdu"):
        archiver([*OBJETS, "project-files/99/perdu"], StockageS3(FauxS3(OBJETS), "src"),
                 chemin, CLE)
    assert not chemin.exists() and not list(tmp_path.iterdir())


def test_les_cles_se_trouvent_par_leur_url_dans_n_importe_quel_texte():
    texte = (f'{{"body_md": "voir ![s]({BASE_SOURCE}/images/t1%253Aa/x.png) et '
             f'<{BASE_SOURCE}/org-logos/3/y.png>", "u": "{BASE_SOURCE}/avatars/z.png?v=2", '
             f'"autre": "https://ailleurs.test/images/w.png", '
             f'"voisin": "{BASE_SOURCE}x/images/v.png"}}')
    assert cles_dans(texte, BASE_SOURCE) == {"images/t1%3Aa/x.png", "org-logos/3/y.png",
                                             "avatars/z.png"}


def test_la_cle_se_tire_du_chemin_de_l_url_decode_d_un_niveau():
    """Constaté sur une vraie copie : la clé réelle porte un `%3A` littéral, son URL
    l'encode (`%253A`). Le chemin décodé d'UN niveau, et d'un seul, redonne la clé."""
    cle = "images/t1%3Aalice/def.png"
    assert url_source(cle) == f"{BASE_SOURCE}/images/t1%253Aalice/def.png"
    assert cles_dans(f"![i]({url_source(cle)})", BASE_SOURCE) == {cle}


def test_un_seul_chemin_l_archive():
    """Décision du 28/09/2026 : ni copie directe d'un seau à l'autre, ni URL signée."""
    assert not hasattr(module_objets, "copier")
    assert not hasattr(module_objets, "StockageLocal")


def test_chaque_colonne_hors_base_du_classement_est_classee_cle_ou_url():
    """Une colonne ajoutée à `hors_base` sans décider de son sort fait rougir ici."""
    declarees = {f"{t}.{c}" for t, e in CLASSEMENT.items() for c in e.hors_base}
    assert declarees == COLONNES_DE_CLES | COLONNES_D_URL
