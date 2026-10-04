"""Le lien de désinscription du résumé des lecteurs suit l'hôte du TENANT de l'org.

Un mail à la marque d'un partenaire qui renvoie vers notre domaine, c'est parler
par-dessus lui : sans hôte déclaré pour son tenant, le résumé ne part pas.
"""
from __future__ import annotations

from oto_mcp import db, digest_lecteurs, outreach_optout, tenancy


class _Registre:
    def __init__(self, hote):
        self._hote = hote

    def callback_host(self, slug):
        return self._hote


def test_une_org_a_nous_garde_l_instance(monkeypatch):
    monkeypatch.setattr(tenancy, "primary_slug", lambda: "oto")
    monkeypatch.setattr(db, "org_tenant_slug", lambda org_id, conn=None: "oto")
    assert digest_lecteurs._base_du_refus(7) == (True, None)


def test_une_org_de_tenant_prend_l_hote_du_tenant(monkeypatch):
    monkeypatch.setattr(tenancy, "primary_slug", lambda: "oto")
    monkeypatch.setattr(db, "org_tenant_slug", lambda org_id, conn=None: "acme")
    monkeypatch.setattr(tenancy, "current", lambda: _Registre("mcp.acme.example"))
    assert digest_lecteurs._base_du_refus(7) == (True, "https://mcp.acme.example")


def test_un_tenant_sans_hote_n_envoie_pas(monkeypatch):
    monkeypatch.setattr(tenancy, "primary_slug", lambda: "oto")
    monkeypatch.setattr(db, "org_tenant_slug", lambda org_id, conn=None: "acme")
    monkeypatch.setattr(tenancy, "current", lambda: _Registre(None))
    assert digest_lecteurs._base_du_refus(7) == (False, None)


def test_le_lien_se_pose_sur_la_base_donnee(monkeypatch):
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "secret-de-test")
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://api.example.test")
    assert outreach_optout.lien_lecteurs("s1", "https://mcp.acme.example/").startswith(
        "https://mcp.acme.example/o/r/")
    assert outreach_optout.lien_lecteurs("s1").startswith("https://api.example.test/o/r/")
