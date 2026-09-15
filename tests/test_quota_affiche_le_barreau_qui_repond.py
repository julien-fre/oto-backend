"""`/api/me` n'annonce un plafond que si la clé PLATEFORME est celle qui répond.

Vécu sur une org servie par une clé de TENANT (L-clés PR 1) : sa fiche connecteur
affichait « 0/200 aujourd'hui » sur serper, alors qu'AUCUN plafond ne lui est
opposé. Le défaut : `status_for` prend le premier barreau `platform` des `hits` —
or les hits portent TOUTE la cascade, pas le seul gagnant, et un connecteur
`platform_key_open` en a toujours un (free tier, ouvert à tous). La condition
« un barreau plateforme existe » n'est donc pas « la clé plateforme te sert ».

Ce que le produit garantit, et que ces deux tests gravent :
- `resolve_api_key` rend AVANT `_win_quota` dès que `win.mode != "platform"` ;
- les outils n'appellent `record_platform_usage` que sous `if is_platform`.
Un plafond annoncé hors du barreau plateforme est donc un plafond FANTÔME : ni
compté, ni opposable.
"""
from __future__ import annotations

import pytest

from oto_mcp import access
from oto_mcp.access import status as A_status


@pytest.fixture
def fiche(monkeypatch):
    """`status_for` réduit à sa projection : la cascade est injectée, rien d'autre
    ne touche la base (le reste du snapshot est stubbé au plus court)."""
    from oto_mcp import db

    monkeypatch.setattr(db, "KEY_PROVIDERS", ("serper",))
    monkeypatch.setattr(A_status.scope, "get_user_role", lambda s: "member")
    monkeypatch.setattr(A_status.group_store, "list_groups_for_user", lambda s, o: [])
    monkeypatch.setattr(A_status.cascade, "group_secret_map", lambda g: {})
    monkeypatch.setattr(A_status.cascade, "preloaded_presence_probe",
                        lambda s, org=None, groups=None: A_status.cascade.PRESENCE_PROBE)
    monkeypatch.setattr(A_status.db, "usage_today_map", lambda s: {"serper": 0})
    monkeypatch.setattr(A_status.rbac, "reachable_team_key", lambda *a, **k: None)
    # Les connecteurs à SESSION (secret_kind="cookie") sont projetés par une
    # seconde boucle, qui sonde le coffre : hors sujet ici, et seule chose qui
    # restait à toucher la base.
    monkeypatch.setattr(A_status.credentials_store, "credential_status",
                        lambda *a, **k: None)

    def _fiche(*rungs):
        monkeypatch.setattr(A_status.chain_shadow, "resolution_rungs",
                            lambda sub, provider, **kw: list(rungs))
        return A_status.status_for("u1", org=269, group=None)["providers"]["serper"]

    return _fiche


_PLATEFORME = access.CascadeRung("platform", "platform", "tulina",
                                 {"label": "tulina", "daily_quota": None})


@pytest.mark.parametrize("gagnant, mode_attendu", [
    (access.CascadeRung("tenant", "tenant", "pilote", True), "tenant"),
    (access.CascadeRung("org", "org", "269", True), "org"),
    (access.CascadeRung("user", "member", "269:u1", True), "user"),
])
def test_aucun_plafond_annonce_quand_une_cle_plus_proche_repond(fiche, gagnant, mode_attendu):
    """Le barreau plateforme reste dans les hits (free tier) — il ne doit plus
    suffire à faire annoncer son plafond."""
    st = fiche(gagnant, _PLATEFORME)
    assert st["mode"] == mode_attendu
    assert st["quota_daily"] is None, (
        f"plafond fantôme annoncé à un appelant servi par sa clé `{mode_attendu}`")
    # Le LABEL, lui, reste servi : « ce sur quoi tu retomberais » est une
    # information juste, et le front l'affiche depuis v1.12.0. Seul le PLAFOND
    # mentait, parce que lui se lit comme une contrainte présente.
    assert st["platform_key_label"] == "tulina"


def test_le_plafond_reste_annonce_quand_la_plateforme_repond(fiche):
    """La moitié qui doit NE PAS bouger : taire un vrai plafond ferait découvrir
    la limite au refus sec, ce que `platform_quota_hint` existe pour éviter."""
    st = fiche(_PLATEFORME)
    assert st["mode"] == "platform"
    assert st["quota_daily"] == 200      # default_quota du registre pour serper
    assert st["quota_used_today"] == 0   # compteur servi, lui aussi, sur ce barreau


# ── le cliquet : la LISTE des champs d'effet, pas un champ nommé ──────────────

# Tout champ qui décrit l'EFFET COURANT du barreau plateforme. Il se lit sur le
# GAGNANT, jamais sur la seule présence d'un barreau dans `hits`.
#
# ⚠️ Ce tuple est le cliquet : un champ d'effet ajouté à `status_for` sans être
# gaté fait échouer le test ci-dessous DÈS qu'il est ajouté ici — et l'oublier
# ici est exactement ce qui a produit le défaut d'origine. Le réflexe à garder :
# un champ neuf décrit-il ce qui SERT MAINTENANT (→ ce tuple, et `winner`), ou ce
# qui EXISTE autour (→ `hits`, comme `platform_key_label`) ?
CHAMPS_D_EFFET_PLATEFORME = ("quota_daily", "quota_used_today")


@pytest.mark.parametrize("gagnant", [
    access.CascadeRung("tenant", "tenant", "pilote", True),
    access.CascadeRung("org", "org", "269", True),
    access.CascadeRung("group", "group", "3", True),
    access.CascadeRung("user", "member", "269:u1", True),
])
@pytest.mark.parametrize("champ", CHAMPS_D_EFFET_PLATEFORME)
def test_aucun_champ_d_effet_plateforme_hors_du_barreau_plateforme(fiche, gagnant, champ):
    """Le cliquet de la CLASSE, pas du seul bug vécu : sur chaque barreau plus
    proche, aucun champ d'effet plateforme n'est servi — le barreau plateforme
    restant pourtant présent dans les hits (free tier)."""
    st = fiche(gagnant, _PLATEFORME)
    assert st[champ] is None, (
        f"`{champ}` servi à un appelant servi par sa clé `{st['mode']}` — "
        "champ d'effet lu sur les hits au lieu du gagnant")


def test_tous_les_champs_d_effet_sont_servis_sur_le_barreau_plateforme(fiche):
    """Le pendant : le cliquet ne doit pas pouvoir être satisfait en TAISANT
    partout. Chaque champ listé est bien rendu quand la plateforme répond."""
    st = fiche(_PLATEFORME)
    for champ in CHAMPS_D_EFFET_PLATEFORME:
        assert st[champ] is not None, f"`{champ}` tu sur le barreau plateforme"
