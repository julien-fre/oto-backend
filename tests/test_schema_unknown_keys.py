"""Une clé de déclaration non interprétée ne doit plus passer en silence (#316).

Le cas réel (13/08) : trois champs posés avec `enum: [...]` au lieu d'`options: [...]`.
La clé a été stockée, rendue fidèlement par `data_get_schema`, affichée — et jamais
lue. Les trois « énumérations » étaient LIBRES sans que rien ne le dise, et 504 valeurs
sont entrées sur un tableau `strict`. Comportement conforme au contrat, et
indistinguable d'un enum contraint à l'usage : l'auteur a cru réparer dix champs, huit
l'ont été.

⚠️ **Le vocabulaire n'est PAS fermé, et ça se garde ici aussi** : les consommateurs
posent leurs propres déclarations (`dated_by`, `compare_by`, `role: qualif`) que le
datastore transporte sans les interpréter. Refuser l'inconnu casserait ce contrat — on
signale, on n'empêche pas.
"""
from __future__ import annotations

from oto_mcp import datastore_schema as dsv2


def _cles(schema):
    return {e["field"]: e for e in dsv2.unknown_declaration_keys(schema)}


# ── le cas qui a coûté 504 valeurs ───────────────────────────────────────────

def test_enum_instead_of_options_is_reported_with_its_correction():
    """Le near-miss est ce qui rend l'avertissement ACTIONNABLE : « clé inconnue » ne
    dit pas quoi faire, « enum → options » si."""
    e = _cles({"fields": [{"key": "statut", "type": "text",
                           "enum": ["Oui", "Non"]}]})

    assert e["statut"]["keys"] == ["enum"]
    assert e["statut"]["near_miss"] == {"enum": "options"}

    msg = dsv2.unknown_keys_warning(list(e.values()))
    assert "enum → options" in msg
    # La CONSÉQUENCE avant la correction : sans elle, on lit un détail de style.
    assert "ne contraint" in msg


def test_the_message_says_what_actually_happens_to_the_key():
    """« Stockées et rendues telles quelles » : c'est précisément ce qui rendait le
    défaut invisible — la clé revient dans `data_get_schema`, donc tout semble en
    ordre."""
    schema = {"fields": [{"key": "s", "enum": ["a"]}]}
    msg = dsv2.unknown_keys_warning(dsv2.unknown_declaration_keys(schema))
    assert "stockées et rendues" in msg


# ── ce qui ne doit PAS être signalé ──────────────────────────────────────────

def test_interpreted_keys_are_silent():
    """Un faux positif — accuser une clé qui marche — ferait ignorer l'avertissement,
    donc le rendrait inutile. Les clés lues restent muettes."""
    assert dsv2.unknown_declaration_keys({"fields": [
        {"key": "societe", "type": "text", "options": ["a", "b"], "required": True,
         "max_length": 80, "required_when": {"statut": "gagne"}}]}) == []


def test_a_third_party_declaration_is_reported_without_a_false_correction():
    """Les déclarations tierces (scout : `dated_by`, `compare_by`…) sont signalées —
    c'est honnête, oto ne les lit pas — mais SANS correction inventée : leur proposer
    « vouliez-vous écrire options ? » serait un contresens."""
    e = _cles({"fields": [{"key": "note", "type": "text",
                           "dated_by": "created_at", "compare_by": "siren"}]})

    assert e["note"]["keys"] == ["compare_by", "dated_by"]
    assert e["note"]["near_miss"] == {}


def test_nothing_to_say_means_no_warning():
    assert dsv2.unknown_keys_warning([]) == ""
    assert dsv2.unknown_declaration_keys(None) == []
    assert dsv2.unknown_declaration_keys({"fields": []}) == []


# ── le vocabulaire est DÉRIVÉ, pas listé ─────────────────────────────────────

def test_the_vocabulary_comes_from_the_code_that_reads_it():
    """⚠️ **Le point de conception.** Une liste parallèle diverge le jour où quelqu'un
    lit une clé de plus — ou cesse d'en lire une. Le signal se met alors à mentir dans
    les deux sens : taire une vraie faute de frappe, ou accuser une clé lue.

    C'est exactement ce qui va se produire : `lifecycle` et `role` sont en cours de
    recadrage (#315/#317). Dérivées, elles sortiront du vocabulaire quand le code
    cessera de les lire, sans que personne n'ait à y penser."""
    lues = dsv2.interpreted_keys()

    for attendue in ("options", "required", "max_length", "required_when",
                     "type", "key", "fields", "of"):
        assert attendue in lues, f"{attendue} est lue par le code, elle doit l'être ici"
    # Et l'inverse : une clé que rien ne lit n'y est pas.
    assert "enum" not in lues and "dated_by" not in lues


def test_every_near_miss_points_to_a_key_the_code_actually_reads():
    """Un near-miss vers une clé que plus personne ne lit serait un mauvais conseil.
    La table est donc filtrée par le vocabulaire dérivé, ce que ce test vérifie sur la
    table ENTIÈRE plutôt que sur un exemple."""
    lues = dsv2.interpreted_keys()
    for faute, vraie in dsv2._NEAR_MISS.items():
        assert vraie in lues, (
            f"le near-miss {faute!r} → {vraie!r} pointe une clé que le code ne lit pas")


def test_derivation_failure_stays_silent_rather_than_lying():
    """Si le source n'est pas lisible (déploiement sans .py), on ne signale RIEN
    plutôt que d'accuser toutes les clés du schéma — un avertissement massif et faux
    est pire que pas d'avertissement."""
    from unittest.mock import patch
    with patch.object(dsv2, "interpreted_keys", return_value=frozenset()):
        assert dsv2.unknown_declaration_keys(
            {"fields": [{"key": "s", "enum": ["a"]}]}) == []


# ── les champs imbriqués ─────────────────────────────────────────────────────

def test_nested_fields_are_inspected_too():
    """Une fiche (`type: object`) ou une liste de sous-records cache autant de
    déclarations mortes qu'un champ de tête — et l'erreur y est moins visible."""
    e = _cles({"fields": [
        {"key": "occupant", "type": "object", "fields": [
            {"key": "statut", "type": "text", "enum": ["a", "b"]}]},
        {"key": "contacts", "type": "list", "of": {
            "type": "object", "fields": [{"key": "role_contact", "choices": ["x"]}]}},
    ]})

    assert "occupant.statut" in e
    assert e["occupant.statut"]["near_miss"] == {"enum": "options"}
    assert "contacts[].role_contact" in e
    assert e["contacts[].role_contact"]["near_miss"] == {"choices": "options"}
