"""Un invité ne reçoit pas d'org perso à côté de l'org qu'il rejoint.

Deux chemins y menaient : l'inscription (`upsert_user`) créait l'org perso même
quand la réconciliation d'invitation venait de faire entrer le compte dans une org,
et l'invité PAR LIEN la recevait au premier appel authentifié, avant que le front ne
présente son jeton. Le premier est gardé dans `upsert_user`
(`test_identite_naissance_bruyante`), le second ici : `liberer_perso_vierge` archive
à l'acceptation l'org perso née de l'inscription — et rien d'autre.

Banc : le PostgreSQL réel de `test_org_quota_archivage`, augmenté des deux tables
que le critère « vierge » lit.
"""
from __future__ import annotations

import pytest

from oto_mcp import discovery
from test_org_quota_archivage import AUTRE, SUB, _espace, _perso, conn, store  # noqa: F401


@pytest.fixture()
def banc(store, conn):
    for t in ("projects", "user_datastores"):
        conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    from test_org_quota_archivage import _real_ddl
    for t in ("projects", "user_datastores"):
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


def test_rejoindre_sa_propre_perso_ne_la_relache_pas(banc):
    perso = _perso(banc, SUB)
    assert banc.liberer_perso_vierge(SUB, perso) is None


# ── sans org perso : la maison de ses objets, et le repli quand il sort ─────

def test_sans_org_perso_la_maison_de_mes_objets_est_mon_org_maison(monkeypatch):
    """Partages nominatifs et objets sans org de création sortent dans une liste :
    l'org perso si le compte en a une, sinon son org maison — jamais une autre."""
    from oto_mcp import org_store, ownership
    monkeypatch.setattr(org_store, "get_personal_org", lambda sub: None)
    monkeypatch.setattr(org_store, "get_active_org", lambda sub: 5)
    assert ownership.org_perso_de("u", 5) is True
    assert ownership.org_perso_de("u", 6) is False
    assert ownership.org_perso_de("u", None) is False
    monkeypatch.setattr(org_store, "get_personal_org", lambda sub: 9)
    assert ownership.org_perso_de("u", 5) is False
    assert ownership.org_perso_de("u", 9) is True


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
