"""L'API d'administration du commerce, sous l'identité de service (#1069).

Deux moitiés :
- **qui passe** : chaque capacité `service.*` ouvre l'authentification au service et
  passe sa règle ; un compte, même super admin, est refusé ;
- **ce qu'elles font**, sur une vraie base : membres par ancienneté, usage sur une
  fenêtre, pose idempotente, retrait d'une seule ligne, refus nommés.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import access, db, org_store
from oto_mcp.auth import service_identity
from oto_mcp.capabilities import _authz, registry
from oto_mcp.capabilities._types import AuthzDenied, RawCtx, ResolvedCtx
from oto_mcp.db._conn import _connect

_SERVICE = {"sub": "service:m2m-commerce", "client_id": "m2m-commerce",
            "roles": frozenset({"commerce"})}
_CLES = ("service.orgs.list", "service.org.members", "service.org.usage",
         "service.org.entitlements.list", "service.org.entitlement.put",
         "service.org.entitlement.delete")


def _cap(cle):
    return next(c for c in registry.CAPABILITIES if c.key == cle)


def _appel(cle, **champs):
    cap = _cap(cle)
    ctx = ResolvedCtx(sub=_SERVICE["sub"], org_id=None, role="service:commerce")
    return cap.handler(ctx, cap.Input(**champs))


@pytest.fixture(autouse=True)
def _nettoie():
    yield
    service_identity.set_current(None)


# ── qui passe ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cle", _CLES)
def test_chaque_capacite_est_ouverte_au_service_et_a_lui_seul(cle, monkeypatch):
    cap = _cap(cle)
    assert cap.mcp is None, "un tuyau de service, pas un outil d'agent"
    assert _authz.accepts_service(cap.authz)
    service_identity.set_current(dict(_SERVICE))
    assert cap.authz(RawCtx(sub=_SERVICE["sub"])).role == "service:commerce"
    service_identity.set_current(None)
    monkeypatch.setattr(access, "is_super_admin", lambda sub: True)
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: True)
    with pytest.raises(AuthzDenied) as refus:
        cap.authz(RawCtx(sub="u-super"))
    assert refus.value.code == "service_required"


def test_une_date_sans_fuseau_sort_en_utc_explicite():
    """#1073 : une colonne de la base servie rendait `created_at` sans fuseau, et le
    commerce ne pouvait pas la comparer à une date réelle."""
    from oto_mcp.capabilities import service_commerce as sc
    assert sc._iso("2026-06-10 23:18:58") == "2026-06-10T23:18:58+00:00", \
        "la forme texte du store du cœur"
    assert sc._iso(datetime(2026, 9, 25, 11, 0)) == "2026-09-25T11:00:00+00:00"
    paris = timezone(timedelta(hours=2))
    assert sc._iso(datetime(2026, 9, 25, 13, 0, tzinfo=paris)) == "2026-09-25T13:00:00+02:00"
    assert sc._iso(None) is None


# ── sur une vraie base ───────────────────────────────────────────────────────

def _org(archivee: bool = False) -> int:
    with _connect() as conn:
        return conn.execute(
            "INSERT INTO orgs (name, archived_at) VALUES (%s, %s) RETURNING id",
            (f"org-{uuid.uuid4().hex[:8]}",
             datetime.now(timezone.utc) if archivee else None)).fetchone()["id"]


def _membre(org: int, joined_at: datetime) -> str:
    sub = f"u-{uuid.uuid4().hex[:8]}"
    db.upsert_user(sub, email=f"{sub}@exemple.test")
    org_store.add_org_member(org, sub, "org_member")
    with _connect() as conn:
        conn.execute("UPDATE org_members SET joined_at = %s WHERE org_id = %s AND sub = %s",
                     (joined_at, org, sub))
    return sub


def _appel_journal(org: int, sub: str, quand: datetime, *, ok=True, key_mode=None):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tool_calls (created_at, kind, sub, tool, ok, org_id, key_mode) "
            "VALUES (%s, 'mcp', %s, 'outil_x', %s, %s, %s)",
            (quand, sub, ok, org, key_mode))


def _refus(cle, **champs) -> AuthzDenied:
    with pytest.raises(AuthzDenied) as refus:
        _appel(cle, **champs)
    return refus.value


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_les_membres_sortent_par_anciennete_avec_leur_derniere_activite(live):
    org = _org()
    cadet = _membre(org, T0 + timedelta(days=5))
    aine = _membre(org, T0)
    _appel_journal(org, cadet, T0 + timedelta(days=6))
    out = _appel("service.org.members", org_id=org)
    assert [m["sub"] for m in out["members"]] == [aine, cadet]
    assert out["members"][0]["last_activity_at"] is None
    assert out["members"][1]["last_activity_at"].startswith("2026-09-07")
    assert out["members"][0]["joined_at"] == T0.isoformat(), "servie avec son fuseau"
    assert out["members"][0]["email"] == f"{aine}@exemple.test"


def test_une_org_archivee_ou_inconnue_est_un_404_nomme(live):
    assert _refus("service.org.members", org_id=_org(archivee=True)).code == "unknown_org"
    assert _refus("service.org.entitlements.list", org_id=10**12).code == "unknown_org"


def test_la_liste_des_orgs_se_pagine_par_curseur_sans_les_archivees(live):
    a, b, archivee = _org(), _org(), _org(archivee=True)
    page = _appel("service.orgs.list", after_id=a - 1, limit=1)
    assert [o["id"] for o in page["orgs"]] == [a] and page["next_after_id"] == a
    assert datetime.fromisoformat(page["orgs"][0]["created_at"]).tzinfo is not None
    suite = _appel("service.orgs.list", after_id=a, limit=1000)
    ids = [o["id"] for o in suite["orgs"]]
    assert b in ids and archivee not in ids and suite["next_after_id"] is None


def test_chaque_org_porte_son_tenant(live):
    """#1072 : l'org d'un tenant tiers se reconnaît dans la liste — le commerce ne doit
    rien lui adresser."""
    from oto_mcp import tenancy
    a_nous, chez_un_tiers = _org(), _org()
    slug = f"t{uuid.uuid4().hex[:8]}"
    with _connect() as conn:
        tid = conn.execute(
            "INSERT INTO tenants (slug, name, issuer, jwks_uri) VALUES (%s, %s, %s, %s) "
            "RETURNING id",
            (slug, slug, f"https://{slug}.exemple.test/oidc",
             f"https://{slug}.exemple.test/oidc/jwks")).fetchone()["id"]
        conn.execute("UPDATE orgs SET tenant_id = %s WHERE id = %s", (tid, chez_un_tiers))
    page = _appel("service.orgs.list", after_id=a_nous - 1, limit=2)
    assert {o["id"]: o["tenant"] for o in page["orgs"]} == {
        a_nous: tenancy.PRIMARY_SLUG, chez_un_tiers: slug}


def test_l_usage_compte_les_reussites_de_la_fenetre_et_les_cles_de_plateforme(live):
    org = _org()
    sub = _membre(org, T0)
    dans = T0 + timedelta(days=2)
    _appel_journal(org, sub, dans)
    _appel_journal(org, sub, dans, key_mode="platform")
    _appel_journal(org, sub, dans, ok=False)                   # un échec ne compte pas
    _appel_journal(org, sub, T0 + timedelta(days=40))          # hors fenêtre
    out = _appel("service.org.usage", org_id=org, since=T0, until=T0 + timedelta(days=30))
    assert out["by_person"] == [{"sub": sub, "calls": 2, "platform_calls": 1}]
    assert _refus("service.org.usage", org_id=org, since=T0, until=T0).code == "invalid_window"


def test_la_pose_est_idempotente_et_nomme_le_service(live):
    org = _org()
    sub = _membre(org, T0)
    for valeur in (1, 1, 0):
        ligne = _appel("service.org.entitlement.put", org_id=org, right_key="unipile",
                       source="trial", value=valeur, sub=sub)
    assert (ligne["value"], ligne["sub"], ligne["granted_by"]) == (0, sub, _SERVICE["sub"])
    lignes = _appel("service.org.entitlements.list", org_id=org)["entitlements"]
    assert [(r["right_key"], r["source"], r["sub"]) for r in lignes] == [("unipile", "trial", sub)]


def test_le_retrait_ne_touche_que_sa_ligne(live):
    org = _org()
    sub = _membre(org, T0)
    for portee in (None, sub):
        _appel("service.org.entitlement.put", org_id=org, right_key="unipile_seats",
               source="trial", value=3, sub=portee)
    assert _appel("service.org.entitlement.delete", org_id=org, right_key="unipile_seats",
                  source="trial") == {"ok": True}
    restant = _appel("service.org.entitlements.list", org_id=org)["entitlements"]
    assert [r["sub"] for r in restant] == [sub]
    assert _refus("service.org.entitlement.delete", org_id=org, right_key="unipile_seats",
                  source="trial").code == "unknown_entitlement"


@pytest.mark.parametrize("champs, code", [
    (dict(right_key="unipile", source="gratuit", value=1), "unknown_source"),
    (dict(right_key="inconnu", source="trial", value=1), "entitlement_unknown_key"),
    (dict(right_key="unipile", source="trial", value=7), "entitlement_value_invalid"),
    (dict(right_key="unipile", source="trial", value=1, sub="u-etranger"), "not_a_member"),
    (dict(right_key="unipile", source="trial", value=1,
          starts_at=T0, expires_at=T0), "invalid_window"),
])
def test_une_pose_refusee_dit_pourquoi_et_n_ecrit_rien(live, champs, code):
    org = _org()
    assert _refus("service.org.entitlement.put", org_id=org, **champs).code == code
    assert _appel("service.org.entitlements.list", org_id=org)["entitlements"] == []
