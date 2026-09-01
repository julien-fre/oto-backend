"""#770 — le journal des accès doit pouvoir ATTESTER de sa complétude.

L'article contractuel promet au Client, sur demande, le journal des accès. L'export
existe et il est en self-service (`GET /api/orgs/{id}/audit-log/export`), mais il ne
rendait que `count = len(calls)` **après troncature** : un fichier de 1000 lignes ne
disait pas si 1000 ou 50 000 appels avaient eu lieu. Une pièce qui ne dit pas si elle
est complète n'atteste de rien, et une absence dans une vue plafonnée se lit comme un
zéro.

Ce qui est gardé vert ici :

1. la réponse porte `total` (la population de la FENÊTRE), `count` (les lignes de
   CETTE réponse), `truncated` et `next_cursor` ;
2. **`total` compte le MÊME jeu que la page** — même org, même `kind`, mêmes bornes.
   Un total calculé sur un autre jeu est pire que pas de total : il a l'air d'attester.
   La garde pose donc des lignes que la page exclut (autre org, `kind='rest'`, hors
   fenêtre) et vérifie qu'elles ne sont ni dans l'une ni dans l'autre ;
3. **la fenêtre est GELÉE et reportée par le curseur** : un appel qui arrive pendant
   qu'on pagine ne change ni le total ni les pages — sinon l'export servirait deux
   vérités successives, et sa concaténation ne vaudrait plus son total ;
4. le curseur parcourt la fenêtre ENTIÈRE, sans trou ni doublon, y compris sur des
   lignes de la même SECONDE (le `created_at` servi est tronqué à la seconde par le
   row factory : un curseur bâti dessus sauterait des lignes) ;
5. repasser `since`/`until` avec un `cursor` est REFUSÉ, pas ignoré — les honorer
   rendrait un total qui ne décrit pas la fenêtre de la page ;
6. un curseur rejoué sur une AUTRE org est refusé : il rendrait une page prise à la
   position d'un autre export, sans erreur et sous un total exact — donc une pièce
   qui a l'air entière sans l'être.
"""
from __future__ import annotations

import os
import uuid

import pytest

from oto_mcp.capabilities import audit_log as al
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="admin", org_id=7)


# ── La forme servie ───────────────────────────────────────────────────────────

def _fake(monkeypatch, *, calls, total, suivant=None, until="2026-09-01T10:00:00.0Z"):
    vu: dict = {}

    def faux(org_id, **kw):
        vu.update(org_id=org_id, **kw)
        return {"until_effectif": until, "total": total,
                "calls": [dict(c) for c in calls], "next": suivant}

    monkeypatch.setattr(al.db, "export_tool_calls_for_org", faux)
    return vu


def test_la_reponse_dit_son_total_et_sa_troncature(monkeypatch):
    """Le défaut d'origine : `count` valait la PAGE, et rien ne disait la population."""
    _fake(monkeypatch, calls=[{"tool": "fr_get"}], total=4485,
          suivant=("2026-08-30T09:00:00.000000Z", 91))
    out = al._export(CTX, al.AuditExportInput(org_id=7, limit=1))

    assert out["count"] == 1, "count reste la taille de la page"
    assert out["total"] == 4485, "le total est la population de la fenêtre"
    assert out["truncated"] is True
    assert out["next_cursor"], "il reste des lignes : un curseur pour aller les chercher"


def test_une_reponse_complete_ne_se_dit_pas_tronquee(monkeypatch):
    _fake(monkeypatch, calls=[{"tool": "fr_get"}, {"tool": "oto_doc"}], total=2)
    out = al._export(CTX, al.AuditExportInput(org_id=7))
    assert (out["count"], out["total"]) == (2, 2)
    assert out["truncated"] is False and out["next_cursor"] is None


def test_une_fenetre_vide_dit_zero_sur_les_deux(monkeypatch):
    """Un zéro n'est lisible que s'il est le total ET le compte : sinon « vide » peut
    vouloir dire « plafonné »."""
    _fake(monkeypatch, calls=[], total=0)
    out = al._export(CTX, al.AuditExportInput(org_id=7))
    assert (out["count"], out["total"], out["truncated"]) == (0, 0, False)


def test_la_borne_haute_reellement_appliquee_est_rendue(monkeypatch):
    """`until: null` ne dit pas jusqu'où l'export porte. `until_effectif` le dit —
    c'est ce qui fait de la pièce une PÉRIODE FERMÉE."""
    _fake(monkeypatch, calls=[], total=0, until="2026-09-01T14:00:00.000000Z")
    out = al._export(CTX, al.AuditExportInput(org_id=7))
    assert out["until"] is None
    assert out["until_effectif"] == "2026-09-01T14:00:00.000000Z"


def test_le_curseur_reporte_la_fenetre_telle_quelle(monkeypatch):
    """Le curseur est opaque : il porte la fenêtre GELÉE, pas seulement la position.
    Sans ça, la page 2 se lirait dans une fenêtre plus large que la page 1."""
    _fake(monkeypatch, calls=[{"tool": "fr_get"}], total=9,
          suivant=("2026-08-30T09:00:00.000000Z", 91),
          until="2026-08-31T23:59:59.000000Z")
    page1 = al._export(CTX, al.AuditExportInput(org_id=7, since="2026-08-01", limit=1))

    vu = _fake(monkeypatch, calls=[{"tool": "oto_doc"}], total=9)
    al._export(CTX, al.AuditExportInput(org_id=7, cursor=page1["next_cursor"]))
    assert vu["since"] == "2026-08-01"
    assert vu["until"] == "2026-08-31T23:59:59.000000Z", "la borne gelée est reportée"
    assert vu["before"] == ("2026-08-30T09:00:00.000000Z", 91)


def test_la_fenetre_repassee_avec_un_curseur_est_refusee(monkeypatch):
    """Refusé, jamais ignoré : honorer les deux rendrait un total qui ne décrit pas
    la fenêtre de la page — le défaut même que ce lot ferme."""
    _fake(monkeypatch, calls=[], total=0, suivant=("2026-08-30T09:00:00.000000Z", 1))
    page1 = al._export(CTX, al.AuditExportInput(org_id=7))
    for bornes in ({"since": "2026-08-01"}, {"until": "2026-08-31"}):
        with pytest.raises(AuthzDenied) as e:
            al._export(CTX, al.AuditExportInput(org_id=7, cursor=page1["next_cursor"],
                                                **bornes))
        assert e.value.status == 400 and e.value.code == "window_with_cursor"


def test_un_curseur_illisible_est_un_400_pas_une_panne(monkeypatch):
    _fake(monkeypatch, calls=[], total=0)
    for abime in ("pas-du-base64!!", "", "eyJ4IjoxfQ"):
        with pytest.raises(AuthzDenied) as e:
            al._export(CTX, al.AuditExportInput(org_id=7, cursor=abime))
        assert e.value.status == 400 and e.value.code == "invalid_cursor"


def test_le_curseur_ne_porte_aucune_donnee_du_journal():
    """Il voyage dans une URL et dans les journaux d'accès d'un proxy : il ne doit
    porter que la position et la fenêtre, jamais un sub, un outil ou une erreur."""
    import base64
    import json

    c = al._encode_cursor(7, "2026-08-01", "2026-08-31T00:00:00.000000Z",
                          ("2026-08-30T09:00:00.000000Z", 91))
    clair = base64.urlsafe_b64decode(c + "=" * (-len(c) % 4)).decode()
    assert set(json.loads(clair)) == {"o", "s", "u", "t", "i"}


def test_un_curseur_d_une_AUTRE_org_est_refuse(monkeypatch):
    """Rejoué sur une org dont l'appelant est aussi administrateur, il rendrait une
    page prise à la position d'un autre export : des lignes sautées, aucune erreur,
    et un `total` qui décrit pourtant bien la nouvelle fenêtre — une pièce qui a
    l'air entière sans l'être. Même garde d'identité que `node_rows`."""
    _fake(monkeypatch, calls=[], total=9, suivant=("2026-08-30T09:00:00.000000Z", 91))
    ailleurs = al._export(CTX, al.AuditExportInput(org_id=7))["next_cursor"]

    with pytest.raises(AuthzDenied) as e:
        al._export(ResolvedCtx(sub="admin", org_id=8),
                   al.AuditExportInput(org_id=8, cursor=ailleurs))
    assert e.value.status == 400 and e.value.code == "invalid_cursor"
    # …et il reste bon sur la sienne : la garde vise l'org, pas le curseur.
    assert al._export(CTX, al.AuditExportInput(org_id=7, cursor=ailleurs))["total"] == 9


# ── Le store : total et page décrivent le MÊME jeu, contre un vrai PostgreSQL ──

@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_audit770_" + uuid.uuid4().hex[:8]
    racine = psycopg.connect(pg_dsn, autocommit=True)
    racine.execute(f'CREATE DATABASE "{nom}"')
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
        racine.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        racine.close()


def _poser(sub, org_id, *, quand, kind="mcp", tool="fr_get"):
    """Une ligne de journal à un instant CHOISI. `insert_tool_call` date à `now()` :
    on repositionne ensuite, c'est la seule façon d'éprouver une fenêtre."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    db.insert_tool_call({"sub": sub, "kind": kind, "tool": tool, "ok": True,
                         "org_id": org_id, "duration_ms": 3})
    with _connect() as conn:
        conn.execute(
            "UPDATE tool_calls SET created_at = %s::timestamptz WHERE id = ("
            "SELECT max(id) FROM tool_calls)", (quand,))


@pytest.fixture
def journal(live):
    """Neuf appels de l'org dans la fenêtre — dont trois à la MÊME seconde — plus
    trois lignes que la page exclut : une autre org, un `kind='rest'`, un appel
    postérieur à la borne haute."""
    from oto_mcp import db, org_store

    sub = "sub-770-" + uuid.uuid4().hex[:6]
    org = org_store.create_org("Audit 770", created_by=sub)
    autre = org_store.create_org("Ailleurs 770", created_by=sub)

    for i in range(6):
        _poser(sub, org, quand=f"2026-08-20T10:{i:02d}:00+00:00")
    # trois lignes de la MÊME seconde : le piège du curseur bâti sur un horodatage
    # tronqué à la seconde par le row factory.
    for micro in (100000, 200000, 300000):
        _poser(sub, org, quand=f"2026-08-20T11:00:00.{micro:06d}+00:00")

    _poser(sub, autre, quand="2026-08-20T10:30:00+00:00")           # autre org
    _poser(sub, org, quand="2026-08-20T10:31:00+00:00", kind="rest")  # pas un outil
    _poser(sub, org, quand="2026-08-25T10:00:00+00:00")             # hors fenêtre haute
    _poser(sub, org, quand="2026-08-01T10:00:00+00:00")             # hors fenêtre basse
    return {"sub": sub, "org": org, "autre": autre,
            "since": "2026-08-10T00:00:00+00:00",
            "until": "2026-08-21T00:00:00+00:00"}


def test_le_total_compte_la_fenetre_pas_la_page(journal):
    from oto_mcp import db

    page = db.export_tool_calls_for_org(journal["org"], since=journal["since"],
                                        until=journal["until"], limit=4)
    assert len(page["calls"]) == 4, "la page est plafonnée"
    assert page["total"] == 9, "le total, lui, décrit la fenêtre entière"
    assert page["next"] is not None


def test_le_total_compte_le_MEME_jeu_que_la_page(journal):
    """Le piège à ne pas reproduire : un total bâti sur d'autres clauses que la page
    a l'air d'attester. On lit tout, et le total doit valoir ce qu'on a lu."""
    from oto_mcp import db

    page = db.export_tool_calls_for_org(journal["org"], since=journal["since"],
                                        until=journal["until"], limit=1000)
    assert page["total"] == len(page["calls"]) == 9
    assert page["next"] is None


def test_le_curseur_parcourt_toute_la_fenetre_sans_trou_ni_doublon(journal):
    from oto_mcp import db

    vus, before, total, gardes = [], None, None, 0
    while gardes < 20:
        gardes += 1
        p = db.export_tool_calls_for_org(journal["org"], since=journal["since"],
                                         until=journal["until"], limit=2, before=before)
        total = p["total"] if total is None else total
        assert p["total"] == total, "le total ne bouge pas d'une page à l'autre"
        vus += [c["id"] for c in p["calls"]]
        before = p["next"]
        if before is None:
            break
    assert len(vus) == len(set(vus)) == total == 9, vus


def test_la_fenetre_est_gelee_quand_la_borne_haute_est_omise(journal):
    """Sans borne haute, l'export GÈLE l'instant et le reporte par le curseur : les
    appels qui arrivent pendant qu'on pagine ne changent ni le total ni les pages.

    La prémisse que ce gel neutralise est affirmée ici : les deux appels ajoutés
    existent, et un export REGELÉ les compte bien. C'est ce qui distingue « la
    fenêtre tient » de « rien n'a été ajouté ».
    """
    from oto_mcp import db

    p1 = db.export_tool_calls_for_org(journal["org"], since=journal["since"], limit=2)
    total_annonce, gel = p1["total"], p1["until_effectif"]
    assert gel.endswith("Z") and total_annonce == 10

    # Deux appels comme la production en produit : `insert_tool_call` les date à
    # `now()`, donc APRÈS le gel. C'est cette datation-là — jamais réécrite — qui
    # fait de la fenêtre gelée un ensemble clos.
    for _ in range(2):
        db.insert_tool_call({"sub": journal["sub"], "kind": "mcp", "tool": "fr_get",
                             "ok": True, "org_id": journal["org"]})

    vus, before, page = [], p1["next"], p1
    while before is not None and len(vus) < 40:
        vus += [c["id"] for c in page["calls"]]
        page = db.export_tool_calls_for_org(journal["org"], since=journal["since"],
                                            until=gel, limit=2, before=before)
        assert page["total"] == total_annonce, "la fenêtre gelée ne s'élargit pas"
        before = page["next"]
    vus += [c["id"] for c in page["calls"]]
    assert len(vus) == len(set(vus)) == total_annonce, (
        "la concaténation des pages vaut EXACTEMENT le total annoncé")

    regel = db.export_tool_calls_for_org(journal["org"], since=journal["since"],
                                         limit=1)
    assert regel["total"] == total_annonce + 2, (
        "les deux appels existent : c'est bien le gel qui les tenait dehors")


def test_la_page_et_le_total_se_lisent_dans_UNE_transaction_au_snapshot_fige(journal):
    """La cohérence n'est pas une intention : elle tient à ce que les deux lectures
    partagent une transaction en REPEATABLE READ. Retirer le `SET` rend ce test rouge.
    """
    from oto_mcp import db
    from oto_mcp.db import usage as u

    vu: list[str] = []
    vrai = u._connect

    class Espion:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *a, **k):
            vu.append(" ".join(str(sql).split())[:60])
            return self._c.execute(sql, *a, **k)

        def __getattr__(self, n):
            return getattr(self._c, n)

    import contextlib

    @contextlib.contextmanager
    def espionne():
        with vrai() as conn:
            yield Espion(conn)

    u._connect = espionne
    try:
        db.export_tool_calls_for_org(journal["org"], since=journal["since"], limit=2)
    finally:
        u._connect = vrai

    assert vu[0].startswith("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"), vu
    assert sum("count(*)" in s for s in vu) == 1, vu
