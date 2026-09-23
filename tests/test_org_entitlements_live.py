"""Droits déclarés par org (ADR 0070 §7), sur une vraie base.

Ce que ces bancs tiennent :
- un droit est vivant ssi `starts_at <= NOW()` et (`expires_at` NULL ou futur), et ce
  filtre est celui de la BASE — échu, pas encore commencé, sans date ;
- deux sources du même droit ne s'écrasent pas : une seule vivante suffit, et retirer
  l'une laisse l'autre ;
- poser deux fois la même ligne ne fait qu'une ligne ;
- la console voit le droit échu (`list_for_org` ne filtre pas) ;
- supprimer l'org emporte ses droits.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from oto_mcp import access
from oto_mcp.access.entitlements import org_has
from oto_mcp.db import entitlements as E
from oto_mcp.db._conn import _connect

HIER = datetime.now(timezone.utc) - timedelta(days=1)
DEMAIN = datetime.now(timezone.utc) + timedelta(days=1)


def _org() -> int:
    """Une org PROPRE au test : la CI répartit les tests un par un sur plusieurs
    processus, donc aucun test ne peut compter sur ce qu'un autre a écrit."""
    with _connect() as conn:
        return conn.execute("INSERT INTO orgs (name) VALUES (%s) RETURNING id",
                            (f"org-{uuid.uuid4().hex[:8]}",)).fetchone()["id"]


def test_sans_date_le_droit_est_vivant(live):
    org = _org()
    assert not org_has(org, "members")
    E.grant(org, "members", "trial")
    assert org_has(org, "members")
    assert not org_has(org, "autre"), "un droit ne s'étend pas aux clés voisines"


def test_une_ligne_echue_ne_donne_rien(live):
    org = _org()
    E.grant(org, "members", "trial", starts_at=HIER - timedelta(days=1), expires_at=HIER)
    assert not E.has(org, "members")


def test_une_ligne_pas_encore_commencee_ne_donne_rien(live):
    org = _org()
    E.grant(org, "members", "subscription", starts_at=DEMAIN)
    assert not E.has(org, "members")


def test_une_ligne_bornee_des_deux_cotes_est_vivante_entre_ses_bornes(live):
    org = _org()
    E.grant(org, "members", "offered", starts_at=HIER, expires_at=DEMAIN)
    assert E.has(org, "members")


def test_deux_sources_une_echue_une_vivante_le_droit_tient(live):
    org = _org()
    E.grant(org, "members", "trial", starts_at=HIER - timedelta(days=1), expires_at=HIER)
    E.grant(org, "members", "subscription", value=10, expires_at=DEMAIN)
    assert E.has(org, "members")
    assert {r["source"] for r in E.list_for_org(org)} == {"trial", "subscription"}
    assert E.revoke(org, "members", "subscription")
    assert not E.has(org, "members"), "la seule source vivante est retirée"
    assert [r["source"] for r in E.list_for_org(org)] == ["trial"]
    assert not E.revoke(org, "members", "subscription"), "rien à retirer deux fois"


def test_rejouer_un_grant_ne_fait_qu_une_ligne_et_remplace_ses_bornes(live):
    org = _org()
    E.grant(org, "seats", "partner", value=3, expires_at=HIER, granted_by="a")
    E.grant(org, "seats", "partner", value=5, granted_by="b")
    (ligne,) = E.list_for_org(org)
    assert (ligne["value"], ligne["expires_at"], ligne["granted_by"]) == (5, None, "b")
    assert E.has(org, "seats"), "la nouvelle pose efface l'échéance de l'ancienne"


def test_value_omise_veut_dire_pas_d_avis(live):
    org = _org()
    E.grant(org, "members", "trial")
    (ligne,) = E.list_for_org(org)
    assert ligne["value"] is None


def test_la_console_voit_le_droit_echu(live):
    org = _org()
    E.grant(org, "members", "trial", starts_at=HIER - timedelta(days=1), expires_at=HIER)
    (ligne,) = E.list_for_org(org)
    assert (ligne["right_key"], ligne["source"]) == ("members", "trial")
    assert ligne["expires_at"] is not None


def test_supprimer_l_org_emporte_ses_droits(live):
    org = _org()
    E.grant(org, "members", "trial")
    with _connect() as conn:
        conn.execute("DELETE FROM orgs WHERE id = %s", (org,))
    assert E.list_for_org(org) == []


def test_le_point_d_acces_est_sur_la_facade_access(live):
    assert access.org_has is org_has
