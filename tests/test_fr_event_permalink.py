"""#341 — le permalien officiel BODACC survit à la projection d'événements de `fr_get`.

`url_complete` (le lien bodacc.fr servi par le producteur DILA, dossier liens #335 :
pleine confiance, à recopier jamais reconstruire) traversait jusqu'à `fr_events`
mais était MANGÉ par l'allowlist `_EVENT_KEEP` de `fr_get` : l'agent qui lit
`recent_events` concluait « pas de lien » alors que la source le sert. La classe
« projection qui ment par omission » (ADR 0028) — une allowlist perd en silence
les champs utiles ajoutés en amont.

Même banc que test_fr_liste_idcc : le VRAI `fr_get` (proxies FOD stubés), pas une
réplique de sa logique qui passerait même si la projection restait cassée.
"""
from __future__ import annotations

import pytest


class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        if a and callable(a[0]):
            return deco(a[0])
        return deco


# Un événement amont RÉEL (relevé par l'appel, société témoin) : le permalien y
# cohabite avec la plomberie DILA et la géo d'annonce que la projection écarte.
_EVENT = {
    "id": "B202601212014",
    "dateparution": "2026-06-28",
    "familleavis": "modification",
    "familleavis_lib": "Modifications diverses",
    "typeavis": "annonce",
    "typeavis_lib": "Avis initial",
    "tribunal": "Greffe du Tribunal des Activités Economiques de Paris",
    "commercant": "DANONE",
    "registre": ["552032534", "552 032 534"],
    "url_complete": "https://www.bodacc.fr/pages/annonces-commerciales-detail/"
                    "?q.id=id:B202601212014",
    # Le descriptif de CE qui a changé — le pendant de `jugement`, que
    # l'allowlist gardait déjà pour les procédures (arbitrage #341 : ajouté).
    "modificationsgenerales": {
        "descriptif": "modification survenue sur le capital (augmentation)"},
    # bruit DILA / géo — la projection existe pour ça, elle doit continuer :
    "numeroannonce": 2014, "parution": "20260121", "publicationavis": "B",
    "ispdf_unitaire": "oui", "pdf_parution_subfolder": 1,
    "ville": "Paris", "cp": "75009", "numerodepartement": "75",
    "region_nom_officiel": "Île-de-France",
}

# Un événement SANS permalien en amont — la clé ne doit pas apparaître à null.
_EVENT_SANS_LIEN = {k: v for k, v in _EVENT.items() if k != "url_complete"} | {
    "id": "B202601212015"}

# Un dépôt de comptes (famille dpc) : son contenu est le champ `depot`.
_EVENT_DPC = {
    "id": "A202600450001", "dateparution": "2026-05-12", "familleavis": "dpc",
    "familleavis_lib": "Dépôts des comptes", "typeavis": "annonce",
    "commercant": "DANONE", "registre": ["552032534", "552 032 534"],
    "depot": {"typeDepot": "Comptes annuels et rapports",
              "dateCloture": "2025-12-31"},
    "url_complete": "https://www.bodacc.fr/pages/annonces-commerciales-detail/"
                    "?q.id=id:A202600450001",
    "ville": "Paris", "numeroannonce": 1,
}


class _Entreprises:
    def __init__(self, *a, **k): ...

    def get_by_siren(self, siren):
        return {"siren": siren, "nom_complet": "DANONE"}


class _Inpi:
    def __init__(self, *a, **k): ...

    def list_exercises(self, siren):
        return []


class _Bodacc:
    def __init__(self, *a, **k): ...

    def search_by_siren(self, siren, famille, limit):
        return {"results": [_EVENT, _EVENT_SANS_LIEN, _EVENT_DPC],
                "total_count": 3}


class _Noop:
    def __init__(self, *a, **k): ...


@pytest.fixture()
def fr_get(monkeypatch):
    monkeypatch.setattr("oto_mcp.fod_fr.entreprises", _Entreprises())
    monkeypatch.setattr("oto_mcp.fod_fr.inpi", _Inpi())
    monkeypatch.setattr("oto_mcp.fod_fr.bodacc", _Bodacc())
    monkeypatch.setattr("oto_mcp.fod_fr.egapro", _Noop())
    monkeypatch.setattr("oto.tools.sirene.SireneClient", _Noop)
    from oto_mcp.tools import fr
    reg = _Reg()
    fr.register(reg)
    return reg.tools["fr_get"]


def test_le_permalien_traverse(fr_get):
    ev = fr_get(siren="552032534")["recent_events"][0]
    assert ev["url_complete"] == _EVENT["url_complete"], \
        "l'événement porte son lien officiel en amont : fr_get doit le rendre (#341)"


def test_le_bruit_reste_dehors(fr_get):
    """Le correctif ouvre UN champ, pas les vannes : la plomberie DILA et la géo
    d'annonce restent projetées — c'est la moitié légitime de l'allowlist."""
    ev = fr_get(siren="552032534")["recent_events"][0]
    for bruit in ("numeroannonce", "ispdf_unitaire", "pdf_parution_subfolder",
                  "ville", "cp", "region_nom_officiel"):
        assert bruit not in ev


def test_pas_de_cle_a_null_sans_lien_amont(fr_get):
    ev = fr_get(siren="552032534")["recent_events"][1]
    assert "url_complete" not in ev, \
        "une clé à null laisserait croire que la source n'a pas de lien"


def test_le_descriptif_de_modification_traverse(fr_get):
    """Le pendant de `jugement` pour la famille modification : sans lui, un avis
    « Modifications diverses » ne dit littéralement rien (arbitrage #341)."""
    ev = fr_get(siren="552032534")["recent_events"][0]
    assert ev["modificationsgenerales"]["descriptif"] == \
        "modification survenue sur le capital (augmentation)"


def test_le_contenu_du_depot_de_comptes_traverse(fr_get):
    ev = fr_get(siren="552032534")["recent_events"][2]
    assert ev["depot"]["typeDepot"] == "Comptes annuels et rapports"
    assert ev["url_complete"], "le permalien vaut pour toutes les familles d'avis"
