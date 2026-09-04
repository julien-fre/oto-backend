"""Une colonne de dates se range par le TEMPS, pas par l'alphabet — contre un vrai
PostgreSQL, parce que c'est le seul endroit où ça se voit (oto-backend#859).

Le tri d'une colonne `date`/`datetime` était TEXTUEL, sur une hypothèse écrite dans
le code : « ISO trie juste par l'alphabet ». Elle est vraie d'un seul format dans un
seul fuseau, et fausse dès qu'une colonne en mélange deux — ce qu'aucune validation
d'écriture n'empêchait. Constaté à l'écran sur une même colonne :

    2026-09-04                   ← date seule
    2026-09-03T00:00:00.000Z     ← horodatage complet

⚠️ **La rupture qui compte n'est pas le mélange de formats, c'est le DÉCALAGE
horaire** : `…T23:00:00+02:00` vaut 21 h UTC, donc il précède `…T22:00:00Z` dans le
temps et le suit dans l'alphabet. Un tri par date rendait l'ordre inverse du vrai,
sans que rien ne le signale.

⚠️ **Et la garde n'en était pas une** : le bloc « conforme » valait `TRUE` pour tout
type de date, donc aucune valeur n'était jamais jugée non rangeable. Le compteur
d'écart du tri restait à zéro par construction, et un texte libre se rangeait comme
s'il était une date. Une garde qui affirme au lieu de vérifier est pire que pas de
garde : elle donne un chiffre rassurant.

Éprouvé rouge le 2026-09-03 : `conforme = "TRUE"` et `typed = value_sql` rétablis ⟹
le test du décalage horaire nomme l'inversion, et celui du texte libre montre qu'il
s'intercale entre deux dates.
"""
from __future__ import annotations

import json

import psycopg
import pytest


def _ddl() -> str:
    from oto_mcp.db import _schema
    s = _schema._SCHEMA
    i = s.index("CREATE TABLE IF NOT EXISTS datastore_rows")
    return s[i:s.index("\n);", i) + 3].replace(
        "REFERENCES user_datastores(id) ON DELETE CASCADE", "")


# Les valeurs du signal, plus les deux qui piègent l'alphabet.
_LIGNES = [
    ("tot_decale", {"d": "2026-09-03T23:00:00+02:00"}),   # 21 h UTC
    ("tard_utc", {"d": "2026-09-03T22:00:00Z"}),          # 22 h UTC
    ("jour_seul", {"d": "2026-09-04"}),                   # date nue
    ("complet", {"d": "2026-09-02T00:00:00.000Z"}),
    ("texte", {"d": "bientôt"}),                          # pas une date
    ("vide", {"d": ""}),
]


@pytest.fixture()
def pg(pg_dsn, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    from oto_mcp.db import _conn
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS datastore_rows")
        c.execute(_ddl())
        for rid, data in _LIGNES:
            c.execute("INSERT INTO datastore_rows (ns_id,row_id,data) "
                      "VALUES (1,%s,%s::jsonb)", (rid, json.dumps(data)))
        yield c
        c.execute("DROP TABLE IF EXISTS datastore_rows")


def _order(sens: str = "asc", type_: str = "datetime") -> list:
    from oto_mcp import db
    return [r["row_id"] for r in db.datastore_list_rows(
        1, order_by="d", order_dir=sens, order_type=type_, limit=10)]


def test_la_requete_PART(pg):
    """Un cast posé sans garde ferait échouer la requête entière sur la première
    valeur libre — c'est le mode de panne du bloc numérique, et il coûte tout le
    tri, pas une ligne."""
    assert len(_order()) == len(_LIGNES)


def test_le_DECALAGE_horaire_range_par_le_temps(pg):
    """Le cœur du lot. 21 h UTC avant 22 h UTC, quoi qu'en dise l'alphabet."""
    ordre = _order("asc")
    assert ordre.index("tot_decale") < ordre.index("tard_utc"), (
        "rangé par l'alphabet : `T23…+02:00` (21 h UTC) est passé après `T22…Z`")


def test_les_deux_FORMATS_cohabitent_dans_le_bon_ordre(pg):
    """Date nue et horodatage complet dans la même colonne — le cas vu à l'écran."""
    ordre = _order("asc")
    assert ordre.index("complet") < ordre.index("jour_seul")


def test_ce_qui_n_est_PAS_une_date_ne_s_intercale_pas(pg):
    """Une valeur qu'on ne sait pas ranger va au bloc suivant : elle ne prend
    jamais la tête et ne se glisse jamais entre deux vraies dates."""
    ordre = _order("asc")
    dates = [ordre.index(k) for k in ("complet", "tot_decale", "tard_utc", "jour_seul")]
    assert ordre.index("texte") > max(dates)


def test_le_VIDE_reste_en_queue_derriere_le_texte_libre(pg):
    """Une absence n'est pas une donnée mal rangée — l'ordre des trois blocs est
    le contrat, dans les deux sens."""
    ordre = _order("asc")
    assert ordre[-1] == "vide"
    assert _order("desc")[-1] == "vide"


def test_le_sens_DESC_inverse_les_dates_sans_deplacer_les_blocs(pg):
    ordre = _order("desc")
    assert ordre.index("jour_seul") < ordre.index("complet")
    assert ordre.index("texte") > ordre.index("jour_seul")


def test_le_compteur_d_ECART_cesse_de_dire_zero(pg):
    """Il annonçait zéro par construction sur toute colonne de date. Il doit
    compter la valeur libre — c'est ce qui rend le défaut VISIBLE au lieu de le
    laisser se deviner à l'ordre des lignes."""
    from oto_mcp.db.query import order_health_sql
    sql, params = order_health_sql("data->>'d'", [], "datetime", None)
    cur = pg.execute(f"SELECT {sql} FROM datastore_rows WHERE ns_id = 1",
                     tuple(params))
    off_type, empty = cur.fetchone()
    assert off_type == 1, "la valeur libre doit être comptée comme non rangeable"
    assert empty == 1


def test_une_date_du_type_date_se_range_pareil(pg):
    """`date` et `datetime` partagent la même branche : un lot qui n'en corrigerait
    qu'un laisserait l'autre trier par l'alphabet."""
    assert _order("asc", "date").index("tot_decale") < _order("asc", "date").index("tard_utc")
