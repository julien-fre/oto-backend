"""La clé de modèle de l'org part avec le travail RÉSERVÉ — et rien d'autre.

Décidé le 02/09/2026 : la clé de modèle vit avec les autres secrets de
connecteurs de l'org, et le worker — qui fait partie du backend — a le droit de
la lire. Ce droit s'exerce à la réservation, une fois, avec le travail : le
runner n'interroge jamais le coffre, sans quoi il pourrait lire autre chose que
ce travail-ci.

D'où la garde que ces bancs tiennent : **elle porte sur le TYPE du dépôt**. Un
worker nomme le dépôt qu'il sait consommer ; s'il pouvait nommer n'importe quel
connecteur, réserver un travail suffirait à faire sortir le secret Folk ou
Salesforce de l'org. Seuls les connecteurs `kind="credential"` — porter une clé
est leur seule raison d'être, aucun outil derrière — sont servis.
"""
from __future__ import annotations

import pytest

from oto_mcp import providers
from oto_mcp.capabilities import runner_jobs as RJ


@pytest.fixture
def _coffre(monkeypatch):
    """Un coffre qui note CE QU'ON LUI DEMANDE — l'entité autant que le dépôt."""
    from oto_mcp import credentials_store
    demandes = []

    def _get(entity_type, entity_id, connector, account=""):
        demandes.append((entity_type, entity_id, connector))
        return {"anthropic": "sk-de-l-org", "folk": "secret-folk-de-l-org"}.get(connector)

    monkeypatch.setattr(credentials_store, "get_credential", _get)
    return demandes


# ── ce que le registre déclare vraiment ───────────────────────────────────────

def test_les_depots_de_cle_sont_d_un_type_a_part_et_ne_portent_aucun_outil():
    """Sans le type distinct, la garde ci-dessous n'aurait rien à quoi se tenir."""
    for nom in ("anthropic", "mistral"):
        c = providers.connector_for_provider(nom)
        assert c and c.kind == "credential", f"{nom} n'est plus un dépôt de clé"
        assert not c.namespaces, f"{nom} expose des outils — ce n'est plus un dépôt"


def test_un_depot_de_cle_est_mono_compte_et_c_est_la_ou_on_le_lit():
    """La dérivation rendrait `multi` (c'est une api_key), et l'écran proposerait
    d'en poser une deuxième que rien ne saurait choisir : `_cle_de_modele` lit le
    compte unique. Une clé déposée sous un nom de compte ne serait jamais lue —
    l'org paierait sur la clé de la plateforme en croyant payer sur la sienne."""
    for nom in ("anthropic", "mistral"):
        c = providers.connector_for_provider(nom)
        assert not c.auth_multi_account, f"{nom} redevenu multi-compte"
        assert c.auth["cardinality"] == "single"


# ── la remise ─────────────────────────────────────────────────────────────────

def test_le_travail_reserve_emporte_la_cle_deposee_par_son_org(_coffre):
    job = RJ._avec_cle({"id": 1, "org_id": 42}, "anthropic")
    assert job["model_key"] == "sk-de-l-org"
    assert _coffre == [("org", "42", "anthropic")]


def test_sans_depot_nomme_aucune_cle_ne_part(_coffre):
    assert "model_key" not in RJ._avec_cle({"id": 1, "org_id": 42}, None)
    assert _coffre == [], "le coffre n'est même pas interrogé"


def test_une_org_qui_n_a_rien_depose_ne_recoit_pas_de_cle(_coffre):
    assert "model_key" not in RJ._avec_cle({"id": 1, "org_id": 42}, "mistral")


# ── la garde : le type, pas le nom ────────────────────────────────────────────

def test_un_connecteur_ordinaire_ne_se_laisse_pas_tirer_par_un_worker(_coffre):
    """`folk` a bien un secret dans cette org — et il ne sort pas. Réserver un
    travail ne doit jamais devenir un moyen de lire le coffre."""
    job = RJ._avec_cle({"id": 1, "org_id": 42}, "folk")
    assert "model_key" not in job
    assert _coffre == [], "le coffre ne doit pas même être interrogé"


def test_un_depot_inconnu_ne_fait_pas_tomber_la_reservation(_coffre):
    assert "model_key" not in RJ._avec_cle({"id": 1, "org_id": 42}, "n-existe-pas")


def test_aucun_connecteur_a_outils_ne_porte_le_type_depot():
    """La garde se tient par CLASSE : le jour où un dépôt de clé gagnerait des
    outils, ou un connecteur ordinaire le type `credential`, la liste
    d'autorisation cesserait d'être une liste d'autorisation."""
    coupables = [c.name for c in providers.REGISTRY.values()
                 if c.kind == "credential" and c.namespaces]
    assert not coupables


# ── ce qui prime sur la remise ────────────────────────────────────────────────

def test_un_travail_refuse_pour_identite_ne_recoit_pas_de_cle(_coffre):
    """Il est déjà marqué échoué : lui remettre une clé serait armer un travail
    qui ne doit pas tourner."""
    job = RJ._avec_cle(
        {"id": 1, "org_id": 42, "delegation_refusee": "compte supprimé"}, "anthropic")
    assert "model_key" not in job
    assert _coffre == []


def test_un_travail_sans_org_ne_recoit_pas_de_cle(_coffre):
    assert "model_key" not in RJ._avec_cle({"id": 1, "org_id": None}, "anthropic")


# ── le contrat servi le dit ───────────────────────────────────────────────────

def test_le_contrat_dit_que_la_cle_appartient_a_l_org_et_ne_se_journalise_pas():
    d = RJ.Job.model_fields["model_key"].description
    assert "op=claim only" in d and "never written" in d


def test_la_cle_ne_sort_que_de_la_reservation_jamais_d_une_lecture():
    """`list` et `get` servent les mêmes travaux à toute l'org : si la clé y
    passait, la lire ne demanderait plus d'en réserver un. Elle n'est écrite
    nulle part en base — elle n'existe que dans la réponse au claim."""
    import inspect
    src = inspect.getsource(RJ._jobs)
    appels = [l.strip() for l in src.splitlines() if "_avec_cle(" in l]
    assert len(appels) == 1 and 'op == "claim"' in src
    branche = src.split('if inp.op == "claim":')[1].split("if inp.op ==")[0]
    assert "_avec_cle(" in branche


# ── ce que le journal peut en voir : rien ─────────────────────────────────────

def test_le_journal_des_appels_ne_garde_aucune_reponse():
    """La clé part dans la RÉPONSE au claim, pas dans ses arguments — le masque
    de `tool_calls.args` (#558/#564) ne la couvre donc pas, et n'a pas à le
    faire : le journal ne stocke aucune réponse. Le jour où une colonne de
    résultat s'ajouterait, ce banc tombe et force à reposer la question — un
    travail réservé journalisé avec sa clé serait une fuite."""
    import inspect

    from oto_mcp.db import usage
    sql = inspect.getsource(usage.insert_tool_call)
    colonnes = sql.split("INSERT INTO tool_calls")[1].split(")")[0]
    for mot in ("result", "response", "output", "reponse", "resultat"):
        assert mot not in colonnes.lower(), (
            f"`tool_calls` garde maintenant une {mot} : la clé de modèle servie "
            "au claim y passerait")


def test_la_remise_ne_modifie_pas_le_travail_d_origine(_coffre):
    """`_avec_cle` rend une COPIE : le dict du claim, lui, peut être relu,
    compté ou tracé ailleurs sans emporter le secret."""
    origine = {"id": 1, "org_id": 42}
    servi = RJ._avec_cle(origine, "anthropic")
    assert servi is not origine and "model_key" not in origine
