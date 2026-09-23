"""Connecteur `transcription` (ADR 0074, #674) — verrouille : l'entrée registre
(clé secrète + langue et vocabulaire NON secrets, `byo_org` + plateforme, mono-compte), la
surface MCP (deux outils), la jointure outil↔client oto-core, et le geste de bout en
bout avec un transport Mistral SIMULÉ : un fichier du projet lu côté serveur, envoyé
avec la langue et le vocabulaire de l'instance, post-traité, rangé en page du projet
par le seam d'écriture des pages — et l'appel qui rend la page, pas le texte.

**ASYNCHRONE (#674)** : `transcription_create` dépose un travail et rend sa référence
tout de suite, SANS appeler Mistral — c'est `transcription_worker._transcribe_batch`
(la boucle de fond, ici invoquée directement, un tour) qui le fait, et
`transcription_status` qui relit le résultat.

Refus éprouvés AVANT la création du travail (donc avant tout appel payant) : sans
`_project`, sans droit d'écriture, un fichier rangé sur un AUTRE projet. Refus après
(côté worker, lus via `transcription_status`) : une clé rejetée par Mistral, aucune
parole reconnue.
"""
import asyncio
import io
import json

import pytest

from oto_mcp import providers
from oto_mcp.mcp_errors import McpError
from oto_mcp.tool_visibility import namespace_of

AUDIO = b"ID3" + b"\x00" * 2048
FICHIER = {"id": 7, "project_id": 42, "s3_key": "project-files/42/abc/visite.m4a",
           "filename": "visite.m4a", "mime": "audio/mp4"}
# La forme mesurée sur l'API réelle : chaque segment porte `start`, `end` et
# `speaker_id` ; `language` revient null, même quand la langue est imposée.
REPONSE_MISTRAL = {
    "model": "voxtral-mini-2602", "language": None,
    "text": "Bonjour. On regarde la toiture. On regarde la toiture. Hum. D'accord.",
    "segments": [
        {"text": "Bonjour.", "start": 0.0, "end": 1.5, "speaker_id": "speaker_0"},
        {"text": "On regarde la toiture et la charpente du garage.", "start": 1.5,
         "end": 6.0, "speaker_id": "speaker_0"},
        {"text": "On regarde la toiture et la charpente du garage.", "start": 6.0,
         "end": 9.0, "speaker_id": "speaker_0"},
        # 0,4 s d'un locuteur inventé, 0,2 s après le précédent, 0,4 s avant le suivant
        {"text": "Hum.", "start": 9.2, "end": 9.6, "speaker_id": "speaker_2"},
        {"text": "D'accord, et les combles sont isolés depuis quand ?", "start": 10.0,
         "end": 14.0, "speaker_id": "speaker_1"},
    ],
    "usage": {"prompt_audio_seconds": 1800},
}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    return {t.name: t for t in asyncio.run(m._list_tools())}


# --- registre et surface --------------------------------------------------------------

def test_entree_registre_cle_secrete_langue_et_vocabulaire_non_secrets():
    c = providers.REGISTRY["transcription"]
    assert c.kind == "tools" and c.secret_kind == "fields"
    assert c.auth_modes == frozenset({"byo_org", "platform"})
    assert c.default_active is False
    champs = {f.name: f for f in c.secret_fields}
    assert list(champs) == ["api_key", "language", "vocabulary"]
    assert champs["api_key"].secret is True
    for nom in ("language", "vocabulary"):
        assert champs[nom].secret is False and champs[nom].required is False
    # « pompe à chaleur » : le nettoyage par défaut retirerait tous les blancs.
    assert champs["vocabulary"].whitespace_significant is True


def test_mono_compte():
    c = providers.REGISTRY["transcription"]
    assert c.cardinality == "mono" and c.auth_multi_account is False


def test_deux_outils_sous_son_namespace(all_tools):
    assert {"transcription_create", "transcription_status"} <= set(all_tools)
    assert namespace_of("transcription_create") == "transcription"
    assert namespace_of("transcription_status") == "transcription"
    assert sorted(t for t in all_tools if t.startswith("transcription_")) == [
        "transcription_create", "transcription_status"]
    assert all_tools["transcription_create"].description
    assert all_tools["transcription_status"].description


def test_client_expose_les_methodes_appelees():
    from oto.tools.mistral import MistralClient
    for meth in ("transcribe", "list_models"):
        assert callable(getattr(MistralClient, meth, None)), f"MistralClient.{meth}"


# --- le monde simulé ----------------------------------------------------------------------

class _S3:
    def __init__(self, objets):
        self.objets = objets
        self.lus: list[str] = []
        self.supprimes: list[str] = []

    def head_object(self, *, Bucket, Key):
        return {"ContentLength": len(self.objets[Key])}

    def get_object(self, *, Bucket, Key):
        self.lus.append(Key)
        return {"Body": io.BytesIO(self.objets[Key])}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):
        self.objets[Key] = Body if isinstance(Body, bytes) else Body.read()

    def delete_object(self, *, Bucket, Key):
        self.supprimes.append(Key)
        self.objets.pop(Key, None)


class _Resp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture
def monde(monkeypatch):
    """Projet 42 visible, lisible et inscriptible ; il porte le fichier 7. Mistral
    répond `REPONSE_MISTRAL`. Chaque test ne débranche que ce qu'il éprouve.

    La file de travaux (`db.create_transcription_job`/`claim_next_transcription_job`/
    `get_transcription_job`/`mark_transcription_job_*`) est simulée en mémoire — même
    patron que `db.create_doc`/`get_doc_by_id` ci-dessous. Ces noms sont PRÉFIXÉS
    (pas `create_job`/`get_job`/`claim_next_job` nus) : `runner_jobs.py` porte déjà
    `get_job`/`claim_next_job` sous une forme différente dans le même namespace plat
    `db.*` — un nom nu écraserait l'un des deux à l'import (#674)."""
    from oto.tools.mistral import client as mistral_client
    from oto_mcp import access, db, file_source, media_store, ownership

    monkeypatch.setenv("OTO_MCP_MASTER_KEY", "4" * 64)  # chiffrement réel du job

    etat = {"envois": [], "pages": [], "activite": [], "droits": [], "jobs": {},
            "creds": {"api_key": "k-test", "language": "", "vocabulary":
                      "pompe à chaleur, Placo\nBA13"},
            "reponse": _Resp(REPONSE_MISTRAL), "projet": 42}
    s3 = _S3({FICHIER["s3_key"]: AUDIO})
    etat["s3"] = s3

    monkeypatch.setattr(access, "current_project", lambda: etat["projet"])
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: "u1")
    monkeypatch.setattr(file_source.access, "current_user_sub_or_raise", lambda: "u1")
    monkeypatch.setattr(file_source.access, "current_org", lambda sub: 3)
    class _Cred:
        is_platform = False
        secret = "unused"

        @property
        def fields(self):
            return dict(etat["creds"])

    class _CredPlateforme:
        """Le secret plateforme est la clé nue : `.fields` la relirait comme du JSON."""
        is_platform = True
        secret = "k-plateforme"

        @property
        def fields(self):
            raise AssertionError("un secret plateforme ne se déballe pas en JSON")

    def _resolve(provider, want="auto", **kw):
        etat["want"] = want
        return _CredPlateforme() if etat.get("plateforme") else _Cred()

    monkeypatch.setattr(access, "resolve_credential", _resolve)

    def _acces(sub, rtype, rid, want="read"):
        etat["droits"].append((rtype, rid, want))
        return True

    monkeypatch.setattr(ownership, "visible_in_org", lambda *a: True)
    monkeypatch.setattr(ownership, "can_access", _acces)
    monkeypatch.setattr(db, "get_project_file",
                        lambda fid: dict(FICHIER) if fid == FICHIER["id"] else None)
    monkeypatch.setattr(media_store, "_get_client", lambda: s3)
    monkeypatch.setattr(media_store, "_bucket", lambda: "media-test")

    def _create_doc(pid, title, **kw):
        etat["pages"].append({"id": 900 + len(etat["pages"]), "project_id": pid,
                              "parent_id": kw.get("parent_id"), "title": title,
                              "body_md": kw.get("body_md"), "kind": kw.get("kind"),
                              "description": None, "position": 16,
                              "public_token": None, "created_at": "t",
                              "updated_at": "t"})
        return etat["pages"][-1]["id"]

    monkeypatch.setattr(db, "create_doc", _create_doc)
    monkeypatch.setattr(db, "get_doc_by_id",
                        lambda did: next(p for p in etat["pages"] if p["id"] == did))
    monkeypatch.setattr(db, "log_project_activity",
                        lambda *a: etat["activite"].append(a))

    # --- la file de travaux, en mémoire -------------------------------------------
    def _create_job(*, project_id, sub, audio_key, filename, mime, language,
                    vocabulary, api_key_enc):
        jid = 1 + len(etat["jobs"])
        etat["jobs"][jid] = {"id": jid, "project_id": project_id, "sub": sub,
                             "status": "pending", "audio_key": audio_key,
                             "filename": filename, "mime": mime, "language": language,
                             "vocabulary": vocabulary, "api_key_enc": api_key_enc,
                             "page_id": None, "result": None, "error": None}
        return jid

    def _claim_next_job():
        for j in etat["jobs"].values():
            if j["status"] == "pending":
                j["status"] = "running"
                return dict(j)
        return None

    def _get_job(jid):
        j = etat["jobs"].get(jid)
        return dict(j) if j else None

    def _mark_done(jid, *, page_id, result):
        etat["jobs"][jid].update(status="done", page_id=page_id, result=result)

    def _mark_failed(jid, *, error):
        etat["jobs"][jid].update(status="failed", error=error)

    monkeypatch.setattr(db, "create_transcription_job", _create_job)
    monkeypatch.setattr(db, "claim_next_transcription_job", _claim_next_job)
    monkeypatch.setattr(db, "get_transcription_job", _get_job)
    monkeypatch.setattr(db, "mark_transcription_job_done", _mark_done)
    monkeypatch.setattr(db, "mark_transcription_job_failed", _mark_failed)

    def _post(url, headers=None, data=None, files=None, timeout=None, json=None):
        etat["envois"].append({"url": url, "headers": headers, "data": data,
                               "files": files, "timeout": timeout})
        return etat["reponse"]

    monkeypatch.setattr(mistral_client.requests, "post", _post)
    return etat


def _appeler(all_tools, **args):
    return all_tools["transcription_create"].fn(**args)


def _statut(all_tools, job_id):
    return all_tools["transcription_status"].fn(job_id=job_id)


def _tourner(monde):
    """Un tour du worker (hors event loop dans le serveur réel, direct ici)."""
    from oto_mcp import transcription_worker
    return transcription_worker._transcribe_batch()


def _pf(pid=42, fid=7):
    return {"kind": "project_file", "project_id": pid, "file_id": fid}


def _champs(envoi) -> dict:
    out: dict = {}
    for k, v in envoi["data"]:
        out.setdefault(k, []).append(v)
    return out


# --- création : rend une référence, n'appelle jamais Mistral --------------------------

def test_create_rend_une_reference_sans_appeler_mistral(all_tools, monde):
    out = _appeler(all_tools, source=_pf())
    assert out == {"job_id": 1, "status": "pending",
                   "note": "Relire avec transcription_status(job_id)."}
    assert monde["envois"] == [] and monde["pages"] == []
    assert monde["jobs"][1]["status"] == "pending"
    assert ("project", "42", "write") in monde["droits"]
    # L'audio est déjà lu et déposé (temporairement) au moment du dépôt du travail —
    # c'est ce qui permet au worker de ne plus avoir besoin du contexte de l'appel.
    assert monde["s3"].lus == [FICHIER["s3_key"]]


def test_sans_projet_rien_n_est_lu_ni_depose(all_tools, monde):
    monde["projet"] = None
    with pytest.raises(McpError, match="_project"):
        _appeler(all_tools, source=_pf())
    assert monde["jobs"] == {} and monde["s3"].lus == []


def test_sans_droit_d_ecriture_rien_n_est_depose(all_tools, monde, monkeypatch):
    from oto_mcp import ownership
    monkeypatch.setattr(ownership, "can_access",
                        lambda sub, rtype, rid, want="read": want != "write")
    with pytest.raises(McpError, match="Écriture refusée"):
        _appeler(all_tools, source=_pf())
    assert monde["jobs"] == {} and monde["s3"].lus == []


def test_un_fichier_d_un_autre_projet_est_refuse_comme_un_inconnu(all_tools, monde):
    """Le fichier 7 est rangé sur le projet 42 ; le désigner sous le projet 41 (où
    l'appelant a pourtant tous les droits) répond comme un fichier inconnu, et rien
    n'est lu dans le stockage ni déposé comme travail."""
    with pytest.raises(McpError) as autre:
        _appeler(all_tools, source=_pf(pid=41, fid=7))
    with pytest.raises(McpError) as inconnu:
        _appeler(all_tools, source=_pf(pid=41, fid=999))
    assert "introuvable" in autre.value.error.message
    assert (autre.value.error.message.replace("#7", "#999")
            == inconnu.value.error.message)
    assert monde["s3"].lus == [] and monde["jobs"] == {}


# --- le tour du worker : de bout en bout ----------------------------------------------

def test_un_fichier_du_projet_devient_une_page_du_projet(all_tools, monde):
    ref = _appeler(all_tools, source=_pf())
    assert _tourner(monde) == 1

    (envoi,) = monde["envois"]
    assert envoi["url"].endswith("/v1/audio/transcriptions")
    assert envoi["files"]["file"] == ("visite.m4a", AUDIO, "audio/mp4")
    assert envoi["timeout"][1] >= 60                 # un délai propre, pas l'infini
    champs = _champs(envoi)
    # Les trois ENSEMBLE, la combinaison du banc — la diarisation sans horodatage
    # par segment est refusée par l'amont (422).
    assert champs["language"] == ["fr"]              # langue vide = fr
    assert champs["timestamp_granularities"] == ["segment"]
    assert champs["diarize"] == ["true"]
    assert champs["context_bias"] == ["pompe,chaleur,Placo,BA13"]

    (page,) = monde["pages"]
    assert page["project_id"] == 42 and page["kind"] == "source"
    assert page["title"].startswith("Transcription — visite.m4a — ")
    paragraphes = page["body_md"].strip().split("\n\n")
    assert paragraphes[1:] == [
        "**Locuteur 1** [00:00] — Bonjour. On regarde la toiture et la charpente du "
        "garage. Hum.",
        "**Locuteur 2** [00:10] — D'accord, et les combles sont isolés depuis quand ?",
    ]

    out = _statut(all_tools, ref["job_id"])
    assert out["status"] == "done"
    assert out["page"]["id"] == page["id"] and out["page"]["title"] == page["title"]
    assert out["speakers"] == ["Locuteur 1", "Locuteur 2"]
    assert out["duration_s"] == 1800.0
    assert out["words"] == 20
    assert out["language"] == "fr"                   # la demandée : l'amont rend null
    assert out["vocabulary_terms"] == 4 and out["vocabulary_dropped"] == ["à"]
    # Le statut rend la page, PAS le texte.
    assert "D'accord" not in json.dumps(out)
    # L'objet audio temporaire est purgé après le tour.
    assert monde["s3"].supprimes == [monde["jobs"][1]["audio_key"]]


def test_langue_auto_n_envoie_aucune_langue_et_garde_l_horodatage(all_tools, monde):
    monde["creds"]["language"] = "auto"
    ref = _appeler(all_tools, source=_pf())
    _tourner(monde)
    champs = _champs(monde["envois"][0])
    assert "language" not in champs
    assert champs["timestamp_granularities"] == ["segment"]
    assert champs["diarize"] == ["true"]
    assert _statut(all_tools, ref["job_id"])["language"] == "auto"


def test_une_langue_posee_est_envoyee_telle_quelle(all_tools, monde):
    monde["creds"]["language"] = "en"
    ref = _appeler(all_tools, source=_pf())
    _tourner(monde)
    assert _champs(monde["envois"][0])["language"] == ["en"]


def test_une_cle_refusee_par_mistral_n_ecrit_rien(all_tools, monde):
    monde["reponse"] = _Resp({"message": "Unauthorized"}, status_code=401)
    ref = _appeler(all_tools, source=_pf())
    _tourner(monde)
    assert monde["pages"] == []
    out = _statut(all_tools, ref["job_id"])
    assert out["status"] == "failed" and "401" in out["error"]


def test_aucune_parole_aucune_page(all_tools, monde):
    monde["reponse"] = _Resp({"text": "", "segments": [], "usage": {}})
    ref = _appeler(all_tools, source=_pf())
    _tourner(monde)
    assert monde["pages"] == []
    out = _statut(all_tools, ref["job_id"])
    assert out["status"] == "failed" and "Aucune parole" in out["error"]


def test_le_statut_dun_travail_dun_autre_projet_est_introuvable(all_tools, monde, monkeypatch):
    """Le job appartient au projet 42 ; un lecteur sans accès à ce projet reçoit le
    même refus qu'un job inexistant — pas d'indice sur son existence."""
    from oto_mcp import ownership
    ref = _appeler(all_tools, source=_pf())
    monkeypatch.setattr(ownership, "can_access",
                        lambda sub, rtype, rid, want="read": False)
    with pytest.raises(McpError, match="introuvable"):
        _statut(all_tools, ref["job_id"])


def test_statut_dun_travail_inconnu(all_tools, monde):
    with pytest.raises(McpError, match="introuvable"):
        _statut(all_tools, 999)


# --- vocabulaire à l'appel, palier plateforme ------------------------------------------

def test_le_credential_se_resout_en_cascade_plateforme_comprise(all_tools, monde):
    _appeler(all_tools, source=_pf())
    assert monde["want"] == "auto"


def test_le_vocabulaire_de_l_appel_complete_celui_de_l_instance(all_tools, monde):
    _appeler(all_tools, source=_pf(), vocabulary="zinguerie, chéneau")
    _tourner(monde)
    assert _champs(monde["envois"][0])["context_bias"] == [
        "pompe,chaleur,Placo,BA13,zinguerie,chéneau"]


def test_le_vocabulaire_de_l_appel_peut_remplacer(all_tools, monde):
    _appeler(all_tools, source=_pf(), vocabulary="zinguerie", vocabulary_replace=True)
    _tourner(monde)
    assert _champs(monde["envois"][0])["context_bias"] == ["zinguerie"]


def test_une_cle_plateforme_est_la_cle_nue_langue_par_defaut(all_tools, monde):
    monde["plateforme"] = True
    _appeler(all_tools, source=_pf(), vocabulary="zinguerie")
    _tourner(monde)
    (envoi,) = monde["envois"]
    assert envoi["headers"]["Authorization"] == "Bearer k-plateforme"
    champs = _champs(envoi)
    assert champs["language"] == ["fr"] and champs["context_bias"] == ["zinguerie"]


def test_un_audio_plus_gros_qu_une_image_est_relu_et_un_echec_de_lecture_purge(
        all_tools, monde, monkeypatch):
    """Le worker relit sous le plafond des fichiers de projet, pas celui d'une image
    (2 Mo) : un enregistrement de 10 Mo échouait à la lecture (première exécution réelle)."""
    from oto_mcp import media_store
    gros = b"ID3" + b"\x00" * (3 * 1024 * 1024)
    monde["s3"].objets[FICHIER["s3_key"]] = gros
    ref = _appeler(all_tools, source=_pf())
    _tourner(monde)
    assert _statut(all_tools, ref["job_id"])["status"] == "done"

    def _boum(key, **kw):
        raise media_store.MediaError(413, "file_too_large", "trop gros")

    ref2 = _appeler(all_tools, source=_pf())
    monkeypatch.setattr(media_store, "fetch_object", _boum)
    _tourner(monde)
    out = _statut(all_tools, ref2["job_id"])
    assert out["status"] == "failed" and "file_too_large" in out["error"]
    assert monde["jobs"][ref2["job_id"]]["audio_key"] in monde["s3"].supprimes
