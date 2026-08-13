"""La surface agent atteint ce que le moteur sait faire (oto#22 barreau 1).

Le moteur savait filtrer sur plusieurs colonnes déclarées, compter une population
conditionnelle et regrouper en union — et un agent en session ne pouvait rien en
demander : `data_rows` ne prenait qu'un dictionnaire colonne→valeur, `data_aggregate`
n'avait même pas les filtres. Une capacité qu'aucune surface n'expose n'existe pas
pour celui qui travaille en conversation, et c'est là que travaille la mission qui l'a
demandée.

Deux niveaux, parce qu'il y a deux façons de rater ce câblage :

- le paramètre doit être DÉCLARÉ au schéma de l'outil — c'est le schéma que l'agent
  lit pour savoir ce qu'il peut envoyer, et ce que le serveur valide avant d'appeler.
  Non déclaré, il est refusé ou ignoré, ce qui revient au même vu de la conversation ;
- il doit ARRIVER au store sous son nom. Un paramètre déclaré mais oublié dans le
  passage donne le pire des cas : l'appel est accepté et la réponse ignore le filtre —
  toutes les lignes rendues, présentées comme filtrées.
"""
from __future__ import annotations

import asyncio

import pytest


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import datastore as D
    m = FastMCP("t")
    D.register(m)
    return asyncio.run(m.get_tool(name))


class _Store:
    """Capture ce que la surface transmet, et rend de quoi finir l'appel."""

    def __init__(self):
        self.vu: dict = {}

    def cursor_rows(self, namespace, **kw):
        self.vu = dict(kw, _verbe="cursor_rows")
        return {"rows": [{"_id": "r1"}], "next_cursor": None}

    def count_rows(self, namespace, **kw):
        self.vu = dict(kw, _verbe="count_rows")
        return 7

    def aggregate(self, namespace, **kw):
        self.vu = dict(kw, _verbe="aggregate")
        return [{"count": 3}]

    def get_schema(self, namespace):
        return None


@pytest.fixture()
def store(monkeypatch):
    from oto_mcp.tools import datastore as D
    s = _Store()
    monkeypatch.setattr(D, "_acting_store", lambda: s)
    return s


# --- le schéma publié -------------------------------------------------------------

_MULTI = ["contact1_fonction", "contact2_fonction", "contact3_fonction"]


@pytest.mark.parametrize("tool,param", [
    ("data_rows", "filters"),
    ("data_aggregate", "filters"),
    ("data_aggregate", "q"),
])
def test_the_parameter_is_declared_to_the_agent(tool, param):
    """Ce que le schéma ne déclare pas, l'agent ne peut pas envoyer."""
    schema = _tool(tool).parameters
    assert param in schema.get("properties", {}), (
        f"`{param}` absent du schéma de `{tool}` — inatteignable en session")


def test_grouping_accepts_a_list_of_columns():
    """`group_by` doit accepter les DEUX formes : un nom, ou des colonnes mises en
    commun. Un schéma qui n'admet que la chaîne rejette la question « tous rangs
    confondus » avant même qu'elle atteigne le serveur."""
    prop = _tool("data_aggregate").parameters["properties"]["group_by"]
    types = {v.get("type") for v in (prop.get("anyOf") or [prop])}
    assert {"string", "array"} <= types, (
        f"`group_by` n'admet pas une liste de colonnes : {prop}")


def test_the_schema_contract_says_how_to_name_a_row():
    """La désignation du titre est passée de `role` à `display`, et la fiche de
    l'outil annonçait encore l'ancienne clé. Une docstring est le contrat que l'agent
    LIT : périmée, elle fait déclarer un schéma qui ne titre rien — et le défaut est
    invisible, puisque le tableau se crée sans erreur."""
    doc = _tool("data_set_schema").description or ""
    assert '"display"' in doc and 'display: "title"' in doc, (
        "le contrat ne dit plus comment nommer une ligne")
    assert '"title|badge|metric|status|qualif|note"' not in doc, (
        "l'ancienne énumération de rôles est encore annoncée")


# --- ce qui arrive au store --------------------------------------------------------

def test_row_filters_reach_the_store(store):
    spec = [{"fields": _MULTI, "op": "in", "value": ["DRH"]}]
    _tool("data_rows").fn(namespace="t", filters=spec)
    assert store.vu.get("filters") == spec, (
        f"`filters` n'est pas parvenu au store : {store.vu}")


def test_the_count_path_carries_them_too(store):
    """`count_only` est un AUTRE chemin dans le même outil : c'est celui qu'on prend
    pour « combien de fiches… », donc exactement la question du barreau."""
    spec = [{"fields": _MULTI, "op": "not_empty"}]
    out = _tool("data_rows").fn(namespace="t", filters=spec, count_only=True)
    assert store.vu.get("_verbe") == "count_rows"
    assert store.vu.get("filters") == spec
    assert out == {"total": 7}


def test_the_aggregate_carries_metrics_filters_and_pooled_grouping(store):
    """LA mesure du brief, telle qu'un agent l'écrit : la population totale et la
    sous-population dans le même appel, segmentées."""
    metrics = [{"op": "count", "label": "fiches"},
               {"op": "count", "label": "avec_rh",
                "where": [{"fields": _MULTI, "op": "in", "value": ["DRH", "DAF"]}]}]
    _tool("data_aggregate").fn(namespace="t", group_by="tranche", metrics=metrics)
    assert store.vu.get("metrics") == metrics, (
        "la condition portée par une métrique doit traverser INTACTE — c'est elle "
        f"qui fait le taux : {store.vu}")


def test_a_pooled_grouping_reaches_the_store_as_a_list(store):
    _tool("data_aggregate").fn(namespace="t", group_by=_MULTI)
    assert store.vu.get("group_by") == _MULTI


def test_the_existing_forms_are_untouched(store):
    """Le dictionnaire colonne→valeur porte tout l'usage d'aujourd'hui."""
    _tool("data_rows").fn(namespace="t", filter={"statut": "ouvert"})
    assert store.vu.get("filter") == {"statut": "ouvert"}
    assert store.vu.get("filters") is None


# --- le piège qu'on ne rouvre pas --------------------------------------------------

def test_a_misspelled_column_is_flagged_in_the_new_form_too(monkeypatch, store):
    """Une colonne mal orthographiée rend 0 ligne, sans erreur — le piège que
    l'avertissement existe pour fermer. L'ouvrir sur la forme NEUVE serait l'ouvrir là
    où l'agent en a le plus besoin : c'est elle qui nomme plusieurs colonnes à la main.

    ⚠️ Le suffixe de couche ne compte pas comme une faute : `contact1_email.origine`
    vise la colonne `contact1_email`, qui existe."""
    from oto_mcp.tools import datastore as D
    store.cursor_rows = lambda namespace, **kw: {"rows": [], "next_cursor": None}
    monkeypatch.setattr(D, "_namespace_keys", lambda s, ns: {"contact1_email"})
    monkeypatch.setattr(D.dsv2, "top_level_keys", lambda schema: {"contact1_email"})

    out = _tool("data_rows").fn(namespace="t", filters=[
        {"fields": ["contact1_email", "contact1_emial.origine"], "op": "not_empty"}])
    assert "contact1_emial" in out.get("warning", ""), (
        f"la faute de frappe doit être signalée : {out}")
    assert "contact1_email" not in out["warning"].replace("contact1_emial", ""), (
        "la colonne qui EXISTE ne doit pas être accusée — un avertissement qui crie "
        "à tort finit ignoré, et celui-ci est le dernier filet")
