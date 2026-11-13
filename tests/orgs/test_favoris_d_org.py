"""Les favoris partagés d'une org (`capabilities/orgs/favoris.py`), sur une vraie base.

Ce qu'on prouve : un membre épingle une page ou un tableau de l'org, tous les membres le
voient, dans l'ordre où il a été posé, et épingler deux fois ne fait qu'un favori ; un
favori n'accorde rien — l'objet perso d'un membre qu'il épingle reste invisible aux
autres, qui ne peuvent pas l'épingler non plus ; un objet d'une autre org ne s'épingle
pas (sauf partagé à l'org), et la réponse est la même que pour un id absent ; tout membre
désépingle, deux fois sans erreur ; un objet supprimé sort de la liste et sa ligne part ;
une page archivée sort de la liste ; un `kind` inconnu est refusé ; un non-membre n'entre
pas.
"""
from __future__ import annotations

import uuid

import pytest

ADMIN, MOI, VOISIN, DEHORS = ("usr_fav_admin", "usr_fav_moi", "usr_fav_voisin",
                              "usr_fav_dehors")


def _nom() -> str:
    return "fav-" + uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def monde(live):
    from oto_mcp import db, org_store, ownership
    from oto_mcp.capabilities.docs import common as _kind_doc  # noqa: F401 — enregistre `doc`
    for sub in (ADMIN, MOI, VOISIN, DEHORS):
        db.upsert_user(sub, email=f"{sub}@t.invalid", name=sub)
    a = org_store.create_org("A favoris", created_by=ADMIN)
    b = org_store.create_org("B favoris", created_by=ADMIN)
    org_store.add_org_member(a, ADMIN, "org_admin")
    org_store.add_org_member(a, MOI)
    org_store.add_org_member(a, VOISIN)
    org_store.add_org_member(b, ADMIN, "org_admin")
    org_store.add_org_member(b, MOI)
    org_store.add_org_member(b, DEHORS)
    tds = ownership.TYPE_RESSOURCE_DATASTORE

    p_org = int(db.create_project("org", str(a), _nom(), created_by=ADMIN))
    p_perso = int(db.create_project("user", MOI, _nom(), created_by=MOI, context_org_id=a))
    p_b = int(db.create_project("org", str(b), _nom(), created_by=ADMIN))
    d = {
        "org": db.create_doc(p_org, "Page de l'org", body_md="x", created_by=ADMIN),
        "archivee": db.create_doc(p_org, "Page archivée", body_md="x", created_by=ADMIN),
        "supprimee": db.create_doc(p_org, "Page supprimée", body_md="x", created_by=ADMIN),
        "perso": db.create_doc(p_perso, "Ma page", body_md="x", created_by=MOI),
        "b": db.create_doc(p_b, "Page de B", body_md="x", created_by=ADMIN),
    }
    t = {
        "org": db.create_datastore("org", str(a), _nom()),
        "supprime": db.create_datastore("org", str(a), _nom()),
        "perso": db.create_datastore("user", MOI, _nom(), context_org_id=a),
        "b": db.create_datastore("org", str(b), _nom()),
        "b_partage_a": db.create_datastore("org", str(b), _nom()),
    }
    ownership.grant(tds, str(t["b_partage_a"]), "org", str(a), "read", granted_by=ADMIN)
    return {"a": a, "b": b, "p_org": p_org, "d": d, "t": t}


@pytest.fixture(autouse=True)
def vide(monde):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM org_favorites WHERE org_id = ANY(%s)",
                     ([monde["a"], monde["b"]],))


def _ctx(sub, org):
    from oto_mcp.capabilities._types import ResolvedCtx
    return ResolvedCtx(sub=sub, org_id=org)


def _poser(sub, org, kind, ref):
    from oto_mcp.capabilities.orgs import favoris as F
    return F._ajouter(_ctx(sub, org), F.FavoriteAddInput(org_id=org, kind=kind, id=ref))


def _retirer(sub, org, kind, ref):
    from oto_mcp.capabilities.orgs import favoris as F
    return F._retirer(_ctx(sub, org),
                      F.FavoriteRemoveInput(org_id=org, kind=kind, ref_id=ref))


def _lire(sub, org):
    from oto_mcp.capabilities.orgs import favoris as F
    return F._lister(_ctx(sub, org), F.FavoritesInput(org_id=org))["favorites"]


def _cles(favoris):
    return [(f["kind"], f["id"]) for f in favoris]


def test_un_membre_epingle_et_tous_les_membres_voient_dans_l_ordre(monde):
    a, d, t = monde["a"], monde["d"], monde["t"]
    _poser(MOI, a, "table", t["org"])
    vue = _poser(MOI, a, "doc", d["org"])["favorites"]
    assert _cles(vue) == [("table", t["org"]), ("doc", d["org"])]
    page = vue[1]
    assert page["title"] == "Page de l'org"
    assert page["project_id"] == monde["p_org"]
    assert page["added_by"] == MOI and page["added_at"]
    assert vue[0]["project_id"] is None and vue[0]["title"]
    assert _cles(_lire(VOISIN, a)) == _cles(vue)
    assert _cles(_lire(ADMIN, a)) == _cles(vue)


def test_epingler_deux_fois_ne_fait_qu_un_favori_et_garde_son_auteur(monde):
    a, t = monde["a"], monde["t"]
    _poser(MOI, a, "table", t["org"])
    vue = _poser(VOISIN, a, "table", t["org"])["favorites"]
    assert _cles(vue) == [("table", t["org"])]
    assert vue[0]["added_by"] == MOI


def test_un_favori_n_accorde_rien(monde):
    """L'objet perso de MOI, épinglé par MOI : lui le voit, VOISIN non — et VOISIN ne
    peut pas l'épingler lui-même (même 404 qu'un id absent)."""
    from oto_mcp.capabilities._types import AuthzDenied
    a, d, t = monde["a"], monde["d"], monde["t"]
    _poser(MOI, a, "doc", d["perso"])
    _poser(MOI, a, "table", t["perso"])
    assert _cles(_lire(MOI, a)) == [("doc", d["perso"]), ("table", t["perso"])]
    assert _lire(VOISIN, a) == []
    for kind, ref in (("doc", d["perso"]), ("table", t["perso"]), ("table", 10 ** 12)):
        with pytest.raises(AuthzDenied) as e:
            _poser(VOISIN, a, kind, ref)
        assert (e.value.status, e.value.code) == (404, "favorite_target_unknown")


def test_un_objet_d_une_autre_org_ne_s_epingle_pas_sauf_partage_a_l_org(monde):
    """MOI lit les objets de B (il en est membre) : ils ne s'épinglent pas dans A. Le
    tableau de B partagé à A, lui, s'y épingle et s'y voit."""
    from oto_mcp.capabilities._types import AuthzDenied
    a, d, t = monde["a"], monde["d"], monde["t"]
    for kind, ref in (("doc", d["b"]), ("table", t["b"])):
        with pytest.raises(AuthzDenied) as e:
            _poser(MOI, a, kind, ref)
        assert (e.value.status, e.value.code) == (404, "favorite_target_unknown")
    _poser(MOI, a, "table", t["b_partage_a"])
    assert _cles(_lire(VOISIN, a)) == [("table", t["b_partage_a"])]
    assert _lire(MOI, monde["b"]) == []


def test_tout_membre_desepingle_et_deux_fois_sans_erreur(monde):
    a, d, t = monde["a"], monde["d"], monde["t"]
    _poser(MOI, a, "doc", d["org"])
    _poser(MOI, a, "table", t["org"])
    assert _cles(_retirer(VOISIN, a, "doc", d["org"])["favorites"]) == [("table", t["org"])]
    assert _cles(_retirer(VOISIN, a, "doc", d["org"])["favorites"]) == [("table", t["org"])]


def test_un_objet_supprime_sort_et_sa_ligne_part(monde):
    from oto_mcp import db
    from oto_mcp.db import favoris as store
    a, d, t = monde["a"], monde["d"], monde["t"]
    _poser(MOI, a, "doc", d["supprimee"])
    _poser(MOI, a, "table", t["supprime"])
    _poser(MOI, a, "doc", d["org"])
    db.delete_doc(d["supprimee"])
    db.delete_datastore_by_id(t["supprime"])
    assert _cles(_lire(VOISIN, a)) == [("doc", d["org"])]
    assert [(f["kind"], f["ref_id"]) for f in store.lister(a)] == [("doc", d["org"])]


def test_une_page_archivee_sort_de_la_liste_sans_perdre_sa_ligne(monde):
    from oto_mcp.db import doc_archivage
    from oto_mcp.db import favoris as store
    a, d = monde["a"], monde["d"]
    _poser(MOI, a, "doc", d["archivee"])
    doc_archivage.archive_doc(d["archivee"])
    assert _lire(MOI, a) == []
    assert [(f["kind"], f["ref_id"]) for f in store.lister(a)] == [("doc", d["archivee"])]


def test_un_kind_inconnu_est_refuse(monde):
    from oto_mcp.capabilities._types import AuthzDenied
    for geste in (_poser, _retirer):
        with pytest.raises(AuthzDenied) as e:
            geste(MOI, monde["a"], "app", 1)
        assert (e.value.status, e.value.code) == (400, "invalid_favorite_kind")


def test_un_non_membre_n_entre_pas(monde):
    from oto_mcp.capabilities._types import AuthzDenied, RawCtx
    from oto_mcp.capabilities.orgs import favoris as F
    from oto_mcp.capabilities.registry import CAPABILITIES
    caps = {c.key: c for c in CAPABILITIES if c.key.startswith("org.favorites.")}
    assert set(caps) == {"org.favorites.list", "org.favorites.add", "org.favorites.remove"}
    with pytest.raises(AuthzDenied) as e:
        caps["org.favorites.list"].authz(RawCtx(sub=DEHORS), F.FavoritesInput(org_id=monde["a"]))
    assert e.value.status == 403
    ctx = caps["org.favorites.list"].authz(RawCtx(sub=VOISIN),
                                          F.FavoritesInput(org_id=monde["a"]))
    assert ctx.org_id == monde["a"]
