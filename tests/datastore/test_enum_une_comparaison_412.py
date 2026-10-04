"""Une énumération, UNE sémantique de comparaison (oto-backend#412).

Sur un champ `enum` à options chaînes (`"1"`…`"5"`), l'entier `5` était REFUSÉ par le
contrôle d'options (égalité de type), alors que `required_when` comparait en `str()` et
tenait la condition `{"priorite": "5"}` pour satisfaite par `5`. Un même schéma, une
même valeur : acceptée par un chemin, refusée par l'autre. Mesuré sur une campagne :
4 refus sur les 40 derniers venaient de ce seul écart — un modèle écrit `5` en JSON.

La règle retenue est celle de la BASE, que lisent le filtre, le tri et le relevé de la
pose : chaque côté sous sa forme comparée (`options_declarees.valeur_comparee`, ce que
rend `data->>champ`). Une seule fonction la porte, `parmi`, pour les options comme pour
les conditions. La valeur stockée reste celle envoyée : filtre et tri lisent son texte,
qui est le même.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore.options_declarees import parmi
from oto_mcp.datastore.validation import validate_row

SCHEMA = {
    "unknown_columns": "report",
    "fields": [
        {"key": "priorite", "type": "enum", "options": ["1", "2", "3", "4", "5"]},
        {"key": "motif", "type": "text", "required_when": {"priorite": "5"}},
    ],
}


def test_rouge_d_abord_l_entier_est_une_option_ET_satisfait_la_condition():
    """Le cas de l'issue : l'entier 5 n'est plus refusé comme option, et il arme
    toujours la condition écrite en chaîne — une seule réponse sur les deux chemins."""
    erreurs = validate_row(SCHEMA, {"priorite": 5})
    assert not [e for e in erreurs if "hors options" in e or "énumération" in e], erreurs
    assert any("motif" in e for e in erreurs), "la condition `\"5\"` est satisfaite par 5"
    assert validate_row(SCHEMA, {"priorite": 5, "motif": "urgent"}) == []


def test_la_chaine_et_l_entier_recoivent_la_meme_reponse():
    for v in (5, "5"):
        assert validate_row(SCHEMA, {"priorite": v, "motif": "x"}) == [], v
    for v in (6, "6"):
        erreurs = validate_row(SCHEMA, {"priorite": v, "motif": "x"})
        assert len(erreurs) == 1 and "hors options" in erreurs[0], erreurs


def test_le_refus_reste_nomme_et_cite_les_options():
    erreurs = validate_row(SCHEMA, {"priorite": 7})
    assert erreurs == ["priorite: valeur '7' hors options (1, 2, 3, 4, 5)"], erreurs


def test_un_composite_n_est_pas_une_valeur_d_enumeration():
    erreurs = validate_row(SCHEMA, {"priorite": ["5"], "motif": "x"})
    assert erreurs and "valeur d'énumération" in erreurs[0], erreurs


@pytest.mark.parametrize("valeur, declarees, attendu", [
    (5, "5", True), ("5", 5, True), (5, ["4", "5"], True),
    (True, "true", True), ("true", True, True), (True, True, True),
    (5.0, "5", False),          # la base rend `5.0` : ce n'est pas `5`
    ("5 ", "5", False),         # aucune normalisation cachée
    (None, "None", False),      # une case vide n'est égale à rien
    ({"a": 1}, "{'a': 1}", False),
])
def test_parmi_compare_la_forme_que_compare_la_base(valeur, declarees, attendu):
    assert parmi(valeur, declarees) is attendu


def test_une_condition_booleenne_se_juge_comme_une_option_booleenne():
    """`str(True)` valait `"True"` et la base rend `true` : une condition écrite `"true"`
    ne s'armait jamais sur une case booléenne. Même forme des deux côtés désormais."""
    schema = {"unknown_columns": "report", "fields": [
        {"key": "joignable", "type": "bool"},
        {"key": "motif", "type": "text", "required_when": {"joignable": "false"}},
    ]}
    assert any("motif" in e for e in validate_row(schema, {"joignable": False}))
    assert validate_row(schema, {"joignable": True}) == []


def test_le_releve_de_la_pose_compare_comme_le_refus(pg_module_dsn, monkeypatch):
    """Le relevé SQL de l'existant à la pose (`datastore_offending_enum_values`) prend
    la même forme des options : une option booléenne `true` ne condamne pas la case
    `true`, une option `5` ne condamne ni `5` ni `"5"`."""
    import psycopg

    from oto_mcp.db import _conn
    from oto_mcp.db.datastore import datastore_offending_enum_values
    monkeypatch.setenv("DATABASE_URL", pg_module_dsn)
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_module_dsn)
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS datastore_rows")
        c.execute("CREATE TABLE datastore_rows (ns_id INT, row_id TEXT, data JSONB, "
                  "created_at TIMESTAMPTZ DEFAULT now())")
        c.execute("""INSERT INTO datastore_rows (ns_id, row_id, data) VALUES
            (1, 'a', '{"ok": true, "n": 5}'), (1, 'b', '{"ok": false, "n": "5"}'),
            (1, 'c', '{"ok": true, "n": 6}')""")
        try:
            hors = datastore_offending_enum_values(1, {"ok": [True], "n": [5]})
        finally:
            c.execute("DROP TABLE datastore_rows")
    assert {h["field"]: sorted(v["value"] for v in h["values"]) for h in hors} == {
        "ok": ["false"], "n": ["6"]}
