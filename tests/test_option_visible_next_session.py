"""Accorder une option ne montre pas l'outil TOUT DE SUITE — et la réponse le dit
(signal #660, 02/09/2026).

Le geste vécu : `oto_admin_set_option(entity_type='org', entity_id=196, option='beta',
on=true)` rend `ok: true`, la ligne est bien posée… et `oto_fleet` reste absent du
catalogue de la session. La visibilité des outils se calcule au HANDSHAKE
(`session_visibility.apply_session_visibility`) et ne se rejoue à chaud que pour les
capacités marquées `refresh_visibility=True` — ce que l'octroi d'option n'était pas.

Le vrai manque n'était pas le rafraîchissement, c'était le SILENCE : `ok: true` +
`platform_key: null` ne permet pas de distinguer « l'option n'a pas pris » de « elle a
pris, la session ne l'a pas encore vue ». Deux états opposés sous la même réponse.

Deux crans, donc :
  1. la capacité re-pousse la visibilité sur la session de l'appelant ;
  2. la réponse NOMME où l'effet atterrit (`visible_next_session`), et la description
     servie l'explique — un booléen que personne ne sait lire ne vaut pas mieux qu'un
     silence.
"""
from __future__ import annotations

import types

import pytest

from oto_mcp.capabilities import users_admin as ua
from oto_mcp.capabilities._types import ResolvedCtx

ADMIN = "sub-admin-660"
CTX = ResolvedCtx(sub=ADMIN, org_id=7)


@pytest.fixture
def sans_db(monkeypatch):
    """L'écriture et la composition de clé neutralisées : ce test-ci ne juge que ce
    que la réponse DÉCLARE, pas ce qu'elle écrit (couvert par test_option_compose)."""
    monkeypatch.setattr(ua.db, "get_user", lambda eid: {"sub": eid})
    monkeypatch.setattr(ua.org_store, "get_org", lambda oid: {"id": oid})
    monkeypatch.setattr(ua.db, "set_option_comp",
                        lambda et, eid, opt, granted_by=None: None)
    monkeypatch.setattr(ua.db, "clear_option_comp", lambda et, eid, opt: None)
    monkeypatch.setattr(ua.providers, "connector_for_provider", lambda p: None)
    monkeypatch.setattr(ua.credentials_store, "list_platform_instances", lambda p: [])
    monkeypatch.setattr(ua.access, "current_org", lambda sub: 7)


def _set(**kw):
    return ua._set_option(CTX, ua.OptionInput(**kw))


# ── Ce que la réponse déclare ────────────────────────────────────────────────

def test_option_sur_soi_meme_l_effet_est_ici(sans_db):
    """Le cas d'usage le plus fréquent — on s'accorde l'option pour tester : la
    session de l'appelant est celle qui change, et `refresh_visibility` la repousse."""
    assert _set(entity_type="user", entity_id=ADMIN,
                option="beta", on=True)["visible_next_session"] is False


def test_option_sur_l_org_effective_de_l_appelant_l_effet_est_ici(sans_db):
    """Une option d'ORG gouverne la boîte à outils de qui travaille SOUS cette org :
    si c'est celle de l'appelant, sa propre liste vient d'être recalculée."""
    assert _set(entity_type="org", entity_id="7",
                option="beta", on=True)["visible_next_session"] is False


def test_option_pour_quelqu_un_d_autre_l_effet_est_ailleurs(sans_db):
    """Le bénéficiaire est un autre compte : aucune session ouverte n'est joignable
    d'ici. Le dire vaut mieux qu'un `ok: true` que l'admin va tester chez lui."""
    assert _set(entity_type="user", entity_id="sub-julien",
                option="beta", on=True)["visible_next_session"] is True


def test_option_sur_une_autre_org_l_effet_est_ailleurs(sans_db):
    """Le geste exact du signal : l'admin travaille sous son org, il accorde l'option
    à l'org 196 (celle du bénéficiaire). Rien ne change dans SA session — et c'est ce
    qu'il faut dire, sinon il teste chez lui et conclut que l'option n'a pas pris."""
    assert _set(entity_type="org", entity_id="196",
                option="beta", on=True)["visible_next_session"] is True


def test_le_retrait_le_declare_aussi(sans_db):
    """Retirer une option masque une surface : la même question se pose, dans l'autre
    sens. Un champ qui ne parlerait qu'à l'octroi laisserait la moitié du doute."""
    assert _set(entity_type="user", entity_id=ADMIN,
                option="beta", on=False)["visible_next_session"] is False


def test_l_org_illisible_ne_promet_rien(sans_db, monkeypatch):
    """Fail-closed du RÉCIT : si l'org effective de l'appelant est illisible, on
    annonce « prochaine session » plutôt que de promettre un rafraîchissement qui
    n'aura peut-être pas lieu. Une promesse fausse coûte plus qu'un doute nommé."""
    monkeypatch.setattr(ua.access, "current_org", lambda sub: None)
    assert _set(entity_type="org", entity_id="7",
                option="beta", on=True)["visible_next_session"] is True


# ── Ce que la surface servie porte ───────────────────────────────────────────

def _cap():
    from oto_mcp.capabilities.registry import CAPABILITIES
    return next(c for c in CAPABILITIES if c.key == "platform.option.set")


def test_la_capacite_repousse_la_visibilite():
    """Le mécanisme existait déjà (`refresh_visibility`, émetteur de
    `tools/list_changed`) — l'octroi d'option n'en faisait simplement pas partie."""
    assert _cap().refresh_visibility is True


def test_la_description_servie_explique_le_champ():
    """Un booléen dont le sens n'est écrit nulle part ne se lit pas : c'est la
    description, relue à chaque appel, qui le rend utilisable."""
    d = _cap().description
    assert "visible_next_session" in d
    assert "handshake" in d
