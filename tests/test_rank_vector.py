"""Vecteurs de classement matérialisés (#318) — le gain sans changer un seul résultat.

Le classement recalculait `to_tsvector` par candidat : 674 ms sur un mot fréquent,
0,2 ms pour le même filtre sans classement. Une colonne matérialisée le ramène à 10 ms.

Ce lot n'a de valeur que s'il ne change RIEN d'observable. Deux garanties se testent
ici, et la première est le critère de recette :

1. **le classement est identique** aux trois états du remplissage (rien, moitié, tout)
   — sur mot rare, fragment et mot fréquent ;
2. **aucun silence n'est possible** — une ligne non encore remplie se classe comme
   avant, par le repli du `COALESCE`, au lieu de disparaître ou de tomber en fin.
"""
from __future__ import annotations

import uuid

import pytest

from oto_mcp.db import search as S


# ── la source unique (sans base) ─────────────────────────────────────────────

def test_the_ranking_expression_falls_back_never_diverges():
    """Le repli n'est pas une béquille de migration : il reste après le remplissage,
    et c'est lui qui rend l'écriture qui rate son vecteur inoffensive."""
    e = S.rank_expr("docs")
    assert e.startswith("COALESCE(search_vec,")
    assert "to_tsvector" in e                 # le repli calcule, comme avant
    assert S.DOCS_TEXT.split("||")[0].strip() in e


def test_an_alias_is_carried_into_both_halves():
    """Une source qui JOINT (le contenu des fichiers) doit viser sa propre table des
    deux côtés du repli — sinon la colonne d'une table et le texte d'une autre."""
    e = S.rank_expr("project_file_texts", "t")
    assert "COALESCE(t.search_vec," in e
    assert "t.extracted_text" in e
    assert "coalesce(extracted_text" not in e, "l'alias doit être partout"


def test_an_unknown_source_keeps_the_old_path():
    """Une source non matérialisée rend une chaîne vide → `_prose_query` garde
    l'expression. Ajouter une source ne peut donc pas casser les autres."""
    assert S.rank_expr("table_inexistante") == ""


def test_every_ranked_source_has_its_text_expression():
    """La table de correspondance EST la source unique (écriture, rattrapage, repli).
    Une entrée sans expression produirait un classement sur du vide, en silence."""
    for table, expr in S.RANKED_SOURCES.items():
        assert isinstance(expr, str) and expr.strip(), table


def test_the_backfill_is_bounded_by_slice():
    """Borné par tranche = jamais un verrou de table. C'est toute la raison de cette
    forme : la variante auto-remplissante tenait `datastore_rows` 7,55 s sous verrou
    exclusif, en pleine production."""
    sql = S.rank_backfill_sql("docs", 500)
    assert "LIMIT 500" in sql
    assert "IS NULL" in sql, "le prédicat de file : ce qui n'a pas encore de vecteur"
    assert "UPDATE docs" in sql


def test_the_column_ddl_never_rewrites_the_table():
    """`ADD COLUMN <tsvector>` SANS défaut ni contrainte : instantané (PG 11+). Un
    défaut ou un `GENERATED` réintroduirait la réécriture que ce lot évite."""
    for ddl in S.rank_column_ddl():
        assert "ADD COLUMN IF NOT EXISTS search_vec tsvector" in ddl
        assert "DEFAULT" not in ddl.upper()
        assert "GENERATED" not in ddl.upper()


def test_every_materialised_source_is_actually_wired_to_the_column():
    """⚠️ **La convention source-unique, transposée.** Elle gardait « l'index et la
    requête viennent de la même expression » ; elle doit maintenant garder « le
    CLASSEMENT vient de la colonne ».

    Une source déclarée dans `RANKED_SOURCES` mais dont la requête oublie
    `rank_vec=` ne casse RIEN de visible : les résultats restent justes, la colonne se
    remplit, et le classement continue de recalculer — le gain disparaît en silence.
    C'est exactement le mode d'échec que l'ancienne version de ce garde-fou décrivait,
    déplacé d'un cran.

    Vérifié sur le SOURCE, parce que c'est là que l'oubli se produit."""
    from pathlib import Path
    src = Path(S.__file__).read_text()

    for table in S.RANKED_SOURCES:
        if table == "guides":
            continue          # plus lue d'ici (#282) : l'index survit pour la prod
        assert f'rank_expr("{table}"' in src, (
            f"la source {table!r} est matérialisée mais son classement ne vise pas "
            "la colonne — le gain serait perdu sans qu'aucun test ne rougisse")


def test_the_rank_is_computed_in_one_place_only():
    """Un seul `ts_rank_cd` dans le module : dès qu'il y en a deux, l'un des deux
    finira par garder l'ancienne expression, et le drift sera invisible."""
    from pathlib import Path
    src = Path(S.__file__).read_text()
    # `ts_rank_cd(` avec sa parenthèse : l'APPEL, pas les mentions en prose des
    # docstrings — un test qui compte les commentaires mesure la documentation.
    code = [l for l in src.splitlines()
            if "ts_rank_cd(" in l and not l.strip().startswith("#")]

    assert len(code) == 1, f"ts_rank_cd apparaît {len(code)} fois : {code}"
    assert "rank_on" in code[0], "le rang doit lire l'expression de classement"


def test_the_filter_still_uses_the_indexed_expression():
    """⚠️ Le pendant : le lot ne touche QUE le classement. Si le `WHERE` se mettait à
    lire la colonne, le planner cesserait d'utiliser les GIN d'expression — et la
    recherche passerait au balayage, sans rien signaler."""
    from pathlib import Path
    src = Path(S.__file__).read_text()
    i = src.index("WHERE ({vec} @@ qq.tsq")
    fenetre = src[i:i + 200]

    assert "{vec}" in fenetre and "rank_on" not in fenetre


# ── le classement ne bouge pas (vrai PostgreSQL) ─────────────────────────────

@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_rank_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture
def corpus(live):
    """Un corpus VARIÉ — pas N copies du même : un vocabulaire uniforme rendrait tous
    les rangs égaux, et le test ne prouverait plus rien sur l'ORDRE."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    p = db.create_project("org", "1", "Recherche " + uuid.uuid4().hex[:6])
    pid = int(p["id"] if isinstance(p, dict) else p)
    textes = [
        ("Réunion de cadrage", "budget arbitrage calendrier " * 8),
        ("Compte rendu client", "budget client Sylvestre relance " * 4),
        ("Note de synthèse", "budget " * 30),                 # rang élevé
        ("Point technique", "déploiement incident correctif"),  # pas de « budget »
        ("Revue Sylvestre", "Boulangerie Sylvestre livraison budget"),
    ]
    with _connect() as conn:
        for titre, corps in textes:
            conn.execute(
                "INSERT INTO docs (project_id, title, body_md) VALUES (%s, %s, %s)",
                (pid, titre, corps))
    return pid


def _ordre(pid: int, q: str) -> list:
    from oto_mcp import db
    return [r["id"] for r in db.search_docs_fts(q, [pid], limit=20)]


def _remplir(fraction: float) -> None:
    """Remplit une part des vecteurs, comme le ferait le worker."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        total = conn.execute("SELECT count(*) AS n FROM docs").fetchone()["n"]
        conn.execute(S.rank_backfill_sql("docs", max(1, int(total * fraction))))


@pytest.mark.parametrize("q", ["budget", "Sylvestre", "ylvestr", "cadrage"])
def test_ranking_is_identical_at_every_backfill_state(corpus, q):
    """⚠️ **Le critère de recette du lot.** Le classement doit être le MÊME que la
    colonne soit vide, à moitié remplie ou pleine — sinon « aucun arbitrage produit »
    serait faux, et le gain se paierait en résultats déplacés sans que personne ne
    l'ait décidé.

    Les quatre requêtes couvrent les régimes qui se comportent différemment : mot
    fréquent (le cas coûteux), mot rare, FRAGMENT interne (servi par le substring,
    rang 0) et mot absent du corpus fréquent."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:                       # état initial : rien de rempli
        conn.execute("UPDATE docs SET search_vec = NULL")

    vide = _ordre(corpus, q)
    _remplir(0.5)
    moitie = _ordre(corpus, q)
    _remplir(1.0)
    plein = _ordre(corpus, q)

    assert vide == moitie == plein, f"le classement a bougé sur « {q} »"


def test_a_row_without_its_vector_is_never_lost(corpus):
    """Le silence est ce qu'on ne pouvait pas se permettre : une ligne non remplie
    doit rester trouvable ET correctement classée, pas reléguée en fin ni disparue.
    C'est ce que garantit le repli, et ce qui a fait préférer cette forme à une
    bascule (qui aurait eu un instant où les deux moitiés ne s'accordaient pas)."""
    from oto_mcp.db._conn import _connect
    _remplir(1.0)
    attendu = _ordre(corpus, "budget")

    with _connect() as conn:      # on vide le vecteur du PREMIER résultat
        conn.execute("UPDATE docs SET search_vec = NULL WHERE id = %s", (attendu[0],))

    assert _ordre(corpus, "budget") == attendu


# ── le maintien à l'écriture (sans le rattrapage) ────────────────────────────

def _vecteur(table: str, where: str, params: tuple):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        r = conn.execute(
            f"SELECT {S.RANK_VECTOR_COLUMN} AS v FROM {table} WHERE {where}", params
        ).fetchone()
    return (r or {}).get("v")


def test_writing_a_page_stamps_its_vector_without_the_backfill(live):
    """⚠️ **Le critère du barreau, et il se prouve en COUPANT le rattrapage** : une
    écriture rend le vecteur frais toute seule, dans sa propre transaction. Sinon le
    test passerait au vert grâce à la boucle de fond, et ne prouverait rien du
    maintien."""
    from oto_mcp import db

    p = db.create_project("org", "1", "Fraîcheur " + uuid.uuid4().hex[:6])
    pid = int(p["id"] if isinstance(p, dict) else p)
    doc_id = db.create_doc(pid, "Note de cadrage", body_md="budget arbitrage")

    # Aucun tour de rattrapage n'a été joué : le vecteur doit déjà être là.
    assert _vecteur("docs", "id = %s", (doc_id,)) is not None

    avant = _vecteur("docs", "id = %s", (doc_id,))
    db.update_doc(doc_id, body_md="déploiement incident correctif")
    apres = _vecteur("docs", "id = %s", (doc_id,))

    assert apres is not None and apres != avant, (
        "le corps a changé : le vecteur doit suivre, sans attendre le rattrapage")
    assert "budget" not in str(apres), "l'ancien texte ne doit plus classer la page"


def test_writing_a_row_stamps_its_vector_without_the_backfill(live):
    """Le volume est ici — c'est la source où un vecteur daté se verrait le plus."""
    from oto_mcp import db

    ns = db.create_datastore_namespace("org", "1", "t-" + uuid.uuid4().hex[:6])
    db.datastore_insert_row(ns, "r1", {"societe": "Boulangerie Sylvestre"})
    assert _vecteur("datastore_rows", "ns_id = %s AND row_id = %s", (ns, "r1")) is not None

    avant = _vecteur("datastore_rows", "ns_id = %s AND row_id = %s", (ns, "r1"))
    db.datastore_upsert_row(ns, "r1", {"societe": "Charcuterie Martin"})
    apres = _vecteur("datastore_rows", "ns_id = %s AND row_id = %s", (ns, "r1"))

    assert apres != avant and "sylvestr" not in str(apres).lower()


def test_an_extracted_file_stamps_its_vector(live):
    """La source du lot précédent : le texte extrait arrive par le worker, pas par une
    surface — le maintien doit y être aussi, sans quoi tout fichier indexé attendrait
    le rattrapage."""
    from oto_mcp import db

    p = db.create_project("org", "1", "Fichiers " + uuid.uuid4().hex[:6])
    pid = int(p["id"] if isinstance(p, dict) else p)
    f = db.add_project_file(pid, "s3/x", "doc.pdf", mime="application/pdf")
    db.save_extracted_text(int(f["id"]), status="ok", text="visite chez Sylvestre")

    assert _vecteur("project_file_texts", "file_id = %s", (int(f["id"]),)) is not None


def test_a_write_that_fails_to_stamp_never_breaks_the_write(live, monkeypatch):
    """Best-effort : le vecteur est un accélérateur, jamais une condition de
    l'écriture métier. S'il échoue, la ligne s'écrit quand même — le rattrapage
    repassera, et le repli du COALESCE fait qu'entre-temps rien ne ment."""
    from oto_mcp import db
    from oto_mcp.db import search as mod

    p = db.create_project("org", "1", "Robuste " + uuid.uuid4().hex[:6])
    pid = int(p["id"] if isinstance(p, dict) else p)
    monkeypatch.setattr(mod, "RANKED_SOURCES", {})     # plus aucune source connue

    doc_id = db.create_doc(pid, "Toujours écrite", body_md="corps")

    assert db.get_doc_by_id(doc_id) is not None
    assert _vecteur("docs", "id = %s", (doc_id,)) is None


def test_the_backfill_round_is_idempotent_and_converges(corpus):
    """Le tour ne fait rien quand tout est rempli — c'est ce qui lui permet de rester
    en place comme réconciliation, sans coûter."""
    from oto_mcp import rank_backfill_worker as w

    while w._backfill_round():
        pass
    assert w._backfill_round() == {}, "rien à faire ⇒ tour vide"
    assert "docs" not in S.rank_pending_counts()
