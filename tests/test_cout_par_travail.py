"""Le COÛT d'un travail — le tarif, la provenance, et ce qu'un total avoue.

Un compteur de coût faux ressemble exactement à un compteur juste : il rend un
nombre. Ces bancs portent donc sur les trois façons dont il peut mentir sans
qu'on le voie —

1. **tarifer faux** : un modèle inconnu qui vaudrait zéro, un cache compté au
   tarif d'entrée, un prix lu à la lecture qui réécrirait le passé ;
2. **attribuer faux** : un travail de webhook compté comme programmé, une
   dépense de l'org portée à la plateforme ;
3. **taire un trou** : un total amputé d'un travail mort qui se présente comme un
   total. C'est le plus grave, parce que c'est le seul qui se propage jusqu'à une
   facture sans jamais lever.
"""
from __future__ import annotations

import datetime

import pytest

from oto_mcp import runner_prix
from oto_mcp.db import runner_job_cost as RJC


# ── 1. le TARIF ───────────────────────────────────────────────────────────────

def test_les_quatre_postes_se_derivent_des_deux_publies():
    """Les fournisseurs publient deux prix et expriment le cache en multiples de
    l'entrée. Les dériver rend un changement de prix ponctuel — une ligne — au
    lieu de quatre occasions de se tromper."""
    _, table = runner_prix.bareme_en_vigueur()
    opus = table["claude-opus-5"]
    assert (opus.entree, opus.sortie) == (5_000, 25_000)
    assert opus.ecriture_cache == 6_250, "écrire coûte 1,25× l'entrée"
    assert opus.lecture_cache == 500, "lire coûte 0,1× l'entrée"


def test_le_cache_n_est_PAS_tarife_comme_l_entree():
    """⚠️ Le défaut le plus coûteux serait invisible : un déroulé bien caché
    paierait dix fois son prix réel sur son poste de lecture."""
    plein, _ = runner_prix.cout_nano_usd("claude-opus-5", entree=1_000_000)
    cache, _ = runner_prix.cout_nano_usd("claude-opus-5", lecture_cache=1_000_000)
    assert plein == 5 * 10**9, "1 M de jetons d'entrée Opus = 5 $"
    assert cache == plein // 10


def test_un_modele_INCONNU_ne_vaut_pas_zero():
    """`0` se lirait « ce déroulé n'a rien coûté » et se propagerait dans toutes
    les sommes. `None` se lit « non tarifé » et oblige l'écran à le dire."""
    montant, bareme = runner_prix.cout_nano_usd("un-modele-inedit", entree=10**6)
    assert montant is None and bareme is None


def test_un_modele_ABSENT_ne_vaut_pas_zero_non_plus():
    """Un travail sans modèle déclaré a tourné sur celui du worker, que nous ne
    connaissons pas."""
    assert runner_prix.cout_nano_usd(None, entree=10**6) == (None, None)


def test_aucun_compte_mesure_ne_vaut_pas_zero():
    """Un travail mort avant de rien rendre : ses quatre postes sont NULL. Le
    tarifer à zéro le ferait passer pour gratuit."""
    assert runner_prix.cout_nano_usd(
        "claude-opus-5", entree=None, sortie=None,
        ecriture_cache=None, lecture_cache=None) == (None, None)


def test_un_deroule_REELLEMENT_nul_vaut_zero():
    """Le bord opposé : mesuré et nul, c'est zéro — et ça se distingue de NULL."""
    montant, bareme = runner_prix.cout_nano_usd("claude-opus-5", entree=0, sortie=0)
    assert montant == 0 and bareme is not None


def test_mistral_est_tarife_parce_que_la_PROD_le_sert():
    """⚠️ Les workers de production ne servent que `mistral` : un barème qui ne le
    porterait pas rendrait NULL sur la totalité du trafic réel — un compteur
    parfaitement juste et parfaitement vide."""
    montant, _ = runner_prix.cout_nano_usd("mistral-large-2512",
                                           entree=10**6, sortie=10**6)
    assert montant == 500 * 10**6 + 1_500 * 10**6, "0,50 $ en entrée, 1,50 $ en sortie"


def test_le_bareme_est_DATE_et_voyage_avec_le_montant():
    """Un relevé gardé pour toujours doit rester EXPLICABLE : sans le nom du
    barème, un montant de l'an dernier ne se recalcule plus."""
    _, bareme = runner_prix.cout_nano_usd("claude-opus-5", sortie=1)
    assert bareme == runner_prix.BAREME_COURANT
    assert bareme in {d for d, _ in runner_prix.BAREMES}


def test_un_travail_ANTERIEUR_au_premier_bareme_est_tarife_au_premier():
    """On n'a pas les prix d'avant. Refuser de tarifer rendrait NULL tout
    l'historique ; le nom du barème garde l'approximation lisible."""
    nom, _ = runner_prix.bareme_en_vigueur(datetime.date(2020, 1, 1))
    assert nom == runner_prix.BAREMES[0][0]


def test_les_dollars_ne_servent_QU_a_l_affichage():
    assert runner_prix.en_dollars(2_500_000_000) == 2.5
    assert runner_prix.en_dollars(None) is None


# ── 2. la PROVENANCE ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload,fleet_id,attendu", [
    ({}, None, RJC.MANUAL),
    ({"trigger_id": 5}, None, RJC.SCHEDULED),
    ({"trigger_id": 5, "hook": True}, None, RJC.HOOK),
    ({"trigger_id": 5}, 9, RJC.BATCH),
    (None, None, RJC.MANUAL),
])
def test_la_provenance_se_DERIVE_de_ce_que_le_travail_porte(payload, fleet_id, attendu):
    """Même vocabulaire que `runner_jobs._SOURCES`, plus `hook` : les deux genres
    de déclencheur portent un `trigger_id`, et seul le webhook porte `hook`. Les
    confondre rendrait « ce que coûtent mes agents programmés » faux des deux
    côtés."""
    assert RJC.source_du_travail(payload, fleet_id) == attendu


# ── 3. ce qu'une ÉCRITURE retient ─────────────────────────────────────────────

class _Conn:
    """La connexion, réduite à l'INSERT que `enregistrer` fait."""
    def __init__(self, rowcount=1):
        self.vu = None
        self._rowcount = rowcount

    def execute(self, sql, params):
        self.vu = (sql, params)
        return type("C", (), {"rowcount": self._rowcount})()


def _colonnes(conn):
    """Les paramètres de l'INSERT, nommés par la liste de colonnes réelle."""
    sql, params = conn.vu
    noms = [c.strip() for c in RJC._COLS.split(",")]
    # `finished_at` est posé par NOW(), il n'a pas de paramètre.
    return dict(zip([n for n in noms if n != "finished_at"], params))


RESULTAT = {"usage_input": 1000, "usage_output": 200, "usage_cache_read": 50,
            "usage_cache_write": 10, "model": "claude-opus-5", "steps": 4}


def test_une_ecriture_retient_les_QUATRE_postes_separement():
    """⚠️ `usage_tokens` (entrée+sortie) ne suffit pas : écrire du cache coûte
    PLUS que l'entrée et en lire coûte le dixième. Un compteur qui ne garde que
    la somme ne peut plus jamais tarifer juste."""
    c = _Conn()
    RJC.enregistrer(c, job_id=1, attempt=1, org_id=7, sub="alexis", run_id="r1",
                    payload={"trigger_id": 5}, fleet_id=None, key_source="org",
                    outcome=RJC.DONE, resultat=RESULTAT)
    col = _colonnes(c)
    assert (col["input_tokens"], col["output_tokens"]) == (1000, 200)
    assert (col["cache_write_tokens"], col["cache_read_tokens"]) == (10, 50)


def test_le_montant_est_FIGE_a_l_ecriture_avec_son_bareme():
    c = _Conn()
    RJC.enregistrer(c, job_id=1, attempt=1, org_id=7, sub=None, run_id=None,
                    payload=None, fleet_id=None, key_source="platform",
                    outcome=RJC.DONE, resultat=RESULTAT)
    col = _colonnes(c)
    attendu, bareme = runner_prix.cout_nano_usd(
        "claude-opus-5", entree=1000, sortie=200,
        ecriture_cache=10, lecture_cache=50)
    assert col["nano_usd"] == attendu and col["bareme"] == bareme


def test_un_travail_PERDU_garde_des_jetons_NULL():
    """⚠️ La ligne d'une épave : des jetons ont été dépensés chez le fournisseur
    et personne ne les a rendus. `0` mentirait ; `NULL` rend le total honnêtement
    incomplet."""
    c = _Conn()
    RJC.enregistrer(c, job_id=1, attempt=2, org_id=7, sub=None, run_id=None,
                    payload=None, fleet_id=None, key_source="platform",
                    outcome=RJC.LOST, resultat=None)
    col = _colonnes(c)
    assert col["input_tokens"] is None and col["output_tokens"] is None
    assert col["nano_usd"] is None
    assert col["outcome"] == RJC.LOST


def test_l_ecriture_porte_les_TROIS_regroupements():
    """Run, agent, passage : tout regroupement ultérieur en dépend."""
    c = _Conn()
    RJC.enregistrer(c, job_id=1, attempt=1, org_id=7, sub="alexis", run_id="r1",
                    payload={"trigger_id": 5}, fleet_id=9, key_source="org",
                    outcome=RJC.DONE, resultat=RESULTAT)
    col = _colonnes(c)
    assert (col["run_id"], col["trigger_id"], col["fleet_id"]) == ("r1", 5, 9)
    assert col["org_id"] == 7 and col["sub"] == "alexis"


def test_le_PAYEUR_est_retenu_tel_qu_on_le_lui_donne():
    for donne, attendu in (("org", "org"), ("platform", "platform"), (None, "platform")):
        c = _Conn()
        RJC.enregistrer(c, job_id=1, attempt=1, org_id=7, sub=None, run_id=None,
                        payload=None, fleet_id=None, key_source=donne,
                        outcome=RJC.DONE, resultat=RESULTAT)
        assert _colonnes(c)["key_source"] == attendu


def test_une_conclusion_REJOUEE_ne_double_pas_le_compte():
    """La clé `(job_id, attempt)` et `ON CONFLICT DO NOTHING` : un worker qui
    retente son appel de conclusion ne doit pas facturer deux fois."""
    c = _Conn(rowcount=0)
    neuve = RJC.enregistrer(c, job_id=1, attempt=1, org_id=7, sub=None,
                            run_id=None, payload=None, fleet_id=None,
                            key_source="platform", outcome=RJC.DONE,
                            resultat=RESULTAT)
    assert neuve is False
    assert "ON CONFLICT (job_id, attempt) DO NOTHING" in c.vu[0]


def test_le_modele_de_la_CHARGE_sert_de_repli():
    """Un résultat sans estampille : la charge sait ce qui a été DEMANDÉ. Mieux
    qu'un NULL, et honnête — le worker a servi ce qu'on lui a dit."""
    c = _Conn()
    RJC.enregistrer(c, job_id=1, attempt=1, org_id=7, sub=None, run_id=None,
                    payload={"model": "claude-haiku-4-5"}, fleet_id=None,
                    key_source="platform", outcome=RJC.DONE,
                    resultat={"usage_input": 10, "usage_output": 1})
    col = _colonnes(c)
    assert col["modele"] == "claude-haiku-4-5" and col["famille"] == "anthropic"


def test_une_ventilation_sur_une_colonne_LIBRE_est_refusee():
    """⚠️ `par` entre dans le SQL : une colonne libre serait une injection."""
    with pytest.raises(ValueError):
        RJC.ventilation(7, par="modele; DROP TABLE runner_job_cost")
