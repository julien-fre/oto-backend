"""Un refus qui dit OÙ poser une clé envoie sur la page connecteurs DU PRODUIT du
compte — et sur rien du tout quand ce produit n'en déclare pas.

Vécu le 2026-09-11 (oto-backend#935). Les quatre refus de credential
(`access/resolve.py`) écrivaient notre adresse en dur : un compte de tenant tiers était
envoyé sur NOTRE tableau de bord, où son compte n'existe pas. Le premier correctif a
collé `/account` — notre chemin — sous l'adresse du tenant : chez le seul tenant tiers
déclaré, cette page répond 404. La carte connecteur (`connectors/readiness.py`) faisait
déjà la même chose, et c'est d'elle que venait le copier-coller.

La règle est celle de `links.py` : pas de patron, pas de lien. Un lien mort est pire
qu'une absence de lien — il ne se diagnostique pas, il se subit.

Les textes sont ceux du code RÉEL : seuls les accès base de la marche (et, pour la
carte, les quatre lectures d'état) sont bouchonnés.
"""
from __future__ import annotations

import pathlib

import pytest

from oto_mcp import session_org, tenancy
from oto_mcp.access import cascade, chain_shadow, indices, resolve, scope
from oto_mcp.connectors import readiness
from oto_mcp.mcp_errors import McpError

NOTRE_PAGE = "https://manage.oto.cx/connectors"
SA_PAGE = "https://app.gamma.test/org/7/connectors"


@pytest.fixture
def registre(monkeypatch):
    # `OTO_APP_URL` reste posé par le gréement global (`conftest.py`, #968) : ce
    # fichier affirme sur NOTRE_PAGE = "https://manage.oto.cx/connectors", exactement
    # cette valeur — pas le cas « rien de déclaré », couvert ailleurs
    # (`test_dashboard_url_par_tenant.py`).
    avant = tenancy.current()
    tenancy.install(tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[
            # Calqué sur le tenant tiers de la prod au 11/09 : une adresse, AUCUN patron.
            {"slug": "part", "issuer": "https://auth.part.test/oidc",
             "dashboard_url": "https://app.part.test", "link_paths": {}},
            # Le même, une fois son patron `connectors` déclaré.
            {"slug": "gamma", "issuer": "https://auth.gamma.test/oidc",
             "dashboard_url": "https://app.gamma.test",
             "link_paths": {"connectors": "/org/{org}/connectors"}},
        ])))
    yield
    tenancy.install(avant)


# --- les quatre refus de credential (access/resolve.py) -----------------------

class _Plateforme:
    mode = "platform"
    payload = {"label": "cle-commune", "secret": "x"}
    entity_type = entity_id = account = None


REFUS = ("aucune_cle", "cle_propre_exigee", "quota", "compte_introuvable")


@pytest.fixture
def refus(monkeypatch):
    monkeypatch.setattr(session_org, "current_call_instance", lambda: None)
    monkeypatch.setattr(session_org, "current_call_account", lambda: "compte-x")
    monkeypatch.setattr(scope, "project_pinned_instance", lambda *a, **k: None)
    monkeypatch.setattr(scope, "current_org", lambda sub: 7)
    monkeypatch.setattr(indices, "_revoked_hint", lambda *a, **k: "")
    monkeypatch.setattr(indices, "_reachable_hint", lambda *a, **k: "")
    monkeypatch.setattr(resolve, "_win_quota", lambda *a, **k: (5, 5))

    def _message(sub, cas):
        monkeypatch.setattr(cascade, "_is_multi_account",
                            lambda *a, **k: cas == "compte_introuvable")
        monkeypatch.setattr(chain_shadow, "barreau_gagnant",
                            lambda *a, **k: _Plateforme() if cas == "quota" else None)
        with pytest.raises(McpError) as e:
            resolve._resolve_credential_impl(
                "apollo", "byo" if cas == "cle_propre_exigee" else "auto", sub)
        return e.value.error.message
    return _message


@pytest.mark.parametrize("cas", REFUS)
def test_refus_un_tenant_SANS_patron_ne_recoit_aucune_adresse(registre, refus, cas):
    msg = refus("part:u-1", cas)
    assert "http" not in msg, f"adresse servie à un produit qui n'en déclare pas : {msg}"
    assert " sur " not in msg, f"la phrase promet une adresse qu'elle ne donne pas : {msg}"


@pytest.mark.parametrize("cas", REFUS)
def test_refus_un_compte_de_chez_nous_recoit_NOTRE_page_connecteurs(registre, refus, cas):
    msg = refus("u-1", cas)
    assert NOTRE_PAGE in msg, msg
    assert "/account" not in msg, msg


@pytest.mark.parametrize("cas", REFUS)
def test_refus_un_tenant_qui_DECLARE_le_patron_recoit_SA_page(registre, refus, cas):
    assert SA_PAGE in refus("gamma:u-1", cas)


# --- la carte connecteur (connectors/readiness.py) ----------------------------

CARTE = {
    readiness.PAID_OPTION_OFF: dict(paid="option-x", open=False, mode="user", rejet=None),
    readiness.NO_CREDENTIAL: dict(paid=None, open=True, mode="forbidden", rejet=None),
    readiness.OVER_QUOTA: dict(paid=None, open=True, mode=readiness.OVER_QUOTA, rejet=None),
    readiness.CREDENTIAL_REJECTED: dict(paid=None, open=True, mode="user",
                                        rejet="401 clé révoquée"),
}


@pytest.fixture
def carte(monkeypatch):
    from oto_mcp import access

    def _etape(sub, cas):
        c = CARTE[cas]
        monkeypatch.setattr(access, "paid_option_for", lambda *a, **k: c["paid"])
        monkeypatch.setattr(access, "option_open", lambda *a, **k: c["open"])
        monkeypatch.setattr(access, "credential_mode_for", lambda *a, **k: c["mode"])
        monkeypatch.setattr(access, "credential_rejection_for", lambda *a, **k: c["rejet"])
        d = readiness.diagnose(sub, "apollo", org=7, group=None)
        assert d is not None and d.reason == cas, d
        return d.next_step
    return _etape


@pytest.mark.parametrize("cas", list(CARTE))
def test_carte_un_tenant_SANS_patron_ne_recoit_aucune_adresse(registre, carte, cas):
    msg = carte("part:u-1", cas)
    assert "http" not in msg and " sur " not in msg, msg


@pytest.mark.parametrize("cas", list(CARTE))
def test_carte_un_compte_de_chez_nous_recoit_NOTRE_page_connecteurs(registre, carte, cas):
    msg = carte("u-1", cas)
    assert NOTRE_PAGE in msg and "/account" not in msg, msg


@pytest.mark.parametrize("cas", list(CARTE))
def test_carte_un_tenant_qui_DECLARE_le_patron_recoit_SA_page(registre, carte, cas):
    assert SA_PAGE in carte("gamma:u-1", cas)


# --- le cliquet ----------------------------------------------------------------

@pytest.mark.parametrize("module", ["access/resolve.py", "connectors/readiness.py"])
def test_aucune_adresse_ni_chemin_de_chez_nous_nest_ecrit_en_dur(module):
    """Le LITTÉRAL a produit la faute deux fois : l'adresse en dur (le 13/08, puis
    ces refus), et notre chemin `/account` recollé sous l'adresse d'un autre. Le
    comportement est tenu par les tests au-dessus ; ceci empêche le prochain
    copier-coller depuis un module voisin, qu'une relecture n'attrape pas."""
    source = (pathlib.Path(__file__).resolve().parent.parent / "oto_mcp" / module
              ).read_text(encoding="utf-8")
    for motif in ("manage.oto.cx", "app.oto.cx", "dashboard.oto", "/account"):
        assert motif not in source, (
            f"`{motif}` écrit dans {module} — l'adresse où poser une clé passe par "
            "`links.ou_poser_la_cle` : pas de patron, pas de lien.")
