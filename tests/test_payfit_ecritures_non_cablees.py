"""PayFit — AUCUNE écriture n'est câblée (décision du 24/09/2026).

Toute op d'écriture des outils PayFit rend le refus nommé `payfit_write_not_wired`,
quel que soit l'argument : rien n'est envoyé à PayFit, et la clé n'est même pas
résolue. Il n'y a plus de `dry_run` : l'ancien `dry_run=False` faisait partir
l'écriture, et c'est exactement ce qui ne doit plus exister.

Ce que ce fichier verrouille :
- chaque op d'écriture lève `payfit_write_not_wired`, avec l'action décrite ;
- ni `resolve_api_key` ni `PayfitClient` ne sont touchés (les deux lèvent s'ils le
  sont) ;
- `dry_run` n'est plus un paramètre d'aucun outil PayFit ;
- le refus ne recopie ni NIR ni motif d'absence (ils finiraient au journal).

Toutes les valeurs sont factices.
"""
import asyncio
import inspect

import pytest

from oto_mcp.mcp_errors import McpError

K = "000000000000000000000a0a"
K2 = "000000000000000000000b0b"
NIR = "185057800608436"


@pytest.fixture
def rien_ne_part(monkeypatch):
    """La clé et le client LÈVENT s'ils sont touchés : un refus qui résoudrait la
    clé, ou pire appellerait PayFit, rougit ici."""
    def _cle(*a, **kw):
        raise AssertionError("une écriture non câblée a résolu la clé PayFit")

    def _client(**kw):
        raise AssertionError("une écriture non câblée a construit le client PayFit")

    monkeypatch.setattr("oto_mcp.access.resolve_api_key", _cle)
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", _client)


def _mcp():
    from fastmcp import FastMCP

    from oto_mcp.tools import payfit as P
    from oto_mcp.tools import payfit_paie as PP
    from oto_mcp.tools import payfit_social as PS

    m = FastMCP("t")
    P.register(m)
    PP.register(m)
    PS.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_mcp().get_tool(name)).fn


_ECRITURES = [
    ("payfit_collaborator", {"op": "create", "first_name": "Ada", "last_name": "Test",
                             "personal_email": "ada@exemple.test"}, "collaborateur"),
    # Arguments manquants : le refus ne dépend de rien, il ne demande pas de compléter.
    ("payfit_collaborator", {"op": "create"}, "collaborateur"),
    ("payfit_contract", {"op": "create", "collaborator_id": K, "job_title": "Poste",
                         "start_date": "2026-02-01"}, "contrat"),
    ("payfit_absence", {"op": "create", "contract_id": K,
                        "absence_type": "fr_conges_payes",
                        "begin_date": "2026-02-02", "end_date": "2026-02-06"}, "absence"),
    ("payfit_absence", {"op": "cancel", "absence_id": K}, "annulé"),
    ("payfit_insurance", {"op": "affiliate", "contract_id": K,
                          "insurance_contract_ids": [K2]}, "mutuelle"),
    ("payfit_insurance", {"op": "affiliate", "kind": "provident", "contract_id": K,
                          "insurance_contract_ids": [K2]}, "prévoyance"),
    ("payfit_insurance", {"op": "regularize", "contract_id": K,
                          "insurance_contract_ids": [K2],
                          "effective_date": "2026-01-01"}, "régularisation"),
]


@pytest.mark.parametrize("tool,kwargs,action", _ECRITURES)
def test_une_ecriture_ne_part_jamais_et_le_dit(rien_ne_part, tool, kwargs, action):
    with pytest.raises(McpError) as exc:
        _tool(tool)(**kwargs)
    err = exc.value.error
    assert err.data["code"] == "payfit_write_not_wired"
    assert err.data["retryable"] is False and err.data["op"] == kwargs["op"]
    assert "payfit_write_not_wired" in err.message
    assert "rien n'a été envoyé à PayFit" in err.message
    assert action in err.message, f"le refus ne nomme pas l'action : {err.message}"


def test_le_refus_reprend_les_identifiants_utiles(rien_ne_part):
    with pytest.raises(McpError) as exc:
        _tool("payfit_contract")(op="create", collaborator_id=K, job_title="Poste",
                                 start_date="2026-02-01")
    assert exc.value.error.data["would_have"] == {
        "collaborator_id": K, "job_title": "Poste", "start_date": "2026-02-01"}


def test_le_refus_ne_recopie_ni_nir_ni_motif_d_absence(rien_ne_part):
    """Le message d'erreur est journalisé : il ne doit pas porter une valeur que le
    défaut serveur masque (NIR, motif d'absence)."""
    with pytest.raises(McpError) as exc:
        _tool("payfit_collaborator")(op="create", first_name="Ada", last_name="Test",
                                     social_security_number=NIR)
    err = exc.value.error
    assert NIR not in err.message and NIR not in repr(err.data)
    assert err.data["would_have"]["champs_fournis"] == ["social_security_number"]

    with pytest.raises(McpError) as exc:
        _tool("payfit_absence")(op="create", contract_id=K,
                                absence_type="fr_maladie_ordinaire",
                                begin_date="2026-02-02", end_date="2026-02-06")
    err = exc.value.error
    assert "fr_maladie_ordinaire" not in err.message
    assert "fr_maladie_ordinaire" not in repr(err.data)


@pytest.mark.parametrize("tool", [
    "payfit_collaborator", "payfit_contract", "payfit_absence", "payfit_insurance",
    "payfit_company", "payfit_payslip", "payfit_payroll", "payfit_worked_time",
    "payfit_meal_voucher", "payfit_document",
])
def test_dry_run_n_est_plus_un_parametre(tool):
    assert "dry_run" not in inspect.signature(_tool(tool)).parameters
