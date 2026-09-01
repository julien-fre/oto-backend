"""Une org créée depuis un front tiers porte la marque de ce front, dès l'INSERT.

Vécu le 15/08, sur une org Acme : un client invité par mail a reçu un lien vers
`oto.cx`, augmenté d'un magic-link minté sur NOTRE Logto — inerte contre l'émetteur
du tenant (`auth.acme.test` depuis le 03/08). Il s'est donc créé un second compte
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
from oto_mcp.capabilities.orgs import core as orgs
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


# --- la dérivation vit dans le STORE : aucun créateur ne peut l'oublier ----------

@pytest.fixture
def insert_capture(monkeypatch):
    """`_connect` factice : capture les paramètres de l'INSERT de `create_org`."""
    vus = {}

    class _Conn:
        def execute(self, sql, params=None):
            if "INSERT INTO orgs" in sql:
                vus["params"] = params
            class _R:
                def fetchone(_s):
                    return {"id": 4243}
            return _R()

    class _Ctx:
        def __enter__(self):
            return _Conn()
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(org_store, "_connect", lambda: _Ctx())
    return vus


def test_le_store_derive_le_front_quand_personne_ne_le_pose(registre, insert_capture):
    """Le trou du 26/08 : deux des trois créateurs d'org ne dérivaient rien. Désormais
    l'appel nu — celui de la console admin et de l'org perso — pose quand même."""
    org_store.create_org("Perso", created_by="acme:u-1")
    _, _, base, brand = insert_capture["params"]
    assert (base, brand) == ("https://app.acme.test", "acme")


def test_le_store_ne_marque_pas_une_org_oto(registre, insert_capture):
    org_store.create_org("Une org oto", created_by="bn01jfy76a5n")
    assert insert_capture["params"][2:] == (None, None)


def test_le_front_suit_le_responsable_pas_loperateur(registre, insert_capture):
    """Console admin : un opérateur oto provisionne une org POUR un compte Acme —
    elle doit être une org Acme (`front_of`), `created_by` restant l'opérateur."""
    org_store.create_org("Pour Acme", created_by="bn01jfy76a5n", front_of="acme:u-1")
    _, created_by, base, brand = insert_capture["params"]
    assert created_by == "bn01jfy76a5n"
    assert (base, brand) == ("https://app.acme.test", "acme")


def test_un_front_pose_par_lappelant_nest_pas_rederive(registre, insert_capture):
    """`capabilities/orgs.py` dérive déjà et passe le résultat : le store le garde."""
    org_store.create_org("X", created_by="acme:u-1",
                         front_base_url="https://app.acme.test", front_brand="acme")
    assert insert_capture["params"][2:] == ("https://app.acme.test", "acme")


def test_la_console_admin_passe_le_front_du_responsable(registre, monkeypatch):
    from oto_mcp.capabilities.orgs import admin as orgs_admin
    vus = {}
    monkeypatch.setattr(org_store, "create_org",
                        lambda name, created_by=None, **kw: vus.update(kw) or 4244)
    monkeypatch.setattr(org_store, "add_org_member", lambda *a, **k: None)
    monkeypatch.setattr(orgs_admin, "_resolve_target", lambda t: "acme:u-1")
    orgs_admin._create_org(ResolvedCtx(sub="bn01jfy76a5n"),
                           orgs_admin.CreateOrgInput(name="Pour Acme", admin="u@acme.test"))
    assert vus["front_of"] == "acme:u-1"
