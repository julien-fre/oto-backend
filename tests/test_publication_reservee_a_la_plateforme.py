"""Publier dans la bibliothèque publique est réservé aux super_admin de la plateforme.

La bibliothèque publique est une vitrine éditée par la plateforme : ses entrées sont
signées Otomata. Ce qui reste ouvert ne change pas — ses procédures personnelles pour
tout compte, le fork d'une entrée pour un org_admin — et garde ses propres gardes.

Ce que ces tests figent, sur la séquence SERVIE (validation → règle d'autz déclarée →
canal posé au seuil → handler, celle de `_rest_adapter` et de `_mcp_adapter`) :
- un org_admin sans rôle plateforme est refusé (403
  `publication_reservee_a_la_plateforme`), et rien n'est lu ni écrit ;
- un opérateur plateforme (`admin`, pas `super_admin`) est refusé de même ;
- le refus de rôle passe AVANT l'exigence d'org active, sur les deux faces ; un
  super_admin sans org active reçoit, lui, `no_active_org` ;
- un super_admin publie depuis son org active, au nom d'Otomata ;
- le droit ANNONCÉ (`capacite_autorise`, `platform_floor`) lit la même règle ;
- sur `oto_procedure`, la garde d'agent parle toujours avant le handler pour un
  super_admin, et un compte qui ne publiera nulle part reçoit le refus de plateforme ;
- le fork garde sa garde org_admin.

⚠️ Le rôle se pose sur `access.get_user_role`, la source commune des deux prédicats
(`is_super_admin`, `is_platform_operator`), jamais sur l'un d'eux : les stubber
séparément fabriquerait un état que le système ne connaît pas (un super_admin qui ne
serait pas opérateur).
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import types

import pytest

from oto_mcp import access, org_store, roles
from oto_mcp.capabilities import _authz
from oto_mcp.capabilities import guide_library as lib
from oto_mcp.capabilities._types import AuthzDenied, RawCtx, ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES

ORG = 7
ROLES = {"compte-membre": "member", "compte-operateur": "admin",
         "compte-super": "super_admin"}
CODE = "publication_reservee_a_la_plateforme"


@pytest.fixture
def banc(monkeypatch):
    """Règles, gates et handlers réels ; seuls les rôles, l'org active et le store
    sont posés. Tous les comptes du banc sont org_admin de leur org active : ce qui
    les distingue ne tient qu'au rôle plateforme."""
    trace = types.SimpleNamespace(lu=[], ecrit=[], org_active=dict.fromkeys(ROLES, ORG))
    monkeypatch.setattr(access, "get_user_role", lambda sub: ROLES[sub])
    monkeypatch.setattr(access, "current_org", lambda sub: trace.org_active[sub])
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org_id: True)

    def _get_instruction(otype, org_id, slug):
        trace.lu.append((otype, org_id, slug))
        return {"body_md": "# corps", "title": "T", "description": "d", "slots": []}

    def _publish_guide(**k):
        trace.ecrit.append(k)
        return {"id": 1, "slug": k["slug"], "version": 1, "visibility": k["visibility"]}

    monkeypatch.setattr(org_store, "get_instruction", _get_instruction)
    monkeypatch.setattr(org_store, "publish_guide", _publish_guide)
    monkeypatch.setattr(org_store, "get_org",
                        lambda org_id: {"id": org_id, "name": "Org de test"})
    return trace


def _cap(cle):
    return next(c for c in CAPABILITIES if c.key == cle)


def _servir(cle, sub, canal, **champs):
    """Rejoue ce que font les deux adaptateurs : `Input` → règle d'autz DÉCLARÉE →
    canal posé au seuil → handler. Que le canal soit bien posé sur un vrai appel a
    son banc à lui (`test_canal_d_appel.py`)."""
    cap = _cap(cle)
    inp = cap.Input(**champs)
    ctx = dataclasses.replace(cap.authz(RawCtx(sub=sub), inp), channel=canal)
    out = cap.handler(ctx, inp)
    return asyncio.run(out) if inspect.isawaitable(out) else out


def _publier_rest(sub):
    return _servir("library.publish", sub, "rest", slug="veille-concurrence")


def _publier_mcp(sub):
    return _servir("org.procedure.console", sub, "mcp", op="publish",
                   slug="veille-concurrence", visibility="public")


@pytest.mark.parametrize("sub", ["compte-membre", "compte-operateur"])
def test_hors_super_admin_la_publication_est_refusee_sans_rien_toucher(banc, sub):
    with pytest.raises(AuthzDenied) as e:
        _publier_rest(sub)
    assert (e.value.status, e.value.code) == (403, CODE)
    assert banc.lu == [], "la procédure source ne doit même pas être lue"
    assert banc.ecrit == [], "rien ne doit être publié"


def test_le_refus_dit_qui_publie_et_ce_qui_reste_ouvert(banc):
    """Un refus qui ne nomme que l'interdit enseigne « ne partage rien » ; un refus
    qui promet un chemin fermé envoie vers un second refus. Il dit donc QUI publie,
    ce qui reste ouvert à tous, et la condition du fork (#632)."""
    with pytest.raises(AuthzDenied) as e:
        _publier_rest("compte-membre")
    m = e.value.message
    assert "super-administrateurs de la plateforme" in m, "qui publie"
    assert "tes procédures personnelles" in m, "ce qui reste ouvert à tout compte"
    assert "si tu es org_admin" in m, "le fork se promet AVEC sa condition"


@pytest.mark.parametrize("servir", [_publier_rest, _publier_mcp], ids=["rest", "mcp"])
def test_le_refus_de_role_passe_avant_l_exigence_d_org_active(banc, servir):
    """Dire « choisis une org » à qui ne peut pas publier ne mènerait qu'au même refus,
    une étape plus loin. Joué par la règle d'autz, sur les deux faces servies."""
    banc.org_active["compte-membre"] = None
    with pytest.raises(AuthzDenied) as e:
        servir("compte-membre")
    assert (e.value.status, e.value.code) == (403, CODE)


def test_un_super_admin_sans_org_active_doit_en_choisir_une(banc):
    """Le corps publié se lit dans une procédure de l'org active : sans elle, rien à
    publier — le refus le dit, avant toute lecture."""
    banc.org_active["compte-super"] = None
    with pytest.raises(AuthzDenied) as e:
        _publier_rest("compte-super")
    assert (e.value.status, e.value.code) == (400, "no_active_org")
    assert banc.lu == [] and banc.ecrit == []


def test_un_super_admin_publie_au_nom_d_otomata(banc):
    out = _publier_rest("compte-super")
    assert out["published"] is True and out["slug"] == "veille-concurrence"
    assert banc.lu == [("org", ORG, "veille-concurrence")], \
        "le corps se lit dans l'org active"
    (ecrit,) = banc.ecrit
    assert (ecrit["author_kind"], ecrit["author_org_id"], ecrit["author_display"]) \
        == ("otomata", None, "Otomata")
    assert (ecrit["source_org_id"], ecrit["published_by"]) == (ORG, "compte-super")


def test_le_droit_annonce_lit_la_meme_regle(banc):
    """Un drapeau de droit servi à un écran se calcule par `capacite_autorise`, qui
    exécute la règle DÉCLARÉE (#695). Tant que le rôle ne vivait que dans le handler,
    elle annonçait le geste à tout membre d'org."""
    for cle, champs in (("library.publish", {}),
                        ("org.procedure.console", {"op": "publish"})):
        assert not _authz.capacite_autorise(cle, "compte-membre", **champs), cle
        assert not _authz.capacite_autorise(cle, "compte-operateur", **champs), cle
        assert _authz.capacite_autorise(cle, "compte-super", **champs), cle
    assert _authz.platform_floor(_cap("library.publish").authz) == "super"
    # La console réunit des ops ouvertes à tous : son plancher reste le plus BAS de ses
    # branches, et `oto_procedure` reste servi à chacun.
    assert _authz.platform_floor(_cap("org.procedure.console").authz) is None


def test_le_filet_du_handler_leve_le_meme_refus(banc):
    """Un appelant qui câblerait `_publish` sous une autre règle rencontre le même
    mur, dit par la même source."""
    ctx = ResolvedCtx(sub="compte-membre", org_id=ORG, role="member")
    with pytest.raises(AuthzDenied) as e:
        lib._publish(ctx, lib.PublishInput(slug="veille-concurrence"))
    assert (e.value.status, e.value.code) == (403, CODE)
    assert e.value.message == _authz._refus_publication_bibliotheque().message
    assert banc.lu == [] and banc.ecrit == []


def test_la_console_garde_l_ordre_des_refus(banc):
    """Face agent : pour un super_admin, la garde d'agent parle toujours avant le
    handler, inchangée. Pour un compte qui ne publiera nulle part, la règle de
    plateforme parle d'abord — la garde d'agent le renverrait vers un dashboard qui
    le refuserait aussi."""
    with pytest.raises(AuthzDenied) as e:
        _publier_mcp("compte-super")
    assert e.value.code == "publication_reservee_a_l_humain"

    for sub in ("compte-membre", "compte-operateur"):
        with pytest.raises(AuthzDenied) as e:
            _publier_mcp(sub)
        assert e.value.code == CODE, sub
    assert banc.lu == [] and banc.ecrit == []


def test_le_fork_reste_ouvert_a_l_org_admin(banc, monkeypatch):
    monkeypatch.setattr(org_store, "get_library_entry",
                        lambda **k: {"id": 3, "body_md": "# corps"})
    monkeypatch.setattr(org_store, "fork_into_org",
                        lambda **k: {"org_id": k["org_id"], "slug": "veille-concurrence",
                                     "version": 1, "forked_from": 3, "source_title": "T"})
    out = _servir("library.fork", "compte-membre", "rest", slug="veille-concurrence")
    assert out["forked"] is True and out["org_id"] == ORG
