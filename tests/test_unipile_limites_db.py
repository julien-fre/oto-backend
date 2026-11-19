"""Les limites d'envoi LinkedIn SUR UNE VRAIE BASE : aucune limite par défaut (tout passe,
tout se compte), la fenêtre pleine qui refuse sans rien envoyer, la place rendue quand
l'envoi échoue, la semaine glissante à côté du jour, 0 qui met l'action en pause, la
limite PAR COMPTE (le même compte relié dans deux orgs n'a qu'un compte), et la pose
réservée au titulaire du compte.
"""
from __future__ import annotations

import pytest

from oto_mcp import unipile_limites
from oto_mcp.capabilities._types import AuthzDenied

# Pris à l'import, avant le double sans base de `conftest.py`.
_COMPTE_DU_CLIENT = unipile_limites.compte_du_client

TITULAIRE, COLLEGUE = "banc-limites-titulaire", "banc-limites-collegue"
COMPTE = "banc-compte-li-1"


class _Client:
    def __init__(self, account_id):
        self._account_id = account_id


@pytest.fixture
def orgs(live, monkeypatch):
    from oto_mcp.db._conn import _connect
    monkeypatch.setattr(unipile_limites, "compte_du_client", _COMPTE_DU_CLIENT)
    with _connect() as c:
        c.execute("INSERT INTO users (sub) VALUES (%s), (%s) ON CONFLICT DO NOTHING",
                  (TITULAIRE, COLLEGUE))
        a = c.execute("INSERT INTO orgs (name) VALUES ('banc-limites-a') RETURNING id"
                      ).fetchone()["id"]
        b = c.execute("INSERT INTO orgs (name) VALUES ('banc-limites-b') RETURNING id"
                      ).fetchone()["id"]
        for org in (a, b):
            c.execute("INSERT INTO unipile_accounts (sub, org_id, provider, account_id, "
                      "account_name) VALUES (%s, %s, 'LINKEDIN', %s, 'Banc')",
                      (TITULAIRE, org, COMPTE))
    yield a, b
    with _connect() as c:
        c.execute("DELETE FROM unipile_sending_log")
        c.execute("DELETE FROM unipile_sending_limits")


def _envoyer(action=unipile_limites.INVITATION, compte=COMPTE):
    with unipile_limites.garde(_Client(compte), action):
        return "envoyé"


def _ctx(sub, org, monkeypatch):
    from oto_mcp import access
    from oto_mcp.capabilities._types import ResolvedCtx
    monkeypatch.setattr(access, "current_org", lambda s: org)
    return ResolvedCtx(sub=sub)


def test_sans_limite_tout_passe_et_tout_se_compte(orgs):
    for _ in range(5):
        assert _envoyer() == "envoyé"
    vue = unipile_limites.vue(COMPTE)
    assert vue["invitations_per_day"]["max"] is None
    assert vue["invitations_per_day"]["used"] == 5
    assert vue["invitations_per_week"]["used"] == 5
    assert vue["messages_per_day"]["used"] == 0


def test_la_fenetre_pleine_refuse_et_dit_quand_une_place_se_libere(orgs):
    from oto_mcp.db import unipile_limites as store
    store.poser(COMPTE, TITULAIRE, {"invitations_per_day": 2})
    _envoyer(), _envoyer()
    with pytest.raises(unipile_limites.LimiteAtteinte) as e:
        _envoyer()
    assert (e.value.lim, e.value.maxi, e.value.used) == ("invitations_per_day", 2, 2)
    assert e.value.resets_at is not None
    assert 0 < e.value.secondes_restantes() <= unipile_limites.JOUR + 1
    # Le refus n'a rien compté ; une autre action du compte n'est pas bornée par elle.
    assert unipile_limites.vue(COMPTE)["invitations_per_day"]["used"] == 2
    assert _envoyer(unipile_limites.MESSAGE) == "envoyé"


def test_un_envoi_qui_echoue_rend_sa_place(orgs):
    from oto_mcp.db import unipile_limites as store
    store.poser(COMPTE, TITULAIRE, {"messages_per_day": 1})
    with pytest.raises(RuntimeError):
        with unipile_limites.garde(_Client(COMPTE), unipile_limites.MESSAGE):
            raise RuntimeError("LinkedIn a refusé")
    assert unipile_limites.vue(COMPTE)["messages_per_day"]["used"] == 0
    assert _envoyer(unipile_limites.MESSAGE) == "envoyé"


def test_la_semaine_borne_aussi_et_zero_met_en_pause(orgs):
    from oto_mcp.db import unipile_limites as store
    store.poser(COMPTE, TITULAIRE, {"invitations_per_day": 10, "invitations_per_week": 1})
    _envoyer()
    with pytest.raises(unipile_limites.LimiteAtteinte) as e:
        _envoyer()
    assert e.value.lim == "invitations_per_week"
    store.poser(COMPTE, TITULAIRE, {"profile_visits_per_day": 0})
    with pytest.raises(unipile_limites.LimiteAtteinte) as e:
        _envoyer(unipile_limites.VISITE)
    assert "paused" in e.value.phrase()


def test_la_limite_est_celle_du_compte_pas_de_l_org(orgs, monkeypatch):
    from oto_mcp.capabilities import unipile_limits as cap
    a, b = orgs
    cap._set(_ctx(TITULAIRE, a, monkeypatch),
             cap.LimitsSetInput(account_id=COMPTE, limits={"invitations_per_day": 1}))
    _envoyer()
    # Le même compte relié dans l'org B : même limite, même compte d'actions.
    vue_b = cap._get(_ctx(TITULAIRE, b, monkeypatch), cap.LimitsInput())
    assert [x["account_id"] for x in vue_b["accounts"]] == [COMPTE]
    lim = vue_b["accounts"][0]["limits"]["invitations_per_day"]
    assert (lim["max"], lim["used"]) == (1, 1)
    with pytest.raises(unipile_limites.LimiteAtteinte):
        _envoyer()


def test_seul_le_titulaire_pose_et_null_retire(orgs, monkeypatch):
    from oto_mcp.capabilities import unipile_limits as cap
    a, _ = orgs
    with pytest.raises(AuthzDenied) as e:
        cap._set(_ctx(COLLEGUE, a, monkeypatch),
                 cap.LimitsSetInput(account_id=COMPTE, limits={"messages_per_day": 1}))
    assert e.value.status == 404
    titulaire = _ctx(TITULAIRE, a, monkeypatch)
    for faux in ({"messages_per_hour": 3}, {"messages_per_day": -1},
                 {"messages_per_day": unipile_limites.SAISIE_MAX + 1}):
        with pytest.raises(AuthzDenied) as e:
            cap._set(titulaire, cap.LimitsSetInput(account_id=COMPTE, limits=faux))
        assert e.value.status == 400
    vue = cap._set(titulaire, cap.LimitsSetInput(
        account_id=COMPTE, limits={"messages_per_day": 40, "invitations_per_day": 15}))
    assert vue["limits"]["messages_per_day"]["max"] == 40
    vue = cap._set(titulaire, cap.LimitsSetInput(
        account_id=COMPTE, limits={"messages_per_day": None}))
    assert vue["limits"]["messages_per_day"]["max"] is None
    assert vue["limits"]["invitations_per_day"]["max"] == 15   # non nommée : gardée
