"""Le SCHÉMA dit quelles colonnes existent — pas l'échantillon de lignes rendues.

Dans une row JSONB, **une colonne vide n'existe pas** : pas de case vide, pas de case
du tout. Un contrôle qui rassemble « les clés présentes dans les lignes de cette
page » déclare donc inconnue une colonne déclarée mais renseignée sur 12 lignes de
500, dès qu'aucune des 12 ne figure sur la page tirée.

Et le message disait « vérifie l'orthographe » : il ne ratait pas seulement sa cible,
il DÉSIGNAIT UNE CAUSE FAUSSE. L'appelant relit son appel, qui est juste, et conclut
que le champ n'existe pas — c'est exactement ce qu'a fait un agent sur
`edition-echantillon-500`, sur un `notes_verification` pourtant déclaré et rempli.

Couvre aussi le pendant enum de `set_schema` : un format ne vaut que pour l'avenir,
donc le poser ne revalide pas l'existant.
"""
from __future__ import annotations

import pytest

from oto_mcp import datastore_schema as dsv2


_SCHEMA = {
    "strict": True,
    "fields": [
        {"key": "raison_sociale", "type": "text"},
        {"key": "notes_verification", "type": "text", "role": "note"},
        {"key": "unite_employeuse", "type": "enum",
         "options": ["oui", "non", "inconnu"]},
        {"key": "libre", "type": "enum"},                      # enum sans options
        {"key": "contacts", "type": "list",
         "of": {"type": "object", "fields": [{"key": "tel", "type": "text"}]}},
    ],
}


# --- les deux helpers, purs ----------------------------------------------------

def test_declared_keys_are_the_top_level_ones():
    assert dsv2.top_level_keys(_SCHEMA) == {
        "raison_sociale", "notes_verification", "unite_employeuse",
        "libre", "contacts"}


def test_declared_keys_on_a_schemaless_namespace():
    """Tableau libre : aucune colonne déclarée, donc l'échantillon reste le seul
    juge — le contrôle doit continuer de fonctionner, pas disparaître."""
    assert dsv2.top_level_keys(None) == set()
    assert dsv2.top_level_keys({}) == set()


def test_enum_options_ignore_the_free_ones():
    """Un enum SANS `options` est libre (le client rend un select vide) : il ne
    condamne aucune valeur, donc il n'a rien à vérifier sur l'existant."""
    assert dsv2.top_level_enum_options(_SCHEMA) == {
        "unite_employeuse": ["oui", "non", "inconnu"]}


# --- la projection ne ment plus sur la cause -----------------------------------

class _Store:
    """Store minimal : une page où AUCUNE ligne ne porte `notes_verification`."""

    def __init__(self, schema=_SCHEMA):
        self._schema = schema

    def get_schema(self, namespace):
        return self._schema

    def cursor_rows(self, namespace, **kw):
        return {"rows": [{"_id": "1", "raison_sociale": "ACME"},
                         {"_id": "2", "raison_sociale": "BETA"}],
                "next_cursor": None}

    def count_rows(self, *a, **k):
        return 2


def _rows(monkeypatch, store, **kw):
    from oto_mcp import access
    from oto_mcp.tools import datastore as D

    monkeypatch.setattr(access, "current_user_sub_from_token", lambda: "u-1")
    monkeypatch.setattr(D, "make_store", lambda sub: store)
    reg = _Reg()
    D.register(reg)
    return reg.tools["data_rows"](namespace="t", **kw)


class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco(a[0]) if a and callable(a[0]) else deco


def test_a_declared_column_absent_from_the_page_is_not_unknown(monkeypatch):
    out = _rows(monkeypatch, _Store(),
                fields=["raison_sociale", "notes_verification"])
    assert "warning" not in out, (
        "colonne DÉCLARÉE mais vide sur cette page : ce n'est pas une faute de nom")


def test_a_truly_unknown_column_is_still_flagged(monkeypatch):
    out = _rows(monkeypatch, _Store(), fields=["raison_sociale", "raison_social"])
    assert "raison_social" in out["warning"], (
        "la faute de frappe, elle, doit continuer d'être signalée")
    assert "notes_verification" not in out.get("warning", "")


def test_without_a_schema_the_sample_still_judges(monkeypatch):
    """Tableau libre : pas de schéma, donc l'échantillon reprend son rôle — le
    correctif ne doit pas éteindre le contrôle, seulement le subordonner."""
    out = _rows(monkeypatch, _Store(schema=None), fields=["inexistante"])
    assert "inexistante" in out["warning"]


# --- l'existant qu'un enum fraîchement posé condamne ---------------------------

def test_posting_an_enum_reports_what_already_breaks_it(monkeypatch):
    """Poser un format ne revalide pas l'existant : sans ce comptage, la table
    PARAÎT conforme (elle a un schéma) en contenant des valeurs invisibles au
    filtrage. Vécu : 504 lignes en « Oui »/« Non »."""
    from oto_mcp import datastore as ds

    monkeypatch.setattr(ds.db, "datastore_offending_enum_values",
                        lambda ns_id, options, **k: [
                            {"field": "unite_employeuse", "rows": 504, "distinct": 2,
                             "values": [{"value": "Oui", "rows": 312},
                                        {"value": "Non", "rows": 192}]}])
    w = ds.DatastorePg._offending_enum_warning(1, _SCHEMA)
    assert "504" in w
    # Les VALEURS fautives, pas un total nu : c'est ce qui permet de choisir entre
    # corriger la donnée et élargir les options.
    assert "Oui" in w and "312" in w
    assert "oui, non, inconnu" in w, "les options déclarées doivent être rappelées"
    assert "INVISIBLES au filtrage" in w


def _no_db(monkeypatch):
    from oto_mcp import datastore as ds

    def _boom(*a, **k):
        raise AssertionError("la base ne devait pas être interrogée")

    monkeypatch.setattr(ds.db, "datastore_offending_enum_values", _boom)
    return ds


def test_a_soft_schema_is_not_scanned(monkeypatch):
    """LA garde, et c'est un choix : sur un schéma souple la validation est
    inactive (opt-in, 0016), donc l'enum ne condamnera rien. Signaler l'existant
    y annoncerait un refus qui n'aura pas lieu — un faux avertissement coûte la
    confiance qu'on met dans les vrais."""
    ds = _no_db(monkeypatch)
    souple = {"fields": [{"key": "u", "type": "enum", "options": ["a", "b"]}]}
    assert ds.DatastorePg._offending_enum_warning(1, souple) is None


def test_a_strict_schema_without_enum_is_not_scanned(monkeypatch):
    """Validation active mais aucun enum à options : rien à condamner, pas de
    requête."""
    ds = _no_db(monkeypatch)
    assert ds.DatastorePg._offending_enum_warning(
        1, {"strict": True, "fields": [{"key": "x", "type": "text"}]}) is None


def test_a_required_field_activates_the_scan(monkeypatch):
    """La garde suit `validation_active`, pas le seul `strict` : un `required`
    suffit à rendre l'enum contraignant, donc à rendre l'avertissement dû."""
    from oto_mcp import datastore as ds
    vu = {}
    monkeypatch.setattr(ds.db, "datastore_offending_enum_values",
                        lambda ns_id, options, **k: vu.update(options=options) or [])
    ds.DatastorePg._offending_enum_warning(1, {"fields": [
        {"key": "nom", "type": "text", "required": True},
        {"key": "u", "type": "enum", "options": ["a", "b"]}]})
    assert vu["options"] == {"u": ["a", "b"]}


def test_a_conformant_table_says_nothing(monkeypatch):
    from oto_mcp import datastore as ds

    monkeypatch.setattr(ds.db, "datastore_offending_enum_values",
                        lambda ns_id, options, **k: [])
    assert ds.DatastorePg._offending_enum_warning(1, _SCHEMA) is None
