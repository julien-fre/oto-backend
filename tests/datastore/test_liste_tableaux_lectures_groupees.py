"""`GET /api/datastores` ne coûte pas des lectures par tableau, et juge la gouvernance
comme `can_govern`.

La liste appelait `ownership.can_govern` tableau par tableau : le propriétaire relu deux
fois, l'escalade, puis une lecture des partages par principal de l'acteur — 5 à 10
lectures par tableau, chacune sur son propre emprunt au pool. Elle juge désormais toute
la page d'un coup (`ownership.can_govern_many`) : le propriétaire est dans la ligne
listée, l'escalade se juge une fois par propriétaire distinct, et les partages `gérant`
se lisent en une requête ; le handler tient UN emprunt (`db.reuse_connection`).

Quatre choses à tenir, sur la base réelle :
1. un nombre de requêtes qui ne dépend pas du nombre de tableaux ;
2. un seul emprunt au pool par appel ;
3. un chemin qui ne fait que lire (la condition de `reuse_connection`) ;
4. une décision identique à `can_govern` pour chaque cas : propriétaire personne,
   équipe, org, partage gérant (à la personne ou à son org), admin d'org, chef
   d'équipe, admin plateforme, non-membre, tableau inconnu.
"""
from __future__ import annotations

import re
import uuid

import psycopg
import pytest

from oto_mcp import ownership
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.capabilities.datastore import datastores as DS

RT = ownership.TYPE_RESSOURCE_DATASTORE

_ECRITURE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|UPSERT|CREATE|ALTER|DROP|TRUNCATE)\b|\bFOR\s+UPDATE\b",
    re.IGNORECASE)


@pytest.fixture
def requetes(monkeypatch):
    compte = {"n": 0}
    execute = psycopg.Cursor.execute

    def compter(self, *a, **k):
        compte["n"] += 1
        return execute(self, *a, **k)
    monkeypatch.setattr(psycopg.Cursor, "execute", compter)
    return compte


def _personne(prefixe: str, u: str) -> str:
    from oto_mcp import db
    sub = f"{prefixe}_{u}"
    db.upsert_user(sub, email=f"{sub}@example.test")
    return sub


def _org(nom: str) -> int:
    from oto_mcp import org_store
    return int(org_store.create_org(nom))


def _membre_avec_tableaux(n: int):
    """Un simple membre d'une org (ni admin, ni chef) dont la liste porte `n` tableaux
    de l'org et `n` tableaux d'une autre org partagés à la sienne en `gérant` : les deux
    branches de la règle (escalade refusée, puis partage gérant) sont jouées."""
    from oto_mcp import db, org_store
    u = uuid.uuid4().hex[:8]
    moi = _personne("moi", u)
    org, autre = _org(f"o_{u}"), _org(f"b_{u}")
    org_store.add_org_member(org, moi)
    org_store.set_active_org(moi, org)
    for i in range(n):
        db.create_datastore("org", str(org), f"t{i}_{u}")
        ns = db.create_datastore("org", str(autre), f"g{i}_{u}")
        db.grant_resource(RT, str(ns), "org", str(org), role="manager")
    return moi, org


def _lister(moi: str, org: int) -> list[dict]:
    return DS._list_datastores(ResolvedCtx(sub=moi, org_id=org),
                               DS.ListDatastoresInput())["datastores"]


def test_la_liste_ne_coute_pas_une_requete_par_tableau(live, requetes):
    petite = _membre_avec_tableaux(2)
    grande = _membre_avec_tableaux(10)
    mesures = []
    for monde in (petite, grande):
        _lister(*monde)                        # caches de processus chauffés
        requetes["n"] = 0
        out = _lister(*monde)
        mesures.append(requetes["n"])
        assert all(e["can_govern"] is (e["shared"] is True) for e in out), (
            "membre simple : il ne gouverne que les tableaux dont il est gérant")
    assert len(_lister(*grande)) == 20
    assert mesures[0] == mesures[1], (
        f"{mesures[0]} requêtes pour 4 tableaux, {mesures[1]} pour 20")


def test_un_seul_emprunt_au_pool_par_appel(live, monkeypatch):
    from oto_mcp.db import _conn as dbconn
    monde = _membre_avec_tableaux(3)
    _lister(*monde)
    pool = dbconn._get_pool()
    emprunts = []
    reel = pool.connection
    monkeypatch.setattr(pool, "connection",
                        lambda *a, **k: emprunts.append(1) or reel(*a, **k))
    assert len(_lister(*monde)) == 6
    assert len(emprunts) == 1, f"{len(emprunts)} emprunts au pool pour une liste"


def test_le_chemin_ne_fait_que_lire(live, monkeypatch):
    """La garde de `reuse_connection` : autocommit, donc une écriture serait visible des
    autres connexions AVANT la fin de l'appel."""
    monde = _membre_avec_tableaux(2)
    vues: list[str] = []
    for classe in (psycopg.Connection, psycopg.Cursor):
        reel = classe.execute

        def _releve(self, query, *a, _reel=reel, **k):
            vues.append(query if isinstance(query, str) else repr(query))
            return _reel(self, query, *a, **k)
        monkeypatch.setattr(classe, "execute", _releve)
    _lister(*monde)
    assert vues, "le relevé ne voit rien : il ne regarde pas le bon chemin"
    ecritures = [q for q in vues if _ECRITURE.search(q)]
    assert not ecritures, f"écriture sur le chemin de la liste : {ecritures[:3]}"


@pytest.fixture
def monde(live, monkeypatch):
    """Une org A (admin, membre, chef d'équipe), son équipe G, une org B, une personne
    du dehors, un gérant extérieur, un admin plateforme — et un tableau par forme de
    propriétaire."""
    from oto_mcp import db, group_store, org_store
    u = uuid.uuid4().hex[:8]
    p = {r: _personne(r, u) for r in
         ("admin", "membre", "chef", "dehors", "gerant", "ops")}
    monkeypatch.setenv("OTO_MCP_ADMIN_SUB", p["ops"])
    a, b = _org(f"a_{u}"), _org(f"b_{u}")
    org_store.add_org_member(a, p["admin"], "org_admin")
    org_store.add_org_member(a, p["membre"])
    org_store.add_org_member(a, p["chef"])
    org_store.add_org_member(b, p["dehors"])
    g = int(group_store.create_group(a, f"g_{u}"))
    group_store.add_group_member(g, p["chef"], "group_admin")
    group_store.add_group_member(g, p["membre"])
    t = {
        "perso": db.create_datastore("user", p["membre"], f"perso_{u}",
                                     context_org_id=a),
        "org": db.create_datastore("org", str(a), f"org_{u}"),
        "equipe": db.create_datastore("group", str(g), f"equipe_{u}"),
        "tiers": db.create_datastore("user", p["dehors"], f"tiers_{u}"),
        "gere": db.create_datastore("org", str(b), f"gere_{u}"),
        "gere_par_org": db.create_datastore("org", str(b), f"gere_org_{u}"),
        "lu": db.create_datastore("org", str(b), f"lu_{u}"),
    }
    db.grant_resource(RT, str(t["gere"]), "user", p["gerant"], role="manager")
    db.grant_resource(RT, str(t["gere_par_org"]), "org", str(a), role="manager")
    db.grant_resource(RT, str(t["lu"]), "org", str(a), role="editor")
    return p, a, t


def test_la_decision_est_celle_de_can_govern(monde):
    p, _, t = monde
    ids = [str(ns) for ns in t.values()] + ["2000000000"]       # + un tableau inconnu
    owners = {rid: ownership.owner_of(RT, rid) for rid in ids}
    assert owners["2000000000"] is None
    for sub in p.values():
        attendu = {rid: ownership.can_govern(sub, RT, rid) for rid in ids}
        assert ownership.can_govern_many(sub, RT, owners) == attendu, sub
    # Les cas jouent vraiment les deux issues de chaque branche.
    verdict = {(r, k): ownership.can_govern(p[r], RT, str(t[k])) for r in p for k in t}
    assert verdict["membre", "perso"] and not verdict["admin", "perso"]
    assert verdict["admin", "org"] and not verdict["membre", "org"]
    assert verdict["chef", "equipe"] and not verdict["membre", "equipe"]
    assert verdict["admin", "equipe"]                    # escalade org_admin
    assert verdict["gerant", "gere"] and not verdict["dehors", "gere"]
    assert verdict["membre", "gere_par_org"]             # gérant par son org
    assert not verdict["membre", "lu"]                   # éditeur ≠ gérant
    assert all(verdict["ops", k] for k in t)             # admin plateforme
    assert not any(verdict["dehors", k] for k in t if k != "tiers")


def test_la_liste_rend_le_verdict_de_can_govern(monde):
    from oto_mcp import org_store
    p, a, _ = monde
    for role in ("admin", "membre", "chef"):
        org_store.set_active_org(p[role], a)
        out = _lister(p[role], a)
        assert out, role
        for e in out:
            assert e["can_govern"] is ownership.can_govern(p[role], RT, str(e["id"])), (
                role, e["datastore"])
