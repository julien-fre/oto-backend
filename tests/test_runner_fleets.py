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

import inspect

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
    assert champs["no_jobs_attached"].is_required()
    assert champs["jobs_total"].is_required()
    # les compteurs de détail, eux, ont le droit d'être absents
    assert not champs["pending"].is_required()


def test_aucun_modele_servi_ne_porte_de_monnaie():
    """Les tarifs changent et diffèrent par fournisseur : une valeur monétaire figée
    en base devient fausse sans que rien ne le dise. Et un NUMERIC servi tel quel
    n'est même pas sérialisable en JSON — la flotte serait illisible dès qu'elle
    porte une borne.

    ⚠️ Ce test a d'abord porté sur `FleetState` SEUL, qui était propre : il passait
    au vert pendant que `Fleet`, l'autre modèle du même fichier, servait un
    `max_cost_usd`. Un test qui vise le bon principe et le mauvais objet est vert et
    inutile. Il balaie maintenant TOUS les modèles servis."""
    monnaie = ("usd", "cost", "euro", "eur", "price", "prix")
    for modele in (RF.Fleet, RF.FleetState, RF.FleetInput, RF.FleetOut):
        fautifs = [c for c in modele.model_fields if any(m in c for m in monnaie)]
        assert not fautifs, f"{modele.__name__} porte de la monnaie : {fautifs}"
    assert "usage_tokens" in RF.FleetState.model_fields
    assert "max_tokens" in RF.Fleet.model_fields


def test_le_contexte_dexecution_est_fige_comme_la_cible():
    """Changer le modèle en vol rend FAUSSE l'attribution des lignes déjà écrites
    sous le passage — exactement l'argument qui gèle la cible."""
    for champ, valeur in (("provider", "openai"), ("model", "un-autre-modele")):
        with pytest.raises(AuthzDenied) as e:
            _appel(_ctx(), op="update", fleet_id=1, **{champ: valeur})
        assert e.value.code == "context_is_frozen"
    from oto_mcp import db
    assert "provider" not in db.CHAMPS_MODIFIABLES
    assert "model" not in db.CHAMPS_MODIFIABLES


def test_status_ne_se_pose_pas_par_update_et_le_refus_le_dit():
    """`status` figure dans l'entrée parce qu'il FILTRE `list`. Le laisser tomber en
    silence rendrait 200 avec la flotte inchangée — et c'est précisément ce qu'un
    agent privé de `stop` tenterait, en lisant un succès dans la réponse."""
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="update", fleet_id=1, status="stopped")
    assert e.value.code == "status_not_settable"
    from oto_mcp import db
    assert "status" not in db.CHAMPS_MODIFIABLES


# ── la capacité est bien servie, et sur les deux faces ────────────────────────

def test_la_capacite_est_servie_sur_les_deux_faces():
    cap = [c for c in CAPABILITIES if c.key == "runner.fleets"]
    assert len(cap) == 1, "la capacité `runner.fleets` doit être enregistrée une fois"
    cap = cap[0]
    assert cap.mcp == "oto_fleet", (
        "une flotte est de la CONFIG utilisateur : elle se pose et se lit en "
        "conversation, contrairement à la file de jobs qui est worker-only")
    assert cap.rest.path == "/api/me/runner/fleets"


def test_lancer_et_arreter_sont_servis_avec_des_PLANCHERS_DIFFÉRENTS():
    """La décision du 01/09/2026, et elle remplace celle que ce fichier gravait.

    Ces tests disaient « ni lancer ni arrêter ne sont servis ici — ils entreront
    ensemble, quand la question de qui a le droit sera tranchée ». Elle l'est, et
    la réponse **casse la symétrie** que je supposais :

        lancer   effets externes IRRÉVERSIBLES (argent dépensé, lignes écrites
                 chez un tiers)          ⟹ plancher ADMIN
        arrêter  une interruption et un travail à reprendre
                 ⟹ tout MEMBRE — un passage qui part en vrille doit pouvoir
                   être stoppé par la première personne qui le voit

    ⚠️ Ils entrent bien par la même porte, mais **ils n'engagent pas la même
    chose** : la garde suit le VERBE, pas l'objet. Attendre un admin pendant
    qu'une flotte dépense est le mauvais échange.
    """
    ops = set(RF.FleetInput.model_fields["op"].annotation.__args__)
    assert {"list", "get", "state", "launch", "stop"} <= ops
    # le plancher d'`ORG_MEMBER` reste celui de la capacité : c'est `launch` qui
    # exige davantage, DANS le handler — le vérifier ici garde la trace que la
    # différence est voulue et non un oubli d'autz.
    src = inspect.getsource(RF._fleets)
    assert "is_org_admin" in src, "`launch` doit exiger l'admin"
    assert src.index("op == \"launch\"") < src.index("is_org_admin"), (
        "la vérification admin appartient à la branche `launch`, pas à toute la "
        "capacité — sinon `stop` deviendrait admin par effet de bord")


def test_un_deroule_ne_lance_pas_et_n_arrete_pas_LE_SIEN():
    """Les deux gardes anti-agent, et elles DIFFÈRENT parce que le coût diffère.

    ⚠️ Un agent qui se RELANCE lui-même dépense en boucle ; un agent qui arrête
    de trop coûte une reprise. D'où : aucun déroulé ne lance, mais un déroulé peut
    arrêter une AUTRE flotte de son org — c'est même le cas utile, un opérateur
    qui pilote par la conversation. **Fermer le verbe à tout le monde pour le
    seul cas dangereux ferait payer le prix sur tous les usages légitimes.**
    """
    src = inspect.getsource(RF._fleets)
    assert "not_from_a_run" in src, "un déroulé ne lance pas"
    assert "not_your_own_fleet" in src, "un déroulé n'arrête pas celle qui l'exécute"
    from oto_mcp import db
    assert callable(db.run_appartient_a_flotte), (
        "la garde repose sur un PRÉDICAT — « ce déroulé tourne-t-il pour CETTE "
        "flotte ? » — et non sur la seule présence d'un run")


def test_lancer_et_arreter_posent_une_INTENTION_jamais_un_fait():
    """⚠️ Le défaut que ce lot a failli commettre, après une journée passée sur
    « trois états, jamais deux » — la leçon ne s'est pas reconnue sur cet objet.

    `launch` pose `armed` (on a DEMANDÉ), pas `running` (un ordonnanceur l'a
    PRISE). `stop` pose `stopping` (l'ordre est posé), pas `stopped` (la boucle a
    accusé réception). Entre l'appel et la lecture, **le passage continue de
    dépenser** : annoncer un arrêt qui n'a pas eu lieu est pire qu'annoncer un
    lancement qui ne part pas.
    """
    src = inspect.getsource(RF._fleets)
    assert "db.armer(" in src and "'running'" not in src.split("op == \"launch\"")[1][:900]
    assert "db.demander_arret(" in src


def test_une_flotte_est_org_scopee():
    with pytest.raises(AuthzDenied) as e:
        _appel(ResolvedCtx(sub="alexis", org_id=None), op="list")
    assert e.value.code == "org_required"
