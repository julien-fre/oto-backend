"""`/api/me/orgs` dit, PAR ORG, si le compte y est bêta.

Le front d'une org lit l'org de l'URL, pas l'org maison de `/api/me` — le champ
vit donc sur chaque entrée de la liste, calculé contre des orgs EXPLICITES (`access.orgs_with_option`, la version
groupée du seam `access.has_option` : une lecture pour toutes les orgs, jamais
contre l'org courante).
Sans lui, un front ne sait rien de la bêta et ne peut que tout montrer ou rien.
"""
from __future__ import annotations

from oto_mcp.capabilities.orgs import reads as R
from oto_mcp.capabilities._types import ResolvedCtx


def _stub_orgs(monkeypatch, rows):
    monkeypatch.setattr(R.org_store, "list_orgs_for_user", lambda sub: rows)
    monkeypatch.setattr(R.org_store, "effective_logo_url", lambda o: None)
    monkeypatch.setattr(R.org_store, "count_org_members",
                        lambda ids: {i: 0 for i in ids})
    monkeypatch.setattr(R, "org_quota", lambda sub: {"created": 0, "cap": 5, "remaining": 5})


def test_beta_est_calcule_par_org_avec_org_explicite(monkeypatch):
    rows = [
        {"org_id": 9196, "name": "Partenaire", "org_role": "org_admin", "is_active": True,
         "personal": False},
        {"org_id": 269, "name": "Client", "org_role": "org_member", "is_active": False,
         "personal": False},
    ]
    _stub_orgs(monkeypatch, rows)
    vus = []

    def _orgs_with_option(sub, option, orgs):
        vus.append((sub, option, sorted(orgs)))
        return {9196}

    monkeypatch.setattr(R.access, "orgs_with_option", _orgs_with_option)
    out = R._list_my_orgs(ResolvedCtx(sub="membre", org_id=9196), R.NoInput())
    by_id = {o["id"]: o for o in out["orgs"]}
    assert by_id[9196]["beta"] is True and by_id[269]["beta"] is False
    # Une seule lecture, pour les deux orgs listées.
    assert vus == [("membre", "beta", [269, 9196])]
    # Le modèle déclaré (OpenAPI dérivé) porte le champ — un front typé le lit.
    assert R.MyOrgEntry(**by_id[9196]).beta is True
    assert R.MyOrgEntry.model_fields["beta"].default is False


def test_beta_fail_closed_si_le_seam_tombe(monkeypatch):
    """Une panne du seam des options ne blanchit pas la liste : bêta fermée partout."""
    _stub_orgs(monkeypatch, [{"org_id": 7, "name": "A", "org_role": "org_member",
                              "is_active": True, "personal": False}])

    def _panne(*a, **k):
        raise RuntimeError("seam en panne")

    monkeypatch.setattr(R.access, "orgs_with_option", _panne)
    out = R._list_my_orgs(ResolvedCtx(sub="membre"), R.NoInput())
    assert [o["beta"] for o in out["orgs"]] == [False]
