"""Purger une colonne d'un tableau (oto-backend#296).

Après un renommage de champs, l'ancienne colonne reste dans les données : le
schéma la sort de la vue, mais la clé est toujours dans chaque ligne, elle se rend
encore à la lecture, et son nom décrit souvent le contenu mieux que le nouveau
(`actualite_business` parle plus que `analyse2`). Trois agents successifs ont écrit
dedans en croyant viser juste. Sans geste de purge, le seul recours est de nommer
les colonnes mortes dans la procédure — de la dette portée à chaque contexte.

Deux étages : les GARDES du geste (destructif → stub suffit) et le SQL, exercé
contre un vrai PostgreSQL — `data - key` est ce qui distingue effacer de mettre à
`null`, et aucun stub ne le prouve.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import core as dsm
from oto_mcp.datastore.core import DatastorePg


STRICT = {"strict": True, "fields": [{"key": "siren", "type": "text"},
                                     {"key": "analyse1", "type": "text"}]}


@pytest.fixture()
def store(monkeypatch):
    st = DatastorePg("u", acting_org=35)
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: 7)
    dropped = []
    monkeypatch.setattr(dsm.db, "get_datastore_namespace_by_id",
                        lambda ns_id: {"id": ns_id, "schema": STRICT})
    monkeypatch.setattr(dsm.db, "datastore_drop_column",
                        lambda ns_id, key: (dropped.append((ns_id, key)) or 12))
    return st, dropped


# ── gardes du geste ──────────────────────────────────────────────────────────

def test_without_confirm_nothing_is_touched(store):
    st, dropped = store
    with pytest.raises(ValueError, match="non confirmée"):
        st.drop_column("v", "actualite_sociale", confirm=False)
    assert dropped == []


def test_with_confirm_it_purges_and_counts(store):
    st, dropped = store
    out = st.drop_column("v", "actualite_sociale", confirm=True)
    assert dropped == [(7, "actualite_sociale")]
    assert out == {"namespace": "v", "key": "actualite_sociale", "rows": 12}


def test_a_field_still_declared_is_refused(store):
    """Un `confirm` ne protège pas d'une faute de nom : la colonne vivante est
    hors d'atteinte tant que le schéma la déclare."""
    st, dropped = store
    with pytest.raises(ValueError, match="encore DÉCLARÉE"):
        st.drop_column("v", "analyse1", confirm=True)
    assert dropped == []


def test_platform_columns_are_not_data(store):
    st, dropped = store
    with pytest.raises(ValueError, match="gérée par la plateforme"):
        st.drop_column("v", "_id", confirm=True)
    assert dropped == []


def test_empty_key_is_refused(store):
    st, _ = store
    with pytest.raises(ValueError, match="key requise"):
        st.drop_column("v", "   ", confirm=True)


# ── ce que la réponse CONSTATE quand rien n'a bougé (#680) ───────────────────
#
# `rows: 0` valait pour DEUX vérités opposées — « la colonne existait, aucune ligne
# ne la portait » et « ce nom n'est pas une colonne, je n'ai rien fait ». Mesuré le
# 31/08/2026 sur une purge de ~190 noms qui s'apprêtait à partir sur un fichier de
# production : l'opérateur aurait coché comme retirés des noms jamais touchés. Un
# geste destructif confirmé qui ne fait rien doit le DIRE — d'où un refus, jamais un
# zéro. Et la phrase doit nommer sa destination sans jamais l'inventer.

@pytest.fixture()
def store_sans_effet(monkeypatch):
    """Le même store, mais dont la purge SQL ne touche aucune ligne.

    `presentes` = les colonnes réellement présentes dans les données, que le test
    peuple ; c'est ce que `datastore_has_column` interroge pour savoir si une base
    de couche existe."""
    st = DatastorePg("u", acting_org=35)
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: 7)
    monkeypatch.setattr(dsm.db, "get_datastore_namespace_by_id",
                        lambda ns_id: {"id": ns_id, "schema": STRICT})
    monkeypatch.setattr(dsm.db, "datastore_drop_column", lambda ns_id, key: 0)
    presentes: set = set()
    monkeypatch.setattr(dsm.db, "datastore_has_column",
                        lambda ns_id, key: key in presentes)
    return st, presentes


def test_a_name_that_matches_no_column_is_refused_not_counted_as_a_purge(
        store_sans_effet):
    """Le cœur du défaut : zéro ligne touchée n'est pas un succès."""
    st, _ = store_sans_effet
    with pytest.raises(ValueError, match="aucune colonne"):
        st.drop_column("v", "actualite_sociale", confirm=True)


def test_a_layer_name_is_refused_and_NAMES_its_column(store_sans_effet):
    """`site_web.comment` est servi à plat mais stocké SOUS `site_web` : la purge ne
    l'atteint pas. Le refus nomme la colonne porteuse ET le geste qui retire la
    couche — sans quoi on interdit sans dire où aller."""
    st, presentes = store_sans_effet
    presentes.add("site_web")
    with pytest.raises(ValueError) as e:
        st.drop_column("v", "site_web.comment", confirm=True)
    msg = str(e.value)
    assert "`site_web`" in msg, "la colonne porteuse est nommée"
    assert "annotation" in msg or "couche" in msg
    assert '"comment": null' in msg, "et le geste qui la retire pour de bon"


def test_a_layer_of_a_DECLARED_column_is_named_too(store_sans_effet):
    """La base peut n'être qu'au schéma, sans une seule ligne écrite : elle n'en est
    pas moins la colonne porteuse."""
    st, _ = store_sans_effet          # `analyse1` est déclarée par STRICT, pas en base
    with pytest.raises(ValueError) as e:
        st.drop_column("v", "analyse1.origine", confirm=True)
    assert "`analyse1`" in str(e.value)


def test_an_unknown_dotted_name_INVENTS_no_column(store_sans_effet):
    """⚠️ Le garde-fou du garde-fou : une faute de frappe qui ressemble à une adresse
    de couche ne doit pas faire nommer une colonne qui n'existe pas. Une destination
    inventée est pire qu'une destination absente."""
    st, _ = store_sans_effet          # rien nulle part : ni en base, ni au schéma
    with pytest.raises(ValueError) as e:
        st.drop_column("v", "site_web.comment", confirm=True)
    msg = str(e.value)
    assert "aucune colonne" in msg
    assert "annotation" not in msg and "couche" not in msg


def test_a_dotted_key_really_STORED_is_purged_not_refused(store):
    """Le troisième cas, et la raison pour laquelle le diagnostic se fait APRÈS la
    purge : une clé pointée littérale au premier niveau du blob (écrite par un chemin
    qui a contourné la garde, #647) EST une colonne — elle se retire, et le geste a
    un effet. Diagnostiquer avant la purge la rendrait inatteignable."""
    st, dropped = store               # la purge SQL rend 12 lignes touchées
    out = st.drop_column("v", "site_web.comment", confirm=True)
    assert dropped == [(7, "site_web.comment")]
    assert out == {"namespace": "v", "key": "site_web.comment", "rows": 12}


# ── le SQL, contre un vrai PostgreSQL ────────────────────────────────────────

@pytest.fixture()
def pg_rows(pg_dsn, monkeypatch):
    """Une table `datastore_rows` minimale + `db.datastore._connect` redirigé
    dessus : c'est la VRAIE fonction et son vrai SQL qui s'exécutent."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    from oto_mcp.db import datastore as dbds

    conn = psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=True)
    conn.execute("DROP TABLE IF EXISTS datastore_rows")
    conn.execute("CREATE TABLE datastore_rows ("
                 " ns_id BIGINT NOT NULL, row_id TEXT NOT NULL, data JSONB NOT NULL,"
                 " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                 " PRIMARY KEY (ns_id, row_id))")

    class _Ctx:
        def __enter__(self): return conn
        def __exit__(self, *a): return False

    monkeypatch.setattr(dbds, "_connect", lambda *a, **k: _Ctx())
    try:
        yield conn, dbds
    finally:
        conn.close()


def _insert(conn, rows):
    import json
    for i, data in enumerate(rows):
        conn.execute("INSERT INTO datastore_rows (ns_id, row_id, data) "
                     "VALUES (%s, %s, %s::jsonb)", (7, f"r{i}", json.dumps(data)))


def test_drop_column_erases_the_key_not_just_its_value(pg_rows):
    """LE point : `null` conserve la clé (donc elle se rend encore), `data - key`
    la fait disparaître."""
    conn, dbds = pg_rows
    _insert(conn, [{"siren": "1", "actualite_sociale": "a"},
                   {"siren": "2", "actualite_sociale": None}])
    assert dbds.datastore_drop_column(7, "actualite_sociale") == 2
    keys = [set(r["data"].keys()) for r in
            conn.execute("SELECT data FROM datastore_rows ORDER BY row_id").fetchall()]
    assert keys == [{"siren"}, {"siren"}]


def test_drop_column_only_rewrites_rows_that_carry_it(pg_rows):
    conn, dbds = pg_rows
    _insert(conn, [{"siren": "1", "vieux": "x"}, {"siren": "2"}, {"siren": "3"}])
    assert dbds.datastore_drop_column(7, "vieux") == 1     # une seule ligne touchée
    assert dbds.datastore_drop_column(7, "vieux") == 0     # rejeu = no-op


def test_has_column_is_EXACT_where_row_keys_only_samples(pg_rows):
    """La question que pose le refus de #680 — « cette colonne existe-t-elle ? » —
    ne se répond pas sur un échantillon : `datastore_row_keys` ne lit que les 1000
    lignes les plus récentes, donc une colonne portée par une seule ligne ANCIENNE
    lui échappe. Ici la 1001ᵉ ligne : le relevé la rate, le prédicat la voit."""
    conn, dbds = pg_rows
    _insert(conn, [{"vieux": "x"}])
    conn.execute(
        "INSERT INTO datastore_rows (ns_id, row_id, data, created_at) "
        "SELECT 7, 'r' || g, '{\"siren\": \"z\"}'::jsonb, NOW() "
        "FROM generate_series(100, 1200) g")
    assert "vieux" not in dbds.datastore_row_keys(7), "le relevé échantillonne"
    assert dbds.datastore_has_column(7, "vieux") is True
    assert dbds.datastore_has_column(7, "jamais_ecrite") is False


def test_has_column_stays_in_its_namespace(pg_rows):
    conn, dbds = pg_rows
    import json
    conn.execute("INSERT INTO datastore_rows (ns_id, row_id, data) "
                 "VALUES (8, 'x', %s::jsonb)", (json.dumps({"ailleurs": 1}),))
    assert dbds.datastore_has_column(7, "ailleurs") is False


def test_drop_column_leaves_other_namespaces_alone(pg_rows):
    conn, dbds = pg_rows
    import json
    conn.execute("INSERT INTO datastore_rows (ns_id, row_id, data) "
                 "VALUES (8, 'x', %s::jsonb)", (json.dumps({"vieux": "garde"}),))
    _insert(conn, [{"vieux": "purge"}])
    dbds.datastore_drop_column(7, "vieux")
    other = conn.execute("SELECT data FROM datastore_rows WHERE ns_id = 8").fetchone()
    assert other["data"] == {"vieux": "garde"}


def test_row_keys_lists_what_lives_in_the_data(pg_rows):
    conn, dbds = pg_rows
    _insert(conn, [{"siren": "1", "analyse1": "a"},
                   {"siren": "2", "actualite_sociale": "vieux"}])
    assert dbds.datastore_row_keys(7) == ["actualite_sociale", "analyse1", "siren"]


def test_set_schema_warns_about_the_columns_it_leaves_behind(pg_rows, monkeypatch):
    """L'avertissement tombe à la POSE du schéma — le moment où le renommage arme
    le piège."""
    conn, _ = pg_rows
    _insert(conn, [{"siren": "1", "actualite_sociale": "vieux"}])
    st = DatastorePg("u", acting_org=35)
    msg = st._orphan_columns_warning(7, STRICT)
    assert msg and "`actualite_sociale`" in msg and "data_drop_column" in msg
    # souple = le champ libre est un droit du contrat : rien à signaler
    assert st._orphan_columns_warning(7, {"fields": [{"key": "siren"}]}) is None
