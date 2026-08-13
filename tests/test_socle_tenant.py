"""Le socle injecté suit le TENANT du compte, pas la plateforme.

Constaté chez un client le 13/08, dès la première conversation sur le connecteur d'un
partenaire : l'assistant se présentait « Sur Tulina (Oto), tu es… », listait « les
connecteurs disponibles sur la plateforme » et renvoyait vers NOTRE tableau de bord.

Ce n'est pas un défaut de formulation : le texte d'accueil vivait au niveau plateforme
alors qu'il décrit un produit. Un tenant qui a le sien reçoit donc le sien.

Invariant tenu de bout en bout : **sans ligne de tenant, l'octet servi est celui
d'avant.** Trois détentes de repli, parce que ce chemin est celui du handshake.
"""
from __future__ import annotations

import pytest

from oto_mcp import instructions, tenancy


@pytest.fixture
def registre():
    avant = tenancy.current()
    tenancy.install(tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[{"slug": "acme", "name": "Acme", "issuer": "https://auth.acme.test/oidc"}])))
    yield
    tenancy.install(avant)


@pytest.fixture
def socle_acme(registre, monkeypatch):
    """Un socle écrit pour le tenant `acme`, et rien pour les autres."""
    from oto_mcp import guide_store
    monkeypatch.setattr(
        guide_store, "init_guide_body",
        lambda scope, owner=None: ("Acme — ta boîte à outils." if
                                   (scope, owner) == ("tenant", "acme") else None))


def test_un_compte_du_tenant_recoit_le_socle_du_tenant(socle_acme):
    corps, label = instructions._socle_for("acme:u-1")
    assert corps == "Acme — ta boîte à outils."
    assert label == "socle Acme", "l'étiquette doit porter le NOM du tenant"


def test_un_compte_de_la_plateforme_est_inchange(socle_acme):
    corps, label = instructions._socle_for("bn01jfy76a5n")
    assert label == "socle oto"
    assert corps.startswith("Oto")


def test_sans_compte_le_socle_reste_celui_de_la_plateforme(socle_acme):
    """Le handshake anonyme (sonde, catalogue public) ne doit pas dépendre d'un tenant."""
    assert instructions._socle_for(None)[1] == "socle oto"
    assert instructions._socle_for("")[1] == "socle oto"


def test_un_tenant_SANS_socle_retombe_sur_la_plateforme(registre, monkeypatch):
    """LE garde-fou d'inertie : déclarer un tenant ne change rien tant que personne
    n'a écrit son socle. C'est ce qui rend ce lot livrable sans coordination."""
    from oto_mcp import guide_store
    monkeypatch.setattr(guide_store, "init_guide_body", lambda scope, owner=None: None)
    corps, label = instructions._socle_for("acme:u-1")
    assert label == "socle oto" and corps.startswith("Oto")


def test_une_lecture_en_erreur_ne_casse_pas_le_handshake(registre, monkeypatch):
    """Ce chemin s'exécute à CHAQUE ouverture de session : une exception y coûterait
    la connexion entière, pour une question de marque."""
    from oto_mcp import guide_store
    vrai = guide_store.init_guide_body

    def _boum(scope, owner=None):
        # ⚠️ On ne casse QUE la lecture du tenant. Tout casser ferait tomber le repli
        # lui-même, donc le test simulerait une panne qui ne peut pas se produire
        # (`init_guide_body` porte déjà son propre fail-open) et prouverait l'inverse
        # de ce qu'il prétend.
        if scope == "tenant":
            raise RuntimeError("base indisponible")
        return vrai(scope, owner)

    monkeypatch.setattr(guide_store, "init_guide_body", _boum)
    assert instructions._socle_for("acme:u-1")[1] == "socle oto"


def test_le_socle_du_tenant_arrive_bien_dans_la_session(socle_acme, monkeypatch):
    """Le helper ne suffit pas : c'est la COMPOSITION servie au client qui compte —
    même famille de piège que la découverte, où le helper existait et la route ne
    l'appelait pas."""
    monkeypatch.setattr(instructions, "_c_layers", lambda sub, org_id: [])
    couches = instructions.session_layers("acme:u-1", None)
    assert couches[0]["body"] == "Acme — ta boîte à outils."
    assert couches[0]["label"] == "socle Acme"
    assert "Acme — ta boîte à outils." in instructions.compose_session("acme:u-1", None)


def test_le_scope_tenant_est_ecrivable():
    """Sans surface d'écriture, le mécanisme serait inerte pour toujours."""
    from oto_mcp import guide_store
    assert "tenant" in guide_store._INIT_IN_GUIDES
    assert guide_store._init_ref("tenant", "acme") == ("acme", guide_store.INIT_SLUG), (
        "l'owner d'un socle de tenant est son SLUG — clé stable du registre, lisible "
        "en base, et jamais un id numérique qui obligerait à une jointure")
