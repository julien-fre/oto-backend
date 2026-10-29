"""`GET /api/me/orgs` (`org.list`) : les mêmes valeurs, un nombre de lectures constant.

La liste relisait, POUR CHAQUE org, tous ses membres (`list_org_members`) juste pour en
prendre la longueur, puis la bêta (`access.has_option`, deux lectures). Elle lit
désormais le compte des membres (`org_store.count_org_members`) et la bêta
(`access.orgs_with_option`) pour toutes les orgs d'un coup, sur UNE connexion
(`db.reuse_connection`).

Trois choses à tenir, sur base réelle :
1. les mêmes valeurs que le calcul org par org — compte de service exclu, don échu
   ignoré, org sans autre membre comprise ;
2. un nombre de requêtes qui ne dépend pas du nombre d'orgs, et un seul emprunt au pool ;
3. le chemin ne fait que lire (la condition de `reuse_connection`).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp.capabilities._types import ResolvedCtx

_ECRITURE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|UPSERT|CREATE|ALTER|DROP|TRUNCATE)\b|\bFOR\s+UPDATE\b",
    re.IGNORECASE)


def _compte(prefixe: str) -> str:
    from oto_mcp import db
    sub = f"usr_{prefixe}_" + uuid.uuid4().hex[:6]
    db.upsert_user(sub, email=f"{sub}@t.invalid", name=sub)
    return sub


@pytest.fixture(scope="module")
def jeu(live):
    from oto_mcp import db, org_store
    seul = _compte("seul")
    une = org_store.create_org("Espace seul", created_by=seul)
    org_store.add_org_member(une["id"] if isinstance(une, dict) else int(une), seul,
                             "org_admin")
    sub = _compte("plusieurs")
    autres = [_compte("autre") for _ in range(2)]
    orgs = []
    for i in range(3):
        oid = org_store.create_org(f"Espace {i}", created_by=sub)
        oid = oid["id"] if isinstance(oid, dict) else int(oid)
        org_store.add_org_member(oid, sub, "org_admin")
        orgs.append(oid)
    for a in autres:                                   # org 0 : trois membres
        org_store.add_org_member(orgs[0], a, "org_member")
    org_store.assurer_compte_de_service(orgs[1], by=sub)  # exclu du compte
    db.set_option_comp("org", str(orgs[1]), "beta", granted_by=sub)
    db.set_option_comp("org", str(orgs[2]), "beta", granted_by=sub,
                       expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    return {"seul": seul, "sub": sub, "orgs": orgs}


def _liste(sub: str) -> dict:
    from oto_mcp.capabilities.orgs import reads as R
    return R._list_my_orgs(ResolvedCtx(sub=sub), R.NoInput())


def test_memes_valeurs_que_le_calcul_org_par_org(jeu):
    from oto_mcp import access, org_store
    out = _liste(jeu["sub"])
    par_id = {o["id"]: o for o in out["orgs"]}
    assert set(jeu["orgs"]) <= set(par_id)
    for oid, o in par_id.items():
        assert o["member_count"] == len(org_store.list_org_members(oid)), oid
        assert o["beta"] is access.has_option(jeu["sub"], "beta", org=oid), oid
    a, b, c = jeu["orgs"]
    assert (par_id[a]["member_count"], par_id[b]["member_count"]) == (3, 1)
    assert (par_id[b]["beta"], par_id[c]["beta"]) == (True, False)


def _relever(monkeypatch) -> list[str]:
    import psycopg
    vues: list[str] = []
    for classe in (psycopg.Connection, psycopg.Cursor):
        reel = classe.execute

        def _releve(self, query, *a, _reel=reel, **k):
            vues.append(query if isinstance(query, str) else repr(query))
            return _reel(self, query, *a, **k)
        monkeypatch.setattr(classe, "execute", _releve)
    return vues


def test_lectures_constantes_et_un_seul_emprunt(jeu, monkeypatch):
    from oto_mcp.db import _conn as dbconn
    pool = dbconn._get_pool()
    emprunts = []
    reel = pool.connection
    monkeypatch.setattr(pool, "connection",
                        lambda *a, **k: emprunts.append(1) or reel(*a, **k))
    _liste(jeu["seul"])          # amorçage : les caches de processus hors du relevé
    vues = _relever(monkeypatch)
    n_orgs, n_lectures = [], []
    for sub in (jeu["seul"], jeu["sub"]):
        vues.clear()
        emprunts.clear()
        n_orgs.append(len(_liste(sub)["orgs"]))
        n_lectures.append(len(vues))
        assert len(emprunts) == 1, f"{len(emprunts)} emprunts au pool pour un appel"
    assert n_orgs[1] > n_orgs[0], "le jeu n'exerce pas plusieurs orgs"
    assert n_lectures[0] == n_lectures[1], (
        f"{n_lectures} requêtes pour {n_orgs} orgs : une lecture par org est revenue")


def test_le_chemin_ne_fait_que_lire(jeu, monkeypatch):
    vues = _relever(monkeypatch)
    _liste(jeu["sub"])
    assert vues, "le relevé ne voit rien : il ne regarde pas le bon chemin"
    ecritures = [q for q in vues if _ECRITURE.search(q)]
    assert not ecritures, f"écriture sur le chemin d'org.list : {ecritures[:3]}"
