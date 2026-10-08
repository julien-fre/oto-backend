"""`POST /api/relay/{token}` — le relais d'upload vers la GED Pennylane (cabinet).

Une route PUBLIQUE (aucun JWT, le jeton du chemin fait foi) : ces épreuves la jouent de
bout en bout par un vrai client HTTP, sans base, comme la CI — le coffre, l'org, la
table anti-rejeu, le journal et le client de la lib sont doublés. Ce qu'elles tiennent :

- seul un jeton de RELAIS, signé, non expiré, jamais servi, ouvre la route ;
- l'org scellée est rejouée : un compte sorti de l'org est refusé, et le jeton de
  cabinet est résolu sous CETTE org, pas sous l'org « maison » du compte ;
- les bornes (`Content-Length`, plafond, places) refusent AVANT de consommer le lien ;
- le doublon de nom est refusé avant d'écrire ;
- le fichier part à la lib en objet fichier (débordé sur disque au-delà d'un
  mégaoctet), jamais lu en entier par la route, et fermé après, succès ou échec ;
- chaque dépôt tenté laisse une ligne de journal, sans jeton.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

JETON_CABINET = "jeton-de-cabinet-secret-123"
ORG = 7
SUB = "u-cabinet"


@pytest.fixture
def banc(monkeypatch):
    import oto.tools.pennylane_firm as pkg
    from oto_mcp import access, db, garde_identite, org_suspension, roles, session_org
    from oto_mcp.api import pennylane_firm as route
    from oto_mcp.connectors import activation
    from oto_mcp.tools import pennylane_firm_socle as socle

    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "secret-de-banc-assez-long")
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://oto.example.test")
    socle._CACHE.clear()

    etat = {"consommes": set(), "journal": [], "membres": {(SUB, ORG)},
            "org_a_la_resolution": [], "recus": [], "reponse": None, "erreur": None}

    def _consommer(jti):
        if jti in etat["consommes"]:
            return False
        etat["consommes"].add(jti)
        return True

    def _resoudre(provider, *a, sub=None, **k):
        etat["org_a_la_resolution"].append((provider, sub, session_org.current_call_org()))
        rc = MagicMock()
        rc.key, rc.mode = JETON_CABINET, "org"
        return rc

    inst = MagicMock()
    inst.list_dms_files.return_value = {"items": [], "has_more": False}

    def _deposer(company_id, fileobj, filename, parent_folder_id, *, name=None,
                 content_type=None, **kw):
        # Comme la lib : lecture PAR MORCEAUX bornés, jamais `read()` entier.
        morceaux = []
        while (m := fileobj.read(256 * 1024)):
            morceaux.append(m)
        etat["recus"].append({"company_id": company_id, "parent_folder_id": parent_folder_id,
                              "name": name, "octets": b"".join(morceaux),
                              "deborde": getattr(fileobj, "_rolled", None),
                              "fichier": fileobj})
        if etat["erreur"] is not None:
            raise etat["erreur"]
        return etat["reponse"] or {"id": 501, "name": name, "path": f"/x/{name}"}

    inst.upload_dms_file.side_effect = _deposer
    monkeypatch.setattr(pkg, "PennylaneFirmClient", lambda key, **kw: inst)
    monkeypatch.setattr(db, "consume_upload_token", _consommer)
    monkeypatch.setattr(db, "insert_tool_call", etat["journal"].append)
    monkeypatch.setattr(garde_identite, "refus", lambda sub: None)
    monkeypatch.setattr(roles, "is_org_member", lambda sub, org: (sub, org) in etat["membres"])
    monkeypatch.setattr(org_suspension, "refus", lambda org: None)
    monkeypatch.setattr(activation, "cran_qui_coupe", lambda *a: None)
    monkeypatch.setattr(access, "resolve_credential", _resoudre)

    app = Starlette(routes=[Route("/api/relay/{token}", route.relay_receive,
                                  methods=["POST"])])
    etat["http"] = TestClient(app)
    etat["client"] = inst
    return etat


def _lien(name="facture.pdf", company_id=9, dossier=5) -> str:
    from oto_mcp.tools import pennylane_firm_relais as relais
    url = relais.frapper(SUB, ORG, company_id, dossier, name)["url"]
    return url.replace("https://oto.example.test", "")


def _poster(banc, chemin, contenu=b"%PDF-1.4 bonjour", nom_fichier="local.pdf"):
    return banc["http"].post(chemin, files={"file": (nom_fichier, contenu,
                                                     "application/pdf")})


def _journal(banc, n=1, delai=2.0):
    fin = time.monotonic() + delai
    while len(banc["journal"]) < n and time.monotonic() < fin:
        time.sleep(0.01)
    return banc["journal"]


# ── Le chemin nominal ─────────────────────────────────────────────────────────

def test_un_depot_part_a_la_ged_et_rend_le_fichier_cree(banc):
    r = _poster(banc, _lien())
    assert r.status_code == 200, r.text
    assert r.json() == {"id": 501, "name": "facture.pdf", "path": "/x/facture.pdf"}
    recu, = banc["recus"]
    assert (recu["company_id"], recu["parent_folder_id"], recu["name"]) == (
        9, 5, "facture.pdf"), "la cible vient du jeton, jamais du client"
    assert recu["octets"] == b"%PDF-1.4 bonjour"
    assert recu["fichier"].closed, "le temporaire est fermé une fois le dépôt fait"


def test_le_jeton_de_cabinet_est_resolu_sous_l_org_SCELLEE(banc):
    _poster(banc, _lien())
    assert banc["org_a_la_resolution"] == [("pennylane_firm", SUB, ORG)], (
        "sans l'épingle, la cascade retomberait sur l'org « maison » du compte")


def test_un_gros_fichier_deborde_sur_disque_et_part_par_morceaux(banc):
    gros = b"x" * (3 * 1024 * 1024)
    r = _poster(banc, _lien(), contenu=gros)
    assert r.status_code == 200, r.text
    recu, = banc["recus"]
    assert recu["octets"] == gros
    assert recu["deborde"] is True, "au-delà d'un mégaoctet, le transit est sur disque"
    assert recu["fichier"].closed


def test_le_journal_garde_la_trace_du_depot_sans_jeton(banc):
    _poster(banc, _lien())
    ligne, = _journal(banc)
    assert ligne["tool"] == "pennylane_firm_relay" and ligne["ok"] is True
    assert ligne["sub"] == SUB and ligne["org_id"] == ORG
    assert ligne["args"] == {"company_id": 9, "parent_folder_id": 5,
                             "name": "facture.pdf", "bytes": 16, "file_id": 501}
    assert JETON_CABINET not in repr(ligne)


# ── Le jeton ──────────────────────────────────────────────────────────────────

def test_un_lien_ne_sert_qu_une_fois(banc):
    from oto_mcp.tools import pennylane_firm_socle as socle
    lien = _lien()
    assert _poster(banc, lien).status_code == 200
    # Le cache dirait déjà « doublon » : on l'oublie, pour que ce soit le JETON qui
    # refuse le rejeu, et pas le nom.
    socle._CACHE.clear()
    r = _poster(banc, lien)
    assert r.status_code == 409 and r.json()["error"] == "token_already_used"
    assert len(banc["recus"]) == 1


def test_un_jeton_d_upload_oto_n_ouvre_pas_le_relais(banc):
    from oto_mcp import upload_tokens
    jeton, _ = upload_tokens.sign(SUB, ORG, {"kind": "pennylane_firm_dms", "company_id": 9,
                                             "parent_folder_id": 5, "name": "a.pdf"})
    r = _poster(banc, f"/api/relay/{jeton}")
    assert r.status_code == 401 and r.json()["error"] == "invalid_or_expired_token"
    assert banc["recus"] == []


@pytest.mark.parametrize("abime", ["expire", "falsifie"])
def test_un_jeton_expire_ou_falsifie_est_refuse(banc, abime):
    from oto_mcp import upload_tokens
    cible = {"kind": "pennylane_firm_dms", "company_id": 9, "parent_folder_id": 5,
             "name": "a.pdf"}
    jeton, _ = upload_tokens.sign(SUB, ORG, cible, ttl=-5 if abime == "expire" else 900,
                                  typ="relay")
    if abime == "falsifie":
        corps, sig = jeton.split(".")
        jeton = f"{corps}.{sig[::-1]}"
    r = _poster(banc, f"/api/relay/{jeton}")
    assert r.status_code == 401
    assert banc["consommes"] == set() and banc["recus"] == []


def test_un_compte_sorti_de_l_org_scellee_est_refuse(banc):
    lien = _lien()
    banc["membres"].clear()
    r = _poster(banc, lien)
    assert r.status_code == 403 and r.json()["error"] == "not_an_org_member"
    assert banc["consommes"] == set(), "un refus d'identité ne brûle pas le lien"
    assert banc["recus"] == []


# ── Les bornes, avant de consommer le lien ─────────────────────────────────────

def test_sans_content_length_le_relais_refuse(banc):
    def _flux():
        yield b"--b\r\n"

    r = banc["http"].post(_lien(), content=_flux(),
                          headers={"content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 411 and r.json()["error"] == "length_required"
    assert banc["consommes"] == set()


def test_un_fichier_annonce_trop_gros_est_refuse_sans_bruler_le_lien(banc, monkeypatch):
    monkeypatch.setenv("OTO_PENNYLANE_FIRM_UPLOAD_MAX_BYTES", "1000")
    r = _poster(banc, _lien(), contenu=b"x" * 200_000)
    assert r.status_code == 413 and r.json()["error"] == "content_too_large"
    assert banc["consommes"] == set() and banc["recus"] == []


def test_un_fichier_au_dela_du_plafond_dans_la_marge_est_coupe_a_la_lecture(banc,
                                                                            monkeypatch):
    # Annoncé sous plafond + enveloppe, mais le fichier lui-même dépasse le plafond.
    monkeypatch.setenv("OTO_PENNYLANE_FIRM_UPLOAD_MAX_BYTES", "1000")
    r = _poster(banc, _lien(), contenu=b"x" * 5000)
    assert r.status_code == 413 and r.json()["error"] == "content_too_large"
    assert banc["recus"] == [], "rien ne part à Pennylane"


def test_toutes_les_places_prises_rend_503_et_le_lien_resert(banc, monkeypatch):
    lien = _lien()
    monkeypatch.setenv("OTO_PENNYLANE_FIRM_RELAY_CONCURRENCY", "0")
    r = _poster(banc, lien)
    assert r.status_code == 503 and r.json()["error"] == "relay_busy"
    assert r.headers["Retry-After"] == "5"
    assert banc["consommes"] == set()
    monkeypatch.setenv("OTO_PENNYLANE_FIRM_RELAY_CONCURRENCY", "4")
    assert _poster(banc, lien).status_code == 200, "le même lien resert"


def test_la_place_est_rendue_meme_sur_un_echec(banc, monkeypatch):
    from oto_mcp.tools import pennylane_firm_relais as relais
    from oto.tools.pennylane_firm import PennylaneFirmRateLimited
    banc["erreur"] = PennylaneFirmRateLimited(429, "rate_limited", "Retry in 60s", retryable=True)
    _poster(banc, _lien())
    assert relais._en_cours == 0


# ── Le doublon, et Pennylane qui refuse ───────────────────────────────────────

def test_un_nom_deja_present_est_refuse_avant_d_ecrire(banc):
    banc["client"].list_dms_files.return_value = {
        "items": [{"id": 1, "name": "facture.pdf"}], "has_more": False}
    r = _poster(banc, _lien())
    assert r.status_code == 409 and r.json()["error"] == "name_already_exists"
    assert banc["consommes"] == set() and banc["recus"] == []


def test_un_depot_reussi_entre_au_cache_et_bloque_le_second(banc):
    assert _poster(banc, _lien()).status_code == 200
    r = _poster(banc, _lien())
    assert r.status_code == 409 and r.json()["error"] == "name_already_exists"
    assert banc["client"].list_dms_files.call_count == 1, "le cache a servi"


def test_un_429_de_pennylane_est_reessayable_et_libere_le_nom(banc):
    from oto.tools.pennylane_firm import PennylaneFirmRateLimited
    from oto_mcp.tools import pennylane_firm_socle as socle
    banc["erreur"] = PennylaneFirmRateLimited(429, "rate_limited", "Retry in 60s", retryable=True)
    r = _poster(banc, _lien())
    assert r.status_code == 429 and r.json()["error"] == "pennylane_rate_limited"
    assert r.headers["Retry-After"] == "60"
    assert banc["recus"][0]["fichier"].closed, "temporaire fermé aussi sur échec"
    assert all(not e["en_vol"] for e in socle._CACHE.values()), "le nom n'est plus en vol"
    ligne, = _journal(banc)
    assert ligne["ok"] is False and ligne["error"] == "pennylane_rate_limited"
