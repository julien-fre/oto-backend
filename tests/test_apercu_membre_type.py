"""Aperçu de ce que reçoit l'agent d'un MEMBRE TYPE (#1194) — sans base.

Ce que ce fichier prouve, sur la composition et non sur une copie :
1. l'aperçu rend les couches d'instructions dans l'ORDRE d'injection (socle → org →
   équipe), avec les MÊMES octets que la session pour ce qui est commun — c'est la
   garde contre une seconde implémentation ;
2. les variables communes sont résolues, celles d'une personne restent telles quelles ;
3. le socle suit le TENANT qui héberge l'org, et les noms d'outils son préfixe ;
4. la lecture est STRICTE : une base illisible lève, au lieu d'un aperçu vide ;
5. la capacité : refus nommés (org inconnue, équipe hors de l'org), connecteurs =
   l'exposition de la visibilité (`exposed_for`), autorisation org_admin/opérateur.
"""
from __future__ import annotations

import datetime as dt

import pytest

from oto_mcp import guide_store, instructions, tenancy, tool_alias
from oto_mcp.capabilities import agent_context
from oto_mcp.capabilities._types import AuthzDenied, RawCtx
from oto_mcp.connectors import activation as connector_activation

_DATE = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)
_ORG = {"id": 7, "name": "Org Témoin"}
_EQUIPE = {"id": 70, "org_id": 7, "name": "Équipe Ventes"}
_CORPS_ORG = ("Règles de {{org}}, équipe {{équipe}}, le {{date}}. "
              "Pour {{user}} ({{rôle}}) : {{connecteurs_actifs}} / {{projets_récents}}. "
              "Range tout avec `oto_doc`.")


def _guides(lignes: dict):
    """Un faux `get_init_guide` (lecture stricte) ET `init_guide_body` (session),
    servis par la MÊME table — les deux faces lisent ce que la base porterait."""
    def strict(scope, owner=None):
        corps, date = lignes.get((scope, str(owner)), ("", None))
        return {"body_md": corps, "updated_at": date}

    def fail_open(scope, owner=None):
        return (lignes.get((scope, str(owner)), ("", None))[0] or "").strip() or None
    return strict, fail_open


@pytest.fixture
def base(monkeypatch):
    lignes = {
        ("platform", "secret_sauce"): ("Socle de la plateforme.", _DATE),
        ("org", "7"): (_CORPS_ORG, _DATE),
        ("group", "70"): ("Équipe {{équipe}} : appelle avant d'écrire.", _DATE),
    }
    strict, fail_open = _guides(lignes)
    monkeypatch.setattr(guide_store, "get_init_guide", strict)
    monkeypatch.setattr(guide_store, "init_guide_body", fail_open)
    monkeypatch.setattr(connector_activation, "tenant_of_org", lambda org_id, conn=None: None)
    return lignes


def test_les_couches_dans_l_ordre_d_injection(base):
    couches = instructions.member_preview_layers(_ORG, _EQUIPE)
    assert [c["scope"] for c in couches] == ["platform", "org", "group"]
    assert couches[0] == {"scope": "platform", "body_md": "Socle de la plateforme.",
                          "updated_at": _DATE}
    assert couches[1]["body_md"].startswith("## README de ton organisation (Org Témoin)\n\n")
    assert couches[2]["body_md"].startswith("## README de ton équipe (Équipe Ventes)\n\n")
    assert all(c["updated_at"] == _DATE for c in couches)


def test_variables_communes_resolues_personnelles_laissees(base):
    org = instructions.member_preview_layers(_ORG, _EQUIPE)[1]["body_md"]
    assert "Règles de Org Témoin, équipe Équipe Ventes, le " in org
    assert dt.date.today().isoformat() in org
    for propre_a_une_personne in ("{{user}}", "{{rôle}}", "{{connecteurs_actifs}}",
                                  "{{projets_récents}}"):
        assert propre_a_une_personne in org, propre_a_une_personne


def test_memes_octets_que_la_session_pour_ce_qui_est_commun(base, monkeypatch):
    """LA garde contre une seconde composition : la couche d'org d'une VRAIE session
    (membre de la même équipe) et celle de l'aperçu ne diffèrent que par les variables
    personnelles. Si l'une change d'en-tête, de rendu ou de substitution sans l'autre,
    ce test rougit."""
    base[("org", "7")] = ("Règles de {{org}} pour {{équipe}}.", _DATE)
    monkeypatch.setattr(instructions, "_resolve_context", lambda sub, org_id: {
        "org_name": "Org Témoin", "user_name": "Une Personne", "role": "member",
        "group_name": "Équipe Ventes", "group_id": 70, "connectors": ["serper"],
        "projects": [], "runs": [], "profile": {}})
    session = {c["key"]: c["body"] for c in instructions.session_layers("u-1", 7)}
    apercu = {c["scope"]: c["body_md"] for c in instructions.member_preview_layers(_ORG, _EQUIPE)}
    assert apercu["platform"] == session["platform"]
    assert apercu["org"] == session["org"]
    assert apercu["group"] == session["group"]


def test_sans_equipe_l_org_seule(base):
    couches = instructions.member_preview_layers(_ORG, None)
    assert [c["scope"] for c in couches] == ["platform", "org"]
    assert "équipe —," in couches[1]["body_md"], "sans équipe : ce que reçoit un membre sans équipe active"


def test_une_couche_vide_est_absente(base):
    base[("org", "7")] = ("   ", _DATE)
    assert [c["scope"] for c in instructions.member_preview_layers(_ORG, _EQUIPE)] == [
        "platform", "group"]


def test_socle_par_defaut_sans_date(base):
    base[("platform", "secret_sauce")] = ("", None)
    socle = instructions.member_preview_layers(_ORG, None)[0]
    assert socle["body_md"] == instructions.default_block(instructions.KEY_SECRET_SAUCE)
    assert socle["updated_at"] is None


@pytest.fixture
def tenant_acme(base, monkeypatch):
    avant = tenancy.current()
    tenancy.install(tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[{"slug": "acme", "name": "Acme", "issuer": "https://auth.acme.test/oidc",
                  "tool_prefix": "acme"}])))
    monkeypatch.setattr(connector_activation, "tenant_of_org", lambda org_id, conn=None: "acme")
    yield base
    tenancy.install(avant)


def test_le_socle_et_les_noms_suivent_le_tenant_de_l_org(tenant_acme):
    tenant_acme[("tenant", "acme")] = ("Acme — ta boîte à outils, `oto_doc` compris.", _DATE)
    couches = instructions.member_preview_layers(_ORG, None)
    assert couches[0]["body_md"] == "Acme — ta boîte à outils, `acme_doc` compris."
    assert "`acme_doc`" in couches[1]["body_md"] and "oto_doc" not in couches[1]["body_md"]


def test_un_tenant_sans_socle_garde_celui_de_la_plateforme(tenant_acme):
    assert instructions.member_preview_layers(_ORG, None)[0]["body_md"] == (
        "Socle de la plateforme.")


def test_la_lecture_est_stricte(base, monkeypatch):
    def _boum(scope, owner=None):
        raise RuntimeError("base indisponible")
    monkeypatch.setattr(guide_store, "get_init_guide", _boum)
    with pytest.raises(RuntimeError):
        instructions.member_preview_layers(_ORG, _EQUIPE)


def test_le_handshake_reste_fail_open(tenant_acme, monkeypatch):
    """Le refactor du choix de socle ne change rien au handshake : une lecture de
    tenant en erreur y retombe sur le socle plateforme."""
    def _boum(scope, owner=None):
        if scope == "tenant":
            raise RuntimeError("base indisponible")
        return "Socle de la plateforme."
    monkeypatch.setattr(guide_store, "init_guide_body", _boum)
    assert instructions._socle_for("acme:u-1") == ("Socle de la plateforme.", "socle oto")


# ── La capacité ──────────────────────────────────────────────────────────────

@pytest.fixture
def capacite(base, monkeypatch):
    monkeypatch.setattr(agent_context.org_store, "get_org",
                        lambda org_id: dict(_ORG) if org_id == 7 else None)
    monkeypatch.setattr(agent_context.group_store, "get_group", lambda gid: {
        70: dict(_EQUIPE), 71: {"id": 71, "org_id": 8, "name": "Ailleurs"}}.get(gid))
    noms = list(agent_context.providers.REGISTRY)[:3]
    monkeypatch.setattr(connector_activation, "exposed_connectors",
                        lambda org_id: set(noms))
    monkeypatch.setattr(connector_activation, "group_cut_connectors",
                        lambda gid: {noms[1]} if gid == 70 else set())
    return noms


def _appel(org_id, group_id=None):
    return agent_context._context_preview(
        None, agent_context.ContextPreviewInput(org_id=org_id, group_id=group_id))


def test_connecteurs_exposes_moins_les_coupures_de_l_equipe(capacite):
    a, b, c = capacite
    reg = agent_context.providers.REGISTRY
    out = _appel(7, 70)
    assert out["org_id"] == 7 and out["group_id"] == 70
    assert out["connectors"] == [
        {"connector": n, "label": reg[n].label, "category": reg[n].category} for n in (a, c)]
    assert [x["connector"] for x in _appel(7)["connectors"]] == [a, b, c]
    assert [la["scope"] for la in out["layers"]] == ["platform", "org", "group"]


def test_l_exposition_est_celle_de_la_visibilite(capacite):
    """Même fonction que la couche d'activation des sessions — pas un recalcul."""
    import inspect
    from oto_mcp import session_visibility
    assert "connector_activation.exposed_for(" in inspect.getsource(session_visibility)
    assert connector_activation.exposed_for(7, 70) == {capacite[0], capacite[2]}


@pytest.mark.parametrize("org_id, group_id, code", [
    (9, None, "unknown_org"),
    (7, 71, "group_not_in_org"),      # équipe d'une AUTRE org
    (7, 404, "group_not_in_org"),     # équipe absente : même refus, rien n'est révélé
])
def test_refus_nommes(capacite, org_id, group_id, code):
    with pytest.raises(AuthzDenied) as e:
        _appel(org_id, group_id)
    assert (e.value.status, e.value.code) == (404, code)


def test_autorisation_org_admin_ou_operateur(monkeypatch):
    from oto_mcp import access, roles
    from oto_mcp.capabilities import registry
    cap = registry.by_key("org.context.preview")
    assert cap.mcp is None and cap.rest.path == "/api/orgs/{id}/context/preview"
    monkeypatch.setattr(access, "get_user_role", lambda sub: "member")
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: sub == "operateur")
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org_id: sub == "admin")
    monkeypatch.setattr("oto_mcp.detenteurs.admins_de_l_org", lambda sub, org_id: None)
    inp = agent_context.ContextPreviewInput(org_id=7)
    assert cap.authz(RawCtx(sub="admin"), inp).org_id == 7
    assert cap.authz(RawCtx(sub="operateur"), inp).org_id == 7
    with pytest.raises(AuthzDenied) as e:
        cap.authz(RawCtx(sub="membre"), inp)
    assert (e.value.status, e.value.code) == (403, "forbidden")


def test_la_description_dit_ce_qui_est_exclu():
    from oto_mcp.capabilities import registry
    d = registry.by_key("org.context.preview").description
    for exclu in ("profile card", "personal note", "installed selection", "{{user}}",
                  "{{connecteurs_actifs}}"):
        assert exclu in d, exclu


def test_prefixe_d_un_tenant_sans_compte(tenant_acme):
    assert tool_alias.declared_prefix_of_tenant("acme") == "acme"
    assert tool_alias.declared_prefix_of_tenant(None) == ""
