"""Filtres par colonne du datastore (vue tableau dashboard, oto-dashboard#18).

Teste le constructeur de clauses `_ds_filter_clauses` / `_ds_where` (pur, sans DB) :
whitelist d'ops, paramétrage du champ (anti-injection), numérique vs texte pour les
comparaisons ordonnées, AND combiné, et le partage list/count (total cohérent).
"""
from __future__ import annotations

import pytest

from oto_mcp import db

# L'expression polymorphe (#318) — référencée, jamais recopiée : ces tests
# portent sur la COMPOSITION (quel opérateur, combien de paramètres), pas sur la
# forme de la lecture, qui évoluera encore.
V = db.FIELD_VALUE_PARAM_SQL


def test_no_filters_is_noop():
    clauses, params = db._ds_filter_clauses(None)
    assert clauses == [] and params == []
    clauses, params = db._ds_filter_clauses([])
    assert clauses == [] and params == []


def test_contains_eq_in():
    clauses, params = db._ds_filter_clauses([
        {"field": "secteur", "op": "contains", "value": "santé"},
        {"field": "statut", "op": "eq", "value": "retenu"},
        {"field": "offre", "op": "in", "value": ["sante_prevoyance", "titres_restaurant"]},
    ])
    assert clauses[0] == f"{V} ILIKE %s"
    assert params[0:3] == ["secteur", "secteur", "%santé%"]
    assert clauses[1] == f"{V} = %s"
    assert params[3:6] == ["statut", "statut", "retenu"]
    assert clauses[2] == f"{V} = ANY(%s)"
    assert params[6:8] == ["offre", "offre"]
    assert params[8] == ["sante_prevoyance", "titres_restaurant"]


def test_numeric_comparison_casts_and_guards():
    # Valeur numérique → cast ::numeric gardé (champ apparaît 2× : regex + cast).
    clauses, params = db._ds_filter_clauses([{"field": "effectif", "op": "gte", "value": "50"}])
    assert "::numeric >= %s::numeric" in clauses[0]
    assert "~ '^-?[0-9]+" in clauses[0]
    assert params == ["effectif"] * 4 + ["50"]


def test_date_comparison_stays_textual():
    # Valeur non numérique (ISO date) → comparaison texte (lexicographique = chrono).
    clauses, params = db._ds_filter_clauses([{"field": "date_depot", "op": "lt", "value": "2024-01-01"}])
    assert clauses[0] == f"{V} < %s"
    assert params == ["date_depot", "date_depot", "2024-01-01"]


def test_empty_not_empty():
    clauses, params = db._ds_filter_clauses([
        {"field": "email", "op": "empty", "value": None},
        {"field": "phone", "op": "not_empty", "value": None},
    ])
    assert clauses[0] == f"({V} IS NULL OR {V} = '')"
    assert params[0:4] == ["email"] * 4
    assert clauses[1] == f"({V} IS NOT NULL AND {V} <> '')"
    assert params[4:8] == ["phone"] * 4


def test_field_is_always_parameterized_no_injection():
    # Un nom de champ hostile ne doit JAMAIS apparaître dans le SQL — il part en param.
    evil = "x'); DROP TABLE datastore_rows; --"
    clauses, params = db._ds_filter_clauses([{"field": evil, "op": "eq", "value": "1"}])
    assert evil not in clauses[0]
    assert clauses[0] == f"{V} = %s"
    assert params[0] == evil


def test_bad_op_and_shape_raise():
    with pytest.raises(ValueError):
        db._ds_filter_clauses([{"field": "a", "op": "nope", "value": "1"}])
    with pytest.raises(ValueError):
        db._ds_filter_clauses([{"field": "", "op": "eq", "value": "1"}])
    with pytest.raises(ValueError):
        db._ds_filter_clauses(["not-a-dict"])
    with pytest.raises(ValueError):
        db._ds_filter_clauses([{"field": "a", "op": "eq", "value": "1"}] * (db._DS_MAX_FILTERS + 1))


class TestMetaColumns:
    """Filtres sur les colonnes MÉTA (`_updated_at`/`_created_at`/`_id`).

    Elles ne vivent pas dans `data` : sans routage, `data ->> '_updated_at'` est NULL
    et le filtre rend 0 ligne SANS erreur. Le tri les connaissait déjà, pas le WHERE.
    """

    def test_updated_at_is_not_read_from_the_json_blob(self):
        clauses, params = db._ds_filter_clauses(
            [{"field": "_updated_at", "op": "gte", "value": "2026-08-01"}])
        assert "data->" not in clauses[0] and "data ->>" not in clauses[0]
        assert clauses[0] == "updated_at >= %s::timestamptz"
        assert params == ["2026-08-01"]

    def test_date_only_lte_covers_the_whole_day(self):
        # « jusqu'au 5 » DOIT inclure le 5 : borne haute = lendemain exclu (un `<=`
        # nu comparerait à minuit et effacerait la journée saisie).
        clauses, params = db._ds_filter_clauses(
            [{"field": "_updated_at", "op": "lte", "value": "2026-08-05"}])
        assert clauses[0] == "updated_at < (%s::date + 1)::timestamptz"
        assert params == ["2026-08-05"]

    def test_date_only_gt_starts_the_next_day(self):
        clauses, _ = db._ds_filter_clauses(
            [{"field": "_created_at", "op": "gt", "value": "2026-08-05"}])
        assert clauses[0] == "created_at >= (%s::date + 1)::timestamptz"

    def test_date_only_eq_is_a_day_window(self):
        clauses, params = db._ds_filter_clauses(
            [{"field": "_created_at", "op": "eq", "value": "2026-08-05"}])
        assert clauses[0] == ("(created_at >= %s::timestamptz "
                              "AND created_at < (%s::date + 1)::timestamptz)")
        assert params == ["2026-08-05", "2026-08-05"]
        clauses, _ = db._ds_filter_clauses(
            [{"field": "_created_at", "op": "ne", "value": "2026-08-05"}])
        assert clauses[0].startswith("NOT (")

    def test_timestamp_value_compares_as_an_instant(self):
        clauses, params = db._ds_filter_clauses(
            [{"field": "_updated_at", "op": "lt", "value": "2026-08-05T14:30"}])
        assert clauses[0] == "updated_at < %s::timestamptz"
        assert params == ["2026-08-05T14:30"]

    def test_malformed_date_raises_instead_of_reaching_postgres(self):
        # Sinon le cast SQL lève → 500 opaque au lieu du 400 « invalid_filters ».
        with pytest.raises(ValueError, match="date invalide"):
            db._ds_filter_clauses(
                [{"field": "_updated_at", "op": "gte", "value": "hier"}])

    def test_ops_without_meaning_on_a_not_null_column_are_refused(self):
        for op in ("empty", "not_empty", "contains", "in"):
            with pytest.raises(ValueError, match="non applicable"):
                db._ds_filter_clauses(
                    [{"field": "_updated_at", "op": op, "value": "x"}])

    def test_row_id_maps_to_the_real_column(self):
        clauses, params = db._ds_filter_clauses(
            [{"field": "_id", "op": "eq", "value": "0199-abc"}])
        assert clauses[0] == "row_id = %s"
        assert params == ["0199-abc"]
        clauses, params = db._ds_filter_clauses(
            [{"field": "_id", "op": "in", "value": ["a", "b"]}])
        assert clauses[0] == "row_id = ANY(%s)"
        assert params == [["a", "b"]]

    def test_user_field_named_like_a_meta_one_is_unaffected(self):
        # Le routage est une correspondance EXACTE : un champ user `updated_at`
        # (sans underscore) reste dans le JSON.
        clauses, params = db._ds_filter_clauses(
            [{"field": "updated_at", "op": "eq", "value": "2026-08-05"}])
        assert clauses[0] == f"{V} = %s"
        assert params == ["updated_at", "updated_at", "2026-08-05"]


def test_where_merges_q_and_filters_in_order():
    from oto_mcp.db.projects import _fold
    where, params = db._ds_where(7, "marseille", [{"field": "statut", "op": "eq", "value": "retenu"}])
    # `q` est ACCENT-INSENSIBLE depuis #67 V2.3 : l'expression de repli est DÉRIVÉE de
    # `_fold` (source unique index↔requête) — la recopier en dur ici la ferait mentir au
    # prochain ajustement du jeu de caractères.
    # La recherche lit les VALEURS, pas les enveloppes (#318) — d'où la constante
    # plutôt que `data::text` : une colonne à couches ne doit pas faire matcher sa
    # provenance (`q=hunter` sur une ligne dont l'email VIENT de Hunter).
    assert where == (f"WHERE ns_id = %s AND {_fold(db.ROW_VALUES_TEXT_SQL)} ILIKE "
                     f"'%%' || {_fold('%s')} || '%%' AND {V} = %s")
    assert params == [7, "marseille", "statut", "statut", "retenu"]  # les % vivent dans le SQL
