"""Un déroulé terminé sans action est REFUSÉ, et les réglages n'ont pas l'air acquis
(#627, oto#175).

Mesuré le 31/08/2026 sur un enrôlement incrémental : la réponse du déroulé
affirmait « 19/19 personnes ajoutées, ouverture conservée mot pour mot » avec une
liste d'actions VIDE. Puis le 09/09/2026 (signal 830) : trois créations de campagne
de suite sur la même table, chacune « completed » en ~90 s avec `actions: []` et
une prose confiante nommant une campagne et un slug inexistants —
`origami_campaigns(op='list_for_table')` rendait VIDE après les trois. Le drapeau
`aucune_action` posé le 03/09 a été le seul discriminateur sur sept appels ; mais un
drapeau se lit ou ne se lit pas, et la prose voyageait quand même comme un succès.

Décision d'Alexis (12/09/2026) : refuser. Le déroulé est rendu en ERREUR qui nomme
la cause (aucune action : rien n'a été créé, la campagne annoncée n'existe pas) et
le geste (vérifier la table, relancer la création).

⚠️ **La garde se TAIT sur ce qu'elle ne voit pas**, et c'est la moitié du lot. La
liste d'actions n'est pas dans le contrat documenté du fournisseur : on la cherche
à deux emplacements plausibles et on ne refuse que si on l'a trouvée vide. Une
garde qui devine une forme fabrique des refus sur des déroulés normaux.

⚠️ **Second défaut du même signal, plus discret** : les deux réglages passés à la
création étaient renvoyés en écho alors qu'ils ne s'appliquaient pas — l'agent
avait enrôlé dans une campagne EXISTANTE, dont les réglages propres gouvernent.
Un écho qui ne dit pas ce qu'il vaut se lit comme un acquis.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import origami
from oto_mcp.tools.origami import _refuse_si_rien_n_a_ete_fait as garde

# Le déroulé du 09/09, réduit à ce qui produit le défaut.
_TERMINE_VIDE = {"id": "v2_run-x", "status": "completed", "actions": [],
                 "steps": {"completed": 6, "total": 30},
                 "response": {"text": "Created the draft campaign wave3-ceo — "
                                      "People enrolled: 15, all successfully added"}}


def test_un_deroule_TERMINE_sans_action_est_REFUSE():
    with pytest.raises(McpError) as e:
        garde(_TERMINE_VIDE)
    msg = e.value.error.message
    assert "NO ACTION" in msg and "v2_run-x" in msg
    assert "do not exist" in msg,"la cause : la campagne annoncée n'existe pas"
    assert e.value.error.data["aucune_action"] is True
    assert e.value.error.data["steps_completed"] == 6


def test_le_refus_dit_le_GESTE_verifier_puis_relancer():
    with pytest.raises(McpError) as e:
        garde(_TERMINE_VIDE)
    msg = e.value.error.message
    assert "origami_campaigns(op='list_for_table'" in msg
    assert "retry origami_campaign_create" in msg
    # …et le symptôme de l'autre cas (enrôlement dans une campagne existante).
    assert "people found" in msg and "contacted count" in msg


def test_le_refus_traverse_le_tool_MONTE(monkeypatch):
    """Ce n'est pas la fonction qui compte, c'est ce que rend `origami_run_get`."""
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("og_live_k", False))
    with patch("oto.tools.origami.client.OrigamiClient") as cls:
        m = FastMCP("t")
        origami.register(m)          # le client se lie au register : patcher AVANT
        tool = asyncio.run(m.get_tool("origami_run_get"))
        cls.return_value.get_run.return_value = dict(_TERMINE_VIDE)
        with pytest.raises(McpError) as e:
            tool.fn(agent_id="ag-1", run_id="v2_run-x")
    assert "NO ACTION" in e.value.error.message


def test_un_deroule_EN_COURS_n_est_pas_refuse():
    """Une liste vide pendant l'exécution est normale — la refuser ferait échouer
    chaque sondage avant la fin."""
    out = garde({"status": "running", "actions": []})
    assert out["status"] == "running" and "aucune_action" not in out


def test_la_garde_SE_TAIT_quand_la_forme_est_absente():
    """Le fournisseur ne documente pas cette liste. Deviner sa présence
    fabriquerait des refus sur des déroulés parfaitement normaux."""
    assert garde({"status": "completed"}) == {"status": "completed"}
    assert garde({"status": "completed", "steps": []})["status"] == "completed"


def test_la_liste_est_cherchee_AUSSI_sous_la_reponse():
    with pytest.raises(McpError):
        garde({"status": "completed", "response": {"actions": []}})


def test_un_deroule_QUI_A_AGI_passe_sans_bruit():
    res = {"status": "completed", "actions": [{"type": "enrol"}]}
    assert garde(res) == res


def test_l_entree_non_dict_traverse_intacte():
    assert garde("texte") == "texte"
    assert garde(None) is None


@pytest.fixture(scope="module")
def prose() -> str:
    m = FastMCP("t")
    origami.register(m)
    return asyncio.run(m.get_tool("origami_campaign_create")).description or ""


def test_la_description_dit_que_les_REGLAGES_peuvent_ne_pas_s_appliquer(prose):
    plat = " ".join(prose.split())
    assert "CREATES" in plat and "NO effect" in plat


def test_la_description_dit_qu_AUCUN_verbe_ne_corrige_une_campagne_existante(prose):
    plat = " ".join(prose.split())
    assert "Nothing in this connector updates an existing campaign's settings" in plat


def test_la_description_dit_que_la_prose_peut_nommer_une_campagne_INEXISTANTE(prose):
    """La troisième demande du signal 830 : dire PLAINEMENT que la prose peut nommer
    une campagne et un slug jamais créés, et que le poll REFUSE ce déroulé."""
    plat = " ".join(prose.split())
    assert "not a measurement" in plat
    assert "never created" in plat
    assert "REFUSES such a run" in plat
    assert "flags that as" not in plat, "l'ancien contrat (un drapeau) n'est plus promis"


def test_run_get_dit_lui_aussi_le_refus():
    m = FastMCP("t")
    origami.register(m)
    prose = " ".join((asyncio.run(m.get_tool("origami_run_get")).description or "").split())
    assert "REFUSED" in prose and "list_for_table" in prose
