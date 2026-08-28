"""La projection agent peut réduire les DONNÉES, jamais les AVERTISSEMENTS.

Depuis le 13/08 les annotations du bloc `finances` et des bilans sont posées par
**FOD** — elles sont vraies quel que soit le consommateur, donc elles vivent au seul
point que tous traversent (ADR 0028 amendée : « FOD dit ce qu'il SAIT, jamais ce
qu'il CROIT »). Le backend ne détecte plus rien ; il **fait passer**.

Or il projette : `_compact_identity` et `_LATEST_BILAN_KEYS` ne gardent que des clés
CONNUES, pour tenir le payload sous le budget de contexte d'un LLM. Une clé neuve y
disparaît **en silence** — et un avertissement disparu rend au consommateur exactement
l'ambiguïté que FOD venait de lever : `chiffre_d_affaires: None` accompagné de
`valeur_indisponible` dit « un montant existe, illisible » ; le même `None` tout seul
dit « pas de dépôt ».

D'où la règle que ces tests figent : **tronquer les dirigeants oui, dropper une alerte
jamais.** C'est le seul invariant qui empêche le doublon de vérité de se reformer par
omission.
"""
from __future__ import annotations

import pytest

# Charges utiles telles que FOD les sert — relevées sur `data.oto.zone` le 13/08/2026.
_NORAUTO = {
    "siren": "480470152", "nom_complet": "NORAUTO FRANCE",
    "tranche_effectif_salarie": "52", "categorie_entreprise": "GE",
    "finances": {"2024": {"ca": None, "resultat_net": 37748283,
                          "alerte": ["non_declare"]}},
    "finances_avertissement": "Bloc `finances` : l'amont ne transmet PAS l'unité…",
    "siege": {}, "dirigeants": [], "matching_etablissements": [],
}

# Michelin 2024 : le dépôt porte un montant que l'INT32 du parquet ne peut pas tenir.
_MICHELIN_EXERCICES = [
    {"date_cloture_exercice": "2024-12-31", "type_bilan": "C",
     "confidentiality": "Public", "chiffre_d_affaires": None,
     "alerte": ["valeur_indisponible", "saturation_probable"],
     "postes_indisponibles": ["0G", "0N", "1A", "AR"]},
    {"date_cloture_exercice": "2022-12-31", "type_bilan": "C",
     "confidentiality": "Public", "chiffre_d_affaires": 5513153},
]


class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco(a[0]) if a and callable(a[0]) else deco


@pytest.fixture()
def register_with(monkeypatch):
    """Fabrique : identité servie par l'amont → tools enregistrés.

    ⚠️ `fr.register()` CAPTURE `fod_fr.entreprises` dans une fermeture — patcher
    après l'enregistrement n'a aucun effet. Le stub doit donc être posé AVANT.
    """
    from oto_mcp.fod import fr as fod_fr
    from oto_mcp.tools import fr

    def _make(identity):
        class _E:
            def search(self, **kw):
                return {"results": [dict(identity)], "total_results": 1}

            def get_by_siren(self, siren):
                return dict(identity)

        class _Inpi:
            def list_exercises(self, siren):
                return [dict(e) for e in _MICHELIN_EXERCICES]

            def get_bilan(self, siren, date_cloture):
                return dict(_MICHELIN_EXERCICES[0], liasse={"FL": 1})

        class _Bodacc:
            def search_by_siren(self, siren, *a):
                return {"results": [], "total_count": 0}

        monkeypatch.setattr(fod_fr, "entreprises", _E())
        monkeypatch.setattr(fod_fr, "inpi", _Inpi())
        monkeypatch.setattr(fod_fr, "bodacc", _Bodacc())
        reg = _Reg()
        fr.register(reg)
        return reg.tools

    return _make


@pytest.fixture()
def fr_tools(register_with):
    return register_with(_NORAUTO)


# --- l'invariant ---------------------------------------------------------------

def test_the_sibling_warning_survives_the_identity_projection(fr_tools):
    """`finances_avertissement` est un FRÈRE de `finances` : hors de la liste des
    clés gardées, il disparaît sans bruit."""
    hit = fr_tools["fr_search"](query="norauto")["results"][0]
    assert hit["finances_avertissement"]


def test_the_nested_alert_survives_too(fr_tools):
    """`alerte` voyage DANS `finances`, donc il suit sa clé parente — figé pour que
    quiconque remplacerait le passthrough par une re-projection du bloc le casse."""
    hit = fr_tools["fr_get"](siren="480470152")["identity"]
    assert hit["finances"]["2024"]["alerte"] == ["non_declare"]
    assert hit["finances"]["2024"]["ca"] is None


def test_bilan_warnings_survive_their_own_projection(fr_tools):
    """`latest_bilan` est projeté sur `_LATEST_BILAN_KEYS` : sans `alerte` et
    `postes_indisponibles` dedans, Michelin ressort avec un CA `None` nu —
    indistinguable d'une entreprise qui n'a jamais déposé."""
    out = fr_tools["fr_get"](siren="855200507")
    bilan = out["latest_bilan"]
    assert bilan["chiffre_d_affaires"] is None
    assert bilan["alerte"] == ["valeur_indisponible", "saturation_probable"]
    assert bilan["postes_indisponibles"] == ["0G", "0N", "1A", "AR"]


def test_data_may_still_be_reduced(register_with):
    """La contrepartie : la projection garde son travail. Réduire les données reste
    permis — c'est l'avertissement, et lui seul, qui est intouchable."""
    bavard = dict(_NORAUTO, est_bio=True, est_qualiopi=False,
                  matching_etablissements=[{"siret": f"4804701520{i:04}"} for i in range(40)])
    hit = register_with(bavard)["fr_search"](query="norauto")["results"][0]
    assert "est_bio" not in hit and "est_qualiopi" not in hit, "booléens d'annuaire écartés"
    assert len(hit["etablissements"]) == 25, "établissements tronqués"
    assert hit["_etablissements_truncated"] == 40, "et la troncature est DÉCLARÉE"
    assert hit["finances_avertissement"], "…mais l'avertissement, jamais"


# --- le backend ne détecte plus ------------------------------------------------

def test_the_backend_no_longer_annotates(fr_tools):
    """Deux détections produiraient deux vocabulaires (`ca_non_declare` ici,
    `non_declare` chez FOD) qui divergeraient au premier changement. Le module local
    a été supprimé ; ce test échoue si quelqu'un le réintroduit."""
    import importlib
    with pytest.raises(ImportError):
        importlib.import_module("oto_mcp.fr_finances")


def test_a_healthy_record_is_not_burdened(register_with):
    """Une fiche sans annotation traverse telle quelle — le backend n'ajoute rien.

    C'est la contrepartie du retrait de la détection locale : là où l'ancien module
    aurait recalculé (et marqué `ca_invraisemblable` sur un montant que FOD juge
    sain), il n'y a plus qu'un passage."""
    sain = dict(_NORAUTO, finances={"2024": {"ca": 5570764860, "resultat_net": -1}})
    sain.pop("finances_avertissement")
    hit = register_with(sain)["fr_search"](query="michelin")["results"][0]
    assert hit["finances"] == {"2024": {"ca": 5570764860, "resultat_net": -1}}
    assert "finances_avertissement" not in hit


# --- ce qui reste propre à la surface agent ------------------------------------

def test_the_filter_warning_stays_here(fr_tools):
    """`ca_min`/`ca_max` sont des paramètres de CE tool : leur avertissement n'a pas
    de sens chez FOD, qui ne les expose pas sous cette forme."""
    assert "filtre_ca_avertissement" not in fr_tools["fr_search"](query="x")
    out = fr_tools["fr_search"](query="x", ca_max=400000)
    assert "ca_max=400000" in out["filtre_ca_avertissement"]
    assert "tranche_effectif_salarie" in out["filtre_ca_avertissement"]
