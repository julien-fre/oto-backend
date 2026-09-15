"""Les deux routes projet écrites à la main — dépôt de fichier et export ZIP.

`oto_mcp/api/projects.py` était à **34 %** le 15/09/2026 alors que
`oto_mcp/capabilities/projects.py` était à **90 %** : `tests/test_projects.py` fait
938 lignes et 89 tests, **tous sur la capacité, aucun sur la route**. Or ce qui est
servi au dashboard passe par la ROUTE, et ces deux-là sont justement celles qui NE
peuvent PAS être des capacités (corps multipart, réponse `application/zip`) — donc
celles qui ne bénéficient d'aucune des garanties de l'adaptateur.

Ce que ce banc tient :

1. **Le gate de CONTEXTE d'org** (`_project_org_context_error`, ADR 0023). Sa
   docstring nomme l'incident : `can_access` est l'union de TOUTES les orgs de
   l'acteur, donc il laisse atteindre par son id un projet d'une AUTRE de ses orgs,
   hors du contexte de consultation. Le gate rend une **404 non-disclosante**, et il
   passe AVANT le 403 de permission : l'ordre est le contrat, un 403 dirait « ce
   projet existe » à qui ne devrait pas le savoir.
2. **La clé de stockage ne sort jamais.** `_signed` fait `row.pop("s3_key")` et
   remplace par une URL signée temporaire. Servir la clé interne donnerait au client
   une adresse stable dans le bucket, là où le modèle ne lui promet qu'un lien qui
   expire — y compris quand la signature ÉCHOUE, cas où le champ retombe à `None`.
3. **L'export exige une lecture**, et il sert un binaire : la réponse passe par
   `base._file`, sans quoi elle sort sans CORS et le navigateur la jette (incident du
   09/09/2026, `test_reponses_binaires_cors.py`).

⚠️ Les trois autres handlers du module (`project_files_list`, `project_file_delete`,
`project_file_public`) ne sont plus MONTÉS : ces chemins sont servis depuis
`capabilities/media_and_files.py` (portage du 2026-08-27). Non testés ici — colorier
du code que la table de routes n'atteint pas ne protège rien.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from oto_mcp import access, db, doc_export, media_store, ownership
from oto_mcp.api import base, projects

PID = 42
LIGNE = {"id": 9, "project_id": PID, "filename": "rapport.pdf",
         "mime": "application/pdf", "size_bytes": 12, "title": None,
         "s3_key": "project-files/42/abcdef.pdf"}


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@projets.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture
def journal():
    return {"objets": [], "lignes": [], "activite": [], "vu_dans_org": [],
            "acces": [], "exports": []}


@pytest.fixture
def monde(monkeypatch, journal):
    """Le monde par défaut : le projet existe, il est DANS l'org de consultation, et
    l'acteur y a l'écriture. Chaque test ne débranche que ce qu'il éprouve."""
    monkeypatch.setattr("oto_mcp.account_suspension.refus", lambda sub: None)
    monkeypatch.setattr(base, "alias_drain_armed", lambda: False)
    monkeypatch.setattr(db, "upsert_user", lambda *a, **k: None)

    monkeypatch.setattr(access, "current_org", lambda sub: 3)
    monkeypatch.setattr(db, "get_project_by_id",
                        lambda pid: {"id": pid, "name": "Étude de marché"})

    def _visible(sub, org_id, rtype, rid):
        journal["vu_dans_org"].append((sub, org_id, rtype, rid))
        return True

    def _acces(sub, rtype, rid, want="read"):
        journal["acces"].append((sub, rtype, rid, want))
        return True

    monkeypatch.setattr(ownership, "visible_in_org", _visible)
    monkeypatch.setattr(ownership, "can_access", _acces)

    def _upload_object(prefix, owner_id, data, content_type, filename):
        journal["objets"].append((prefix, owner_id, len(data), content_type, filename))
        return f"{prefix}/{owner_id}/abcdef.pdf"

    def _add(pid, key, filename, **kw):
        journal["lignes"].append((pid, key, filename, kw))
        return dict(LIGNE, s3_key=key, filename=filename, **
                    {k: v for k, v in kw.items() if k in ("title", "description")})

    monkeypatch.setattr(media_store, "upload_object", _upload_object)
    monkeypatch.setattr(media_store, "presign_get",
                        lambda key, **kw: f"https://media.invalid/{key}?sig=xyz")
    monkeypatch.setattr(db, "add_project_file", _add)
    monkeypatch.setattr(db, "log_project_activity",
                        lambda *a: journal["activite"].append(a))
    monkeypatch.setattr(db, "list_docs_for_project", lambda pid: [])
    monkeypatch.setattr(doc_export, "build_export",
                        lambda docs, racine: journal["exports"].append(racine) or b"PK\x03\x04zip")
    return journal


@pytest.fixture
def client(monde):
    verifier = _Verifier()
    return TestClient(Starlette(routes=[
        Route("/api/me/projects/{project_id:int}/files",
              base.bind(projects.project_files_upload, verifier=verifier),
              methods=["POST"]),
        Route("/api/me/projects/{id}/export",
              base.bind(projects.me_project_export, verifier=verifier), methods=["GET"]),
    ]))


def _fichier():
    return {"file": ("rapport.pdf", b"%PDF-1.4 xx", "application/pdf")}


def _en_tant_que(sub="u1") -> dict:
    return {"Authorization": f"Bearer {sub}"}


def _rien_n_a_ete_depose(journal):
    assert journal["objets"] == [], "un refus ne doit pas écrire dans le stockage"
    assert journal["lignes"] == [], "un refus ne doit pas créer de ligne"
    assert journal["activite"] == [], "un refus ne doit pas laisser de trace d'activité"


# ── sans jeton ───────────────────────────────────────────────────────────────

def test_deposer_un_fichier_sans_jeton_est_refuse(client, journal):
    r = client.post(f"/api/me/projects/{PID}/files", files=_fichier())
    assert r.status_code == 401 and r.json()["error"] == "missing_bearer"
    _rien_n_a_ete_depose(journal)


def test_exporter_sans_jeton_est_refuse(client, journal):
    r = client.get(f"/api/me/projects/{PID}/export")
    assert r.status_code == 401 and r.json()["error"] == "missing_bearer"
    assert journal["exports"] == []


# ── le cloisonnement par org : le cœur du lot ────────────────────────────────

def test_un_projet_hors_de_l_org_de_consultation_est_INTROUVABLE(client, monkeypatch,
                                                                 journal):
    """`can_access` est l'union de toutes mes orgs ; le contexte, lui, est UNE org.
    Sans ce gate, un id suffit à ouvrir un projet d'une autre de mes orgs — la fuite
    cross-org que la docstring d'`ownership.visible_in_org` nomme."""
    monkeypatch.setattr(ownership, "visible_in_org", lambda *a: False)
    r = client.post(f"/api/me/projects/{PID}/files", files=_fichier(),
                    headers=_en_tant_que())
    assert r.status_code == 404 and r.json()["error"] == "unknown_project"
    _rien_n_a_ete_depose(journal)


def test_le_404_de_contexte_passe_AVANT_le_403_de_permission(client, monkeypatch,
                                                             journal):
    """L'ordre est le contrat. Un 403 sur un projet hors contexte confirmerait son
    existence à qui n'a pas à la connaître — et le distinguerait d'un id inexistant."""
    monkeypatch.setattr(ownership, "visible_in_org", lambda *a: False)
    monkeypatch.setattr(ownership, "can_access", lambda *a, **k: True)
    r = client.post(f"/api/me/projects/{PID}/files", files=_fichier(),
                    headers=_en_tant_que())
    assert r.status_code == 404, "un acteur qui A le droit ailleurs voit quand même 404"
    assert r.json()["error"] == "unknown_project"


def test_le_gate_interroge_l_org_de_CONSULTATION_pas_une_autre(client, journal,
                                                               monkeypatch):
    """Le seam est `access.current_org(sub)` (ADR 0023) : l'org que le requérant
    consulte, pas son org maison figée. Interroger la mauvaise rendrait le gate
    incohérent avec ce que le dashboard affiche."""
    monkeypatch.setattr(access, "current_org", lambda sub: 77)
    client.post(f"/api/me/projects/{PID}/files", files=_fichier(),
                headers=_en_tant_que("u-consultant"))
    assert journal["vu_dans_org"] == [("u-consultant", 77, "project", str(PID))]


def test_un_projet_inexistant_rend_404_avant_toute_question_d_org(client, monkeypatch,
                                                                  journal):
    monkeypatch.setattr(db, "get_project_by_id", lambda pid: None)
    r = client.post(f"/api/me/projects/{PID}/files", files=_fichier(),
                    headers=_en_tant_que())
    assert r.status_code == 404 and r.json()["error"] == "unknown_project"
    assert journal["vu_dans_org"] == []


def test_un_lecteur_sans_ecriture_ne_depose_pas_de_fichier(client, monkeypatch, journal):
    """Le projet est bien dans mon contexte, je le LIS — écrire dedans est un autre
    droit. Le `want="write"` demandé est ce qui sépare les deux."""
    monkeypatch.setattr(ownership, "can_access",
                        lambda sub, t, i, want="read": want != "write")
    r = client.post(f"/api/me/projects/{PID}/files", files=_fichier(),
                    headers=_en_tant_que())
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    _rien_n_a_ete_depose(journal)


def test_le_droit_demande_au_depot_est_bien_l_ECRITURE(client, journal):
    client.post(f"/api/me/projects/{PID}/files", files=_fichier(), headers=_en_tant_que())
    assert journal["acces"] == [("u1", "project", str(PID), "write")]


# ── le corps multipart, écrit à la main ──────────────────────────────────────

def test_un_depot_sans_champ_file_est_refuse(client, journal):
    r = client.post(f"/api/me/projects/{PID}/files", data={"title": "sans fichier"},
                    headers=_en_tant_que())
    assert r.status_code == 400 and r.json()["error"] == "missing_file"
    _rien_n_a_ete_depose(journal)


def test_un_champ_file_textuel_est_un_400_pas_un_500(client, journal):
    r = client.post(f"/api/me/projects/{PID}/files",
                    data={"file": "https://ailleurs.invalid/x.pdf"},
                    headers=_en_tant_que())
    assert r.status_code == 400 and r.json()["error"] == "missing_file"
    _rien_n_a_ete_depose(journal)


@pytest.mark.parametrize("entete, corps", [
    ("multipart/form-data", b"peu importe"),
    ("multipart/form-data; boundary=f", b"\x00\x01n-importe-quoi"),
])
def test_un_multipart_illisible_est_un_400_nomme_pas_une_pile(client, journal,
                                                              entete, corps):
    r = client.post(f"/api/me/projects/{PID}/files", content=corps,
                    headers={**_en_tant_que(), "content-type": entete})
    assert r.status_code == 400 and r.json()["error"] == "invalid_multipart"
    _rien_n_a_ete_depose(journal)


def test_un_refus_du_stockage_ne_laisse_pas_de_ligne_orpheline(client, monkeypatch,
                                                               journal):
    """La ligne est écrite APRÈS l'objet : si le stockage refuse, la base ne doit pas
    garder une entrée qui pointe sur rien — le dashboard afficherait un fichier
    intéléchargeable."""
    def _refuse(*a, **k):
        raise media_store.MediaError(413, "file_too_large", "Fichier trop gros.")

    monkeypatch.setattr(media_store, "upload_object", _refuse)
    r = client.post(f"/api/me/projects/{PID}/files", files=_fichier(),
                    headers=_en_tant_que())
    assert r.status_code == 413 and r.json()["error"] == "file_too_large"
    assert journal["lignes"] == [] and journal["activite"] == []


# ── ce que la réponse a le droit de porter ───────────────────────────────────

def test_la_cle_de_stockage_ne_sort_jamais_dans_la_reponse(client):
    """`s3_key` est une adresse INTERNE et STABLE dans le bucket. Le contrat servi
    promet un lien signé qui expire ; laisser la clé à côté annulerait la promesse."""
    r = client.post(f"/api/me/projects/{PID}/files", files=_fichier(),
                    headers=_en_tant_que())
    assert r.status_code == 200
    fichier = r.json()["file"]
    assert "s3_key" not in fichier
    assert "abcdef.pdf?sig=" in fichier["download_url"]


def test_une_signature_qui_echoue_rend_null_et_ne_reexpose_pas_la_cle(client,
                                                                     monkeypatch):
    """Le stockage peut être indisponible ; la liste doit rester lisible. Mais la
    branche de repli ne doit pas être celle qui rend la clé au client."""
    def _echoue(key, **kw):
        raise media_store.MediaError(500, "presign_failed", "presign KO")

    monkeypatch.setattr(media_store, "presign_get", _echoue)
    r = client.post(f"/api/me/projects/{PID}/files", files=_fichier(),
                    headers=_en_tant_que())
    assert r.status_code == 200
    assert r.json()["file"]["download_url"] is None
    assert "s3_key" not in r.json()["file"]


def test_le_depot_nomme_le_fichier_et_son_type_depuis_la_partie_multipart(client,
                                                                         journal):
    r = client.post(f"/api/me/projects/{PID}/files",
                    files={"file": ("étude.csv", b"a,b\n1,2\n", "text/csv")},
                    data={"title": "  Étude  ", "description": "   "},
                    headers=_en_tant_que())
    assert r.status_code == 200
    prefix, owner, taille, ct, nom = journal["objets"][0]
    assert (prefix, owner, ct, nom) == ("project-files", str(PID), "text/csv", "étude.csv")
    _, _, _, kw = journal["lignes"][0]
    assert kw["title"] == "Étude", "le titre est trimé"
    assert kw["description"] is None, "une description blanche vaut absente, pas ''"
    assert kw["created_by"] == "u1"


# ── l'export ZIP ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("brut", ["abc", "42bis", "1.5", "-"])
def test_un_identifiant_de_projet_illisible_est_un_400_nomme(client, brut, journal):
    """Le chemin de l'export est `{id}` sans convertisseur (contrairement au dépôt, en
    `{project_id:int}`) : la chaîne arrive au handler."""
    r = client.get(f"/api/me/projects/{brut}/export", headers=_en_tant_que())
    assert r.status_code == 400 and r.json()["error"] == "bad_project"
    assert journal["exports"] == []


def test_exporter_sans_droit_de_lecture_est_refuse(client, monkeypatch, journal):
    monkeypatch.setattr(ownership, "can_access", lambda *a, **k: False)
    r = client.get(f"/api/me/projects/{PID}/export", headers=_en_tant_que())
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    assert journal["exports"] == [], "aucune KB ne doit être assemblée sur un refus"


def test_l_export_demande_la_LECTURE_pas_l_ecriture(client, journal):
    """Exporter est de la réversibilité (oto/#6) : un lecteur doit pouvoir emporter ce
    qu'il lit. Exiger `write` fermerait la porte de sortie à la moitié des membres."""
    client.get(f"/api/me/projects/{PID}/export", headers=_en_tant_que())
    assert journal["acces"] == [("u1", "project", str(PID), "read")]


def test_le_zip_sort_nomme_et_avec_son_CORS(client):
    """Une `Response` nue sort SANS CORS et le navigateur la jette (incident du
    09/09/2026) ; et un ZIP sans `Content-Disposition` arrive sans nom."""
    r = client.get(f"/api/me/projects/{PID}/export",
                   headers={**_en_tant_que(), "Origin": "https://manage.oto.cx"})
    assert r.status_code == 200 and r.content == b"PK\x03\x04zip"
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="etude-de-marche.zip"'
    assert r.headers["access-control-allow-origin"] == "https://manage.oto.cx"
    assert "Content-Disposition" in r.headers["access-control-expose-headers"]


def test_un_nom_de_projet_hostile_ne_compose_pas_un_en_tete_ni_un_chemin(client,
                                                                        monkeypatch):
    """Le nom du projet est une donnée d'utilisateur qui atterrit dans un en-tête ET
    dans un nom de fichier. CR/LF, guillemets et séparateurs de chemin n'y survivent
    pas — sinon une injection d'en-tête ou une écriture hors du ZIP."""
    monkeypatch.setattr(db, "get_project_by_id",
                        lambda pid: {"id": pid, "name": '../../etc\r\nX-Injecte: 1"'})
    r = client.get(f"/api/me/projects/{PID}/export", headers=_en_tant_que())
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert ".." not in disposition and "/" not in disposition
    assert "x-injecte" not in {k.lower() for k in r.headers}


def test_un_projet_sans_nom_garde_un_nom_de_fichier(client, monkeypatch):
    monkeypatch.setattr(db, "get_project_by_id", lambda pid: {"id": pid, "name": ""})
    r = client.get(f"/api/me/projects/{PID}/export", headers=_en_tant_que())
    assert r.status_code == 200
    assert r.headers["content-disposition"].endswith('.zip"')
    assert 'filename=""' not in r.headers["content-disposition"]


# MESURÉ le 15/09/2026 : `me_project_export` était la seule route projet PAR-ID de ce
# module à ne PAS appeler `_project_org_context_error` — elle s'en tenait à
# `can_access(read)`, que la docstring d'`ownership.visible_in_org` qualifie de « trop
# large pour une lecture par-id » (union de TOUTES les orgs de l'acteur). La KB entière
# d'un projet d'une AUTRE de mes orgs s'exportait donc depuis un contexte où le même
# projet rendait 404 sur `GET .../files`. Ce banc a d'abord été posé en `xfail(strict)`,
# le temps d'établir la portée exacte : l'acteur avait bien un droit sur la ressource
# (ce n'était pas une fuite entre organisations), mais la bascule d'org ne bornait pas
# l'export. Le gate est posé, le marqueur retiré — ce test garde les cinq routes
# alignées.
def test_l_export_devrait_lui_aussi_etre_borne_a_l_org_de_consultation(client,
                                                                      monkeypatch):
    monkeypatch.setattr(ownership, "visible_in_org", lambda *a: False)
    r = client.get(f"/api/me/projects/{PID}/export", headers=_en_tant_que())
    assert r.status_code == 404
