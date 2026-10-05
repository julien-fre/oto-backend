"""`GET /api/me/connectors` sur UNE connexion du pool (oto-backend#1148).

Médiane 3,0 s, p95 15,7 s en production les 03-04/10/2026, pour une lecture que le
tableau de bord fait à l'ouverture de la bibliothèque de connecteurs. Le chemin de
`connectors.me` fait ~160 lectures unitaires (sélection, coffre, cascade de clé,
options payantes, apps OAuth) : chacune empruntait sa connexion au pool et payait son
`BEGIN`/`COMMIT`, et chaque emprunt attendait son tour sous charge. Mesuré sur une base
locale : 176 emprunts pour un appel, 1 après.

Trois choses à tenir :
1. UN emprunt par appel, quelle que soit la forme (compacte, verbeuse, ciblée) ;
2. le chemin ne fait QUE lire — c'est la condition de `db.reuse_connection` (autocommit,
   aucune isolation commune) : une écriture qui y entrerait doit rougir ici ;
3. l'option payante se juge une fois par (option, porteur du credential), pas une fois
   par canal — et rend la même réponse que l'appel ligne par ligne.
"""
from __future__ import annotations

import re

import pytest

from oto_mcp import access, providers
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.capabilities.connectors import selection as S

SUB = "membre-1148"

_ECRITURE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|UPSERT|CREATE|ALTER|DROP|TRUNCATE)\b|\bFOR\s+UPDATE\b",
    re.IGNORECASE)


@pytest.fixture(scope="module")
def org_id(live):
    from oto_mcp import db, org_store
    db.upsert_user(SUB, email="membre-1148@example.invalid")
    org = org_store.create_org("org-1148", created_by=SUB)
    oid = org["id"] if isinstance(org, dict) else int(org)
    org_store.add_org_member(oid, SUB, "org_admin")
    return oid


_FORMES = [S.MyConnectorsInput(verbose=True), S.MyConnectorsInput(),
           S.MyConnectorsInput(name="linkedin"), S.MyConnectorsInput(state="active")]


@pytest.mark.parametrize("inp", _FORMES, ids=["verbeux", "compact", "cible", "filtre"])
def test_un_seul_emprunt_au_pool_par_appel(org_id, inp, monkeypatch):
    from oto_mcp.db import _conn as dbconn
    pool = dbconn._get_pool()
    emprunts = []
    reel = pool.connection
    monkeypatch.setattr(pool, "connection",
                        lambda *a, **k: emprunts.append(1) or reel(*a, **k))
    out = S._me(ResolvedCtx(sub=SUB, org_id=org_id), inp)
    assert out["connectors"] or inp.state, "le catalogue est servi"
    assert len(emprunts) == 1, f"{len(emprunts)} emprunts au pool pour un appel"


def test_le_chemin_ne_fait_que_lire(org_id, monkeypatch):
    """La garde de `reuse_connection` : autocommit, donc une écriture serait visible des
    autres connexions AVANT la fin de l'appel. On relève chaque requête, y compris
    celles dont l'échec serait rattrapé par un repli fail-open."""
    import psycopg
    vues: list[str] = []
    for classe in (psycopg.Connection, psycopg.Cursor):
        reel = classe.execute

        def _releve(self, query, *a, _reel=reel, **k):
            vues.append(query if isinstance(query, str) else repr(query))
            return _reel(self, query, *a, **k)
        monkeypatch.setattr(classe, "execute", _releve)
    for inp in _FORMES:
        S._me(ResolvedCtx(sub=SUB, org_id=org_id), inp)
    assert vues, "le relevé ne voit rien : il ne regarde pas le bon chemin"
    ecritures = [q for q in vues if _ECRITURE.search(q)]
    assert not ecritures, f"écriture sur le chemin de connectors.me : {ecritures[:3]}"


def test_l_option_se_juge_une_fois_par_porteur_et_rend_la_meme_reponse(org_id, monkeypatch):
    ctx = ResolvedCtx(sub=SUB, org_id=org_id)
    attendu = {c["name"]: access.option_open(SUB, c["name"], org=org_id)
               for c in S._visible_catalog(ctx)}
    appels = []
    reel = access.option_open
    monkeypatch.setattr(access, "option_open",
                        lambda sub, nom, **k: appels.append(nom) or reel(sub, nom, **k))
    out = S._me(ctx, S.MyConnectorsInput())
    assert {r["name"]: r["option_ok"] for r in out["connectors"]} == attendu
    cles = [(access.paid_option_for(n), providers.credential_provider(n)) for n in appels]
    assert len(cles) == len(set(cles)), f"une même (option, porteur) jugée deux fois : {appels}"
    canaux = [n for n in attendu if providers.credential_provider(n) == "unipile"]
    assert len(canaux) > 1 and sum(n in canaux for n in appels) == 1, (
        "le compte hébergé et ses canaux partagent option et clé : un seul jugement")
