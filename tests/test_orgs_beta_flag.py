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
        {"org_id": 196, "name": "Tulina", "org_role": "org_admin", "is_active": True},
        {"org_id": 269, "name": "Client", "org_role": "org_member", "is_active": False},
    ]
    _stub_orgs(monkeypatch, rows)
    vus = []

    def _has_option(sub, option, *, org=None):
        vus.append((sub, option, org))
        return org == 196

    monkeypatch.setattr(R.access, "has_option", _has_option)
    out = R._list_my_orgs(ResolvedCtx(sub="julien", org_id=196), R.NoInput())
    by_id = {o["id"]: o for o in out["orgs"]}
    assert by_id[196]["beta"] is True and by_id[269]["beta"] is False
    assert sorted(vus) == [("julien", "beta", 196), ("julien", "beta", 269)]
    # Le modèle déclaré (OpenAPI dérivé) porte le champ — un front typé le lit.
    assert R.MyOrgEntry(**by_id[196]).beta is True
    assert R.MyOrgEntry.model_fields["beta"].default is False
