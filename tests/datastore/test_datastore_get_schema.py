"""Un schéma déclaré doit pouvoir se relire — et par les DEUX faces.

`data_set_schema` posait un schéma sans qu'aucun verbe ne le rende. Pour connaître
l'existant il fallait lister TOUS les namespaces accessibles et filtrer soi-même :
une jointure imposée à l'appelant, et de la donnée inutile ramenée en contexte.

L'enjeu n'est pas le confort. `set_schema` pose le schéma ENTIER, il ne fusionne pas :
ajouter un champ sans avoir lu l'existant efface le reste en silence. Et ce qu'on perd
en premier est `schema.key` — la clé métier, qui porte un index UNIQUE partiel : la
re-poster absente lève la contrainte sans que rien ne le signale.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import core as D
from oto_mcp.capabilities.datastore import schema as CAP
from oto_mcp.capabilities._types import AuthzDenied


_SCHEMA = {"fields": [{"key": "email", "type": "email", "role": "title"},
                      {"key": "score", "type": "number", "role": "metric"}],
           "key": "email"}


@pytest.fixture
def store(monkeypatch):
    """Store dont la lecture de namespace est stubbée — logique pure, sans PG."""
    ns = {"id": 1, "namespace": "leads", "schema": _SCHEMA}
    monkeypatch.setattr(D.db, "get_datastore_namespace_by_id",
                        lambda ns_id: ns if ns_id == 1 else None)
    s = D.DatastorePg("u1")
    monkeypatch.setattr(s, "_resolve", lambda n, write=False: 1 if n == "leads" else 2)
    return s


def test_reads_back_what_was_declared(store):
    assert store.get_schema("leads") == _SCHEMA


def test_the_business_key_survives_the_roundtrip(store):
    """La clé est la partie CONTRAIGNANTE du schéma : la perdre à la relecture ferait
    re-poster un schéma qui lève l'index UNIQUE sans le dire."""
    assert store.get_schema("leads")["key"] == "email"


def test_a_namespace_without_schema_is_not_an_error(store):
    """État normal — le datastore est schema-free par défaut. Rendre None, pas lever :
    l'appelant doit distinguer « pas de schéma » de « tableau inconnu »."""
    assert store.get_schema("libre") is None


# ── la capacité : un descripteur, deux faces ──

def _cap():
    return next(c for c in CAP.CAPABILITIES if c.key == "me.datastore.get_schema")


def test_the_capability_exposes_both_faces():
    """C'est la raison d'être du choix « capacité » plutôt que tool écrit à la main :
    le dashboard édite les schémas, il lui faut la même lecture — sans qu'une seconde
    implémentation REST apparaisse à côté (ADR 0042)."""
    cap = _cap()
    assert cap.mcp == "data_get_schema"
    assert cap.rest is not None
    assert cap.rest.verb == "GET"
    assert "{namespace}" in cap.rest.path


def test_an_unknown_namespace_is_a_404_not_a_crash(monkeypatch):
    def _boom(sub):
        class _S:
            def get_schema(self, ns):
                raise D.NamespaceNotFound(ns)
        return _S()
    monkeypatch.setattr(CAP, "make_store", _boom)
    with pytest.raises(AuthzDenied) as e:
        CAP._get_schema(_Ctx(), CAP.GetSchemaInput(namespace="fantome"))
    assert e.value.status == 404


class _Ctx:
    sub = "u1"
    org_id = None


# ── #416 : la lecture porte l'avertissement, la surface aussi ────────────────

def _lire(monkeypatch, schema):
    """La capacité EXERCÉE, pas seulement la fonction qui met en forme la phrase.

    Un garde-fou se prouve sur le chemin réel : c'est `data_get_schema` qui sert le
    schéma contradictoire, et c'est donc lui qui doit porter l'avertissement — le
    vérifier sur le formateur seul laisserait le câblage non couvert."""
    class _S:
        def get_schema(self, ns):
            return schema
    monkeypatch.setattr(CAP, "make_store", lambda sub: _S())
    return CAP._get_schema(_Ctx(), CAP.GetSchemaInput(namespace="vivier"))


def test_le_schema_servi_avec_une_cle_morte_porte_son_avertissement(monkeypatch):
    """#416 : `unknown_declaration_keys` existait déjà, mais ne parlait qu'à la POSE
    — et un schéma déjà pollué ne se repose jamais. Trois tableaux mesurés en
    production le 28/08 (9 454 lignes) servaient un `enum` résiduel à côté de
    l'`options` qui fait foi, sans un mot."""
    out = _lire(monkeypatch, {"fields": [
        {"key": "retraitement", "type": "enum", "enum": ["non", "oui"],
         "options": ["non", "budget", "outil", "epuise"]}]})

    assert "retraitement" in out["warning"]
    assert "`options`" in out["warning"], "le lecteur doit savoir laquelle décide"


def test_un_schema_sain_ne_porte_pas_la_cle_warning(monkeypatch):
    """Une clé toujours présente devient un ornement qu'on cesse de lire — et c'est
    l'avertissement UTILE qu'on perd avec elle."""
    out = _lire(monkeypatch, {"fields": [
        {"key": "retraitement", "type": "enum", "options": ["non", "budget"]}]})

    assert "warning" not in out


def test_un_tableau_sans_schema_ne_declenche_rien(monkeypatch):
    """L'état le plus courant du datastore (schema-free) traverse sans bruit."""
    assert "warning" not in _lire(monkeypatch, None)
