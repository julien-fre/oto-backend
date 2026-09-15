"""`/api/upload/{token}` — la seule route d'ÉCRITURE servie sans jeton d'auth.

Trois verbes (PUT brut, POST multipart, GET page) partagent une URL que n'importe qui
peut appeler : il n'y a **pas de JWT** ici, le jeton scellé dans l'URL fait foi
(issue #105). Tout ce qui empêche cette route d'être une porte d'écriture ouverte tient
dans quatre gardes, et `oto_mcp/api/uploads.py` était à **22 %** de couverture le
15/09/2026 — la moitié de ces gardes ne s'exécutait dans aucun test.

Les quatre, dans l'ordre où le handler les applique :

1. **La signature** (`upload_tokens.verify`) : HMAC, `typ == "upload"`, expiration. Le
   champ `typ` existe pour qu'un *state* OAuth — signé avec le MÊME secret partagé
   `OTO_MCP_OAUTH_STATE_SECRET` — ne puisse pas être rejoué ici comme jeton d'upload.
   Ce banc forge exactement ce jeton-là.
2. **L'autz RÉAPPLIQUÉE** (`check_target_access`) : le jeton ne fait pas foi seul. Un
   jeton légitime émis pour un projet dont l'accès a été retiré depuis ne doit plus
   écrire — c'est le verrou IDOR d'ADR 0009/0030.
3. **La borne de taille**, avant toute écriture.
4. **L'usage unique** (`db.consume_upload_token`), consommé **AVANT** la
   matérialisation : un jeton dont l'écriture échoue est brûlé quand même. C'est
   délibéré (anti-double-écriture) et c'est contre-intuitif — donc testé.

Et une cinquième garde qui n'est pas dans le handler mais dans la page : le GET rend du
HTML **à un appelant anonyme**, en y interpolant un libellé qui vient du jeton (nom de
fichier, titre). `_html.escape` est la seule chose entre ce libellé et un XSS servi
depuis notre domaine. On le prouve en signant un jeton dont le nom de fichier est une
balise.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from oto_mcp import db, upload_tokens
from oto_mcp.api import uploads

SECRET = "secret-de-banc-assez-long-pour-etre-credible"
CIBLE = {"kind": "project_file", "project_id": 42, "filename": "rapport.pdf"}


@pytest.fixture
def journal():
    """Carnet de bord : ce que le handler a réellement appelé, dans l'ordre.

    Volontairement pas un mock — l'essentiel des assertions de ce fichier porte sur ce
    qui n'a PAS été appelé, et un mock nu répond à tout.
    """
    return {"autz": [], "consommes": [], "materialises": [], "brules": set()}


@pytest.fixture
def client(monkeypatch, journal):
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", SECRET)

    def _autz(sub, target):
        journal["autz"].append((sub, target))

    def _consomme(jti):
        """Vraie sémantique d'usage unique : la seconde fois rend False."""
        journal["consommes"].append(jti)
        if jti in journal["brules"]:
            return False
        journal["brules"].add(jti)
        return True

    def _materialise(sub, target, data, ct):
        journal["materialises"].append((sub, target, len(data), ct))
        return {"ok": True, "kind": target["kind"], "bytes": len(data)}

    monkeypatch.setattr(upload_tokens, "check_target_access", _autz)
    monkeypatch.setattr(upload_tokens, "materialize", _materialise)
    monkeypatch.setattr(db, "consume_upload_token", _consomme)

    return TestClient(Starlette(routes=[
        Route("/api/upload/{token}", uploads.upload_receive, methods=["PUT", "POST"]),
        Route("/api/upload/{token}", uploads.upload_form, methods=["GET"]),
    ]))


def _jeton(sub="u-porteur", target=None, *, ttl=900) -> str:
    return upload_tokens.sign(sub, 7, target or CIBLE, ttl=ttl)[0]


def _forge(payload: dict, secret: str = SECRET) -> str:
    """Un jeton signé à la main — pour éprouver ce que `verify` regarde DANS le payload."""
    brut = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(secret.encode(), brut, hashlib.sha256).digest()
    b64 = lambda d: base64.urlsafe_b64encode(d).rstrip(b"=").decode()  # noqa: E731
    return f"{b64(brut)}.{b64(sig)}"


def _rien_n_a_ete_ecrit(journal):
    assert journal["consommes"] == [], "un jeton brûlé sur un refus est un jeton perdu"
    assert journal["materialises"] == [], "un refus ne doit rien écrire dans la cible"


# ── 1. la signature : ce qui n'est pas un jeton d'upload valide ──────────────

@pytest.mark.parametrize("cas, fabrique", [
    ("chaîne quelconque", lambda: "pas-un-jeton"),
    ("sans séparateur", lambda: "abcdef"),
    ("signature vide", lambda: _jeton().split(".")[0] + "."),
    ("expiré", lambda: _jeton(ttl=-1)),
    ("signé avec un AUTRE secret", lambda: _forge(
        {"typ": "upload", "jti": "j1", "sub": "u-porteur", "org": 7,
         "target": CIBLE, "exp": int(time.time()) + 900}, secret="un-autre-secret")),
    ("payload retouché, signature d'origine", lambda:
        _jeton().split(".")[0][:-2] + "AA." + _jeton().split(".")[1]),
])
def test_un_jeton_qui_ne_tient_pas_n_ecrit_rien(client, journal, cas, fabrique):
    r = client.put(f"/api/upload/{fabrique()}", content=b"charge utile")
    assert r.status_code == 401, cas
    assert r.json()["error"] == "invalid_or_expired_token"
    _rien_n_a_ete_ecrit(journal)


def test_un_state_oauth_signe_du_meme_secret_n_est_pas_un_jeton_d_upload(client, journal):
    """Le champ `typ` porte tout le poids : le secret HMAC est PARTAGÉ avec les states
    OAuth. Sans lui, un state capturé dans une URL de rappel — une valeur qui transite
    par le navigateur, donc par l'historique et le Referer — deviendrait un droit
    d'écrire dans un projet."""
    state = _forge({"typ": "oauth_state", "jti": "j2", "sub": "u-porteur", "org": 7,
                    "target": CIBLE, "exp": int(time.time()) + 900})
    r = client.put(f"/api/upload/{state}", content=b"charge utile")
    assert r.status_code == 401 and r.json()["error"] == "invalid_or_expired_token"
    _rien_n_a_ete_ecrit(journal)


def test_un_jeton_sans_typ_du_tout_est_refuse(client, journal):
    muet = _forge({"jti": "j3", "sub": "u-porteur", "org": 7, "target": CIBLE,
                   "exp": int(time.time()) + 900})
    r = client.put(f"/api/upload/{muet}", content=b"x")
    assert r.status_code == 401
    _rien_n_a_ete_ecrit(journal)


# ── 2. l'autz réappliquée : le jeton ne fait pas foi seul ────────────────────

def test_un_acces_retire_depuis_l_emission_refuse_l_ecriture(client, journal, monkeypatch):
    """Le verrou IDOR. Le jeton est VALIDE et pourtant on refuse : entre l'émission et
    la réception, l'accès écriture au projet a pu être retiré."""
    def _refuse(sub, target):
        raise upload_tokens.UploadError(403, "forbidden", "Écriture refusée.")

    monkeypatch.setattr(upload_tokens, "check_target_access", _refuse)
    r = client.put(f"/api/upload/{_jeton()}", content=b"charge utile")
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    _rien_n_a_ete_ecrit(journal)


def test_le_sub_oppose_a_la_cible_est_celui_SCELLE_pas_un_en_tete(client, journal):
    """L'identité vient du jeton et de nulle part ailleurs. Si un en-tête pouvait la
    déplacer, le porteur d'un lien choisirait au nom de qui il écrit."""
    r = client.put(f"/api/upload/{_jeton(sub='u-scelle')}", content=b"x",
                   headers={"Authorization": "Bearer u-quelqu-un-dautre",
                            "X-Oto-Org": "999"})
    assert r.status_code == 200
    assert journal["autz"][0][0] == "u-scelle"
    assert journal["materialises"][0][0] == "u-scelle"


def test_une_cible_devenue_inconnue_remonte_son_statut_et_son_code(client, journal,
                                                                   monkeypatch):
    def _disparue(sub, target):
        raise upload_tokens.UploadError(404, "unknown_project", "Projet inconnu.")

    monkeypatch.setattr(upload_tokens, "check_target_access", _disparue)
    r = client.put(f"/api/upload/{_jeton()}", content=b"x")
    assert r.status_code == 404 and r.json()["error"] == "unknown_project"
    _rien_n_a_ete_ecrit(journal)


# ── 3. la borne de taille, et le corps vide ──────────────────────────────────

def test_un_corps_vide_est_refuse_sans_bruler_le_jeton(client, journal):
    """`curl --data-binary @fichier-absent` envoie zéro octet : l'appelant doit pouvoir
    recommencer avec le même lien plutôt que d'avoir à en redemander un."""
    r = client.put(f"/api/upload/{_jeton()}", content=b"")
    assert r.status_code == 400 and r.json()["error"] == "empty_body"
    _rien_n_a_ete_ecrit(journal)


def test_un_corps_au_dela_du_plafond_est_refuse_avant_toute_ecriture(client, journal,
                                                                     monkeypatch):
    monkeypatch.setenv("OTO_MCP_UPLOAD_MAX_BYTES", "64")
    r = client.put(f"/api/upload/{_jeton()}", content=b"o" * 65)
    assert r.status_code == 413 and r.json()["error"] == "content_too_large"
    _rien_n_a_ete_ecrit(journal)


def test_le_corps_exactement_au_plafond_passe(client, journal, monkeypatch):
    """La borne est `>` : un fichier de la taille annoncée doit passer, sinon le
    plafond documenté n'est pas celui qui est servi."""
    monkeypatch.setenv("OTO_MCP_UPLOAD_MAX_BYTES", "64")
    r = client.put(f"/api/upload/{_jeton()}", content=b"o" * 64)
    assert r.status_code == 200 and journal["materialises"][0][2] == 64


# ── 4. l'usage unique, consommé AVANT la matérialisation ─────────────────────

def test_le_meme_jeton_ne_sert_pas_deux_fois(client, journal):
    jeton = _jeton()
    assert client.put(f"/api/upload/{jeton}", content=b"une fois").status_code == 200
    r = client.put(f"/api/upload/{jeton}", content=b"deux fois")
    assert r.status_code == 409 and r.json()["error"] == "token_already_used"
    assert len(journal["materialises"]) == 1, "le rejeu a écrit une seconde fois"


def test_une_materialisation_qui_echoue_brule_quand_meme_le_jeton(client, journal,
                                                                  monkeypatch):
    """Contre-intuitif et VOULU : consommer avant d'écrire. Rendre le jeton après un
    échec ouvrirait une fenêtre de double-écriture (deux `curl` concurrents dont le
    premier échoue tard). L'appelant redemande un lien — c'est le prix assumé."""
    def _echoue(sub, target, data, ct):
        raise upload_tokens.UploadError(500, "storage_unavailable", "stockage HS")

    monkeypatch.setattr(upload_tokens, "materialize", _echoue)
    jeton = _jeton()
    r = client.put(f"/api/upload/{jeton}", content=b"charge")
    assert r.status_code == 500 and r.json()["error"] == "storage_unavailable"
    assert len(journal["consommes"]) == 1

    # Et le jeton est bien brûlé : la seconde tentative ne re-tente même pas d'écrire.
    r2 = client.put(f"/api/upload/{jeton}", content=b"charge")
    assert r2.status_code == 409


# ── le POST multipart (fallback humain) ──────────────────────────────────────

@pytest.mark.parametrize("entete, corps", [
    ("multipart/form-data", b"peu importe"),
    ("multipart/form-data; boundary=f", b"\x00\x01n-importe-quoi"),
    ("multipart/form-data; boundary=frontiere", b"--frontiere\r\nincomplet"),
])
def test_un_multipart_illisible_ne_brule_pas_le_jeton(client, journal, entete, corps):
    """Le formulaire est le chemin HUMAIN : un envoi interrompu doit pouvoir être
    refait avec le MÊME lien. Brûler le jeton sur un corps mal formé obligerait la
    personne à redemander un lien à l'agent."""
    r = client.post(f"/api/upload/{_jeton()}", content=corps,
                    headers={"content-type": entete})
    assert r.status_code == 400
    assert r.json()["error"] in ("invalid_multipart", "missing_file")
    _rien_n_a_ete_ecrit(journal)


def test_un_formulaire_sans_fichier_est_refuse(client, journal):
    r = client.post(f"/api/upload/{_jeton()}", data={"autre": "x"})
    assert r.status_code == 400 and r.json()["error"] == "missing_file"
    _rien_n_a_ete_ecrit(journal)


def test_un_champ_file_textuel_est_un_400_pas_un_500(client, journal):
    r = client.post(f"/api/upload/{_jeton()}", data={"file": "https://ailleurs/x.pdf"})
    assert r.status_code == 400 and r.json()["error"] == "missing_file"
    _rien_n_a_ete_ecrit(journal)


def test_le_type_declare_par_la_partie_multipart_atteint_la_materialisation(client,
                                                                           journal):
    r = client.post(f"/api/upload/{_jeton()}",
                    files={"file": ("rapport.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert r.status_code == 200
    assert journal["materialises"][0][3] == "application/pdf"


def test_l_accuse_ne_renvoie_jamais_le_corps_recu(client):
    """« Accusé léger, jamais le body » : ce chemin existe précisément pour que le
    contenu ne repasse pas par le contexte du modèle. Le lui rendre en réponse
    annulerait la raison d'être de la route."""
    secret = b"VERBATIM-QUI-NE-DOIT-PAS-REVENIR"
    r = client.put(f"/api/upload/{_jeton()}", content=b"entete\n" + secret + b"\nfin")
    assert r.status_code == 200
    assert secret.decode() not in r.text


# ── la page GET : anonyme, et elle interpole ce que le jeton porte ───────────

def test_un_lien_invalide_rend_une_page_sans_formulaire(client, journal):
    r = client.get("/api/upload/pas-un-jeton")
    assert r.status_code == 401
    assert "<form" not in r.text, "un formulaire sur un lien mort fait poster dans le vide"
    assert "invalide ou expiré" in r.text
    assert journal["consommes"] == []


def test_le_GET_ne_consomme_PAS_le_jeton(client, journal):
    """Documenté et essentiel : ouvrir le lien (ou le faire pré-charger par un webmail,
    un antivirus, un aperçu de messagerie) ne doit pas griller l'upload avant que la
    personne ait choisi son fichier."""
    jeton = _jeton()
    assert client.get(f"/api/upload/{jeton}").status_code == 200
    assert journal["consommes"] == []
    assert client.put(f"/api/upload/{jeton}", content=b"x").status_code == 200


def test_un_libelle_de_cible_ne_peut_pas_injecter_de_balise(client):
    """La page est servie **sans authentification** depuis notre domaine, et son libellé
    vient d'un nom de fichier choisi à l'émission. Sans échappement, un lien suffit à
    exécuter du script dans l'origine `mcp.oto.cx`."""
    piege = {"kind": "project_file", "project_id": 42,
             "filename": '<script>alert(1)</script>'}
    r = client.get(f"/api/upload/{_jeton(target=piege)}")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


@pytest.mark.parametrize("chemin, attendu", [
    ("pas-un-jeton", 401),
    (None, 200),
])
def test_la_page_ne_laisse_fuir_le_lien_ni_par_le_cache_ni_par_le_referer(
        client, chemin, attendu):
    """Le jeton EST le secret et il vit DANS l'URL : un cache partagé ou un `Referer`
    vers une ressource tierce le publierait."""
    r = client.get(f"/api/upload/{chemin or _jeton()}")
    assert r.status_code == attendu
    assert r.headers["cache-control"] == "private"
    assert r.headers["referrer-policy"] == "no-referrer"
