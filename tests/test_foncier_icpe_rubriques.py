"""Une fiche ICPE sans ses rubriques ne dit pas ce que le site CONSOMME.

La fiche Géorisques porte `rubriques` — numéro de nomenclature, nature, régime,
quantité autorisée — et le compacteur les jetait toutes : la whitelist `_ICPE_KEEP`
ne gardait que l'identité, le régime global et les inspections. Or c'est la rubrique
qui distingue un entrepôt de pneus d'une chaufferie de 12 MW, et c'est elle qu'on
cherche quand la consommation réseau manque (secret statistique, site HTB).

Deux exigences, mesurées le 11/09/2026 sur la fiche réelle d'une commune du Nord
(33 installations, rubriques 2663 « stockage de pneumatiques » et 2795 « lavage ») :
les rubriques sortent, et celles qui portent un signal énergétique ne se font pas
manger par la troncature d'une fiche longue.
"""
from __future__ import annotations

from oto_mcp.tools.foncier import _ICPE_MAX_RUBRIQUES, _compact_rubriques


def _rubrique(numero: str, nature: str = "peu importe") -> dict:
    return {
        "numeroRubrique": numero,
        "nature": nature,
        "regimeAutoriseAlinea": "Déclaration",
        "quantiteTotale": "1.000",
        "unite": "m3",
    }


def test_les_rubriques_sortent_avec_leur_quantite_et_leur_unite():
    """Sans la quantité et son unité, « rubrique 2910 » ne se compare à rien."""
    rubriques, _, _ = _compact_rubriques([_rubrique("2663", "Stockage de pneumatiques")])
    assert rubriques == [
        {
            "numero": "2663",
            "nature": "Stockage de pneumatiques",
            "regime": "Déclaration",
            "quantite": "1.000",
            "unite": "m3",
        }
    ]


def test_une_rubrique_energetique_est_LUE_pas_seulement_rendue():
    """Le numéro seul suppose que l'appelant connaît la nomenclature ICPE."""
    _, energie, _ = _compact_rubriques([_rubrique("2910", "Combustion")])
    assert energie[0]["numero"] == "2910"
    assert energie[0]["lecture"] == "combustion (boilers, engines)"


def test_une_rubrique_sans_signal_energetique_n_est_pas_interpretee():
    """Ranger un stockage de pneus en « énergie » inventerait le signal cherché."""
    _, energie, _ = _compact_rubriques([_rubrique("2663")])
    assert energie == []


def test_la_troncature_ne_mange_pas_la_rubrique_qui_porte_le_signal():
    """Le cas qui motive tout : une fiche de gros site en porte des dizaines.
    Si 2910 arrive en dernier dans la réponse amont, un plafond naïf la supprime
    — et la fiche rendue dit « pas de combustion » là où il y en a une."""
    brutes = [_rubrique(str(4000 + i)) for i in range(_ICPE_MAX_RUBRIQUES + 5)]
    brutes.append(_rubrique("2910", "Combustion"))
    rubriques, energie, tronquees = _compact_rubriques(brutes)

    assert tronquees is True, "la coupe doit se dire"
    assert len(rubriques) == _ICPE_MAX_RUBRIQUES
    assert rubriques[0]["numero"] == "2910", "le signal passe devant, pas à la trappe"
    assert energie[0]["numero"] == "2910"


def test_une_fiche_sans_rubrique_rend_une_liste_vide_pas_une_erreur():
    """L'API ne garantit pas le champ ; l'absence se rend, elle ne casse pas."""
    assert _compact_rubriques([]) == ([], [], False)
