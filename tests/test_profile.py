"""Fiche « situation avec oto » (`oto_profile`) + seed du projet « Découverte ».

L'onboarding n'est plus un mode scripté : la fiche profil est entretenue au fil de
l'eau (`oto_profile`), et le projet d'accueil est semé à la création de l'org perso
(`discovery.seed_for_org`). On monkeypatche les seams DB — pas de vraie DB.
"""
import pytest

import oto_mcp.capabilities.profile as P
import oto_mcp.discovery as discovery


# ── seed du projet « Découverte » ────────────────────────────────────────────
def test_seed_for_org_creates_project(monkeypatch):
    rec = {}

    def create_project(ot, oid, name, brief, created_by=None):
        rec["args"] = (ot, oid, name, created_by)
        return 555
    monkeypatch.setattr("oto_mcp.db.create_project", create_project)
    monkeypatch.setattr("oto_mcp.db.log_project_activity", lambda *a, **k: None)

    pid = discovery.seed_for_org("u1", 77)
    assert pid == 555
    assert rec["args"] == ("org", "77", discovery.PROJECT_NAME, "u1")


def test_seed_for_org_best_effort(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("oto_mcp.db.create_project", boom)
    # Un échec de seed ne lève pas (ne casse pas la création d'org) → None.
    assert discovery.seed_for_org("u1", 77) is None


# ── capacité `me.profile` : face MCP op-aware + faces REST par-verbe ─────────
# Depuis le 2026-07-28 la fiche n'est plus un tool écrit à la main mais une capacité
# (ADR 0042 §Convergence des surfaces) : on exerce les handlers directement.
def _ctx():
    return P.ResolvedCtx(sub="u1")


def _store(monkeypatch, state):
    monkeypatch.setattr(P.db, "get_account_profile", lambda sub: {"profile": dict(state),
                                                                 "updated_at": None})

    def update(sub, fields=None):
        state.update(fields or {})
        return {"profile": dict(state), "updated_at": "2026-07-28 10:00:00"}
    monkeypatch.setattr(P.db, "update_account_profile", update)


def test_profile_get_reports_missing(monkeypatch):
    _store(monkeypatch, {"full_name": "Jean"})
    out = P._profile_op(_ctx(), P.ProfileOpInput(op="get"))
    assert out["profile"] == {"full_name": "Jean"}
    assert "full_name" not in out["missing"]      # rempli
    assert "role" in out["missing"]               # vide
    assert out["fields"] is P.PROFILE_FIELDS      # schéma suggéré servi aux deux faces


def test_profile_update_persists_clean(monkeypatch):
    _store(monkeypatch, {})
    out = P._profile_op(_ctx(), P.ProfileOpInput(op="update",
                                                 fields={"role": "fondateur", "crm": ""}))
    assert out["profile"]["role"] == "fondateur"
    assert "crm" not in out["profile"]            # face agent : valeur vide ignorée


def test_profile_update_requires_fields(monkeypatch):
    _store(monkeypatch, {})
    with pytest.raises(P.AuthzDenied):
        P._profile_op(_ctx(), P.ProfileOpInput(op="update", fields=None))
    with pytest.raises(P.AuthzDenied):            # que des valeurs vides = rien à écrire
        P._profile_op(_ctx(), P.ProfileOpInput(op="update", fields={"role": ""}))


def test_profile_rejects_bad_op():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):          # `op` est un Literal → validé par l'Input
        P.ProfileOpInput(op="bogus")


def test_rest_set_can_clear_a_value(monkeypatch):
    """Face HUMAINE (dashboard) : vider un champ doit passer — pas de filtre ici,
    contrairement à la face agent (`op=update`)."""
    state = {"role": "fondateur"}
    _store(monkeypatch, state)
    out = P._set_profile(_ctx(), P.SetProfileInput(fields={"role": ""}))
    assert out["profile"]["role"] == ""


def test_profile_capability_has_both_faces():
    """Le point de l'amendement : UNE capacité porte les DEUX faces (plus de double
    implémentation MCP main-écrite + capacité REST)."""
    caps = {c.key: c for c in P.CAPABILITIES if c.key.startswith("me.profile")}
    assert caps["me.profile"].mcp == "oto_profile"
    assert [b.path for b in caps["me.profile.get"].rest_bindings()] == ["/api/me/profile"]
    assert [b.verb for b in caps["me.profile.set"].rest_bindings()] == ["PUT"]
