"""#559 — un `account_id` ne se lie pas sur parole, quel que soit le chemin.

La clé Unipile de la plateforme est **partagée entre les organisations** : elle adresse
tout l'abonnement. Un `account_id` accepté sans contrôle sur ce socle-là, c'est une
frontière entre organisations qui tient à la bonne foi d'un corps de requête.

Deux chemins écrivent la MÊME liaison — le webhook de notification et la
réconciliation poll-and-bind — et **un seul contrôlait**. Le webhook reprenait
`body["account_id"]` tel quel : le nonce prouve « c'est bien la session de connexion
de cette personne », il ne dit rien de « c'est bien le compte qui vient d'être créé ».

Ce que ce fichier tient, contre un VRAI PostgreSQL (la garde EST une requête : la
stubber ne prouverait que le stub) :

1. le webhook **refuse** un identifiant qui appartient à quelqu'un d'autre — et n'écrit
   rien, ni pour l'attaquant ni contre la victime ;
2. il **lie** l'identifiant attendu (libre, ou une ligne morte du réclamant qu'Unipile
   réutilise à la reconnexion) ;
3. **rejoué**, il n'écrit qu'une fois (le nonce est consommé au premier passage) ;
4. son refus est **journalisé**, et sa réponse reste **indiscernable** d'un succès —
   un appelant anonyme n'apprend pas ce qu'on attendait ;
5. le **cliquet du lot** : les deux chemins refusent le même identifiant hostile. C'est
   la seule assertion qui empêche la garde de se re-perdre sur un seul des deux, ce qui
   est exactement ce qui s'est produit.
"""
from __future__ import annotations

import json
import logging
import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

VICTIME = "usr_victime_559"
PIRATE = "usr_pirate_559"
ACC_VICTIME = "acc_de_la_victime"


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Base JETABLE + vrai `init_db()`, sur son propre pool.

    Base PROPRE et non le conteneur partagé : un `init_db()` dans la base de session
    y laisse ~67 tables et fait tomber des tests étrangers qui recréent la leur.
    """
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_559_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom

    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db

        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


def _exec(sql, params=()):
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        conn.execute(sql, params)


def _rows(sql, params=()):
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


@pytest.fixture
def scene(live):
    """Deux orgs, deux personnes, et le compte de la victime DÉJÀ lié chez elle.

    La victime et le pirate sont dans des orgs différentes : c'est la frontière que
    le lot défend. Les deux orgs partagent la clé plateforme — donc `ACC_VICTIME`
    est techniquement joignable depuis l'org du pirate, et rien d'autre que cette
    garde ne l'en empêche.
    """
    _exec("DELETE FROM unipile_accounts")
    _exec("DELETE FROM unipile_pending")
    org_victime = _rows("INSERT INTO orgs (name) VALUES ('Org victime') RETURNING id")[0]["id"]
    org_pirate = _rows("INSERT INTO orgs (name) VALUES ('Org pirate') RETURNING id")[0]["id"]
    from oto_mcp import db

    db.set_unipile_account(VICTIME, ACC_VICTIME, account_name="La victime",
                           org_id=org_victime, provider="LINKEDIN", platform_seat=True)
    return {"org_victime": org_victime, "org_pirate": org_pirate}


def _client():
    from oto_mcp.api import connectors as api_connectors

    # Le webhook n'utilise AUCUNE des primitives d'auth (il est anonyme par nature) :
    # les passer à None prouve au passage qu'il ne s'en sert pas en douce.
    return TestClient(Starlette(routes=api_connectors.make_routes(
        None, None, None, None, None)))


def _pending(sub: str, org_id: int, provider: str = "LINKEDIN") -> str:
    from oto_mcp import db

    nonce = "nonce_" + uuid.uuid4().hex
    db.create_unipile_pending(nonce, sub, org_id, provider, platform_seat=True)
    return nonce


def _post(nonce: str, account_id: str, status: str = "CREATION_SUCCESS"):
    return _client().post("/api/unipile/webhook",
                          content=json.dumps({"status": status, "name": nonce,
                                              "account_id": account_id}),
                          headers={"content-type": "application/json"})


def _liaisons(sub: str) -> list[dict]:
    return _rows("SELECT account_id, org_id, disconnected_at FROM unipile_accounts "
                 "WHERE sub = %s ORDER BY org_id", (sub,))


# ─── 1. Le compte d'un tiers n'est pas réclamable ────────────────────────────

def test_webhook_refuse_le_compte_dun_tiers(scene, caplog):
    """Le cœur de #559 : nonce VALIDE (le pirate a bien lancé sa connexion), mais
    l'identifiant nommé est celui de quelqu'un d'autre."""
    nonce = _pending(PIRATE, scene["org_pirate"])
    with caplog.at_level(logging.WARNING, logger="oto_mcp.api.connectors"):
        r = _post(nonce, ACC_VICTIME)

    assert r.status_code == 200
    assert _liaisons(PIRATE) == [], (
        "le compte d'un tiers a été lié au pirate — la clé plateforme étant partagée, "
        "il opère désormais sous le LinkedIn de la victime")
    # Et la victime n'a rien perdu : le refus ne doit pas non plus DÉPLACER la ligne.
    assert [(l["account_id"], l["org_id"]) for l in _liaisons(VICTIME)] == [
        (ACC_VICTIME, scene["org_victime"])]
    assert caplog.records, "un refus muet est un refus qu'on ne saura jamais avoir eu"


def test_le_refus_ne_dit_rien_a_lappelant(scene):
    """Même corps, même code : un anonyme n'apprend pas ce qu'on attendait.

    Répondre autre chose sur un refus transformerait le webhook en oracle — « cet
    identifiant est-il déjà pris ? » se lirait à la réponse. Et un non-2xx ferait
    rejouer le fournisseur en boucle, ce que ce handler évite par construction."""
    refus = _post(_pending(PIRATE, scene["org_pirate"]), ACC_VICTIME)
    succes = _post(_pending(PIRATE, scene["org_pirate"]), "acc_tout_neuf")
    assert (refus.status_code, refus.content) == (succes.status_code, succes.content)


# ─── 2. Ce que l'org attend est lié ──────────────────────────────────────────

def test_webhook_lie_un_identifiant_libre(scene):
    nonce = _pending(PIRATE, scene["org_pirate"])
    assert _post(nonce, "acc_tout_neuf").status_code == 200
    assert [(l["account_id"], l["org_id"]) for l in _liaisons(PIRATE)] == [
        ("acc_tout_neuf", scene["org_pirate"])]


def test_webhook_relie_ma_propre_ligne_morte(scene):
    """Reconnexion : Unipile RÉUTILISE le compte existant (même `account_id`). Une
    ligne morte du réclamant est donc une preuve de propriété, pas un obstacle — la
    réconciliation le sait déjà (`dead_unipile_account_ids_for`), le webhook doit
    en dire autant, sans quoi la garde casse la reconnexion qu'elle prétend servir."""
    from oto_mcp import db

    db.clear_unipile_account(VICTIME, scene["org_victime"], "LINKEDIN")
    nonce = _pending(VICTIME, scene["org_victime"])
    assert _post(nonce, ACC_VICTIME).status_code == 200
    vivantes = [l for l in _liaisons(VICTIME) if l["disconnected_at"] is None]
    assert [l["account_id"] for l in vivantes] == [ACC_VICTIME]


# ─── 3. Rejeu ────────────────────────────────────────────────────────────────

def test_rejeu_du_meme_webhook_nest_pas_une_seconde_liaison(scene):
    nonce = _pending(PIRATE, scene["org_pirate"])
    assert _post(nonce, "acc_tout_neuf").status_code == 200
    assert _post(nonce, "acc_tout_neuf").status_code == 200
    assert len(_liaisons(PIRATE)) == 1


def test_rejeu_avec_un_autre_compte_ne_repasse_pas(scene):
    """Le nonce est consommé au premier passage : un second corps, même bien formé,
    ne rebinde rien. C'est la garde qui existait déjà ; on vérifie que le lot ne
    l'a pas défaite en déplaçant l'ordre des contrôles."""
    nonce = _pending(PIRATE, scene["org_pirate"])
    _post(nonce, "acc_tout_neuf")
    _post(nonce, "acc_encore_un_autre")
    assert [l["account_id"] for l in _liaisons(PIRATE)] == ["acc_tout_neuf"]


# ─── 4. Le cliquet : une seule garde, deux chemins ───────────────────────────

def test_les_deux_chemins_refusent_le_meme_identifiant(scene, monkeypatch):
    """L'assertion qui vaut le lot.

    #559 n'est pas « une garde oubliée » mais « une garde posée UNE fois sur DEUX
    chemins ». Un test qui n'interroge que le webhook laisserait la même divergence
    se réinstaller à la prochaine écriture parallèle. Ici, le MÊME identifiant
    hostile est présenté aux deux, et les deux le refusent.
    """
    from oto_mcp import access, unipile_connect

    class _Cred:
        is_platform, key, config = True, "clef", {}

    monkeypatch.setattr(access, "resolve_credential",
                        lambda *a, **k: _Cred())

    class _Client:
        def list_accounts(self):
            # Le compte de la victime est bien VISIBLE sur la clé partagée : c'est
            # précisément ce qui rend la garde nécessaire.
            return [{"id": ACC_VICTIME, "provider": "linkedin",
                     "created_at": "2099-01-01 00:00:00+00", "name": "La victime"}]

        def account_alive(self, _aid):
            return True

    import oto.tools.unipile as core_unipile

    monkeypatch.setattr(core_unipile, "make_unipile_client", lambda **k: _Client())

    _pending(PIRATE, scene["org_pirate"])
    assert unipile_connect.reconcile_pending(PIRATE) == {"bound": False, "accounts": []}
    assert _liaisons(PIRATE) == []

    _post(_pending(PIRATE, scene["org_pirate"]), ACC_VICTIME)
    assert _liaisons(PIRATE) == []


def test_la_garde_est_une_seule_fonction(scene, monkeypatch):
    """Structurel, et assumé comme tel : les deux chemins passent par le MÊME
    verrou. Le neutraliser doit suffire à ouvrir les deux — s'il n'en ouvre qu'un,
    c'est qu'un second contrôle vit ailleurs, et la divergence est déjà de retour."""
    from oto_mcp import unipile_connect

    vus = []
    monkeypatch.setattr(unipile_connect, "account_claimable",
                        lambda sub, account_id, **k: vus.append(account_id) or True)

    _post(_pending(PIRATE, scene["org_pirate"]), ACC_VICTIME)
    assert vus == [ACC_VICTIME], (
        "le webhook n'a pas consulté la garde partagée — il en a une à lui, ou aucune")
    assert [l["account_id"] for l in _liaisons(PIRATE)] == [ACC_VICTIME]


# ─── 5. Le cliquet structurel : la liste FERMÉE des écrivains ────────────────

# Qui a le droit d'écrire une liaison en s'adressant DIRECTEMENT à la base — donc sans
# passer par la garde. La liste est fermée et chaque entrée porte sa raison. Un
# quatrième nom qui apparaît ici est exactement ce qui s'est produit avec #559 : une
# écriture parallèle née sans la garde de sa voisine.
_ECRIVAINS_DIRECTS = {
    # L'écrivain GARDÉ — celui par lequel tout identifiant venu d'un tiers doit passer.
    ("oto_mcp/unipile_connect.py", "bind_account"),
    # ADOPTION : l'identifiant sort d'une ligne que la base attribue déjà à ce `sub`
    # (`seat_binding_elsewhere` filtre dessus). Rien d'extérieur à confronter.
    ("oto_mcp/unipile_connect.py", "hosted_auth_url"),
    # BASCULE BYO : l'identifiant vient bien d'un appelant, mais il doit exister sur
    # SA PROPRE clé (`cli.list_accounts()`), et le sélecteur REFUSE la clé plateforme
    # (`_unipile_client` rend None hors BYO). Le socle partagé — d'où vient #559 —
    # n'est donc pas atteignable par là. La question « et deux membres d'une même org
    # sur une clé BYO commune ? » reste ouverte, et se traite ailleurs qu'ici : lui
    # appliquer cette garde refuserait un compte d'org délibérément partagé.
    ("oto_mcp/connectors/identities.py", "_unipile_select"),
}


def _appels_directs() -> set:
    """Relevé AST, au grain de la FONCTION englobante — pas un grep, et pas un
    relevé par fichier : une allowlist par fichier blanchirait la prochaine
    écriture ajoutée dans le même module."""
    import ast
    import pathlib

    racine = pathlib.Path(__file__).resolve().parent.parent
    trouves = set()
    for chemin in (racine / "oto_mcp").rglob("*.py"):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for interne in ast.walk(noeud):
                if (isinstance(interne, ast.Call)
                        and isinstance(interne.func, ast.Attribute)
                        and interne.func.attr == "set_unipile_account"):
                    trouves.add((str(chemin.relative_to(racine)), noeud.name))
    return trouves


def test_la_liste_des_ecrivains_de_liaison_est_fermee():
    trouves = _appels_directs()
    assert trouves == _ECRIVAINS_DIRECTS, (
        "une écriture de liaison a été ajoutée ou déplacée. Si elle prend son "
        "`account_id` d'un tiers (corps de requête, inventaire fournisseur), elle "
        "passe par `unipile_connect.bind_account` ; sinon, elle se justifie ici.\n"
        f"en trop : {sorted(trouves - _ECRIVAINS_DIRECTS)}\n"
        f"disparus : {sorted(_ECRIVAINS_DIRECTS - trouves)}")


# ─── 6. Le corps n'est plus recopié dans le journal ──────────────────────────

def test_le_corps_brut_ne_part_plus_dans_le_journal(scene, caplog):
    """Constat annexe de #559 : 2 Ko d'un corps entièrement contrôlé par un appelant
    anonyme partaient en clair au niveau INFO, **nonce compris**. Le nonce est le
    seul secret de ce chemin ; l'instrumentation qui le recopiait avait un objet
    (relever le format réel, fait), elle n'a plus de raison de tourner."""
    nonce = _pending(PIRATE, scene["org_pirate"])
    with caplog.at_level(logging.DEBUG, logger="oto_mcp.api.connectors"):
        _post(nonce, "acc_tout_neuf")
    journal = "\n".join(r.getMessage() for r in caplog.records)
    assert nonce not in journal, "le nonce ne doit apparaître dans aucun journal"
    assert "raw=" not in journal
