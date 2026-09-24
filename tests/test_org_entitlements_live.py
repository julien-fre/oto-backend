"""Droits déclarés (ADR 0070 §7, #1066), sur une vraie base.

Ce que ces bancs tiennent :
- **la forme** : une ligne par (org, personne, droit, source) — une ligne d'org et une
  ligne de personne du même droit et de la même source coexistent, reposer remplace ;
- **les dates** : début inclus, fin exclue, borne nulle = non bornée, et ce filtre est
  celui de la BASE ;
- **le cumul** : le plus généreux gagne, org et personne confondues, toutes sources ;
- **le défaut** : sans ligne valide, le défaut DÉCLARÉ par l'instance — et une ligne
  moins généreuse que lui l'emporte quand même ;
- **le catalogue** : clé hors catalogue, valeur vide, valeur hors genre refusées à la
  pose, rien n'est écrit ;
- les listes (org, personne, droit), et la suppression de l'org qui emporte ses droits.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import access
from oto_mcp import entitlements_catalogue as C
from oto_mcp.access.entitlements import org_has, value_for
from oto_mcp.db import entitlements as E
from oto_mcp.db._conn import _connect

MAINTENANT = datetime.now(timezone.utc)
HIER = MAINTENANT - timedelta(days=1)
DEMAIN = MAINTENANT + timedelta(days=1)
SIEGES = C.UNIPILE_SEATS
# Le défaut d'instance gréé par `tests/conftest.py` pour `unipile_seats`.
DEFAUT_SIEGES = 5


def _org() -> int:
    """Une org PROPRE au test : la CI répartit les tests un par un sur plusieurs
    processus, donc aucun test ne peut compter sur ce qu'un autre a écrit."""
    with _connect() as conn:
        return conn.execute("INSERT INTO orgs (name) VALUES (%s) RETURNING id",
                            (f"org-{uuid.uuid4().hex[:8]}",)).fetchone()["id"]


def _sub() -> str:
    return f"u-{uuid.uuid4().hex[:8]}"


# ── la forme ────────────────────────────────────────────────────────────────────

def test_ligne_d_org_et_ligne_de_personne_coexistent_meme_droit_meme_source(live):
    org, sub = _org(), _sub()
    E.grant(org, SIEGES, "subscription", value=3)
    E.grant(org, SIEGES, "subscription", value=7, sub=sub)
    lignes = E.list_for_org(org)
    assert [(r["sub"], r["value"]) for r in lignes] == [(None, 3), (sub, 7)]


def test_reposer_remplace_la_ligne_de_la_meme_portee_et_de_la_meme_source(live):
    org, sub = _org(), _sub()
    E.grant(org, SIEGES, "partner", value=3, expires_at=HIER, granted_by="a", sub=sub)
    E.grant(org, SIEGES, "partner", value=5, granted_by="b", sub=sub)
    (ligne,) = E.list_for_org(org)
    assert (ligne["sub"], ligne["value"], ligne["expires_at"], ligne["granted_by"]) == (
        sub, 5, None, "b")


def test_la_base_tient_l_unicite_sub_nul_compris(live):
    """La contrainte, pas le code : deux lignes d'org (`sub` NULL) du même droit et de
    la même source sont refusées par la base elle-même."""
    import psycopg
    org = _org()
    E.grant(org, SIEGES, "offered", value=1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with _connect() as conn:
            conn.execute("INSERT INTO org_entitlements (org_id, right_key, source, value) "
                         "VALUES (%s, %s, 'offered', 2)", (org, SIEGES))


def test_retirer_une_portee_laisse_l_autre(live):
    org, sub = _org(), _sub()
    E.grant(org, C.UNIPILE, "trial", value=1)
    E.grant(org, C.UNIPILE, "trial", value=1, sub=sub)
    assert E.revoke(org, C.UNIPILE, "trial", sub=sub)
    assert [r["sub"] for r in E.list_for_org(org)] == [None]
    assert not E.revoke(org, C.UNIPILE, "trial", sub=sub), "rien à retirer deux fois"
    assert E.revoke(org, C.UNIPILE, "trial")
    assert E.list_for_org(org) == []


# ── les dates ───────────────────────────────────────────────────────────────────

def test_une_ligne_echue_ou_pas_encore_commencee_ne_compte_pas(live):
    org = _org()
    E.grant(org, SIEGES, "trial", value=40, starts_at=HIER - timedelta(days=1),
            expires_at=HIER)
    E.grant(org, SIEGES, "subscription", value=50, starts_at=DEMAIN)
    assert value_for(None, org, SIEGES) == DEFAUT_SIEGES


def test_debut_inclus_fin_exclue(live):
    org = _org()
    debut, fin = MAINTENANT - timedelta(hours=1), MAINTENANT + timedelta(hours=1)
    E.grant(org, SIEGES, "offered", value=9, starts_at=debut, expires_at=fin)
    assert value_for(None, org, SIEGES, now=debut) == 9, "le début est inclus"
    assert value_for(None, org, SIEGES, now=fin - timedelta(microseconds=1)) == 9
    assert value_for(None, org, SIEGES, now=fin) == DEFAUT_SIEGES, "la fin est exclue"
    assert value_for(None, org, SIEGES, now=debut - timedelta(seconds=1)) == DEFAUT_SIEGES


def test_sans_borne_la_ligne_vaut_toujours(live):
    org = _org()
    E.grant(org, SIEGES, "contract", value=12, starts_at=HIER)
    assert value_for(None, org, SIEGES, now=MAINTENANT + timedelta(days=3650)) == 12


# ── le cumul ────────────────────────────────────────────────────────────────────

def test_le_plus_genereux_gagne_org_et_personne_confondues(live):
    org, sub, autre = _org(), _sub(), _sub()
    E.grant(org, SIEGES, "subscription", value=3)
    E.grant(org, SIEGES, "offered", value=10, sub=sub)
    assert value_for(sub, org, SIEGES) == 10, "la ligne de la personne, plus généreuse"
    assert value_for(autre, org, SIEGES) == 3, "une autre personne n'a que l'org"
    assert value_for(None, org, SIEGES) == 3, "l'org seule ne voit pas la personne"
    E.grant(org, SIEGES, "partner", value=20)
    assert value_for(sub, org, SIEGES) == 20, "l'org plus généreuse gagne à son tour"


def test_une_ligne_de_personne_ne_deborde_pas_sur_une_autre_org(live):
    org, ailleurs, sub = _org(), _org(), _sub()
    E.grant(ailleurs, SIEGES, "offered", value=30, sub=sub)
    assert value_for(sub, org, SIEGES) == DEFAUT_SIEGES


def test_un_non_explicite_ne_retire_pas_un_oui(live):
    org = _org()
    E.grant(org, C.UNIPILE, "offered", value=0)
    assert not org_has(org, C.UNIPILE), "une ligne à 0 dit « non »"
    E.grant(org, C.UNIPILE, "subscription", value=1)
    assert org_has(org, C.UNIPILE), "un don ne retire jamais ce qu'un abonnement donne"


# ── le défaut d'instance ────────────────────────────────────────────────────────

def test_sans_ligne_le_defaut_declare_par_l_instance(live, monkeypatch):
    org = _org()
    assert value_for(None, org, SIEGES) == DEFAUT_SIEGES
    assert value_for(None, None, SIEGES) == DEFAUT_SIEGES, "sans org, le défaut"
    assert value_for(None, org, C.platform_key("hunter")) == 0, "le joker"
    monkeypatch.setenv("OTO_ENTITLEMENT_DEFAULTS",
                       '{"unipile": 1, "platform_unmetered": 0, "unipile_seats": 2, '
                       '"members_max": "unlimited", "platform_key:*": 0, '
                       '"platform_key:hunter": 7}')
    assert org_has(org, C.UNIPILE), "une instance qui ouvre la messagerie à tous"
    assert value_for(None, org, C.platform_key("hunter")) == 7, "la surcharge"
    assert value_for(None, org, C.MEMBERS_MAX) == C.SANS_PLAFOND


def test_une_ligne_moins_genereuse_que_le_defaut_l_emporte(live):
    org = _org()
    E.grant(org, SIEGES, "contract", value=2)
    assert value_for(None, org, SIEGES) == 2 < DEFAUT_SIEGES


# ── le catalogue ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cle, valeur, code", [
    ("members", 1, "entitlement_unknown_key"),
    ("beta", 1, "entitlement_unknown_key"),
    ("platform_key:connecteur-inconnu", 5, "entitlement_unknown_key"),
    (C.UNIPILE, None, "entitlement_value_required"),
    (SIEGES, None, "entitlement_value_required"),
    (C.UNIPILE, 2, "entitlement_value_invalid"),
    (SIEGES, -1, "entitlement_value_invalid"),
    (SIEGES, "5", "entitlement_value_invalid"),
    (C.UNIPILE, True, "entitlement_value_invalid"),
])
def test_la_pose_refuse_hors_catalogue_et_valeur_vide(live, cle, valeur, code):
    org = _org()
    with pytest.raises(ValueError) as e:
        E.grant(org, cle, "offered", value=valeur)
    assert str(e.value).startswith(code + ":")
    assert E.list_for_org(org) == [], "rien n'est écrit"


def test_la_pose_accepte_le_catalogue(live):
    org = _org()
    E.grant(org, C.platform_key("hunter"), "offered", value=50)
    E.grant(org, C.MEMBERS_MAX, "contract", value=C.SANS_PLAFOND)
    E.grant(org, C.PLATFORM_UNMETERED, "offered", value=1)
    assert value_for(None, org, C.platform_key("hunter")) == 50
    assert value_for(None, org, C.MEMBERS_MAX) == C.SANS_PLAFOND


def test_la_lecture_refuse_une_cle_hors_catalogue(live):
    with pytest.raises(ValueError, match="^entitlement_unknown_key:"):
        value_for(None, _org(), "members")


# ── les listes ──────────────────────────────────────────────────────────────────

def test_listes_par_personne_et_par_droit(live):
    org, ailleurs, sub = _org(), _org(), _sub()
    E.grant(org, C.UNIPILE, "trial", value=1, sub=sub)
    E.grant(ailleurs, C.UNIPILE, "offered", value=1, sub=sub)
    E.grant(org, C.UNIPILE, "subscription", value=1, expires_at=HIER,
            starts_at=HIER - timedelta(days=1))
    assert {r["org_id"] for r in E.list_for_person(sub)} == {org, ailleurs}
    assert [r["org_id"] for r in E.list_for_person(sub, org)] == [org]
    vivants = {(r["org_id"], r["sub"]) for r in E.list_for_right(C.UNIPILE)
               if r["org_id"] in (org, ailleurs)}
    assert vivants == {(org, sub), (ailleurs, sub)}, "l'échue n'est pas vivante"
    tous = {(r["org_id"], r["sub"]) for r in E.list_for_right(C.UNIPILE, live_only=False)
            if r["org_id"] in (org, ailleurs)}
    assert tous == vivants | {(org, None)}


def test_la_console_voit_le_droit_echu(live):
    org = _org()
    E.grant(org, C.UNIPILE, "trial", value=1, starts_at=HIER - timedelta(days=1),
            expires_at=HIER)
    (ligne,) = E.list_for_org(org)
    assert (ligne["right_key"], ligne["source"]) == (C.UNIPILE, "trial")
    assert ligne["expires_at"] is not None


def test_supprimer_l_org_emporte_ses_droits(live):
    org = _org()
    E.grant(org, C.UNIPILE, "trial", value=1)
    E.grant(org, C.UNIPILE, "trial", value=1, sub=_sub())
    with _connect() as conn:
        conn.execute("DELETE FROM orgs WHERE id = %s", (org,))
    assert E.list_for_org(org) == []


def test_le_point_de_lecture_est_sur_la_facade_access(live):
    assert access.org_has is org_has
    assert access.value_for is value_for


def test_la_fusion_de_comptes_emmene_les_droits_de_la_personne(live):
    """`migrate_sub` : un droit de personne SUIT la personne ; si les deux comptes
    portent la même ligne, la fusion ne lève pas et il en reste une."""
    from oto_mcp import db
    org, vieux, neuf = _org(), _sub(), _sub()
    for s in (vieux, neuf):
        db.upsert_user(s, email=f"{s}@exemple.invalid")
    E.grant(org, SIEGES, "offered", value=8, sub=vieux)
    E.grant(org, C.UNIPILE, "trial", value=1, sub=vieux)
    E.grant(org, C.UNIPILE, "trial", value=1, sub=neuf)
    assert db.migrate_sub(vieux, neuf, operator_source="test") is True
    assert sorted((r["right_key"], r["sub"]) for r in E.list_for_org(org)) == [
        (C.UNIPILE, neuf), (SIEGES, neuf)]
    assert value_for(neuf, org, SIEGES) == 8
