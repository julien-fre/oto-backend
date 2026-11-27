"""L'accès à un tableau À TRAVERS une app seulement (`datastore/par_l_app.py`), contre un
PostgreSQL réel : sans aucun partage du tableau, le lecteur le lit — et l'écrit par les
gestes de l'app — dans l'appel de CETTE app, borné par son rôle ; hors de l'app (outils
de tableau, une autre app), rien. L'accès part avec le partage de l'app ; seul qui peut
partager le tableau l'ouvre ainsi."""
from __future__ import annotations

import uuid

import pytest

HOTE = "https://manage.oto.cx"


def _ctx(m, sub):
    """L'org de l'APPEL : la sienne pour le partenaire (un invité de l'extérieur
    consulte depuis son espace), celle de l'app pour les autres."""
    from oto_mcp.capabilities._types import ResolvedCtx
    org = m["org_p"] if sub == m["partenaire"] else m["org"]
    return ResolvedCtx(sub=sub, org_id=org, channel="rest")


def _apps(m, sub, **kw):
    from oto_mcp.capabilities import apps as A
    return A._app(_ctx(m, sub), A.AppInput(app_id=m["app"], **kw))


def _lire(m, sub, **kw):
    from oto_mcp.capabilities import apps_appel as C
    return C._call(_ctx(m, sub), C.AppCallInput(app_id=m["app"], action="read_rows",
                                                table="fiches", **kw))


def _refus(fn, *a, **kw):
    from oto_mcp.capabilities._types import AuthzDenied
    with pytest.raises(AuthzDenied) as e:
        fn(*a, **kw)
    return e.value


def _comme(sub):
    from contextlib import contextmanager

    from oto_mcp import client_trace, geste

    @contextmanager
    def ctx():
        jeton = client_trace.set_current(ip=None, user_agent=None, origin=HOTE, fetch_dest="empty")
        try:
            with geste.portee(geste.CONSOLE, sub, f"g-{uuid.uuid4().hex[:6]}"):
                yield
        finally:
            client_trace.reset(jeton)
    return ctx()



SCHEMA = {"fields": [{"key": "name", "type": "text"}, {"key": "status", "type": "text"},
                     {"key": "notes", "type": "text"}]}


@pytest.fixture
def monde(live, monkeypatch):
    from oto_mcp import db, org_store
    from oto_mcp.capabilities import resources as R
    from oto_mcp.capabilities.apps_manifeste import forme
    from oto_mcp.datastore.core import make_store
    monkeypatch.setattr(R.email, "send_resource_shared_email", lambda to, **kw: True)
    u = uuid.uuid4().hex[:8]
    auteur, partenaire, gerant_app = f"auteur_{u}", f"partenaire_{u}", f"gerant_{u}"
    for s in (auteur, partenaire, gerant_app):
        db.upsert_user(s, email=f"{s}@example.test")
    org = org_store.create_org(f"o_{u}", created_by=auteur)
    org_store.add_org_member(org, auteur, "org_admin")
    # Le partenaire vit dans SA propre org : un invité de l'extérieur.
    org_p = org_store.create_org(f"p_{u}", created_by=partenaire)
    org_store.add_org_member(org_p, partenaire, "org_admin")
    ns_id = db.create_datastore("user", auteur, f"fiches_{u}", context_org_id=org)
    st = make_store(auteur)
    st.set_schema(str(ns_id), SCHEMA)
    ligne = st.append_row(str(ns_id), {"name": "Acme", "status": "open", "notes": "secret"})
    clean, problems = forme({
        "tables": {"fiches": {"table": str(ns_id), "fields": ["name", "status", "notes"],
                              "set": {"status": ["won", "lost"]}, "add": ["name"]}},
        "roles": {"partner": {"tables": {"fiches": {"fields": ["name", "status"]}}},
                  "reader": {"tables": {"fiches": {"fields": ["name"], "set": {},
                                                   "add": []}}}},
        "default_role": "partner"})
    assert problems == []
    clean["tables"]["fiches"]["table"] = str(ns_id)
    app = db.apps.create(("user", auteur), org, title=f"Desk {u}", manifest=clean,
                         html="", thumbnail=None, by=auteur)
    m = {"auteur": auteur, "partenaire": partenaire, "gerant": gerant_app, "org": org,
         "org_p": org_p, "ns_id": ns_id, "app": int(app["id"]), "row": ligne["_id"],
         "rev": ligne["_revision"]}
    return m


@pytest.fixture(autouse=True)
def _partages(monde):
    from oto_mcp.capabilities import resources as R
    from oto_mcp.capabilities.resources_v2 import ResourceInputV2
    m = monde
    for s, role in ((m["partenaire"], "contributor"), (m["gerant"], "manager")):
        R._resources(_ctx(m, m["auteur"]), ResourceInputV2(
            op="share", resource_type="app", resource_id=str(m["app"]), audience="person",
            email=f"{s}@example.test", role=role))


def _ouvrir(m, by=None, **kw):
    return _apps(m, by or m["auteur"], op="grant_through_app", principal_type="user",
                 principal_id=m["partenaire"], **kw)


def _outil_de_tableau(m, sub):
    from oto_mcp.capabilities.datastore import rows as R
    return R._list_rows(_ctx(m, sub), R.ListRowsInput(datastore=str(m["ns_id"])))


def test_sans_acces_par_l_app_le_partenaire_ne_lit_rien(monde):
    m = monde
    _refus(_lire, m, m["partenaire"])


def test_par_l_app_il_lit_son_role_et_rien_hors_de_l_app(monde):
    m = monde
    out = _ouvrir(m)
    assert out["skipped"] == []
    acces = {g["principal_id"]: g["through_app"] for g in out["grants"]}[m["partenaire"]]
    assert acces == [{"table": "fiches", "permission": "write"}]
    rows = _lire(m, m["partenaire"])["rows"]
    assert rows[0]["name"] == "Acme" and "notes" not in rows[0]
    # Hors de l'app : l'outil de tableau ne le trouve pas.
    _refus(_outil_de_tableau, m, m["partenaire"])
    from oto_mcp.datastore.core import DatastoreNotFound, make_store
    with pytest.raises(DatastoreNotFound):
        make_store(m["partenaire"]).get_schema(str(m["ns_id"]))


def test_par_l_app_il_ecrit_par_les_gestes_de_l_app(monde):
    from oto_mcp.capabilities import apps_appel as C
    m = monde
    _ouvrir(m)
    corps = dict(action="write_cell", table="fiches", row_id=m["row"], field="status",
                 value="won", expected_revision=m["rev"])
    with _comme(m["partenaire"]):
        accord = C._consent(_ctx(m, m["partenaire"]), C.AppCallInput(app_id=m["app"], **corps))
        out = C._call(_ctx(m, m["partenaire"]), C.AppCallInput(
            app_id=m["app"], consent=accord["consent"], **corps))
    assert out["row"]["status"] == "won"


def test_l_acces_part_avec_le_partage_de_l_app(monde):
    from oto_mcp import ownership
    m = monde
    _ouvrir(m)
    ownership.revoke("app", str(m["app"]), "user", m["partenaire"])
    _refus(_lire, m, m["partenaire"])


def test_retirer_l_acces_par_l_app(monde):
    m = monde
    _ouvrir(m)
    _apps(m, m["auteur"], op="revoke_through_app", principal_type="user",
          principal_id=m["partenaire"])
    _refus(_lire, m, m["partenaire"])


def test_qui_ne_peut_partager_le_tableau_ne_l_ouvre_pas(monde):
    """Le gérant de l'APP n'a aucun droit de partage sur le tableau : sauté, nommé."""
    m = monde
    out = _ouvrir(m, by=m["gerant"])
    assert out["skipped"] == [{"table": "fiches", "reason": "you can't share this table"}]
    _refus(_lire, m, m["partenaire"])


def test_seulement_a_qui_l_app_est_partagee(monde):
    from oto_mcp import db
    m = monde
    x = f"x_{uuid.uuid4().hex[:6]}"
    db.upsert_user(x, email=f"{x}@example.test")
    e = _refus(_apps, m, m["auteur"], op="grant_through_app", principal_type="user",
               principal_id=x)
    assert e.code == "not_shared"


def test_la_portee_ne_sort_pas_de_l_appel(monde):
    from oto_mcp.datastore import par_l_app
    m = monde
    _ouvrir(m)
    _lire(m, m["partenaire"])
    assert par_l_app.droit(m["partenaire"], str(m["ns_id"])) is None


def _geste(m, sub, **corps):
    from oto_mcp.capabilities import apps_appel as C
    with _comme(sub):
        accord = C._consent(_ctx(m, sub), C.AppCallInput(app_id=m["app"], **corps))
        return C._call(_ctx(m, sub), C.AppCallInput(app_id=m["app"],
                                                     consent=accord["consent"], **corps))


def test_agregat_lot_et_ajout_passent_par_la_meme_portee(monde):
    from oto_mcp.capabilities import apps_appel as C
    m = monde
    _ouvrir(m)
    groupes = C._call(_ctx(m, m["partenaire"]), C.AppCallInput(
        app_id=m["app"], action="read_aggregate", table="fiches",
        options={"group_by": "status"}))["groups"]
    assert groupes
    lot = C._call(_ctx(m, m["partenaire"]), C.AppCallInput(
        app_id=m["app"], action="batch",
        reads=[{"id": "a", "action": "read_rows", "table": "fiches"}]))["results"][0]
    assert lot["ok"] and "notes" not in lot["rows"][0]
    out = _geste(m, m["partenaire"], action="add_row", table="fiches", values={"name": "Beta"})
    assert out["through_app"] is True


def test_l_ecriture_servie_par_l_app_le_dit(monde):
    m = monde
    _ouvrir(m)
    out = _geste(m, m["partenaire"], action="write_cell", table="fiches", row_id=m["row"],
                 field="status", value="lost", expected_revision=m["rev"])
    assert out["through_app"] is True


def test_un_role_qui_n_ecrit_plus_ramene_l_acces_a_la_lecture(monde):
    m = monde
    _ouvrir(m)
    _apps(m, m["auteur"], op="set_role", principal_type="user", principal_id=m["partenaire"],
          role="reader")
    rows = _lire(m, m["partenaire"])["rows"]
    assert set(rows[0]) & {"name", "status", "notes"} == {"name"}
    e = _refus(_geste, m, m["partenaire"], action="write_cell", table="fiches",
               row_id=m["row"], field="status", value="won", expected_revision=m["rev"])
    assert e.status in (400, 403)


def test_un_editeur_qui_ne_peut_partager_le_tableau_n_elargit_rien(monde):
    """Il réécrit l'app (le rôle du partenaire lit maintenant `notes`) : il en devient
    l'auteur, et l'accès par l'app s'éteint au lieu de s'élargir."""
    from oto_mcp import db
    from oto_mcp.capabilities import resources as R
    from oto_mcp.capabilities.resources_v2 import ResourceInputV2
    m = monde
    _ouvrir(m)
    ed = f"ed_{uuid.uuid4().hex[:6]}"
    db.upsert_user(ed, email=f"{ed}@example.test")
    from oto_mcp import org_store
    org_store.add_org_member(m["org"], ed, "org_member")
    db.grant_resource("datastore_namespace", str(m["ns_id"]), "user", ed, role="viewer",
                      granted_by=m["auteur"])
    R._resources(_ctx(m, m["auteur"]), ResourceInputV2(
        op="share", resource_type="app", resource_id=str(m["app"]), audience="person",
        email=f"{ed}@example.test", role="editor"))
    vu = _apps(m, ed, op="get")["app"]
    manifeste = vu["manifest"]
    manifeste["roles"]["partner"]["tables"]["fiches"]["fields"] = ["name", "status", "notes"]
    _apps(m, ed, op="update", manifest=manifeste, expected_rev=vu["rev"])
    _refus(_lire, m, m["partenaire"])


def test_un_gerant_de_l_app_seul_ne_change_pas_le_role_d_un_lecteur_par_l_app(monde):
    m = monde
    _ouvrir(m)
    e = _refus(_apps, m, m["gerant"], op="set_role", principal_type="user",
               principal_id=m["partenaire"], role="reader")
    assert e.code == "forbidden"


def test_la_portee_ne_sert_que_son_lecteur(monde):
    from oto_mcp.datastore import par_l_app
    m = monde
    p = par_l_app.Portee(m["partenaire"], lambda ns: "write")
    with par_l_app.portee(p):
        assert par_l_app.droit(m["partenaire"], "1") == "write"
        assert par_l_app.droit(m["auteur"], "1") is None
        assert par_l_app.droit(None, "1") is None
