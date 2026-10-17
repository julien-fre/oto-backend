"""Plafond d'engagement — une soumission facturée au relevé ne part que si l'org peut la payer.

Un job FullEnrich se soumet gratuitement et se facture au relevé, sur ce qu'il a
trouvé : rien ne bornait ce qu'une org pouvait SOUMETTRE. Une org gratuite a ainsi
soumis des centaines de contacts sous son plafond, puis l'a dépassé de moitié en
quelques relevés, avant que le commerce ne la coupe. Le commerce pousse désormais ce
qui reste engageable (`service.org.spend_cap`, unités du fournisseur), et la
soumission réserve son coût au pire avant d'envoyer (`access/spend_cap.py`).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from oto_mcp.access import spend_cap


def _tool(name):
    from fastmcp import FastMCP
    from oto_mcp.tools import fullenrich

    m = FastMCP("t")
    fullenrich.register(m)
    return asyncio.run(m.get_tool(name))


def _contacts(n: int) -> list[dict]:
    return [{"first_name": f"F{i}", "last_name": f"L{i}", "linkedin_slug": f"f{i}-l{i}"}
            for i in range(n)]


@pytest.fixture(autouse=True)
def _compte_approvisionne(monkeypatch):
    from oto_mcp.tools import fullenrich
    monkeypatch.setattr(fullenrich, "_solde", lambda headers: 100)


# ── Le coût au pire, sur le barème du fournisseur ─────────────────────────────

@pytest.mark.parametrize("fields,par_contact", [
    (None, 11),                                         # défaut : e-mail pro + mobile
    (["contact.work_emails"], 1),
    (["contact.phones"], 10),
    (["contact.work_emails", "contact.personal_emails", "contact.phones"], 14),
    (["contact.phones", "contact.phones"], 10),         # un champ compte une fois
    (["contact.inconnu"], 10),                          # inconnu = le plus cher
])
def test_le_cout_au_pire_suit_les_champs_demandes(fields, par_contact):
    from oto_mcp.tools.fullenrich import _worst_case_credits
    assert _worst_case_credits(20, fields) == 20 * par_contact


# ── La soumission ──────────────────────────────────────────────────────────────

def _soumettre(*, is_platform=True, reserver=None, submit=None, **kw):
    reserver = reserver or (lambda c, u: spend_cap.Reservation(7, c, u))
    with patch("oto_mcp.access.resolve_api_key", return_value=("k", is_platform)), \
         patch.object(spend_cap, "reserver", side_effect=reserver) as res, \
         patch.object(spend_cap, "liberer") as lib, \
         patch("oto_mcp.tools.fullenrich.session_org.note_call_trace") as trace, \
         patch("oto.tools.fullenrich.client.FullenrichClient") as client_cls:
        if submit is not None:
            client_cls.return_value.submit.side_effect = submit
        else:
            client_cls.return_value.submit.return_value = "enr_1"
        try:
            out = _tool("fullenrich_enrich_linkedin").fn(**kw)
        except Exception as e:  # noqa: BLE001 — rendu au banc, qui l'inspecte
            out = e
    return out, res, lib, trace, client_cls.return_value.submit


def test_la_soumission_reserve_son_cout_au_pire_et_le_trace_avec_son_job():
    out, res, lib, trace, submit = _soumettre(contacts=_contacts(3))
    res.assert_called_once_with("fullenrich", 33)
    submit.assert_called_once()
    lib.assert_not_called()
    trace.assert_called_once_with(quantity=3, enrichment_id="enr_1", engaged_credits=33)
    assert out["enrichment_id"] == "enr_1"


def test_un_lot_que_l_org_ne_peut_pas_payer_est_refuse_AVANT_l_envoi():
    from oto_mcp.mcp_errors import McpError

    def plein(c, u):
        raise spend_cap.SpendCapExceeded(c, u, 22)

    out, _, lib, trace, submit = _soumettre(reserver=plein, contacts=_contacts(10))
    assert isinstance(out, McpError)
    assert out.error.data["code"] == spend_cap.CODE
    assert out.error.data["remaining"] == 22 and out.error.data["requested"] == 110
    assert "about 2 contacts" in out.error.message
    submit.assert_not_called()
    trace.assert_not_called()
    lib.assert_not_called()


@pytest.mark.parametrize("erreur", [ValueError("bad contact"), RuntimeError("upstream")])
def test_un_lot_refuse_par_le_fournisseur_rend_sa_reservation(erreur):
    out, _, lib, trace, _ = _soumettre(submit=erreur, contacts=_contacts(2))
    assert isinstance(out, Exception)
    lib.assert_called_once_with(spend_cap.Reservation(7, "fullenrich", 22))
    trace.assert_not_called()


def test_sur_sa_propre_cle_l_org_n_est_pas_plafonnee():
    _, res, _, trace, submit = _soumettre(is_platform=False, contacts=_contacts(2))
    res.assert_not_called()
    submit.assert_called_once()
    trace.assert_called_once_with(quantity=2, enrichment_id="enr_1", engaged_credits=22)


def test_le_job_et_l_engagement_rejoignent_le_journal_et_facturent_sur_la_cible():
    from oto_mcp import server
    from oto_mcp.calllog import apply_call_trace
    from oto_mcp.db import usage as dbu
    from oto_mcp.tools import meta

    noms = {"enrichment_id", dbu.BILLABLE_ENGAGED_ARG}
    assert noms <= set(server._TRACED_ARGS)
    assert noms <= set(meta._BILLING_TRACE_KEYS)
    assert "enrichment_id" in dbu.BILLABLE_JOB_ARGS
    row = apply_call_trace({"args": {"contacts": "[...]"}},
                           {"quantity": 3, "enrichment_id": "enr_1", "engaged_credits": 33},
                           server._TRACED_ARGS)
    assert row["args"] == {"contacts": "[...]", "enrichment_id": "enr_1",
                           "engaged_credits": 33}


def test_la_capacite_ne_plafonne_que_ce_qu_un_outil_reserve():
    """Deux listes du même fait : la porte de service et la table des outils qui
    engagent. Un plafond posé sur un connecteur qu'aucun outil ne réserve ne
    bornerait rien."""
    import typing
    from oto_mcp.capabilities.org_spend_cap import OrgSpendCapInput
    assert set(typing.get_args(OrgSpendCapInput.model_fields["connector"].annotation)) == \
        set(spend_cap.ENGAGING_TOOLS)


# ── La réservation, sans base ─────────────────────────────────────────────────

def test_sans_identite_rien_n_est_reserve():
    with patch("oto_mcp.access.spend_cap.current_user_sub_from_token", return_value=None):
        assert spend_cap.reserver("fullenrich", 10) is None


def test_une_org_illisible_sert_l_appel_et_le_dit(caplog):
    with patch("oto_mcp.access.spend_cap.current_user_sub_from_token", return_value="s"), \
         patch("oto_mcp.access.current_org", side_effect=RuntimeError("db down")), \
         caplog.at_level(logging.ERROR):
        assert spend_cap.reserver("fullenrich", 10) is None
    assert "appel servi" in caplog.text


def test_un_compteur_en_panne_sert_l_appel_et_le_dit(caplog):
    with patch.object(spend_cap, "_org", return_value=42), \
         patch.object(spend_cap.db_cap, "reserver", side_effect=RuntimeError("db down")), \
         caplog.at_level(logging.ERROR):
        assert spend_cap.reserver("fullenrich", 10) is None
    assert "appel servi" in caplog.text


# ── Contre un vrai PostgreSQL ─────────────────────────────────────────────────

def _soumission(org, *, quand, engaged, ok=True, tool="fullenrich_enrich_linkedin"):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    db.insert_tool_call({"sub": "s", "kind": "mcp", "tool": tool, "ok": ok, "org_id": org,
                         "duration_ms": 3, "quantity": 1, "key_mode": "platform",
                         "args": {"enrichment_id": f"enr-{uuid.uuid4().hex[:6]}",
                                  "engaged_credits": engaged}})
    with _connect() as conn:
        conn.execute("UPDATE tool_calls SET created_at = %s WHERE id = "
                     "(SELECT max(id) FROM tool_calls)", (quand,))


def test_sans_plafond_pose_rien_n_est_borne(live):
    from oto_mcp.db import spend_cap as db_cap
    assert db_cap.reserver(424242, "fullenrich", 10_000) is None


def test_la_reservation_borne_et_se_rend(live):
    from oto_mcp import org_store
    from oto_mcp.db import spend_cap as db_cap

    org = org_store.create_org("Plafond", created_by="s-" + uuid.uuid4().hex[:6])
    db_cap.poser(org, "fullenrich", 100, datetime.now(timezone.utc),
                 "fullenrich_enrich_linkedin")
    assert db_cap.reserver(org, "fullenrich", 60) == {"ok": True, "units_left": 100,
                                                      "engaged": 60}
    assert db_cap.reserver(org, "fullenrich", 41) == {"ok": False, "units_left": 100,
                                                      "engaged": 60}
    assert db_cap.reserver(org, "fullenrich", 40)["ok"] is True
    db_cap.liberer(org, "fullenrich", 40)
    assert db_cap.reserver(org, "fullenrich", 40)["engaged"] == 100
    db_cap.poser(org, "fullenrich", None, datetime.now(timezone.utc),
                 "fullenrich_enrich_linkedin")
    assert db_cap.reserver(org, "fullenrich", 10_000) is None


def test_une_poussee_recompte_ce_qui_a_ete_engage_APRES_sa_lecture(live):
    """Le commerce a lu jusqu'à `as_of` : ce qui a été soumis après, il ne l'a pas vu
    — une remise à zéro le rendrait engageable une seconde fois."""
    from oto_mcp import org_store
    from oto_mcp.db import spend_cap as db_cap

    org = org_store.create_org("Recompte", created_by="s-" + uuid.uuid4().hex[:6])
    autre = org_store.create_org("Ailleurs", created_by="s-" + uuid.uuid4().hex[:6])
    lu = datetime.now(timezone.utc) - timedelta(minutes=5)
    _soumission(org, quand=lu - timedelta(minutes=1), engaged=500)   # vu par le commerce
    _soumission(org, quand=lu + timedelta(minutes=1), engaged=33)
    _soumission(org, quand=lu + timedelta(minutes=2), engaged=22)
    _soumission(org, quand=lu + timedelta(minutes=2), engaged=99, ok=False)   # échec
    _soumission(autre, quand=lu + timedelta(minutes=2), engaged=77)           # autre org
    _soumission(org, quand=lu + timedelta(minutes=3), engaged=11, tool="fullenrich_result")
    row = db_cap.poser(org, "fullenrich", 200, lu, "fullenrich_enrich_linkedin")
    assert row["engaged"] == 55 and row["units_left"] == 200


def test_la_lentille_rend_le_job_et_l_engagement_d_une_soumission(live):
    from oto_mcp import db, org_store

    org = org_store.create_org("Lentille", created_by="s-" + uuid.uuid4().hex[:6])
    quand = datetime.now(timezone.utc) - timedelta(minutes=10)
    _soumission(org, quand=quand, engaged=33)
    p = db.list_billable_calls_for_org(org, "fullenrich_enrich_linkedin",
                                       since=(quand - timedelta(minutes=1)).isoformat())
    (c,) = p["calls"]
    assert c["engaged"] == 33 and c["job_id"].startswith("enr-")
    assert "engaged_credits" not in c
