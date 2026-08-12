"""Le bloc `finances` ne doit jamais se lire comme des euros sûrs (#399).

L'amont (API Recherche Entreprises) sert `{ca, resultat_net}` sans unité et code
l'absence par un 0. Les deux manques sont invisibles : le nombre reste plausible pour
une petite structure, donc un consommateur affiche « 392 287 € » pour une banque
régionale à 392 M€ sans que rien ne cloche.

Valeurs de référence relevées sur l'API le 12/08/2026 — elles ancrent les tests sur
du réel, pas sur des cas inventés.
"""
from __future__ import annotations

import pytest

from oto_mcp import fr_finances


def _last(annotated):
    return annotated[sorted(annotated)[-1]]


# --- ce qui n'est pas une donnée est retiré ------------------------------------

def test_zero_is_absence_not_a_null_turnover():
    """NORAUTO FRANCE : `ca: 0` chez l'amont, 974 718 176 € au dépôt 2023.

    Rendre le 0 tel quel fait afficher « 0 € » pour une entreprise qui pèse presque
    un milliard — l'erreur la plus grave du lot, parce qu'elle est muette."""
    ann, avert = fr_finances.annotate(
        {"2024": {"ca": 0, "resultat_net": 37748283}}, "52")
    assert _last(ann)["ca"] is None
    assert _last(ann)["alerte"] == ["ca_non_declare"]
    assert avert


def test_negative_turnover_is_unreadable_but_the_raw_value_survives():
    """SAFRAN NACELLES : ca = -1 002 180 648. On ne sait pas le lire, mais on ne
    jette pas ce que l'amont a dit — l'appelant doit pouvoir constater."""
    ann, _ = fr_finances.annotate(
        {"2024": {"ca": -1002180648, "resultat_net": 36026000}}, "51")
    assert _last(ann)["ca"] is None
    assert _last(ann)["ca_valeur_amont"] == -1002180648
    assert _last(ann)["alerte"] == ["ca_valeur_aberrante"]


# --- ce qui est réel mais illisible est marqué, jamais corrigé -----------------

def test_implausible_turnover_keeps_its_value_and_shows_the_ratio():
    """BANQUE POPULAIRE VAL DE FRANCE : 392 287 pour 2 000-4 999 salariés.

    Le montant est réel (c'est un dépôt en milliers), donc on le garde : convertir
    serait deviner, et une conversion fausse est indétectable en aval. On rend le
    ratio qui a déclenché l'alerte pour que l'appelant juge lui-même."""
    ann, avert = fr_finances.annotate(
        {"2024": {"ca": 392287, "resultat_net": 82684}}, "51")
    assert _last(ann)["ca"] == 392287, "on ne convertit RIEN"
    assert _last(ann)["ca_par_salarie"] == 196
    assert _last(ann)["alerte"] == ["ca_invraisemblable"]
    assert avert


def test_the_alert_never_asserts_the_cause():
    """L'étiquette dit « invraisemblable », pas « en milliers ».

    Mesuré sur 311 entreprises de 50+ salariés : les cas marqués étaient une
    association vivant de subventions et une holding portant les salariés d'un
    groupe — pas des erreurs d'unité. Affirmer la cause serait l'inventer."""
    assert "unite_suspecte" not in fr_finances.AVERTISSEMENT
    for cause in ("milliers", "subventions", "holding"):
        assert cause in fr_finances.AVERTISSEMENT, (
            f"l'avertissement doit citer « {cause} » comme cause POSSIBLE")
    assert "NON établie" in fr_finances.AVERTISSEMENT


# --- une fiche saine ne doit pas être alourdie --------------------------------

@pytest.mark.parametrize("ca,tranche,label", [
    (5570764860, "53", "Michelin : 557 076 €/salarié"),
    (733914, "NN", "petite boulangerie, effectif non renseigné"),
    (84937663, "12", "ALIAPUR, 20-49 salariés"),
])
def test_healthy_filings_pass_through_untouched(ca, tranche, label):
    src = {"2024": {"ca": ca, "resultat_net": 1}}
    ann, avert = fr_finances.annotate(src, tranche)
    assert ann == src, label
    assert avert is None, f"pas d'avertissement sur une fiche saine ({label})"


def test_unknown_headcount_disables_the_plausibility_check():
    """Sans effectif, le ratio n'existe pas : on se tait plutôt que de supposer.

    La moitié des établissements n'ont pas de tranche renseignée — inventer un
    plancher ferait des faux positifs en masse."""
    ann, avert = fr_finances.annotate({"2024": {"ca": 1}}, None)
    assert ann == {"2024": {"ca": 1}}
    assert avert is None


# --- robustesse de forme -------------------------------------------------------

@pytest.mark.parametrize("finances", [None, {}, "pas un dict", []])
def test_absent_or_malformed_block_is_returned_as_is(finances):
    assert fr_finances.annotate(finances, "51") == (finances, None)


def test_year_entries_that_are_not_blocks_survive():
    """L'amont peut changer de forme ; on ne casse pas la fiche pour autant."""
    ann, avert = fr_finances.annotate({"2024": "inattendu"}, "51")
    assert ann == {"2024": "inattendu"}
    assert avert is None


def test_the_source_block_is_not_mutated():
    """`annotate` travaille sur une copie : l'appelant garde le brut de l'amont."""
    src = {"2024": {"ca": 0, "resultat_net": 5}}
    fr_finances.annotate(src, "51")
    assert src == {"2024": {"ca": 0, "resultat_net": 5}}


def test_every_year_is_examined_not_only_the_latest():
    """Une entreprise peut être saine une année et illisible la suivante — c'est
    exactement le cas Michelin (euros jusqu'en 2018, milliers ensuite)."""
    ann, avert = fr_finances.annotate(
        {"2018": {"ca": 5500000000}, "2024": {"ca": 5513153}}, "53")
    assert "alerte" not in ann["2018"]
    assert ann["2024"]["alerte"] == ["ca_invraisemblable"]
    assert avert


# --- le filtre ----------------------------------------------------------------

def test_the_filter_warning_states_what_was_measured():
    """`ca_min`/`ca_max` filtrent en amont sur ce même nombre : l'avertissement doit
    porter le constat, pas une précaution vague."""
    txt = fr_finances.FILTRE_CA_AVERTISSEMENT
    assert "ca_max=400000" in txt and "12" in txt
    assert "tranche_effectif_salarie" in txt, "il faut dire par quoi remplacer"


# --- câblage : l'annotation doit ATTEINDRE l'appelant ---------------------------
# Les tests ci-dessus prouvent que le module est juste ; ceux-ci prouvent qu'il est
# BRANCHÉ. La projection `_compact_identity` ne garde que des clés connues — une
# annotation posée au mauvais endroit serait silencieusement perdue (le piège est
# déjà commenté dans `fr_search` pour `matched_by`).

class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco(a[0]) if a and callable(a[0]) else deco


# Une banque : montant réel en milliers, effectif 2 000-4 999 → invraisemblable.
_BANQUE = {
    "siren": "549800373", "nom_complet": "BANQUE POPULAIRE VAL DE FRANCE",
    "tranche_effectif_salarie": "51", "categorie_entreprise": "GE",
    "finances": {"2024": {"ca": 392287, "resultat_net": 82684}},
    "siege": {}, "dirigeants": [], "matching_etablissements": [],
}


@pytest.fixture()
def fr_tools(monkeypatch):
    from oto_mcp import fod_fr
    from oto_mcp.tools import fr

    class _Entreprises:
        def search(self, **kw):
            return {"results": [dict(_BANQUE)], "total_results": 1}

        def get_by_siren(self, siren):
            return dict(_BANQUE)

    class _Inpi:
        def list_exercises(self, siren):
            return []

    class _Bodacc:
        def search_by_siren(self, siren, *a):
            return {"results": [], "total_count": 0}

    monkeypatch.setattr(fod_fr, "entreprises", _Entreprises())
    monkeypatch.setattr(fod_fr, "inpi", _Inpi())
    monkeypatch.setattr(fod_fr, "bodacc", _Bodacc())
    reg = _Reg()
    fr.register(reg)
    return reg.tools


def test_fr_search_surfaces_the_alert_and_the_warning(fr_tools):
    out = fr_tools["fr_search"](query="banque")
    hit = out["results"][0]
    assert hit["finances"]["2024"]["alerte"] == ["ca_invraisemblable"]
    assert hit["finances"]["2024"]["ca_par_salarie"] == 196
    assert "finances_avertissement" in hit, (
        "l'avertissement doit survivre à la projection _compact_identity")


def test_fr_get_surfaces_the_alert(fr_tools):
    out = fr_tools["fr_get"](siren="549800373")
    ident = out["identity"]
    assert ident["finances"]["2024"]["alerte"] == ["ca_invraisemblable"]
    assert "finances_avertissement" in ident


def test_the_filter_warning_appears_only_when_the_filter_is_used(fr_tools):
    assert "filtre_ca_avertissement" not in fr_tools["fr_search"](query="banque")
    for kw in ({"ca_max": 400000}, {"ca_min": 1}, {"ca_min": 0}, {"ca_max": 0}):
        out = fr_tools["fr_search"](query="banque", **kw)
        assert "filtre_ca_avertissement" in out, (
            f"{kw} filtre sur le CA → l'appelant doit être averti")
