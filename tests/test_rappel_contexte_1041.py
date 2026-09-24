"""Le README d'une org atteint le modèle : rappelé en tête des réponses d'outils tant
qu'il n'est pas lu (oto-backend#1041).

Trois relais, trois bancs :
1. le VERDICT, lu dans la base — README modifié contre journal des lectures
   (`db.contexte_non_lu`) : vraie base, parce que c'est ce que le STOCKAGE porte qui
   décide, et un simulacre rendrait ce qu'on lui a appris ;
2. la RÈGLE autour (exemptions, runner, cache, extinction à la lecture) ;
3. la PLOMBERIE sur un vrai serveur FastMCP : la ligne arrive en tête, survit au rendu
   du vide, ne touche pas une erreur — et on MESURE ce qu'elle ajoute à la réponse.
"""
from __future__ import annotations

import threading

import pytest

from oto_mcp import rappel_contexte


@pytest.fixture(autouse=True)
def _cache_vide():
    rappel_contexte._cache.clear()
    yield
    rappel_contexte._cache.clear()


# ── la ligne : courte, actionnable, bornée ───────────────────────────────────

def test_la_ligne_est_bornee_meme_sous_un_nom_d_org_fleuve():
    texte = rappel_contexte.ligne("Organisation " * 40, 42)
    assert len(texte) <= rappel_contexte.MAX_LIGNE
    assert "`oto_context`" in texte and "`_org=42`" in texte
    court = rappel_contexte.ligne("Acme", 7)
    assert "« Acme »" in court
    # La mesure : ce que le rappel ajoute à une réponse, au plus.
    assert len(court) < 200


# ── 1. le verdict, dans la base ──────────────────────────────────────────────

def _org(nom):
    from oto_mcp.db import _connect
    with _connect() as conn:
        return conn.execute("INSERT INTO orgs (name) VALUES (%s) RETURNING id",
                            (nom,)).fetchone()["id"]


def _readme(scope, owner, corps):
    from oto_mcp import db
    db.set_init_guide_db(scope, str(owner), "readme", corps)


def _lecture(sub, org_id, *, ok=True, tool="oto_context"):
    from oto_mcp import db
    db.insert_tool_call({"tool": tool, "sub": sub, "kind": "mcp", "ok": ok,
                         "org_id": org_id, "duration_ms": 1})


def _vieillir(scope, owner, secondes):
    """Recule la date de modification d'une couche : « lue APRÈS modification »."""
    from oto_mcp.db import _connect
    with _connect() as conn:
        conn.execute("UPDATE nodes SET updated_at = NOW() - make_interval(secs => %s) "
                     "WHERE owner_type = %s AND owner_id = %s AND props->>'slug' = 'readme'",
                     (secondes, scope, str(owner)))


def _couches(org_id, sub, gid=None):
    c = [("org", str(org_id), "readme"), ("user", sub, "readme")]
    if gid is not None:
        c.append(("group", str(gid), "readme"))
    return c


def test_une_org_sans_readme_ne_declenche_rien(live):
    from oto_mcp import db
    org = _org("Sans readme")
    _readme("user", "u-a", "ma note")  # une autre couche seule ne suffit pas
    assert db.contexte_non_lu("u-a", org, _couches(org, "u-a")) is None
    _readme("org", org, "   \n ")  # un corps blanc n'est pas un README
    assert db.contexte_non_lu("u-a", org, _couches(org, "u-a")) is None


def test_non_lu_puis_lu_puis_modifie(live):
    from oto_mcp import db
    org = _org("Acme")
    _readme("org", org, "Règle : valider avant d'envoyer.")
    assert db.contexte_non_lu("u-b", org, _couches(org, "u-b")) == "Acme"

    _vieillir("org", org, 60)
    _lecture("u-b", org)
    assert db.contexte_non_lu("u-b", org, _couches(org, "u-b")) is None

    # README réécrit APRÈS la lecture : le rappel revient, en cours de session.
    _readme("org", org, "Règle : valider avant d'envoyer. Et signer.")
    assert db.contexte_non_lu("u-b", org, _couches(org, "u-b")) == "Acme"


def test_une_couche_cumulee_modifiee_rallume_le_rappel(live):
    from oto_mcp import db
    org = _org("Beta")
    _readme("org", org, "README d'org")
    _readme("group", 9001, "README d'équipe")
    _vieillir("org", org, 120)
    _vieillir("group", 9001, 120)
    _lecture("u-c", org)
    assert db.contexte_non_lu("u-c", org, _couches(org, "u-c", 9001)) is None
    _readme("group", 9001, "README d'équipe, réécrit")
    assert db.contexte_non_lu("u-c", org, _couches(org, "u-c", 9001)) == "Beta"


def test_ce_qui_ne_compte_pas_comme_une_lecture(live):
    """Une lecture sous une AUTRE org, par un AUTRE compte, en échec, ou un autre
    outil : rien de tout cela n'éteint le rappel."""
    from oto_mcp import db
    org, ailleurs = _org("Gamma"), _org("Delta")
    _readme("org", org, "README")
    _vieillir("org", org, 60)
    _lecture("u-d", ailleurs)
    _lecture("u-autre", org)
    _lecture("u-d", org, ok=False)
    _lecture("u-d", org, tool="oto_guide")
    assert db.contexte_non_lu("u-d", org, _couches(org, "u-d")) == "Gamma"


# ── 2. la règle : exemptions, runner, cache ──────────────────────────────────

@pytest.fixture
def appel(monkeypatch):
    """Un appel MCP simulé : compte, org, jeton — et un compteur de requêtes."""
    from oto_mcp import access, db
    from oto_mcp.auth import hooks
    etat = {"sub": "u-e", "org": 5, "token": {}, "requetes": 0, "nom": "Epsilon"}
    monkeypatch.setattr(hooks, "current_user_sub_from_token", lambda: etat["sub"])
    monkeypatch.setattr(hooks, "current_token_axes", lambda: etat["token"])
    monkeypatch.setattr(access, "current_org", lambda sub: etat["org"])
    monkeypatch.setattr(access, "current_group", lambda sub: None)

    def compte(sub, org_id, couches):
        etat["requetes"] += 1
        return etat["nom"]
    monkeypatch.setattr(db, "contexte_non_lu", compte)
    return etat


def test_rappel_sur_un_outil_de_travail(appel):
    cle, texte = rappel_contexte.pour_l_appel("data_rows")
    assert cle == ("u-e", 5)
    assert "« Epsilon »" in texte and "`_org=5`" in texte


@pytest.mark.parametrize("outil", sorted(rappel_contexte.EXEMPTS))
def test_jamais_sur_la_lecture_ni_sur_l_identite(appel, outil):
    cle, texte = rappel_contexte.pour_l_appel(outil)
    assert cle == ("u-e", 5) and texte is None
    assert appel["requetes"] == 0


def test_jamais_pour_un_travail_du_runner(appel):
    appel["token"] = {"token_kind": "delegation", "token_id": 1}
    assert rappel_contexte.pour_l_appel("data_rows") == (None, None)


def test_sans_compte_ou_sans_org_rien(appel):
    appel["org"] = None
    assert rappel_contexte.pour_l_appel("data_rows") == (None, None)
    appel["org"], appel["sub"] = 5, None
    assert rappel_contexte.pour_l_appel("data_rows") == (None, None)


def test_une_requete_par_compte_et_par_org_dans_la_fenetre_du_cache(appel):
    for _ in range(5):
        rappel_contexte.pour_l_appel("data_rows")
    assert appel["requetes"] == 1


def test_la_lecture_servie_eteint_le_rappel_tout_de_suite(appel):
    cle, texte = rappel_contexte.pour_l_appel("data_rows")
    assert texte
    rappel_contexte.constater_lecture(cle)
    assert rappel_contexte.pour_l_appel("data_rows") == (cle, None)
    assert appel["requetes"] == 1


# ── 3. la plomberie, sur un vrai serveur ─────────────────────────────────────

LIGNE = "⚠️ oto — rappel de test"


def _serveur(monkeypatch, *, ligne=LIGNE, vus=None):
    from fastmcp import FastMCP
    from fastmcp.exceptions import ToolError

    from oto_mcp.middleware.empty_result import EmptyResultMiddleware
    from oto_mcp.middleware.rappel_contexte import (ConstatContexteMiddleware,
                                                    RappelContexteMiddleware)

    lectures: list = []
    boucle = threading.get_ident()

    def pour_l_appel(outil):
        # HORS de la boucle : le verdict lit la base.
        assert threading.get_ident() != boucle
        if vus is not None:
            vus.append(outil)
        return ("u", 1), (None if outil in rappel_contexte.EXEMPTS else ligne)

    monkeypatch.setattr(rappel_contexte, "pour_l_appel", pour_l_appel)
    monkeypatch.setattr(rappel_contexte, "constater_lecture", lectures.append)

    mcp = FastMCP("t1041")

    @mcp.tool
    def travail(n: int) -> str:
        return "x" * n

    @mcp.tool
    def rien() -> list:
        return []

    @mcp.tool
    def oto_context() -> str:
        return "le contexte"

    @mcp.tool
    def casse() -> str:
        raise ToolError("boom")

    mcp.add_middleware(RappelContexteMiddleware())
    mcp.add_middleware(EmptyResultMiddleware())
    mcp.add_middleware(ConstatContexteMiddleware())
    return mcp, lectures


def _textes(res):
    return [b.text for b in res.content if getattr(b, "text", None) is not None]


@pytest.mark.asyncio
async def test_la_ligne_arrive_en_tete_et_ne_coute_qu_elle_meme(monkeypatch):
    from fastmcp import Client

    mcp, _ = _serveur(monkeypatch)
    async with Client(mcp) as c:
        res = await c.call_tool("travail", {"n": 500})
    textes = _textes(res)
    assert textes[0] == LIGNE
    assert textes[1:] == ["x" * 500]
    # La mesure de l'effet sur la taille servie : exactement la ligne, rien d'autre.
    assert sum(map(len, textes)) - 500 == len(LIGNE)


@pytest.mark.asyncio
async def test_la_ligne_survit_au_rendu_du_vide(monkeypatch):
    from fastmcp import Client

    mcp, _ = _serveur(monkeypatch)
    async with Client(mcp) as c:
        res = await c.call_tool("rien", {})
    textes = _textes(res)
    assert textes[0] == LIGNE and len(textes) == 2


@pytest.mark.asyncio
async def test_la_lecture_n_est_pas_rappelee_et_eteint_le_rappel(monkeypatch):
    from fastmcp import Client

    mcp, lectures = _serveur(monkeypatch)
    async with Client(mcp) as c:
        res = await c.call_tool("oto_context", {})
    assert _textes(res) == ["le contexte"]
    assert lectures == [("u", 1)]


@pytest.mark.asyncio
async def test_une_erreur_reste_une_erreur_nue(monkeypatch):
    from fastmcp import Client

    mcp, lectures = _serveur(monkeypatch)
    async with Client(mcp) as c:
        res = await c.call_tool("casse", {}, raise_on_error=False)
    assert res.is_error
    assert all(LIGNE not in t for t in _textes(res))
    assert lectures == []


@pytest.mark.asyncio
async def test_un_verdict_en_panne_ne_casse_pas_l_appel(monkeypatch, caplog):
    from fastmcp import Client

    mcp, _ = _serveur(monkeypatch)

    def panne(outil):
        raise RuntimeError("base indisponible")
    monkeypatch.setattr(rappel_contexte, "pour_l_appel", panne)
    async with Client(mcp) as c:
        res = await c.call_tool("travail", {"n": 3})
    assert _textes(res) == ["xxx"]
    assert "rappel du contexte d'org indisponible" in caplog.text


def test_la_chaine_de_production_porte_le_rappel_au_bon_etage():
    """Sur la vraie chaîne : la pose AU-DESSUS du rendu du vide, le verdict SOUS le
    contexte d'appel (l'ordre complet est gardé par `test_middleware_order`)."""
    from _mcp_app import static_mcp

    noms = [type(m).__name__ for m in static_mcp().middleware]
    assert noms.index("RappelContexteMiddleware") < noms.index("EmptyResultMiddleware")
    assert noms.index("RappelContexteMiddleware") < noms.index("MarkdownBodyMiddleware")
    assert noms.index("CallContextMiddleware") < noms.index("ConstatContexteMiddleware")
