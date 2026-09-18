"""PayFit — le VERROU des documents, éprouvé sur des valeurs SENTINELLES.

Les masques par défaut du connecteur (`field_filter_defaults.SERVER_DEFAULTS["payfit"]`)
ne s'appliquent qu'à des champs JSON : `FieldFilter` matche des clés, il ne lit pas
l'intérieur d'un fichier. Or quatre documents servis portent exactement ce que ces
masques cachent — le bulletin PDF (NIR), le fichier de virement (IBAN de chaque
salarié), l'export comptable (noms et montants par personne), les documents fiscaux
britanniques. Décision d'Alexis (18/09/2026) : **même verrou** — un document ne sort
que si la politique EFFECTIVE de l'appelant pour `payfit` ne masque rien.

Ce fichier prouve, pour chaque document, par le chemin réel
(`access.resolve_field_filter` → `payfit_garde.serve_document`) :
- politique par défaut (le plancher serveur) → refus NOMMÉ, et l'amont n'est même
  pas appelé ;
- org qui a posé une AUTRE règle → refus aussi (un fichier ne se filtre pas du tout) ;
- org qui a levé les masques (`rules: []`) → le document est servi, sentinelle
  comprise ;
- politique illisible → refus (fail-closed), jamais le document.

Toutes les valeurs sont factices.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import payfit_garde

K = "000000000000000000000a0a"
K2 = "000000000000000000000b0b"
MOIS = "202601"
# Le contenu de chaque document : une chaîne reconnaissable, textuelle pour que le
# rendu partagé la serve INLINE (pas de S3 en test) — si elle sort, on la voit.
NIR = "SENTINELLE-NIR-BULLETIN"
IBAN = "SENTINELLE-IBAN-VIREMENT"
MONTANT = "SENTINELLE-NOM;SENTINELLE-MONTANT"
NINO = "SENTINELLE-NI-NUMBER"

# (outil, arguments, méthode du client, sentinelle contenue dans le document)
DOCUMENTS = [
    ("payfit_payslip", {"op": "download", "collaborator_id": K, "contract_id": K2,
                        "payslip_id": "42"}, "get_payslip", NIR),
    ("payfit_payroll", {"op": "payment_file", "date": MOIS}, "get_payment_file", IBAN),
    ("payfit_payroll", {"op": "accounting_export", "date": MOIS},
     "get_accounting_export", MONTANT),
    ("payfit_document", {"op": "download", "document_id": K}, "get_document", NINO),
]
IDS = ["bulletin", "virement", "export_comptable", "document_uk"]


@pytest.fixture
def client(monkeypatch):
    inst = MagicMock()
    for _, _, methode, sentinelle in DOCUMENTS:
        getattr(inst, methode).return_value = {
            "data": sentinelle.encode(), "filename": "doc.txt", "mimetype": "text/plain"}
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("pf-test", False))
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-test")
    return inst


def _politique(monkeypatch, filtres):
    """La politique de l'org active, lue par le VRAI `resolve_field_filter` : on ne
    remplace que ses sources (qui appelle, dans quelle org, ce que l'org a posé)."""
    monkeypatch.setattr("oto_mcp.access.rbac.current_user_sub_from_token",
                        lambda: "sub-test")
    monkeypatch.setattr("oto_mcp.access.rbac.scope.current_org", lambda sub: 1)
    monkeypatch.setattr("oto_mcp.access.rbac.org_store.get_org_field_filters",
                        filtres if callable(filtres) else (lambda org_id: filtres))


def _tool(name: str):
    from fastmcp import FastMCP

    from oto_mcp.tools import payfit as P
    from oto_mcp.tools import payfit_paie as PP
    from oto_mcp.tools import payfit_social as PS

    m = FastMCP("t")
    for mod in (P, PP, PS):
        mod.register(m)
    return asyncio.run(m.get_tool(name)).fn


# --- verrou fermé -------------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,methode,sentinelle", DOCUMENTS, ids=IDS)
def test_the_default_policy_refuses_the_document_by_name(monkeypatch, client, tool,
                                                         kwargs, methode, sentinelle):
    _politique(monkeypatch, {})          # aucune politique d'org → plancher serveur
    with pytest.raises(McpError) as exc:
        _tool(tool)(**kwargs)
    message = str(exc.value)
    assert message == payfit_garde.DOCUMENTS_LOCKED
    assert "NIR" in message and "IBAN" in message and "org_admin" in message
    assert sentinelle not in message
    getattr(client, methode).assert_not_called()   # pas même téléchargé


@pytest.mark.parametrize("tool,kwargs,methode,sentinelle", DOCUMENTS, ids=IDS)
def test_an_org_that_masks_ANYTHING_keeps_the_documents_locked(monkeypatch, client,
                                                               tool, kwargs, methode,
                                                               sentinelle):
    """Une org qui masque seulement la date de naissance a quand même dit qu'un champ
    ne sort pas — et un PDF ne se filtre pas. Le verrou ne s'ouvre que sur « rien »."""
    _politique(monkeypatch, {"payfit": {"rules": [
        {"fields": ["birthDate"], "action": "drop"}]}})
    with pytest.raises(McpError, match="document non servi"):
        _tool(tool)(**kwargs)
    getattr(client, methode).assert_not_called()


def test_the_refusal_prescribes_no_detour():
    """Le refus dit pourquoi et QUI peut ouvrir ; il ne propose aucune autre voie à
    l'agent — un agent à qui l'on suggère un détour le prend."""
    for texte in (payfit_garde.DOCUMENTS_LOCKED, payfit_garde.DOCUMENTS_POLICY_UNREADABLE):
        assert "payfit_" not in texte and "op=" not in texte and "_account" not in texte


# --- verrou ouvert ------------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,methode,sentinelle", DOCUMENTS, ids=IDS)
def test_an_org_that_lifted_the_masks_gets_the_document(monkeypatch, client, tool,
                                                        kwargs, methode, sentinelle):
    _politique(monkeypatch, {"payfit": {"rules": []}})
    out = _tool(tool)(**kwargs)
    assert out["encoding"] == "text" and sentinelle in out["content"]
    getattr(client, methode).assert_called_once()


# --- politique illisible : fail-closed ----------------------------------------

@pytest.mark.parametrize("tool,kwargs,methode,sentinelle", DOCUMENTS, ids=IDS)
def test_an_unreadable_policy_refuses_the_document(monkeypatch, client, tool, kwargs,
                                                   methode, sentinelle):
    def _base_en_panne(org_id):
        raise RuntimeError("base indisponible")

    _politique(monkeypatch, _base_en_panne)
    with pytest.raises(McpError) as exc:
        _tool(tool)(**kwargs)
    assert str(exc.value) == payfit_garde.DOCUMENTS_POLICY_UNREADABLE
    assert sentinelle not in str(exc.value)
    getattr(client, methode).assert_not_called()


# --- ce que le verrou ne touche pas -------------------------------------------

@pytest.mark.parametrize("tool,kwargs,methode", [
    ("payfit_payroll", {"date": MOIS}, "get_payroll_status"),
    ("payfit_payroll", {"op": "accounting", "date": MOIS}, "list_accounting_entries"),
    ("payfit_payslip", {"collaborator_id": K}, "list_payslips"),
])
def test_data_is_never_locked(monkeypatch, client, tool, kwargs, methode):
    """Les données JSON se filtrent champ par champ : le verrou ne les concerne pas."""
    _politique(monkeypatch, {})
    getattr(client, methode).return_value = {}
    _tool(tool)(**kwargs)
    getattr(client, methode).assert_called_once()
