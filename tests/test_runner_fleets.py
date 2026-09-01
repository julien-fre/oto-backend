"""Flottes du runner — la cible figée, le périmètre orphelin, et le vide qui se dit.

Trois familles, et chacune vient d'un incident payé pendant la mise au point du
chantier, pas d'une précaution abstraite :

1. **La cible ne se modifie pas.** Rediriger un passage en vol vers un autre
   tableau est exactement le geste que la déclaration existe pour empêcher — et il
   compte double depuis que les bancs d'essai et la production d'un client ne
   vivent plus dans la même org.
2. **Un périmètre sans tableau est refusé**, et le refus le NOMME : un `row_filter`
   seul donne l'illusion d'un passage borné alors qu'il n'a aucune cible à opposer.
3. **Un passage sans travail le DIT.** Des compteurs à zéro se lisent « rien ne
   s'est passé » ; `aucun_travail_rattache` distingue le vide constaté du vide
   supposé — le défaut qui a coûté le plus cher sur ce chantier.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import runner_fleets as RF
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES


def _ctx(sub="alexis", org_id=2):
    return ResolvedCtx(sub=sub, org_id=org_id)


def _appel(ctx, **kw):
    return RF._fleets(ctx, RF.FleetInput(**kw))


# ── la cible est figée à la déclaration ───────────────────────────────────────

def test_update_refuse_de_deplacer_la_cible_et_dit_pourquoi():
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="update", fleet_id=1, namespace="un-autre-tableau")
    assert e.value.code == "target_is_frozen"
    assert "autre flotte" in e.value.message


def test_update_refuse_aussi_de_deplacer_le_seul_perimetre():
    """Le périmètre est la moitié de la cible : le restreindre en vol change ce que
    le passage touche, sans changer le tableau — donc sans que ça se voie."""
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="update", fleet_id=1, row_filter={"lot": "autre"})
    assert e.value.code == "target_is_frozen"


def test_la_cible_ne_figure_pas_parmi_les_champs_modifiables():
    """La garde du haut refuse ; celle-ci vérifie qu'aucun autre chemin ne passe."""
    from oto_mcp import db
    assert "namespace" not in db.CHAMPS_MODIFIABLES
    assert "row_filter" not in db.CHAMPS_MODIFIABLES


# ── un périmètre sans tableau est refusé ──────────────────────────────────────

def test_un_perimetre_sans_tableau_est_refuse_et_le_refus_le_nomme():
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="create", label="essai", procedure="p", tools=["oto_kb"],
               row_filter={"lot": "essai-1"})
    assert e.value.code == "target_incomplete"
    assert "namespace" in e.value.message


def test_une_flotte_sans_cible_du_tout_reste_possible():
    """Toutes les flottes n'écrivent pas dans un tableau — seule l'incohérence
    « périmètre sans cible » est refusée, pas l'absence de cible."""
    inp = RF.FleetInput(op="create", label="veille", procedure="p", tools=["oto_kb"])
    assert inp.namespace is None and inp.row_filter is None


def test_create_exige_le_nom_la_procedure_et_les_outils():
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="create", label="essai")
    assert e.value.code == "missing_fields"
    assert "procedure" in e.value.message and "tools" in e.value.message


# ── le vide se déclare ────────────────────────────────────────────────────────

def test_l_etat_declare_le_vide_au_lieu_de_rendre_des_zeros():
    """`aucun_travail_rattache` est un champ REQUIS du contrat : un lecteur ne peut
    pas confondre « aucun travail » avec « des travaux tous à zéro »."""
    champs = RF.FleetState.model_fields
    assert champs["aucun_travail_rattache"].is_required()
    assert champs["jobs_total"].is_required()
    # les compteurs de détail, eux, ont le droit d'être absents
    assert not champs["pending"].is_required()


def test_l_etat_ne_rend_aucun_cout_en_monnaie():
    """Les tarifs changent et diffèrent par fournisseur : une valeur monétaire
    figée en base devient fausse sans que rien ne le dise. L'état rend des JETONS,
    la conversion appartient à qui lit, avec un tarif daté."""
    champs = set(RF.FleetState.model_fields)
    assert "usage_tokens" in champs
    assert not [c for c in champs if "usd" in c or "cost" in c or "euro" in c]


# ── la capacité est bien servie, et sur les deux faces ────────────────────────

def test_la_capacite_est_servie_sur_les_deux_faces():
    cap = [c for c in CAPABILITIES if c.key == "runner.fleets"]
    assert len(cap) == 1, "la capacité `runner.fleets` doit être enregistrée une fois"
    cap = cap[0]
    assert cap.mcp == "oto_fleet", (
        "une flotte est de la CONFIG utilisateur : elle se pose et se lit en "
        "conversation, contrairement à la file de jobs qui est worker-only")
    assert cap.rest.path == "/api/me/runner/fleets"


def test_les_operations_de_lecture_existent_avant_tout_lancement():
    """L'ordre de construction est une décision : lire d'abord. `state` rend un
    opérateur autonome sans rien pouvoir casser ; lancer écrit dans un fichier
    client et attend que le reste ait tourné."""
    ops = set(RF.FleetInput.model_fields["op"].annotation.__args__)
    assert {"list", "get", "state"} <= ops
    assert "launch" not in ops and "start" not in ops


def test_ni_lancer_ni_arreter_ne_sont_servis_ici():
    """Arrêter est le geste SYMÉTRIQUE de lancer, et il doit sortir par la même
    porte. Les deux faces d'une capacité aboutissent au même handler et doivent se
    comporter pareil : servir `stop` ici le rendrait appelable par un AGENT, qui
    pourrait arrêter le passage qui le fait tourner — ou celui d'un autre."""
    ops = set(RF.FleetInput.model_fields["op"].annotation.__args__)
    assert "stop" not in ops
    # la mécanique existe au niveau de la donnée, pour l'ordonnanceur — c'est la
    # SURFACE agent qui ne la sert pas.
    from oto_mcp import db
    assert callable(db.set_status)


def test_une_flotte_est_org_scopee():
    with pytest.raises(AuthzDenied) as e:
        _appel(ResolvedCtx(sub="alexis", org_id=None), op="list")
    assert e.value.code == "org_required"
