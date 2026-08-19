"""Une org créée depuis un front tiers porte la marque de ce front, dès l'INSERT.

Vécu le 15/08, sur une org Tulina : un client invité par mail a reçu un lien vers
`oto.cx`, augmenté d'un magic-link minté sur NOTRE Logto — inerte contre l'émetteur
du tenant (`auth.tulina.ai` depuis le 03/08). Il s'est donc créé un second compte
CHEZ NOUS, a accepté l'invitation avec celui-là, puis, revenu sur le front de son
fournisseur, s'est retrouvé membre de rien : deux identités pour une personne, et
l'org inatteignable depuis le seul front qu'il connaisse.

`b6e1d27` avait donné aux invitations la marque de l'org (`orgs.front_base_url`) —
mais RIEN ne posait jamais ces colonnes. `_create_org` les laissait NULL, donc
chaque nouvelle org d'un tenant tiers repartait sur oto, et la correction ne
s'appliquait qu'aux orgs qu'on pensait à renseigner à la main.

Le chemin SQL n'est pas exerçable ici (pas de PostgreSQL sur le poste, cf.
`test_tenant_l2_registre_emetteurs`) : on teste la dérivation — pure — et le fait
que la création la transmet bien jusqu'à l'écriture.
"""
from __future__ import annotations

import pytest

from oto_mcp import config, org_store, tenancy
from oto_mcp.capabilities import orgs
from oto_mcp.capabilities._types import ResolvedCtx


@pytest.fixture
def registre():
    avant = tenancy.current()
    tenancy.install(tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[
            {"slug": "acme", "issuer": "https://auth.acme.test/oidc",
             "dashboard_url": "https://app.acme.test"},
            # Un tenant SANS adresse déclarée : rien ne doit être posé.
            {"slug": "beta", "issuer": "https://auth.beta.test/oidc"},
        ])))
    yield
    tenancy.install(avant)


# --- la dérivation ------------------------------------------------------------

def test_un_compte_de_tenant_pose_le_front_de_son_produit(registre):
    assert config.front_for("acme:u-1") == ("https://app.acme.test", "acme")


def test_un_compte_de_la_plateforme_ne_pose_rien(registre):
    """Le tenant `oto` garde un sub NU et des colonnes NULL : l'existant ne bouge
    pas d'une ligne, c'est ce qui rend le changement additif."""
    assert config.front_for("bn01jfy76a5n") == (None, None)


def test_un_tenant_SANS_adresse_ne_pose_rien(registre):
    """L'inertie, même règle que `dashboard_url_for` : déclarer un tenant ne marque
    aucune org tant qu'on ne lui a pas donné d'adresse. Jamais une adresse devinée."""
    assert config.front_for("beta:u-1") == (None, None)


def test_sans_compte_on_ne_pose_rien(registre):
    assert config.front_for(None) == (None, None)
    assert config.front_for("") == (None, None)


def test_un_registre_illisible_ne_bloque_pas_la_creation(monkeypatch):
    """Ce chemin est celui de la CRÉATION d'une org : un registre indisponible doit
    dégrader vers oto, jamais faire échouer la création."""
    def _boum():
        raise RuntimeError("registre indisponible")

    monkeypatch.setattr(tenancy, "current", _boum)
    assert config.front_for("acme:u-1") == (None, None)


# --- la création transmet bien la dérivation ----------------------------------

@pytest.fixture
def creation_sans_db(monkeypatch):
    """Les seams DB de `_create_org`, monkeypatchés ; on capture l'appel d'écriture."""
    vus = {}

    def _create_org(name, created_by=None, front_base_url=None, front_brand=None):
        vus.update(name=name, created_by=created_by,
                   front_base_url=front_base_url, front_brand=front_brand)
        return 4242

    monkeypatch.setattr(org_store, "count_orgs_created_by", lambda sub: 0)
    monkeypatch.setattr(org_store, "create_org", _create_org)
    monkeypatch.setattr(org_store, "add_org_member", lambda *a, **k: None)
    monkeypatch.setattr(org_store, "set_active_org", lambda *a, **k: None)
    return vus


def test_la_creation_transmet_le_front_derive(registre, creation_sans_db):
    out = orgs._create_org(ResolvedCtx(sub="acme:u-1"),
                           orgs.CreateOrgInput(name="Un espace Acme"))
    assert out["org_id"] == 4242
    assert creation_sans_db["front_base_url"] == "https://app.acme.test"
    assert creation_sans_db["front_brand"] == "acme"


def test_la_creation_par_un_compte_de_la_plateforme_reste_nulle(registre,
                                                                creation_sans_db):
    """La régression qui coûterait le plus cher : marquer par erreur les orgs oto
    enverrait TOUTES leurs invitations sur un front qui n'est pas le leur."""
    orgs._create_org(ResolvedCtx(sub="bn01jfy76a5n"),
                     orgs.CreateOrgInput(name="Une org oto"))
    assert creation_sans_db["front_base_url"] is None
    assert creation_sans_db["front_brand"] is None


def test_le_front_nest_pas_declarable_par_lappelant(registre, creation_sans_db):
    """Dérivé, jamais déclaré (b6e1d27) : l'entrée ne porte QUE le nom. Un champ
    accepté ici laisserait une org revendiquer un front auquel elle n'appartient
    pas — et c'est le contrat d'entrée qu'il faudrait ensuite retirer aux
    intégrateurs. `CreateOrgInput` doit donc rester à un seul champ."""
    assert set(orgs.CreateOrgInput.model_fields) == {"name"}
