"""`/api/me/orgs` dit, PAR ORG, si le compte y est bêta.

Le front d'une org lit l'org de l'URL, pas l'org maison de `/api/me` — le champ
vit donc sur chaque entrée de la liste, calculé avec `org=` EXPLICITE (le seam
`access.has_option` prévoit ce mode pour la fiche admin : même anti-fuite ici).
Sans lui, un front ne sait rien de la bêta et ne peut que tout montrer ou rien.
"""
from __future__ import annotations

from oto_mcp.capabilities.orgs import reads as R
from oto_mcp.capabilities._types import ResolvedCtx


def _stub_orgs(monkeypatch, rows):
    monkeypatch.setattr(R.org_store, "list_orgs_for_user", lambda sub: rows)
    monkeypatch.setattr(R.org_store, "effective_logo_url", lambda o: None)
    monkeypatch.setattr(R.org_store, "list_org_members", lambda org_id: [])
    monkeypatch.setattr(R, "org_quota", lambda sub: {"created": 0, "cap": 5, "remaining": 5})


def test_beta_est_calcule_par_org_avec_org_explicite(monkeypatch):
    rows = [
        {"org_id": 9196, "name": "Partenaire", "org_role": "org_admin", "is_active": True},
        {"org_id": 269, "name": "Client", "org_role": "org_member", "is_active": False},
    ]
    _stub_orgs(monkeypatch, rows)
    vus = []

    def _has_option(sub, option, *, org=None):
        vus.append((sub, option, org))
        return org == 9196

    monkeypatch.setattr(R.access, "has_option", _has_option)
    out = R._list_my_orgs(ResolvedCtx(sub="membre", org_id=9196), R.NoInput())
    by_id = {o["id"]: o for o in out["orgs"]}
    assert by_id[9196]["beta"] is True and by_id[269]["beta"] is False
    # `beta` ne lit QUE `beta` ; `agents` lit `agents` puis `beta` (court-circuit
    # sur le premier vrai) — toujours avec `org=` explicite.
    assert sorted(set(vus)) == [("membre", "agents", 269), ("membre", "agents", 9196),
                                ("membre", "beta", 269), ("membre", "beta", 9196)]
    assert all(org in (269, 9196) for _, _, org in vus)
    # Le modèle déclaré (OpenAPI dérivé) porte le champ — un front typé le lit.
    assert R.MyOrgEntry(**by_id[9196]).beta is True
    assert R.MyOrgEntry.model_fields["beta"].default is False


def test_agents_est_un_champ_a_part_ouvert_par_agents_OU_beta(monkeypatch):
    """23/09/2026 : un tenant ouvre les agents hébergés à toute sa population sans
    ouvrir le reste de la bêta. Le front lit `agents`, plus `beta`, pour la section
    Agents ; `beta` ne bouge pas."""
    rows = [
        {"org_id": 1, "name": "Agents seulement", "org_role": "org_member", "is_active": True},
        {"org_id": 2, "name": "Bêta", "org_role": "org_member", "is_active": False},
        {"org_id": 3, "name": "Rien", "org_role": "org_member", "is_active": False},
    ]
    _stub_orgs(monkeypatch, rows)
    ouvert = {(1, "agents"), (2, "beta")}
    monkeypatch.setattr(R.access, "has_option",
                        lambda sub, option, *, org=None: (org, option) in ouvert)
    out = R._list_my_orgs(ResolvedCtx(sub="membre", org_id=1), R.NoInput())
    by_id = {o["id"]: (o["agents"], o["beta"]) for o in out["orgs"]}
    assert by_id == {1: (True, False), 2: (True, True), 3: (False, False)}
    assert R.MyOrgEntry.model_fields["agents"].default is False


def test_un_hoquet_ferme_agents_sans_ouvrir(monkeypatch):
    _stub_orgs(monkeypatch, [{"org_id": 1, "name": "x", "org_role": "org_member",
                              "is_active": True}])

    def _boom(sub, option, *, org=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(R.access, "has_option", _boom)
    out = R._list_my_orgs(ResolvedCtx(sub="membre", org_id=1), R.NoInput())
    assert (out["orgs"][0]["agents"], out["orgs"][0]["beta"]) == (False, False)
