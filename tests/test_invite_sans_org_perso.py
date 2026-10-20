"""Un invité ne reçoit pas d'org perso à côté de l'org qu'il rejoint.

Deux chemins y menaient : l'inscription (`upsert_user`) créait l'org perso même
quand la réconciliation d'invitation venait de faire entrer le compte dans une org,
et l'invité PAR LIEN la recevait au premier appel authentifié, avant que le front ne
présente son jeton. Le premier est gardé dans `upsert_user`
(`test_identite_naissance_bruyante`), le second ici : `liberer_perso_vierge` archive
à l'acceptation l'org perso née de l'inscription — et rien d'autre.

Banc : le PostgreSQL réel de `test_org_quota_archivage`, augmenté des tables que le
critère « vierge » lit (projets, tableaux, coffre, instances, comptes connectés,
partages).

Revue du 19/10 : la maison d'un invité est FIXE et EXPLICITE — son org perso, créée à
la demande par un partage nominatif (`ownership.maison_du_partage`), jamais son org
active ; et un échec de `_rendre_un_espace` ou de `liberer_perso_vierge` LÈVE.
"""
from __future__ import annotations

import pytest

from oto_mcp import discovery
from test_org_quota_archivage import AUTRE, SUB, _espace, _perso, conn, store  # noqa: F401


@pytest.fixture()
def banc(store, conn):
    tables = ("projects", "user_datastores", "connector_credentials",
              "connector_instances", "unipile_accounts", "resource_grants")
    for t in reversed(tables):
        conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    from test_org_quota_archivage import _real_ddl
    for t in tables:
        conn.execute(_real_ddl(t))
    conn.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS context_org_id BIGINT")
    return store


def _invite_par_lien(store) -> tuple[int, int]:
    """L'état exact après l'acceptation d'un lien : org perso née à l'inscription
    (avec son projet « Découverte »), puis l'org de l'invitation rejointe."""
    perso = _perso(store, SUB)
    with store._connect() as c:
        c.execute("INSERT INTO projects (owner_type, owner_id, name) VALUES ('org', %s, %s)",
                  (str(perso), discovery.PROJECT_NAME))
    boite = _espace(store, AUTRE, "Boîte")
    store.add_org_member(boite, SUB, "org_member")
    return perso, boite


def test_la_perso_vierge_est_relachee_et_l_org_rejointe_reste_maison(banc):
    perso, boite = _invite_par_lien(banc)
    assert banc.liberer_perso_vierge(SUB, boite) == perso
    assert [o["org_id"] for o in banc.list_orgs_for_user(SUB)] == [boite]
    assert banc.get_active_org(SUB) == boite
    assert banc.get_personal_org(SUB) is None


def test_une_perso_qui_a_servi_n_est_pas_relachee(banc):
    perso, boite = _invite_par_lien(banc)
    with banc._connect() as c:
        c.execute("INSERT INTO projects (owner_type, owner_id, name) VALUES ('org', %s, 'Mon travail')",
                  (str(perso),))
    assert banc.liberer_perso_vierge(SUB, boite) is None
    assert banc.get_personal_org(SUB) == perso


def test_une_perso_avec_un_tableau_n_est_pas_relachee(banc):
    perso, boite = _invite_par_lien(banc)
    with banc._connect() as c:
        c.execute("INSERT INTO user_datastores (owner_type, owner_id, namespace, context_org_id) "
                  "VALUES ('user', %s, 'leads', %s)", (SUB, perso))
    assert banc.liberer_perso_vierge(SUB, boite) is None


def test_une_perso_partagee_n_est_pas_relachee(banc):
    perso, boite = _invite_par_lien(banc)
    banc.add_org_member(perso, AUTRE, "org_member")
    assert banc.liberer_perso_vierge(SUB, boite) is None


def test_une_perso_ancienne_n_est_pas_relachee(banc):
    """Au-delà de l'heure d'inscription, ce n'est plus un artefact d'inscription."""
    perso, boite = _invite_par_lien(banc)
    with banc._connect() as c:
        c.execute("UPDATE orgs SET created_at = now() - interval '2 hours' WHERE id = %s", (perso,))
    assert banc.liberer_perso_vierge(SUB, boite) is None


@pytest.mark.parametrize("entity_type, entity_id", [
    ("org", "{perso}"),                  # clé déposée au coffre de l'org perso
    ("member", "{perso}:" + SUB),        # clé du membre DANS l'org perso
])
def test_une_perso_avec_une_cle_deposee_n_est_pas_relachee(banc, entity_type, entity_id):
    perso, boite = _invite_par_lien(banc)
    with banc._connect() as c:
        c.execute("INSERT INTO connector_credentials (entity_type, entity_id, connector, "
                  "secret_enc) VALUES (%s, %s, 'serper', 'x')",
                  (entity_type, entity_id.format(perso=perso)))
    assert banc.liberer_perso_vierge(SUB, boite) is None
    assert banc.get_personal_org(SUB) == perso


def test_la_cle_d_une_autre_org_ne_compte_pas(banc):
    """Le critère lit l'org perso, pas les autres : la clé de membre posée dans l'org
    rejointe (`<org rejointe>:<sub>`) n'empêche pas de relâcher la perso."""
    perso, boite = _invite_par_lien(banc)
    with banc._connect() as c:
        c.execute("INSERT INTO connector_credentials (entity_type, entity_id, connector, "
                  "secret_enc) VALUES ('member', %s, 'serper', 'x')", (f"{boite}:{SUB}",))
    assert banc.liberer_perso_vierge(SUB, boite) == perso


def test_une_perso_avec_une_instance_n_est_pas_relachee(banc):
    perso, boite = _invite_par_lien(banc)
    with banc._connect() as c:
        c.execute("INSERT INTO connector_instances (connector, owner_type, owner_id) "
                  "VALUES ('serper', 'member', %s)", (f"{perso}:{SUB}",))
    assert banc.liberer_perso_vierge(SUB, boite) is None


@pytest.mark.parametrize("deconnecte", [False, True])
def test_une_perso_avec_un_compte_connecte_n_est_pas_relachee(banc, deconnecte):
    """Même déconnecté : la ligne est la preuve de propriété du compte."""
    perso, boite = _invite_par_lien(banc)
    with banc._connect() as c:
        c.execute("INSERT INTO unipile_accounts (sub, account_id, org_id, disconnected_at) "
                  "VALUES (%s, 'acc', %s, %s)",
                  (SUB, perso, "2026-10-19" if deconnecte else None))
    assert banc.liberer_perso_vierge(SUB, boite) is None


def test_une_perso_maison_d_un_partage_n_est_pas_relachee(banc):
    perso, boite = _invite_par_lien(banc)
    with banc._connect() as c:
        c.execute("INSERT INTO resource_grants (resource_type, resource_id, "
                  "principal_type, principal_id) VALUES ('project', '1', 'user', %s)", (SUB,))
    assert banc.liberer_perso_vierge(SUB, boite) is None


def test_rejoindre_sa_propre_perso_ne_la_relache_pas(banc):
    perso = _perso(banc, SUB)
    assert banc.liberer_perso_vierge(SUB, perso) is None


# ── sans org perso : une maison fixe et explicite, créée à la demande ────────

def test_sans_org_perso_aucune_org_n_est_la_maison(monkeypatch):
    """Pas de repli sur l'org active : sans org perso, aucune org n'est la maison —
    ni l'org active (l'org d'entreprise), ni une autre quand l'active change."""
    from oto_mcp import org_store, ownership
    monkeypatch.setattr(org_store, "get_personal_org", lambda sub: None)
    monkeypatch.setattr(org_store, "get_active_org", lambda sub: 5)
    assert ownership.org_perso_de("u", 5) is False
    assert ownership.perso_de_la_liste("u", 5) == []
    assert ownership.org_perso_de("u", None) is False
    monkeypatch.setattr(org_store, "get_personal_org", lambda sub: 9)
    assert ownership.org_perso_de("u", 5) is False
    assert ownership.org_perso_de("u", 9) is True


@pytest.fixture()
def maison(monkeypatch):
    """`grant` sur doublures : l'org perso de chacun, les créations, les écritures."""
    from oto_mcp import db, org_store, ownership
    persos: dict[str, int] = {"a-une-perso": 9}
    journal: list = []
    monkeypatch.setattr(org_store, "get_personal_org", lambda sub: persos.get(sub))
    monkeypatch.setattr(db, "get_user", lambda sub: {"email": f"{sub}@x.tld", "name": sub})

    def _ensure(sub, email=None, name=None):
        journal.append(("ensure", sub, email))
        persos[sub] = 42
        return 42
    monkeypatch.setattr(org_store, "ensure_personal_org", _ensure)
    monkeypatch.setattr(db, "grant_resource",
                        lambda *a, **k: journal.append(("grant",) + a[:4]))
    return ownership, journal


def test_un_partage_nominatif_cree_la_maison_de_l_invite(maison):
    ownership, journal = maison
    ownership.grant("project", "1", "user", "invite", role="viewer")
    assert journal == [("ensure", "invite", "invite@x.tld"),
                       ("grant", "project", "1", "user", "invite")]
    assert ownership.org_perso_de("invite", 42) is True


@pytest.mark.parametrize("kind, ptype, pid", [
    ("project", "user", "a-une-perso"),   # a déjà sa maison
    ("project", "org", "5"),              # partage à l'org : pas une personne
    ("project", "group", "3"),
    ("runner_trigger", "user", "invite"), # agent : listé dans l'org où il tourne
])
def test_un_partage_qui_n_en_demande_pas_ne_cree_rien(maison, kind, ptype, pid):
    ownership, journal = maison
    ownership.grant(kind, "1", ptype, pid, role="viewer")
    assert [e for e in journal if e[0] == "ensure"] == []


def test_sans_maison_le_partage_n_est_pas_pose(maison, monkeypatch):
    """Un échec de création lève, et rien n'est écrit : pas de partage orphelin."""
    from oto_mcp import org_store
    ownership, journal = maison

    def _panne(*a, **k):
        raise RuntimeError("base indisponible")
    monkeypatch.setattr(org_store, "ensure_personal_org", _panne)
    with pytest.raises(RuntimeError):
        ownership.grant("project", "1", "user", "invite", role="viewer")
    assert journal == []


@pytest.mark.parametrize("restantes, recree", [([], True), ([{"org_id": 3}], False)])
def test_quitter_sa_derniere_org_rend_un_espace(monkeypatch, restantes, recree):
    from oto_mcp import db, org_store
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.orgs import members as M
    monkeypatch.setattr(M, "refuser_maison_sous_verrou", lambda ctx, geste: None)
    monkeypatch.setattr(M, "_require_org_exists", lambda org_id: None)
    monkeypatch.setattr(org_store, "get_personal_org", lambda sub: None)
    monkeypatch.setattr(org_store, "get_org_role", lambda org_id, sub: "org_member")
    monkeypatch.setattr(org_store, "remove_org_member", lambda org_id, sub, actor=None: True)
    monkeypatch.setattr(org_store, "list_orgs_for_user", lambda sub: restantes)
    monkeypatch.setattr(db, "get_user", lambda sub: {"email": "a@b.c", "name": "A"})
    crees = []
    monkeypatch.setattr(org_store, "ensure_personal_org",
                        lambda sub, email=None, name=None: crees.append(sub))
    M._leave_org(ResolvedCtx(sub="u", org_id=4), M.LeaveOrgInput(org_id=4))
    assert crees == (["u"] if recree else [])


def test_quitter_sa_derniere_org_leve_si_l_espace_n_est_pas_rendu(monkeypatch):
    """Chemin de rattrapage (`docs/conventions.md`) : plus d'`except` qui masque — la
    panne de `ensure_personal_org` remonte à l'appelant."""
    from oto_mcp import db, org_store
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.orgs import members as M
    monkeypatch.setattr(M, "refuser_maison_sous_verrou", lambda ctx, geste: None)
    monkeypatch.setattr(M, "_require_org_exists", lambda org_id: None)
    monkeypatch.setattr(org_store, "get_personal_org", lambda sub: None)
    monkeypatch.setattr(org_store, "get_org_role", lambda org_id, sub: "org_member")
    monkeypatch.setattr(org_store, "remove_org_member", lambda org_id, sub, actor=None: True)
    monkeypatch.setattr(org_store, "list_orgs_for_user", lambda sub: [])
    monkeypatch.setattr(db, "get_user", lambda sub: {})

    def _panne(*a, **k):
        raise RuntimeError("base indisponible")
    monkeypatch.setattr(org_store, "ensure_personal_org", _panne)
    with pytest.raises(RuntimeError):
        M._leave_org(ResolvedCtx(sub="u", org_id=4), M.LeaveOrgInput(org_id=4))


def test_accepter_leve_si_la_perso_vierge_n_est_pas_relachee(monkeypatch):
    from oto_mcp import org_store
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.orgs import invites as I
    monkeypatch.setattr(org_store, "accept_invitation",
                        lambda token, sub: {"org_id": 7, "org_role": "org_member"})
    monkeypatch.setattr(org_store, "get_org", lambda oid: {"id": oid, "name": "Boîte"})
    monkeypatch.setattr(org_store, "get_active_org", lambda sub: 7)

    def _panne(*a, **k):
        raise RuntimeError("base indisponible")
    monkeypatch.setattr(org_store, "liberer_perso_vierge", _panne)
    with pytest.raises(RuntimeError):
        I._invite_accept(ResolvedCtx(sub="u", org_id=None), I.InviteAcceptInput(token="t"))
