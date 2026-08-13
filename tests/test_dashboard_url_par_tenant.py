"""Les liens rendus à un utilisateur portent l'adresse de SON produit.

Vécu le 13/08 : un client d'un partenaire, en conversation avec l'assistant de son
fournisseur, s'est vu servir un lien vers NOTRE tableau de bord — un produit qu'il
n'a pas, sur un domaine qu'il ne connaît pas.

Deux défauts empilés, corrigés ensemble :

1. **Trois variables d'environnement** désignaient la même adresse, et la production
   n'en posait qu'une : tout ce qui lisait les autres retombait sur un défaut écrit
   en dur, pointant la **preprod**.
2. Même corrigée, l'adresse restait **unique pour l'installation** : aucune notion
   d'« adresse de ce partenaire ».
"""
from __future__ import annotations

import pytest

from oto_mcp import config, tenancy


@pytest.fixture
def env_propre(monkeypatch):
    for var in ("OTO_APP_URL", "OTO_DASHBOARD_URL", "OTO_DASHBOARD_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def registre():
    avant = tenancy.current()
    tenancy.install(tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[
            {"slug": "acme", "issuer": "https://auth.acme.test/oidc",
             "dashboard_url": "https://app.acme.test"},
            # Un tenant SANS adresse déclarée : il doit retomber sur la nôtre.
            {"slug": "beta", "issuer": "https://auth.beta.test/oidc"},
        ])))
    yield
    tenancy.install(avant)


# --- une seule source, et un défaut qui ne ment pas ----------------------------

def test_le_defaut_vise_la_prod_jamais_la_preprod(env_propre):
    """C'est le défaut qui a servi la preprod à un client : un environnement mal
    configuré doit dégrader vers le vrai produit, pas vers un bac à sable."""
    assert config.dashboard_url() == "https://manage.oto.cx"


@pytest.mark.parametrize("var", ["OTO_APP_URL", "OTO_DASHBOARD_URL",
                                 "OTO_DASHBOARD_BASE_URL"])
def test_les_trois_variables_historiques_sont_lues(env_propre, monkeypatch, var):
    """Elles ont coexisté pour la même chose. Les lire toutes évite qu'un
    environnement configuré « à l'ancienne » retombe silencieusement sur le défaut."""
    monkeypatch.setenv(var, "https://tableau.test/")
    assert config.dashboard_url() == "https://tableau.test", "le slash final est retiré"


def test_lordre_de_precedence_est_stable(env_propre, monkeypatch):
    monkeypatch.setenv("OTO_APP_URL", "https://premier.test")
    monkeypatch.setenv("OTO_DASHBOARD_URL", "https://second.test")
    assert config.dashboard_url() == "https://premier.test"


# --- l'adresse suit le tenant du compte ----------------------------------------

def test_un_compte_de_tenant_recoit_ladresse_de_son_produit(registre, env_propre):
    assert config.dashboard_url_for("acme:u-1") == "https://app.acme.test"


def test_un_compte_de_la_plateforme_recoit_la_notre(registre, env_propre):
    assert config.dashboard_url_for("bn01jfy76a5n") == config.dashboard_url()


def test_un_tenant_SANS_adresse_retombe_sur_la_notre(registre, env_propre):
    """L'inertie : déclarer un tenant ne change aucun lien tant qu'on ne lui a pas
    donné d'adresse."""
    assert config.dashboard_url_for("beta:u-1") == config.dashboard_url()


def test_sans_compte_on_sert_la_notre(registre, env_propre):
    assert config.dashboard_url_for(None) == config.dashboard_url()
    assert config.dashboard_url_for("") == config.dashboard_url()


def test_un_registre_illisible_ne_casse_aucun_lien(env_propre, monkeypatch):
    """Ce chemin construit des liens DANS des réponses d'outils : il ne doit jamais
    lever, sous peine de transformer une question anodine en erreur."""
    def _boum():
        raise RuntimeError("registre indisponible")

    monkeypatch.setattr(tenancy, "current", _boum)
    assert config.dashboard_url_for("acme:u-1") == config.dashboard_url()


# --- les surfaces qui rendent ces liens ----------------------------------------

def test_le_lien_dun_tableau_suit_le_tenant(registre, env_propre):
    """LE lien de la capture du 13/08."""
    from oto_mcp.datastore import _ns_url
    assert _ns_url(203, "acme:u-1") == "https://app.acme.test/data/203"
    assert _ns_url(203, "bn01jfy76a5n") == f"{config.dashboard_url()}/data/203"


def test_le_lien_public_dune_page_suit_le_tenant(registre, env_propre):
    """Celui-ci part chez des TIERS : c'est la vitrine la plus visible de la marque."""
    from oto_mcp.capabilities.docs import _public_doc_url
    assert _public_doc_url("tok", "acme:u-1") == "https://app.acme.test/p/d/tok"


def test_aucune_adresse_de_tableau_de_bord_nest_ecrite_en_dur():
    """TRIPWIRE — une adresse en dur redevient invisible à la première relecture, et
    c'est exactement comme ça que la prod a servi la preprod."""
    import pathlib
    autorises = {  # commentaires, listes d'origines CORS : jamais un lien rendu
        "api_routes.py", "public_doc_page.py",
    }
    fautifs = []
    for f in pathlib.Path("oto_mcp").rglob("*.py"):
        if f.name in autorises:
            continue
        for n, ligne in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            nu = ligne.strip()
            if "dashboard.oto.ninja" in nu and not nu.startswith("#"):
                fautifs.append(f"{f}:{n}")
    assert not fautifs, (
        "adresse de tableau de bord écrite en dur :\n  " + "\n  ".join(fautifs)
        + "\n→ passer par `config.dashboard_url_for(sub)`.")
