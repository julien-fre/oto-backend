"""oto#33 — la plateforme dit ce QU'ELLE EXÉCUTE, sur les trois surfaces.

Ce qui est testé n'est pas « il y a une version quelque part » : c'est que la
coordonnée servie est la bonne, et qu'elle le reste dans les cas où les
coordonnées faciles mentent. Chaque test ci-dessous correspond à une façon de se
tromper qu'on a réellement rencontrée :

- `pip show oto-core` annonce un numéro GELÉ à 1.100.0 (mesuré le 01/09/2026 dans
  le venv du backend : 1.100.0 pour un v1.101.0 installé) → on lit
  `direct_url.json`, et le test le prouve DIFFÉRENTIELLEMENT, en montant une
  distribution dont les deux coordonnées divergent. Un test qui n'aurait fait
  qu'appeler `oto_core()` sur le venv réel aurait passé au vert avec la mauvaise
  source le jour où les deux numéros coïncident.
- un fichier de déploiement réécrit sous un processus vivant (la couleur en cours
  de vidange) ne doit pas lui faire annoncer la version de son successeur.
- l'étiquette doit atteindre les TROIS surfaces. Un endpoint correct pendant que
  l'en-tête est absent, c'est précisément le cas qui a coûté une matinée.

Logique de transport et de métadonnées pure : aucun accès DB.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from oto_mcp import openapi, version
from oto_mcp.api import routes as api_routes
from oto_mcp.version_header import VersionHeader, valeur_entete


@pytest.fixture(autouse=True)
def _instantane_frais(monkeypatch):
    """`instantane()` est mémoïsé DÉLIBÉRÉMENT (cf. son docstring) : sans purge, le
    premier test qui l'appelle figerait la valeur pour tous les suivants — et les
    tests qui suivent passeraient en mesurant le cache, pas le code."""
    monkeypatch.delenv(version.ENV_REF, raising=False)
    monkeypatch.delenv(version.ENV_SHA, raising=False)
    monkeypatch.delenv(version.ENV_AT, raising=False)
    version.instantane.cache_clear()
    yield
    version.instantane.cache_clear()


# ── l'étiquette : une seule forme, servie partout ────────────────────────────

def test_letiquette_porte_le_tag_ET_le_commit():
    """Le tag DIT la release, le commit la DÉSIGNE. Le tag seul ment après un
    retag ; le commit seul ne se lit pas. On sert les deux."""
    assert version.etiquette("v1.2.3", "6d5bf16b1234") == "v1.2.3+6d5bf16b"


@pytest.mark.parametrize("ref, commit, attendu", [
    ("origin/main", "6d5bf16b1234", "origin/main+6d5bf16b"),  # préprod : une branche
    ("v1.2.3", None, "v1.2.3"),                               # pas de SHA relevé
    (None, "6d5bf16b1234", "6d5bf16b"),                       # pas de ref
    (None, None, "unknown"),                                  # rien : on le DIT
])
def test_letiquette_ne_fabrique_jamais_ce_quelle_na_pas(ref, commit, attendu):
    assert version.etiquette(ref, commit) == attendu


# ── d'où vient la coordonnée ─────────────────────────────────────────────────

def test_lenvironnement_prime_sur_le_fichier(monkeypatch, tmp_path):
    """C'est la seule voie d'une image Docker / d'un on-premise (pas d'arbre git),
    et la reprise si le fichier manque."""
    (tmp_path / version.FICHIER_DEPLOIEMENT).write_text(
        json.dumps({"ref": "v0.0.1", "commit": "aaaaaaaa"}), encoding="utf-8")
    monkeypatch.setattr(version, "racine_de_l_arbre", lambda: tmp_path)
    monkeypatch.setenv(version.ENV_REF, "v9.9.9")
    monkeypatch.setenv(version.ENV_SHA, "bbbbbbbbcccc")

    instantane = version.instantane()
    assert instantane["version"] == "v9.9.9+bbbbbbbb"
    assert instantane["source"] == "env"


def test_le_fichier_du_deploiement_est_lu_quand_lenv_se_tait(monkeypatch, tmp_path):
    (tmp_path / version.FICHIER_DEPLOIEMENT).write_text(json.dumps({
        "ref": "v1.2.3", "commit": "6d5bf16b1234",
        "deployed_at": "2026-09-01T10:12:33+02:00",
    }), encoding="utf-8")
    monkeypatch.setattr(version, "racine_de_l_arbre", lambda: tmp_path)

    instantane = version.instantane()
    assert instantane["version"] == "v1.2.3+6d5bf16b"
    assert instantane["ref"] == "v1.2.3"
    assert instantane["commit"] == "6d5bf16b1234"
    assert instantane["deployed_at"] == "2026-09-01T10:12:33+02:00"
    assert instantane["source"] == "deploy_file"


@pytest.mark.parametrize("contenu", [
    '{"ref": "v1.2.3", "commit": "6d5bf1',   # écriture interrompue en plein vol
    '[]',                                    # pas un objet
    '{}',                                    # objet vide
    '{"ref": "   "}',                        # coordonnée blanche
])
def test_une_coordonnee_a_moitie_ecrite_vaut_absence(monkeypatch, tmp_path, contenu):
    """Un déploiement interrompu au milieu du `printf` laisse un fichier tronqué.
    Mieux vaut « unknown » — qui est VRAI — qu'une moitié de coordonnée, qui
    daterait une mesure sur du faux."""
    (tmp_path / version.FICHIER_DEPLOIEMENT).write_text(contenu, encoding="utf-8")
    monkeypatch.setattr(version, "racine_de_l_arbre", lambda: tmp_path)

    instantane = version.instantane()
    assert instantane["version"] == version.INCONNU
    assert instantane["source"] == version.INCONNU


def test_sans_fichier_ni_env_on_dit_quon_ne_sait_pas(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "racine_de_l_arbre", lambda: tmp_path)
    assert version.instantane()["version"] == version.INCONNU


def test_un_fichier_reecrit_sous_le_processus_ne_change_pas_ce_quil_annonce(
        monkeypatch, tmp_path):
    """Le scénario réel : la couleur en cours de VIDANGE finit ses requêtes pendant
    que son successeur s'installe. Elle exécute encore l'ancien code — elle doit
    donc continuer à annoncer l'ancienne version, sinon la vidange fabrique
    exactement l'erreur d'attribution que ce lot supprime."""
    fichier = tmp_path / version.FICHIER_DEPLOIEMENT
    fichier.write_text(json.dumps({"ref": "v1.0.0", "commit": "aaaaaaaa"}),
                       encoding="utf-8")
    monkeypatch.setattr(version, "racine_de_l_arbre", lambda: tmp_path)
    assert version.instantane()["version"] == "v1.0.0+aaaaaaaa"

    fichier.write_text(json.dumps({"ref": "v2.0.0", "commit": "bbbbbbbb"}),
                       encoding="utf-8")
    assert version.instantane()["version"] == "v1.0.0+aaaaaaaa", (
        "le processus a relu le fichier — il annonce la version de son successeur")


# ── le piège oto-core : `pip show` ment ──────────────────────────────────────

class _FausseDistribution:
    """Une distribution dont les DEUX coordonnées divergent — ce qu'est réellement
    le venv du backend : `Version: 1.100.0` dans METADATA, `v1.101.0` dans
    `direct_url.json`."""

    def __init__(self, direct_url: str | None):
        self._direct_url = direct_url
        self.metadata = {"Version": "1.100.0"}

    def read_text(self, nom):
        return self._direct_url if nom == "direct_url.json" else None


def test_le_tag_oto_core_vient_de_ce_que_pip_ECRIT_pas_de_pip_show(monkeypatch):
    """Le différentiel qui compte : METADATA dit 1.100.0, `direct_url.json` dit
    v1.101.0, et c'est v1.101.0 qui exécute les appels. Un test qui se contenterait
    de lire une version non vide passerait au vert avec la mauvaise source."""
    monkeypatch.setattr(version.metadata, "distribution", lambda _: _FausseDistribution(
        '{"url": "https://github.com/otomata-tech/oto-core.git",'
        ' "vcs_info": {"vcs": "git", "commit_id": "371a8c0de65ab410",'
        ' "requested_revision": "v1.101.0"}}'))

    core = version.oto_core()
    assert core == {"tag": "v1.101.0", "commit": "371a8c0de65ab410",
                    "source": "direct_url"}
    assert core["tag"] != "1.100.0", "on a resservi le numéro gelé de pip show"


def test_sans_direct_url_on_sert_le_numero_gele_EN_LE_NOMMANT(monkeypatch):
    """Installation depuis PyPI : plus de `direct_url.json`. On sert ce qui reste
    — mais `source` dit d'où ça vient, pour qu'un lecteur sache que la précision a
    baissé plutôt que de croire à un tag."""
    monkeypatch.setattr(version.metadata, "distribution",
                        lambda _: _FausseDistribution(None))
    assert version.oto_core() == {"tag": "1.100.0", "commit": None,
                                  "source": "metadata"}


def test_oto_core_absent_se_dit(monkeypatch):
    def _absent(_):
        raise version.metadata.PackageNotFoundError("oto-core")

    monkeypatch.setattr(version.metadata, "distribution", _absent)
    assert version.oto_core() == {"tag": None, "commit": None, "source": "absent"}


def test_loto_core_reellement_installe_est_servi():
    """Sur le venv RÉEL de ce test : ce qui est rendu doit être la coordonnée
    d'installation, pas le champ `Version` du paquet."""
    core = version.oto_core()
    assert core["source"] in ("direct_url", "metadata", "absent")
    if core["source"] == "direct_url":
        assert core["commit"], "une install git rend toujours son commit résolu"


# ── surface 1 : GET /api/version, sans auth ──────────────────────────────────

def _endpoint_version():
    """Le handler tel que la TABLE DE ROUTES RÉELLE le monte — pas la fonction
    importée à la main. C'est le montage qu'on veut figer : un handler juste qui
    n'est pas monté ne sert rien (convention « garde-fou exercé sur le montage
    réel »)."""
    for r in api_routes.make_routes(types.SimpleNamespace()):
        if getattr(r, "path", None) == "/api/version" and "GET" in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError("/api/version n'est pas montée dans la table de routes")


def _requete(path="/api/version", entetes=None):
    from starlette.requests import Request
    return Request({
        "type": "http", "method": "GET", "path": path, "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "http_version": "1.1", "headers": entetes or [], "path_params": {},
    })


def test_api_version_repond_sans_le_moindre_en_tete_dauth(monkeypatch, tmp_path):
    """Sans auth, et c'est le point : un contrôle externe ou un agent qui constate
    une dérive doit pouvoir la dater avant d'avoir résolu quoi que ce soit."""
    (tmp_path / version.FICHIER_DEPLOIEMENT).write_text(json.dumps({
        "ref": "v1.2.3", "commit": "6d5bf16b1234"}), encoding="utf-8")
    monkeypatch.setattr(version, "racine_de_l_arbre", lambda: tmp_path)

    reponse = asyncio.run(_endpoint_version()(_requete()))
    assert reponse.status_code == 200
    charge = json.loads(bytes(reponse.body).decode())
    assert charge["service"] == "oto-backend"
    assert charge["version"] == "v1.2.3+6d5bf16b"
    assert charge["started_at"], "sans démarrage du process, on ne sait pas depuis quand"
    assert set(charge["oto_core"]) == {"tag", "commit", "source"}


def test_api_version_ne_sert_aucune_valeur():
    """Le document est public : il décrit une COORDONNÉE, jamais une donnée. Le
    figer évite qu'on y greffe un jour un état de configuration."""
    charge = json.loads(bytes(asyncio.run(_endpoint_version()(_requete())).body).decode())
    assert set(charge) == {"service", "version", "ref", "commit", "deployed_at",
                           "started_at", "source", "oto_core"}


# ── surface 2 : info.version de l'OpenAPI ────────────────────────────────────

def test_le_descriptif_openapi_porte_la_version_servie(monkeypatch, tmp_path):
    """Il portait « 1 » en dur : une carte dérivée du serveur à chaque requête ne
    disait pas de quel jour elle datait."""
    (tmp_path / version.FICHIER_DEPLOIEMENT).write_text(json.dumps({
        "ref": "v1.2.3", "commit": "6d5bf16b1234"}), encoding="utf-8")
    monkeypatch.setattr(version, "racine_de_l_arbre", lambda: tmp_path)

    assert openapi.build()["info"]["version"] == "v1.2.3+6d5bf16b"


def test_les_trois_surfaces_servent_la_MEME_etiquette(monkeypatch, tmp_path):
    """Une seule forme partout, pour qu'un descriptif d'API et un journal d'appels
    se recoupent sans traduction. Trois sources qui divergent, c'est trois versions
    à réconcilier à la main — soit le problème d'origine."""
    (tmp_path / version.FICHIER_DEPLOIEMENT).write_text(json.dumps({
        "ref": "v1.2.3", "commit": "6d5bf16b1234"}), encoding="utf-8")
    monkeypatch.setattr(version, "racine_de_l_arbre", lambda: tmp_path)

    endpoint = json.loads(bytes(asyncio.run(_endpoint_version()(_requete())).body).decode())
    entete = valeur_entete(version.version_servie()).decode()
    assert endpoint["version"] == openapi.build()["info"]["version"] == entete


# ── surface 3 : X-Oto-Version sur CHAQUE réponse ─────────────────────────────

def _app_etiquetee(etiquette="v1.2.3+6d5bf16b"):
    interne = Starlette(routes=[
        Route("/api/n-importe-quoi", lambda r: PlainTextResponse("ok")),
        Route("/mcp", lambda r: PlainTextResponse("ok")),
        Route("/boom", lambda r: PlainTextResponse("non", status_code=500)),
    ])
    return TestClient(VersionHeader(interne, etiquette))


@pytest.mark.parametrize("chemin", ["/api/n-importe-quoi", "/mcp", "/boom", "/inconnu"])
def test_chaque_reponse_porte_letiquette(chemin):
    """Aucun filtre de chemin, délibérément : `/mcp`, `/api/*`, un 404, un 500. Un
    filtre sur `/api/` aurait raté exactement la face dont les agents se servent —
    et c'est sur une réponse d'ERREUR qu'on cherche le plus à dater."""
    assert _app_etiquetee().get(chemin).headers["x-oto-version"] == "v1.2.3+6d5bf16b"


def test_letiquette_deja_posee_nest_jamais_ecrasee():
    """La couche AJOUTE, elle ne réécrit pas : une réponse qui porte déjà sa propre
    version (un jour, un pont amont) reste telle quelle."""
    interne = Starlette(routes=[Route(
        "/x", lambda r: PlainTextResponse("ok", headers={"X-Oto-Version": "deja"}))])
    reponse = TestClient(VersionHeader(interne, "v1.2.3")).get("/x")
    assert reponse.headers["x-oto-version"] == "deja"
    assert len(reponse.headers.get_list("x-oto-version")) == 1


@pytest.mark.parametrize("brut, attendu", [
    ("v1.2.3+6d5bf16b", b"v1.2.3+6d5bf16b"),
    ("origin/main+6d5bf16b", b"origin/main+6d5bf16b"),
    ("v1.2.3 ⚠ étiquette abîmée", b"v1.2.3tiquetteabme"),   # filtré, pas levé
    ("é", b"unknown"),                                       # plus rien de servable
])
def test_une_etiquette_abimee_ne_fait_pas_tomber_le_serveur(brut, attendu):
    """Un en-tête non encodable lèverait sur CHAQUE réponse. Une version un peu
    abîmée vaut infiniment mieux qu'un serveur qui ne répond plus."""
    assert valeur_entete(brut) == attendu


def test_le_streaming_nest_pas_tamponne():
    """Le corps n'est jamais lu par la couche : un SSE doit continuer à sortir
    morceau par morceau, sinon `/mcp` se met à répondre d'un bloc."""
    morceaux = []

    async def _flux(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        for i in range(3):
            await send({"type": "http.response.body", "body": f"data: {i}\n\n".encode(),
                        "more_body": i < 2})

    async def _capture(message):
        morceaux.append(message)

    async def _receive():
        return {"type": "http.request"}

    asyncio.run(VersionHeader(_flux, "v1.2.3")(
        {"type": "http", "method": "GET", "path": "/mcp", "headers": []},
        _receive, _capture))

    corps = [m for m in morceaux if m["type"] == "http.response.body"]
    assert len(corps) == 3, "la couche a tamponné le flux"
    entetes = dict(morceaux[0]["headers"])
    assert entetes[b"x-oto-version"] == b"v1.2.3"


def test_le_lifespan_traverse_sans_wrapper():
    """Pas de réponse HTTP à étiqueter, donc pas de coût sur le chemin — et surtout
    pas de `send` réécrit dans le protocole de démarrage."""
    vus = []

    async def _interne(scope, receive, send):
        vus.append((scope["type"], send))

    async def _send(_):
        pass

    asyncio.run(VersionHeader(_interne, "v1.2.3")({"type": "lifespan"}, None, _send))
    assert vus == [("lifespan", _send)], "le send du lifespan a été enveloppé"


# ── l'assemblage : une couche qu'on peut retirer sans rougir n'en est pas une ─

def test_la_couche_est_bien_servie_par_lapp_racine():
    """`build_root_app` est le point d'assemblage exact. La retirer doit faire
    rouge ici — sinon rien ne garantit que l'étiquette atteint le fil."""
    from oto_mcp.response_charset import ResponseCharset
    from oto_mcp.server import build_root_app
    from oto_mcp.subdomain_project import HostDispatch

    racine = build_root_app(object(), object())
    assert isinstance(racine.app, VersionHeader), (
        "l'étiquetage de version n'est plus sous la garde de déconnexion")
    assert isinstance(racine.app.app, ResponseCharset)
    assert isinstance(racine.app.app.app, HostDispatch), (
        "posée sous le dispatch, elle raterait l'app anonyme des sous-domaines")


def test_len_tete_est_lisible_par_un_navigateur():
    """Sans `Access-Control-Expose-Headers`, l'en-tête part sur le fil mais reste
    illisible à `fetch` — donc au dashboard. Un en-tête qu'aucun consommateur ne
    peut lire ne date rien."""
    from oto_mcp.api.base import _allowed_origins, _cors_headers

    entetes = _cors_headers(_allowed_origins()[0])
    assert "X-Oto-Version" in entetes["Access-Control-Expose-Headers"]
