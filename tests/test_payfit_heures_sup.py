"""PayFit — les heures sup d'un bulletin, et rien d'autre.

L'API ne sert aucune ligne de bulletin ; `payfit_payslip(op="overtime")` lit le PDF
CÔTÉ SERVEUR et n'en rend que les lignes qui nomment des heures sup. Ce fichier
verrouille : le tri paiement / allègement, la lecture des nombres français, le fait
qu'aucune autre ligne du bulletin (NIR, IBAN, salaire de base) ne sort, le filtre du
mois, l'absence de verrou documents sur l'op (le bulletin ne quitte jamais le
serveur), et la ligne brute qui, elle, suit ce verrou.

Toutes les valeurs sont factices.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from _pdf_texte import pdf_sans_texte, pdf_texte
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import payfit_bulletin as pb
from oto_mcp.tools import payfit_garde
from test_payfit_documents_verrou import _politique

K = "000000000000000000000a0a"
BULLETIN = [
    "Salaire de base 151,67 12,50 1 895,88",
    "Heures supplémentaires 25% 8,00 15,63 125,04",
    "Heures sup. majorées 50 % 2,00 18,75 37,50",
    "Réduction salariale heures supplémentaires -14,25",
    "Heures complémentaires 10 % 4,00 13,75 55,00",
    "N° de sécurité sociale 1 85 05 75 000 000 00",
    "IBAN FR76 0000 0000 0000 0000 0000 000",
]


# ── Le parseur ────────────────────────────────────────────────────────────────

def test_only_overtime_lines_come_out():
    lignes = pb.overtime_lines("\n".join(BULLETIN))
    assert [l["label"] for l in lignes] == [
        "Heures supplémentaires", "Heures sup. majorées",
        "Réduction salariale heures supplémentaires", "Heures complémentaires"]
    texte = " ".join(l["line"] for l in lignes)
    assert "sécurité sociale" not in texte and "IBAN" not in texte
    assert "Salaire de base" not in texte


def test_numbers_and_rates_are_read_the_french_way():
    hs = pb.overtime_lines("Heures supplémentaires 25% 8,00 15,63 1 125,04")[0]
    assert hs["rates"] == [25.0]
    assert hs["numbers"] == [8.0, 15.63, 1125.04]


def test_a_contribution_relief_is_never_a_payment():
    kinds = {l["label"]: l["kind"] for l in pb.overtime_lines("\n".join(BULLETIN))}
    assert kinds["Réduction salariale heures supplémentaires"] == pb.ALLEGEMENT
    assert kinds["Heures supplémentaires"] == pb.PAIEMENT


@pytest.mark.parametrize("ligne", ["HS 25 8,00 15,63 125,04", "H. sup 3,00 45,00",
                                   "HEURES SUPPLEMENTAIRES 1,00 15,00"])
def test_short_and_unaccented_labels_are_found(ligne):
    assert len(pb.overtime_lines(ligne)) == 1


def test_nothing_to_read_is_an_empty_list():
    assert pb.overtime_lines("") == []
    assert pb.overtime_lines("Salaire de base 151,67") == []


# ── L'op, de bout en bout (PDF réel fabriqué, client simulé) ──────────────────

@pytest.fixture
def client(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("pf-test", False))
    inst.list_payslips.return_value = {"payslips": [
        {"year": 2026, "month": 1, "contractId": "c1", "payslipId": "p1"},
        {"year": 2026, "month": 2, "contractId": "c1", "payslipId": "p2"},
    ]}
    inst.get_payslip.return_value = {"data": pdf_texte(BULLETIN),
                                     "filename": "bulletin.pdf",
                                     "mimetype": "application/pdf"}
    return inst


def _payslip():
    from fastmcp import FastMCP

    from oto_mcp.tools import payfit_paie as PP
    m = FastMCP("t")
    PP.register(m)
    return asyncio.run(m.get_tool("payfit_payslip")).fn


def test_overtime_reads_one_month_and_returns_ids_not_the_payslip(client):
    out = _payslip()(op="overtime", collaborator_id=K, date="202602")
    client.get_payslip.assert_called_once_with(K, "c1", "p2")
    assert out["count"] == 1
    slip = out["payslips"][0]
    assert (slip["year"], slip["month"], slip["payslipId"]) == (2026, 2, "p2")
    assert len(slip["lines"]) == 4
    assert "sécurité sociale" not in str(out) and "IBAN" not in str(out)


def test_overtime_without_date_reads_the_most_recent_first(client):
    out = _payslip()(op="overtime", collaborator_id=K)
    assert [p["payslipId"] for p in out["payslips"]] == ["p2", "p1"]


def test_overtime_is_not_behind_the_documents_lock(client, monkeypatch):
    # Le verrou garde les documents qui SORTENT ; celui-ci ne sort pas — seule sa
    # ligne brute, extrait verbatim, reste derrière.
    monkeypatch.setattr(payfit_garde, "documents_open", lambda: False)
    out = _payslip()(op="overtime", collaborator_id=K, date="202601")
    assert out["payslips"][0]["lines"] and "line_withheld" in out


def test_an_unreadable_payslip_says_why(client):
    client.get_payslip.return_value = {"data": pdf_sans_texte(),
                                       "filename": "scan.pdf",
                                       "mimetype": "application/pdf"}
    slip = _payslip()(op="overtime", collaborator_id=K, date="202601")["payslips"][0]
    assert slip["lines"] == [] and slip["unreadable"].startswith("empty")


@pytest.mark.parametrize("date", ["2026-01", "202613", "2026"])
def test_a_malformed_month_is_refused_before_the_network(client, date):
    with pytest.raises(McpError, match="AAAAMM"):
        _payslip()(op="overtime", collaborator_id=K, date=date)
    client.list_payslips.assert_not_called()


@pytest.mark.parametrize("kwargs,champ", [
    ({"contract_id": "c1"}, "`contract_id`"), ({"payslip_id": "p1"}, "`payslip_id`"),
    ({"fields": ["year"]}, "`fields`")])
def test_overtime_refuses_what_it_does_not_use(client, kwargs, champ):
    with pytest.raises(McpError, match=champ):
        _payslip()(op="overtime", collaborator_id=K, **kwargs)


def test_date_is_refused_on_the_other_ops(client):
    with pytest.raises(McpError, match="`date`"):
        _payslip()(collaborator_id=K, date="202601")


# ── La politique de champs de l'org (alerte du scanner de sécurité) ───────────
#
# `numbers`, `rates`, `label` et `kind` sont des DONNÉES : le middleware de rédaction
# les filtre champ par champ, comme toute sortie JSON du connecteur. `line`, elle, est
# du TEXTE DU BULLETIN — un filtre de champs n'en voit pas l'intérieur, donc elle suit
# le verrou des documents (`payfit_garde.documents_open`). Éprouvé par le chemin réel
# du middleware (`redaction.redact_payload`).


def _servi(out) -> str:
    """Ce que l'agent reçoit : la sortie de l'outil après la politique de l'org."""
    import json

    from oto_mcp import redaction
    red = redaction.redact_payload("payfit", out)
    return json.dumps(out if red is redaction.PASSTHROUGH else red, ensure_ascii=False)


def test_an_org_that_masks_amounts_and_rates_gets_them_nowhere(client, monkeypatch):
    """L'org masque les montants et les taux : ni `numbers`, ni `rates`, ni la ligne
    brute qui les répète ne les laissent sortir."""
    _politique(monkeypatch, {"payfit": {"rules": [
        {"fields": ["numbers", "rates"], "action": "drop"}]}})
    servi = _servi(_payslip()(op="overtime", collaborator_id=K, date="202601"))
    for montant in ("125,04", "15,63", "125.04", "15.63", "25%", "25.0"):
        assert montant not in servi
    assert "Heures supplémentaires" in servi           # le libellé, lui, reste


def test_the_default_policy_withholds_the_raw_line_and_says_why(client, monkeypatch):
    _politique(monkeypatch, {})          # aucune politique d'org → plancher serveur
    out = _payslip()(op="overtime", collaborator_id=K, date="202601")
    lignes = out["payslips"][0]["lines"]
    assert lignes and all("line" not in l for l in lignes)
    assert lignes[0]["numbers"] == [8.0, 15.63, 125.04]   # rien que le plancher
    assert out["line_withheld"] == payfit_garde.OVERTIME_LINE_LOCKED


def test_an_org_that_opened_the_documents_gets_the_raw_line(client, monkeypatch):
    _politique(monkeypatch, {"payfit": {"rules": [], "documents": True}})
    out = _payslip()(op="overtime", collaborator_id=K, date="202601")
    assert all(l["line"] for l in out["payslips"][0]["lines"])
    assert "line_withheld" not in out


def test_an_unreadable_policy_refuses_overtime_before_the_network(client, monkeypatch):
    def _base_en_panne(org_id):
        raise RuntimeError("base indisponible")

    _politique(monkeypatch, _base_en_panne)
    with pytest.raises(McpError) as exc:
        _payslip()(op="overtime", collaborator_id=K, date="202601")
    assert str(exc.value) == payfit_garde.DOCUMENTS_POLICY_UNREADABLE
    client.list_payslips.assert_not_called()
    client.get_payslip.assert_not_called()


def test_the_withheld_line_prescribes_no_detour():
    texte = payfit_garde.OVERTIME_LINE_LOCKED
    assert "op=" not in texte and "_account" not in texte and "org_admin" in texte
