"""Les limites d'envoi LinkedIn SANS base : le refus nommé que rend l'outil, la garde qui
laisse passer quand la base ne répond pas (fail-open), le 422 `cannot_resend_yet` de
LinkedIn rendu en refus nommé, et la validation des valeurs posées.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import unipile_limites
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import unipile as outil

_COMPTE_DU_CLIENT = unipile_limites.compte_du_client


class _Client:
    _account_id = "banc-compte"


@pytest.fixture
def garde_active(monkeypatch):
    monkeypatch.setattr(unipile_limites, "compte_du_client", _COMPTE_DU_CLIENT)
    return monkeypatch


def test_fenetre_pleine_rend_un_refus_nomme_et_n_appelle_rien(garde_active):
    store = unipile_limites.store
    garde_active.setattr(store, "limites", lambda a: {"invitations_per_day": 20})
    garde_active.setattr(store, "proprietaire", lambda a: "titulaire")
    libre = datetime.now(timezone.utc) + timedelta(hours=3)
    garde_active.setattr(store, "reserver", lambda *a: {"refus": {
        "lim": "invitations_per_day", "max": 20, "used": 20, "resets_at": libre}})
    appels = []
    with pytest.raises(McpError) as e:
        outil._within_limits(_Client(), unipile_limites.INVITATION,
                             lambda: appels.append("envoi"))
    assert appels == []
    data = e.value.error.data
    assert data["code"] == "linkedin_sending_limit_reached"
    assert data["retryable"] is True
    assert (data["limit"], data["max"], data["used"]) == ("invitations_per_day", 20, 20)
    assert 3 * 3600 - 60 < data["retry_after_seconds"] <= 3 * 3600 + 1
    assert "20/20" in e.value.error.message


def test_base_muette_l_envoi_part_quand_meme(garde_active):
    def panne(*a):
        raise RuntimeError("base injoignable")
    garde_active.setattr(unipile_limites.store, "limites", panne)
    assert outil._within_limits(_Client(), unipile_limites.MESSAGE, lambda: "envoyé") == "envoyé"


def test_sans_compte_resolu_rien_n_est_lu(garde_active):
    garde_active.setattr(unipile_limites.store, "limites",
                         lambda a: pytest.fail("aucune lecture sans compte"))

    class _SansCompte:
        _account_id = None
    assert outil._within_limits(_SansCompte(), unipile_limites.MESSAGE, lambda: 1) == 1


def test_le_422_cannot_resend_yet_devient_un_refus_nomme():
    from oto.tools.unipile.client import UnipileError
    refus = outil._invitation_refused(
        UnipileError("422: {\"type\": \"errors/cannot_resend_yet\"}", status_code=422))
    assert refus.error.data["code"] == "linkedin_invitation_limit"
    assert outil._invitation_refused(UnipileError("422: autre", status_code=422)) is None
    assert outil._invitation_refused(UnipileError("500", status_code=500)) is None


def test_validation_des_valeurs():
    assert unipile_limites.valider({"messages_per_day": 0, "invitations_per_day": None}) == {
        "messages_per_day": 0, "invitations_per_day": None}
    for faux in ({"inconnue": 1}, {"messages_per_day": True}, {"messages_per_day": 1.5},
                 {"messages_per_day": -1}, {"messages_per_day": 1001}):
        with pytest.raises(ValueError):
            unipile_limites.valider(faux)
