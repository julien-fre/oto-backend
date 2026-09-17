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

def test_rien_de_declare_refuse_plutot_que_de_deviner(env_propre):
    """Jusqu'au 16/09/2026 (#968), rien de déclaré retombait en silence sur NOTRE
    dashboard — c'est ce défaut-là qui avait servi la preprod à un client (13/08).
    Désormais : aucune des trois posée ⟹ refus nommé, pas un produit deviné."""
    with pytest.raises(RuntimeError, match="OTO_APP_URL"):
        config.dashboard_url()


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


def test_un_compte_de_la_plateforme_recoit_la_notre(registre):
    assert config.dashboard_url_for("bn01jfy76a5n") == config.dashboard_url()


def test_un_tenant_SANS_adresse_retombe_sur_la_notre(registre):
    """L'inertie : déclarer un tenant ne change aucun lien tant qu'on ne lui a pas
    donné d'adresse."""
    assert config.dashboard_url_for("beta:u-1") == config.dashboard_url()


def test_sans_compte_on_sert_la_notre(registre):
    assert config.dashboard_url_for(None) == config.dashboard_url()
    assert config.dashboard_url_for("") == config.dashboard_url()


def test_un_registre_illisible_ne_casse_aucun_lien(monkeypatch):
    """Ce chemin construit des liens DANS des réponses d'outils : il ne doit jamais
    lever, sous peine de transformer une question anodine en erreur."""
    def _boum():
        raise RuntimeError("registre indisponible")

    monkeypatch.setattr(tenancy, "current", _boum)
    assert config.dashboard_url_for("acme:u-1") == config.dashboard_url()


# --- les surfaces qui rendent ces liens ----------------------------------------

def test_une_adresse_seule_ne_suffit_pas_a_faire_un_lien(registre):
    """⚠️ Ce test affirmait l'inverse jusqu'au 13/08 — il collait NOS chemins sous
    LEUR domaine. Le code du partenaire a montré que ça fabrique des liens morts :
    ses chemins ne ressemblent pas aux nôtres, et il n'a aucune vue tableau.

    Une adresse ne suffit donc plus : il faut un patron par type (`links`). Sans
    patron, aucun lien — c'est le sujet de `test_links_par_tenant.py`."""
    from oto_mcp.datastore.core import _ns_url
    from oto_mcp.capabilities.docs.view import public_doc_url
    assert _ns_url(203, "acme:u-1") is None
    assert public_doc_url("tok", "acme:u-1") is None
    # Nous, inchangés.
    assert _ns_url(203, "bn01jfy76a5n") == f"{config.dashboard_url()}/data/203"
    assert public_doc_url("tok", "bn01jfy76a5n") == f"{config.dashboard_url()}/p/d/tok"


import ast
import pathlib


def _lignes_de_prose(source: str, chemin: str = "<source>") -> set[int]:
    """Les lignes appartenant à une docstring — module, classe ou fonction.

    Le tripwire excluait déjà les commentaires `#` : son intention est de viser ce que
    le code REND, pas ce qu'il explique. Une docstring est de la prose au même titre, et
    l'oublier force à contourner le contrôle en appauvrissant une note —
    `public_doc_page` explique légitimement sur quelle ORIGINE sa page sort, ce qui est
    l'argument de son mode de rendu sûr. Une garde qui pousse à effacer une explication
    juste se retourne contre ce qu'elle protège.
    """
    lignes: set[int] = set()
    arbre = ast.parse(source, filename=chemin)
    for n in ast.walk(arbre):
        if not isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                              ast.AsyncFunctionDef)):
            continue
        corps = getattr(n, "body", None)
        if (corps and isinstance(corps[0], ast.Expr)
                and isinstance(corps[0].value, ast.Constant)
                and isinstance(corps[0].value.value, str)):
            d = corps[0]
            lignes.update(range(d.lineno, (d.end_lineno or d.lineno) + 1))
    return lignes


def _hotes_de_tableau_de_bord() -> set[str]:
    """Les hôtes que ce contrôle surveille — **dérivés, pas gravés**.

    Le principal vient de la source elle-même : l'hôte du défaut de
    `config.dashboard_url()`. Une liste écrite à la main vieillirait exactement comme
    l'adresse qu'elle surveille.

    ⚠️ **L'ancien tripwire ne cherchait que `dashboard.oto.ninja`** — la PRÉPRODUCTION.
    Il avait été écrit contre l'incident du 13/08 (« la prod a servi la preprod ») et
    visait donc la chaîne de cet incident, pas l'axe : il gardait le sens dans lequel on
    s'était trompé une fois et laissait l'autre grand ouvert. Neuf endroits écrivaient
    `manage.oto.cx` en dur sans qu'il bronche, dont six dans des messages SERVIS à
    l'agent. C'est le défaut de garde le plus courant, et il est d'autant plus tenace
    qu'il a une histoire : chacun sait pourquoi la garde existe, donc personne ne se
    demande contre quoi elle devrait exister.
    """
    from urllib.parse import urlparse
    hotes = {urlparse(config.dashboard_url()).hostname}
    # L'ancienne adresse de préproduction : elle a été le défaut jusqu'au 13/08, donc
    # elle peut encore être recopiée de mémoire ou d'un vieux fichier.
    hotes.add("dashboard.oto.ninja")
    return {h for h in hotes if h}


def adresses_en_dur(source: str, hotes: set[str], chemin: str = "<source>") -> list[int]:
    """Les lignes de CODE (ni commentaire, ni docstring) qui écrivent un de ces hôtes.

    Extraite du test pour être éprouvable **à rebours** : une garde qu'on élargit sans
    l'avoir vue rougir sur son élargissement n'est pas prouvée."""
    prose = _lignes_de_prose(source, chemin)
    return [n for n, ligne in enumerate(source.splitlines(), 1)
            if any(h in ligne for h in hotes)
            and not ligne.strip().startswith("#") and n not in prose]


def test_aucune_adresse_de_tableau_de_bord_nest_ecrite_en_dur():
    """TRIPWIRE — une adresse en dur redevient invisible à la première relecture, et
    c'est exactement comme ça que la prod a servi la preprod.

    Depuis le 15/09/2026 il vise **toute** adresse de tableau de bord, production
    comprise : voir `_hotes_de_tableau_de_bord` pour ce que l'ancienne version laissait
    passer.
    """
    autorises = {  # commentaires, listes d'origines CORS : jamais un lien rendu
        # `api/base.py` depuis le 2026-08-27 : la liste d'origines CORS
        # (`_allowed_origins`) a suivi les primitives partagées hors d'`api/routes.py`
        # lors de la découpe par domaine. Même raison, autre fichier.
        # `public_doc_page.py` a quitté cette liste le 15/09/2026 : sa page suit
        # désormais la marque du propriétaire du doc, et son seul lien en dur est celui
        # de NOTRE pied — servi uniquement quand la page est à nous.
        "api/base.py",
        # `config.py` PORTE le défaut : c'est la source dont tout le reste dérive.
        "config.py",
    }
    hotes = _hotes_de_tableau_de_bord()
    fautifs = []
    racine = pathlib.Path("oto_mcp")
    for f in racine.rglob("*.py"):
        if f.relative_to(racine).as_posix() in autorises:
            continue
        source = f.read_text(encoding="utf-8")
        fautifs += [f"{f}:{n}" for n in adresses_en_dur(source, hotes, str(f))]
    assert not fautifs, (
        "adresse de tableau de bord écrite en dur :\n  " + "\n  ".join(fautifs)
        + "\n→ passer par `config.dashboard_url_for(sub)`.")


@pytest.mark.parametrize("source, attendu, quoi", [
    ('URL = "https://manage.oto.cx/console"\n', True, "un littéral de PRODUCTION"),
    ('def f(s):\n    return f"Va sur https://manage.oto.cx/ ({s})"\n', True,
     "une f-string servie"),
    ('X = "https://dashboard.oto.ninja/p"\n', True, "l'ancienne préproduction"),
    ('"""La page sort sur manage.oto.cx."""\nX = 1\n', False, "une docstring"),
    ('# voir https://manage.oto.cx\nX = 1\n', False, "un commentaire"),
])
def test_le_tripwire_MORD_sur_son_nouvel_axe(source, attendu, quoi):
    """Éprouvé à rebours : une garde élargie qui n'a jamais rougi sur son élargissement
    n'est pas prouvée. Avant le 15/09/2026, les deux premiers cas passaient — c'est
    exactement ce que neuf endroits du code servi faisaient sans être vus."""
    trouve = bool(adresses_en_dur(source, _hotes_de_tableau_de_bord()))
    assert trouve is attendu, f"{quoi} : dénoncé={trouve}, attendu={attendu}"
