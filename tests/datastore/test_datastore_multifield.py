"""Interroger PLUSIEURS champs déclarés en une fois — barreau 1 d'oto#22.

Une même notion vit souvent sur des colonnes numérotées : `contact1_fonction`,
`contact2_fonction`, `contact3_fonction`… — 21 colonnes pour une seule notion sur un
vivier réel. Aucune requête ne savait alors poser *« combien de fiches ont un contact
RH, tous rangs confondus »*. La mesure PRINCIPALE d'un brief client (79 % de contacts
RH/finance chez les 50 salariés et plus, 34 % sous dix) a été produite à coups
d'expressions régulières sur des lignes lues en entier : non rejouable, et
déraisonnable à 8 910 lignes. Elle est le test d'acceptation de ce barreau, et elle
doit tenir en UNE requête.

**L'appelant DÉCLARE les champs membres.** Le serveur n'interprète aucun motif de nom
— pas de famille `contact*` devinée, jamais. Ce serait réintroduire la convention de
nommage qu'on vient de sortir des rôles, et faire dépendre un résultat de
l'orthographe des colonnes.

Contre un VRAI PostgreSQL, et sur le VRAI DDL : le sujet est ce que la requête REND,
pas la chaîne SQL qu'on croit avoir écrite. L'ordre des `%s` entre SELECT, LATERAL,
WHERE et LIMIT est exactement le genre de défaut qu'une assertion sur du texte laisse
passer et qu'une exécution attrape.
"""
from __future__ import annotations

import psycopg
import pytest


# Fonctions considérées RH/finance par le brief — DÉCLARÉES, comme les champs.
_RH = ["DRH", "Responsable RH", "DAF"]
_AUTRES = ["Dirigeant", "Commercial", "Directeur technique"]


def _ddl() -> str:
    """Le VRAI DDL de `datastore_rows`, extrait de `_schema.py`.

    Un banc qui RECONSTITUE le schéma mesure la représentation qu'on s'en fait, pas le
    système — et toujours dans le sens rassurant. Seule la FK vers `user_datastores`
    saute (le barreau ne parle pas de propriété de namespace, et la porter obligerait
    à monter la moitié du modèle pour tester une clause WHERE)."""
    from oto_mcp.db import _schema
    src = _schema._SCHEMA
    i = src.index("CREATE TABLE IF NOT EXISTS datastore_rows")
    j = src.index("\n);", i) + 3
    ddl = src[i:j].replace(
        "REFERENCES user_datastores(id) ON DELETE CASCADE", "")
    assert "data JSONB" in ddl, "le DDL extrait n'est pas celui de la table"
    return ddl


@pytest.fixture()
def pg(pg_dsn, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    from oto_mcp.db import _conn
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS datastore_rows")
        c.execute(_ddl())
        yield c
        c.execute("DROP TABLE IF EXISTS datastore_rows")


def _ins(c, ns: int, rid: str, data: str) -> None:
    c.execute("INSERT INTO datastore_rows (ns_id, row_id, data) VALUES (%s,%s,%s::jsonb)",
              (ns, rid, data))


@pytest.fixture()
def vivier(pg):
    """Un vivier de forme RÉELLE : trois rangs de contacts, le rang porteur qui varie,
    des colonnes absentes, des colonnes vides, et quelques valeurs à couches.

    Les proportions sont celles du brief — 79 sur 100 chez les grands, 34 sur 100 chez
    les petits — pour que le test d'acceptation lise la mesure elle-même, pas une
    approximation qui « ressemble »."""
    import json
    n = 0
    for tranche, avec_rh in (("50 et plus", 79), ("moins de 10", 34)):
        for i in range(100):
            n += 1
            row: dict = {"tranche": tranche,
                         "effectif": 120 if tranche == "50 et plus" else 6}
            if i < avec_rh:
                # Le rang porteur TOURNE : une mesure qui ne regarderait que le rang 1
                # rendrait un tiers du chiffre, sans rien signaler.
                rang = (i % 3) + 1
                fonction = _RH[i % len(_RH)]
                # Une part des valeurs porte ses couches (#318) : le multi-champs doit
                # hériter du polymorphisme, pas le contourner.
                row[f"contact{rang}_fonction"] = (
                    {"valeur": fonction, "origine": "socle client"} if i % 5 == 0
                    else fonction)
                # …et les autres rangs portent du bruit non-RH, vide ou absent.
                for autre in (r for r in (1, 2, 3) if r != rang):
                    if autre % 2 == 0:
                        row[f"contact{autre}_fonction"] = _AUTRES[i % len(_AUTRES)]
                    elif i % 4 == 0:
                        row[f"contact{autre}_fonction"] = ""
            else:
                for rang in (1, 2, 3):
                    if rang <= (i % 3):
                        row[f"contact{rang}_fonction"] = _AUTRES[i % len(_AUTRES)]
            _ins(pg, 1, f"r{n:04d}", json.dumps(row))
    return pg


# --- ce que l'appelant déclare ---------------------------------------------------

def _clauses(spec: dict):
    from oto_mcp import db
    return db._ds_filter_clauses([spec])


def test_declaring_both_a_field_and_a_list_is_refused():
    """`field` et `fields` sont deux façons de dire la cible : les cumuler laisse le
    serveur choisir, donc rendre un résultat qu'on n'a pas demandé."""
    with pytest.raises(ValueError) as e:
        _clauses({"field": "a", "fields": ["a", "b"], "op": "eq", "value": "x"})
    assert "fields" in str(e.value) and "field" in str(e.value)


@pytest.mark.parametrize("bad", [[], "contact1_fonction", {}, [""], [None]])
def test_an_unusable_field_list_says_so(bad):
    """Une liste vide filtrerait sur rien — donc rendrait TOUT, en silence. C'est la
    forme de défaut que ce barreau existe pour éliminer, pas pour reproduire.

    Le message doit nommer `fields` : un refus générique (« field manquant ») envoie
    l'appelant corriger l'autre paramètre, celui qu'il n'a pas employé."""
    with pytest.raises(ValueError) as e:
        _clauses({"fields": bad, "op": "not_empty"})
    assert "fields" in str(e.value)


def test_an_unknown_match_names_the_two_that_exist():
    with pytest.raises(ValueError) as e:
        _clauses({"fields": ["a", "b"], "op": "eq", "value": "x", "match": "some"})
    assert "any" in str(e.value) and "all" in str(e.value)


def test_the_number_of_declared_fields_is_bounded():
    with pytest.raises(ValueError) as e:
        _clauses({"fields": [f"c{i}" for i in range(200)], "op": "not_empty"})
    assert "fields" in str(e.value)


def test_no_name_pattern_is_ever_interpreted(pg):
    """LA contrainte de conception, vérifiée sur le comportement : un nom de colonne
    est un NOM, pas un motif. Une colonne réellement nommée `contact*_fonction` se
    filtre comme telle, et n'attrape pas ses voisines numérotées.

    (Sa première forme inspectait le code source à la recherche de `*` — elle
    attrapait le gras d'une docstring et n'exerçait rien du système.)"""
    import json
    from oto_mcp import db
    _ins(pg, 9, "etoile", json.dumps({"contact*_fonction": "DRH"}))
    for i in (1, 2, 3):
        _ins(pg, 9, f"rang{i}", json.dumps({f"contact{i}_fonction": "DRH"}))
    trouves = sorted(r["row_id"] for r in db.datastore_list_rows(
        9, filters=[{"fields": ["contact*_fonction"], "op": "eq", "value": "DRH"}]))
    assert trouves == ["etoile"]


# --- l'existence à travers les champs --------------------------------------------

def _rows(ns: int, filters: list) -> list[str]:
    from oto_mcp import db
    return sorted(r["row_id"] for r in db.datastore_list_rows(ns, filters=filters))


def _count(ns: int, filters: list) -> int:
    from oto_mcp import db
    return db.datastore_count_rows(ns, filters=filters)


def test_any_field_matching_is_enough(vivier):
    """« Les fiches dont UN des trois rangs porte une fonction RH » — la question qui
    n'était pas posable."""
    n = _count(1, [{"fields": [f"contact{i}_fonction" for i in (1, 2, 3)],
                    "op": "in", "value": _RH}])
    assert n == 113, "79 grands + 34 petits, tous rangs confondus"


def test_the_rank_that_carries_it_does_not_matter(vivier):
    """Interroger un seul rang rend une FRACTION — c'est l'état d'avant, et il rendait
    une réponse plausible."""
    seul = _count(1, [{"field": "contact1_fonction", "op": "in", "value": _RH}])
    assert 0 < seul < 113


def test_layers_are_read_through_the_list(vivier):
    """Une valeur enveloppée (`{"valeur": …, "origine": …}`) compte comme sa valeur :
    le multi-champs hérite de l'expression polymorphe (#318) au lieu de la doubler.
    Sans ça, une fiche dont la fonction vient du socle client sortirait du compte."""
    from oto_mcp import db
    n = db.datastore_count_rows(1, filters=[
        {"fields": [f"contact{i}_fonction" for i in (1, 2, 3)], "op": "in",
         "value": _RH},
        {"fields": [f"contact{i}_fonction.origine" for i in (1, 2, 3)],
         "op": "not_empty"}])
    assert n > 0, "les valeurs à couches doivent être atteintes, valeur ET origine"


def test_all_requires_every_declared_field(vivier):
    """`match=all` : « AUCUN des trois rangs n'est renseigné » = les fiches sans le
    moindre contact. C'est le complément direct de la mesure, et il n'est pas
    exprimable par la négation d'un `any` — d'où les deux."""
    aucun = _count(1, [{"fields": [f"contact{i}_fonction" for i in (1, 2, 3)],
                        "op": "empty", "match": "all"}])
    au_moins_un = _count(1, [{"fields": [f"contact{i}_fonction" for i in (1, 2, 3)],
                              "op": "not_empty"}])
    assert aucun + au_moins_un == 200, "les deux jeux partitionnent le vivier"
    assert aucun > 0 and au_moins_un > 0


def test_any_is_the_default(vivier):
    a = _count(1, [{"fields": ["contact1_fonction", "contact2_fonction"],
                    "op": "not_empty"}])
    b = _count(1, [{"fields": ["contact1_fonction", "contact2_fonction"],
                    "op": "not_empty", "match": "any"}])
    assert a == b


def test_multi_field_filters_still_combine_with_the_others(vivier):
    """Un filtre multi-champs est UN filtre : il se croise en AND avec les autres,
    comme n'importe lequel. C'est ce qui rend la segmentation possible."""
    grands = _count(1, [
        {"field": "tranche", "op": "eq", "value": "50 et plus"},
        {"fields": [f"contact{i}_fonction" for i in (1, 2, 3)], "op": "in",
         "value": _RH}])
    assert grands == 79


def test_a_single_field_is_unchanged(vivier):
    """La régression qu'on ne veut pas : la forme `field` porte tout l'existant."""
    a = _rows(1, [{"field": "tranche", "op": "eq", "value": "50 et plus"}])
    b = _rows(1, [{"fields": ["tranche"], "op": "eq", "value": "50 et plus"}])
    assert a == b and len(a) == 100


# --- le comptage conditionnel -----------------------------------------------------

def _agg(ns: int, **kw) -> list[dict]:
    from oto_mcp import db
    return db.datastore_aggregate(ns, **kw)


def test_a_metric_can_carry_its_own_condition(vivier):
    """Une métrique porte son `where` : c'est ce qui permet de compter DEUX
    populations dans la même requête — le total et le sous-ensemble — donc d'obtenir
    un taux sans recouper deux appels dont les périmètres peuvent diverger."""
    res = _agg(1, metrics=[
        {"op": "count", "label": "total"},
        {"op": "count", "label": "avec_rh",
         "where": [{"fields": [f"contact{i}_fonction" for i in (1, 2, 3)],
                    "op": "in", "value": _RH}]}])
    assert res == [{"total": 200, "avec_rh": 113}]


def test_two_metrics_of_the_same_op_keep_distinct_names(vivier):
    """Deux `count` sans étiquette ne doivent pas s'écraser l'un l'autre — une clé
    perdue en route rendrait un résultat qui a l'air complet."""
    res = _agg(1, metrics=[
        {"op": "count"},
        {"op": "count", "where": [{"field": "tranche", "op": "eq",
                                   "value": "50 et plus"}]}])
    assert len(res[0]) == 2, f"une métrique a écrasé l'autre : {res[0]}"
    assert sorted(res[0].values()) == [100, 200]


def test_a_conditional_sum_only_sums_its_condition(vivier):
    """Le `FILTER` vaut pour tous les agrégats, pas seulement `count`."""
    res = _agg(1, metrics=[
        {"op": "sum", "field": "effectif", "label": "tout"},
        {"op": "sum", "field": "effectif", "label": "grands",
         "where": [{"field": "tranche", "op": "eq", "value": "50 et plus"}]}])
    assert res[0]["tout"] == 100 * 120 + 100 * 6
    assert res[0]["grands"] == 100 * 120


def test_an_unconditional_metric_is_unchanged(vivier):
    assert _agg(1, metrics=[{"op": "count"}]) == [{"count": 200}]


# --- LA mesure d'acceptation ------------------------------------------------------

def test_the_briefs_measure_holds_in_a_single_query(vivier):
    """79 % chez les 50 salariés et plus, 34 % sous dix — la mesure qui avait coûté
    des expressions régulières sur 8 910 lignes lues en entier, rendue par UN appel.

    C'est le test d'acceptation du barreau : segmentation en `group_by`, population
    concernée en métrique conditionnelle multi-champs, taux calculable par groupe."""
    res = _agg(1, group_by="tranche", metrics=[
        {"op": "count", "label": "fiches"},
        {"op": "count", "label": "avec_rh",
         "where": [{"fields": [f"contact{i}_fonction" for i in (1, 2, 3)],
                    "op": "in", "value": _RH}]}])
    taux = {r["tranche"]: round(100 * r["avec_rh"] / r["fiches"]) for r in res}
    assert taux == {"50 et plus": 79, "moins de 10": 34}


# --- la répartition en union ------------------------------------------------------

def test_grouping_over_several_fields_pools_their_values(vivier):
    """« Répartition des fonctions, tous contacts confondus » : une fiche contribue
    une occurrence par rang renseigné. Les rangs vides ou absents ne fabriquent pas un
    groupe vide — ils ne sont pas des contacts."""
    champs = [f"contact{i}_fonction" for i in (1, 2, 3)]
    res = _agg(1, group_by=champs, metrics=[{"op": "count"}])
    cle = "|".join(champs)
    vus = {r[cle]: r["count"] for r in res}
    assert None not in vus and "" not in vus
    assert set(vus) == set(_RH) | set(_AUTRES)
    assert sum(vus[f] for f in _RH) == 113, (
        "chaque fiche RH porte exactement une fonction RH, sur un rang ou un autre")


def test_a_pooled_group_counts_occurrences_and_rows_distinctly(vivier):
    """Sous une répartition en union, `count` compte les OCCURRENCES (deux contacts
    commerciaux sur la même fiche font deux) et `count_rows` les FICHES. Les deux sont
    légitimes ; c'est de les confondre qui produit un chiffre plausible et faux."""
    champs = [f"contact{i}_fonction" for i in (1, 2, 3)]
    res = _agg(1, group_by=champs,
               metrics=[{"op": "count"}, {"op": "count_rows"}])
    assert any(r["count"] > r["count_rows"] for r in res), (
        "aucun groupe ne distingue occurrences et fiches — la mesure est fausse "
        "d'un côté ou de l'autre")


def test_a_pooled_group_still_honours_the_filters(vivier):
    champs = [f"contact{i}_fonction" for i in (1, 2, 3)]
    res = _agg(1, group_by=champs, metrics=[{"op": "count"}],
               filters=[{"field": "tranche", "op": "eq", "value": "50 et plus"}])
    cle = "|".join(champs)
    assert sum(r["count"] for r in res if r[cle] in _RH) == 79


def test_a_single_group_by_is_unchanged(vivier):
    res = _agg(1, group_by="tranche", metrics=[{"op": "count"}])
    assert sorted(res, key=lambda r: r["tranche"]) == [
        {"tranche": "50 et plus", "count": 100},
        {"tranche": "moins de 10", "count": 100}]
