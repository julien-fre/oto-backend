"""Projeter une sortie d'outil sans en perdre le sens (oto-core#36).

83 % du coût d'une conversation d'enrichissement venait des retours d'outils (mesuré le
10/08/2026 : `prompt_tokens=784`, `total=8203`, dont 6 800 de sorties) — et un run a
atteint `finish_reason: error` à 27 % d'occupation de fenêtre. Faute de projection côté
oto, le pilote écrivait une fonction de réduction maison par outil, invisible et refaite
à sa façon par chaque consommateur.

Le piège à éviter en corrigeant est documenté juste à côté : `fr_get` projetait par
allowlist et a laissé tomber `liste_idcc` EN SILENCE (oto-core#37), un champ resté vide
sur 500 lignes. D'où la ligne que ces tests tiennent : **la duplication pure part par
défaut, le détail ne part que sur demande**, et l'enveloppe (crédits, pagination) survit
toujours — sans elle l'agent croit avoir tout vu.
"""
import pytest

from oto_mcp.output_projection import project

SERP = {
    "searchParameters": {"q": "gallimard"},
    "knowledgeGraph": {"title": "Gallimard", "description": "…"},
    "organic": [
        {"title": "Gallimard", "link": "https://gallimard.fr", "snippet": "…",
         "sitelinks": [{"title": "Contact", "link": "…"}], "position": 1},
        {"title": "Wikipédia", "link": "https://fr.wikipedia.org/…", "snippet": "…",
         "position": 2},
    ],
    "relatedSearches": [{"query": "gallimard catalogue"}],
    "credits": 1,
}


def test_untouched_without_instructions():
    """Le brut reste le défaut : l'agent décide, on ne pré-filtre pas à sa place."""
    assert project(SERP) == SERP


def test_dropping_top_level_blocks():
    out = project(SERP, drop=("knowledgeGraph", "relatedSearches", "searchParameters"))
    assert set(out) == {"organic", "credits"}
    assert len(out["organic"]) == 2


def test_dropping_a_key_inside_each_result():
    out = project(SERP, items_path="organic", item_drop=("sitelinks",))
    assert all("sitelinks" not in r for r in out["organic"])
    assert out["organic"][0]["title"] == "Gallimard"


def test_fields_keeps_only_what_was_asked():
    out = project(SERP, items_path="organic", fields=("title", "link"))
    assert out["organic"][0] == {"title": "Gallimard", "link": "https://gallimard.fr"}


def test_the_envelope_survives_a_field_projection():
    """`fields` filtre les RÉSULTATS, jamais l'enveloppe : sans `credits` ni curseur,
    l'agent ne sait plus ce qu'il a consommé ni s'il reste des pages."""
    out = project(SERP, items_path="organic", fields=("title",))
    assert out["credits"] == 1
    assert "knowledgeGraph" in out


def test_a_nested_path_is_reachable():
    """Hunter range ses adresses sous `data.emails` — la profondeur ne doit pas
    obliger à écrire une réduction par connecteur."""
    payload = {"data": {"domain": "x.fr", "emails": [
        {"value": "a@x.fr", "sources": [1, 2, 3], "verification": {"status": "ok"}}]},
        "meta": {"results": 1}}
    out = project(payload, items_path="data.emails",
                  item_drop=("sources", "verification"))
    assert out["data"]["emails"] == [{"value": "a@x.fr"}]
    assert out["data"]["domain"] == "x.fr" and out["meta"] == {"results": 1}


def test_the_input_is_never_mutated():
    """Un payload peut venir d'un CACHE (les fiches société Unipile y vivent 6 h) :
    le muter empoisonnerait toutes les lectures suivantes."""
    payload = {"data": {"emails": [{"value": "a@x.fr", "sources": [1]}]}}
    project(payload, items_path="data.emails", item_drop=("sources",))
    assert payload["data"]["emails"][0]["sources"] == [1]


@pytest.mark.parametrize("payload", [
    {"organic": "pas une liste"},          # l'amont a changé de forme
    {"autre_chose": [1, 2]},               # le chemin ne mène nulle part
    {"data": None},                        # un niveau intermédiaire nul
    [],                                    # même pas un dict
    "texte",
])
def test_an_unexpected_shape_passes_through(payload):
    """Une API tierce change de forme sans prévenir. Une projection qui lève
    transformerait une réponse utile en panne — et le connecteur porterait le blâme."""
    assert project(payload, items_path="data.emails", item_drop=("x",)) is not None


def test_a_non_dict_row_is_left_alone():
    out = project({"organic": ["brut", {"title": "t", "x": 1}]},
                  items_path="organic", item_drop=("x",))
    assert out["organic"] == ["brut", {"title": "t"}]
