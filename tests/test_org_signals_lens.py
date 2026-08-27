"""Un responsable d'org lit le CORPS des signaux de son org, pas seulement le compte.

**Le manque a coûté cinq jours à cinq clients.** Un revendeur avait vendu une
ingestion quotidienne à ses clients ; elle échouait tous les matins chez chacun, et
les agents le signalaient fidèlement. Les responsables voyaient « 8 manques » et
n'avaient AUCUN moyen de savoir lesquels : les lentilles d'org rendaient l'intitulé
et le nombre, jamais la prose — et la prose disait « le projet de destination a été
archivé le 21/08 ». Une cause qui ne se déduit d'aucun compteur.

Deux invariants gardés ici : le scope est ce qui a été ÉMIS SOUS l'org (jamais
l'appartenance du rapporteur, sinon un prestataire verse ses retours chez tous ses
clients), et notre arbitrage interne ne descend pas.
"""
import pytest

from oto_mcp.capabilities import org_monitoring as om
from oto_mcp.capabilities._types import ResolvedCtx

CTX = ResolvedCtx(sub="chef", org_id=196)


def _row(i, org, **kw):
    d = {"id": i, "org_id": org, "signal": "gap", "kind": "missing_tool",
         "target": "lire Notion", "body": "le projet de destination est archivé",
         "status": "open", "resolved_by": "op-plateforme", "resolution": "en cours"}
    d.update(kw)
    return d


def test_le_corps_est_rendu(monkeypatch):
    """C'est TOUT l'objet du lot : le compte ne dit jamais pourquoi."""
    monkeypatch.setattr(om.db, "list_usage_signals", lambda **k: [_row(1, 245)])
    out = om._signals(CTX, om.OrgMonitoringInput(op="signals", org_id=245))

    assert out["count"] == 1
    assert "archivé" in out["signals"][0]["body"]


def test_le_scope_est_l_org_d_EMISSION(monkeypatch):
    """Jamais l'appartenance du rapporteur : un prestataire qui travaille pour trois
    clients ne doit pas verser ses retours dans les trois."""
    vu = {}
    monkeypatch.setattr(om.db, "list_usage_signals",
                        lambda **k: vu.update(k) or [])
    om._signals(CTX, om.OrgMonitoringInput(op="signals", org_id=245))
    assert vu["org_id"] == 245


def test_notre_arbitrage_interne_ne_descend_pas(monkeypatch):
    """Qui a tranché chez nous est notre conduite, pas celle du client. La NOTE, elle,
    descend — c'est la réponse qu'on lui doit."""
    monkeypatch.setattr(om.db, "list_usage_signals", lambda **k: [_row(1, 245)])
    s = om._signals(CTX, om.OrgMonitoringInput(op="signals", org_id=245))["signals"][0]

    assert "resolved_by" not in s
    assert s["resolution"] == "en cours"


def test_les_filtres_passent(monkeypatch):
    vu = {}
    monkeypatch.setattr(om.db, "list_usage_signals", lambda **k: vu.update(k) or [])
    om._signals(CTX, om.OrgMonitoringInput(
        op="signals", org_id=245, signal="gap", tool="notion_search", status="open"))

    assert vu["signal"] == "gap" and vu["target"] == "notion_search"
    assert vu["status"] == "open"


def test_la_lentille_est_reservee_a_l_admin_de_CETTE_org():
    """Même palier que le journal d'audit : le corps d'un signal est de la prose libre,
    il ne se rend pas à un membre ordinaire ni à l'admin d'une autre org."""
    cap = next(c for c in om.CAPABILITIES if c.key == "org.monitoring.signals")
    assert cap.authz is om._ADMIN_OF
    assert cap.mcp is None      # servi par la console consolidée, pas un tool de plus
