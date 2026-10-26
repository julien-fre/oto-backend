"""`me.app.call` — une app ouverte n'agit que si son AUTEUR et son LECTEUR le peuvent.

On monkeypatche l'app, l'ownership, l'accès aux agents et les capacités exécutées pour
le lecteur : ce qu'on vérifie ici, c'est le jugement (qui, quoi, borné à quoi), pas
le stockage. Assertions sur le CODE des refus.
"""
import pytest

from oto_mcp import db, ownership
from oto_mcp.capabilities import _acces_agent
from oto_mcp.capabilities import apps_appel as C
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.datastore import rows as R

LECTEUR = "lecteur"
AUTEUR = "auteur"
CTX = ResolvedCtx(sub=LECTEUR, org_id=42)
MANIFEST = {
    "tables": {"leads": {"table": "12", "fields": ["name", "status"],
                         "set": {"status": ["ok", "no"]}, "add": ["name"]},
               "all": {"table": "13", "fields": None, "set": {}, "add": []}},
    "agents": {"enrich": {"id": 9, "run": True, "input": True},
               "watch": {"id": 10, "run": False, "input": False}},
}
APP = {"id": 5, "project_id": 7, "title": "Leads", "manifest": MANIFEST, "html": "",
       "thumbnail": None, "updated_by": AUTEUR, "archived_at": None}


@pytest.fixture
def seams(monkeypatch):
    rec = {"list": [], "agg": [], "update": [], "append": [], "run": []}
    # Ce que chacun peut : par défaut, tout ; un test retire ce qu'il éprouve.
    droits = {(LECTEUR, "project"): True, (AUTEUR, "12", "read"): True,
              (AUTEUR, "12", "write"): True, (AUTEUR, "13", "read"): True,
              (AUTEUR, "13", "write"): True}
    niveaux = {LECTEUR: "editor", AUTEUR: "owner"}

    def can_access(sub, rtype, rid, want="read"):
        if rtype == "project":
            return droits.get((sub, "project"), False)
        return droits.get((sub, rid, want), False)

    monkeypatch.setattr(ownership, "can_access", can_access)
    monkeypatch.setattr(db.apps, "get", lambda i: dict(APP) if i == 5 else None)
    monkeypatch.setattr(db, "get_trigger", lambda tid, org: {"id": tid, "sub": AUTEUR} if tid in (9, 10) else None)
    monkeypatch.setattr(_acces_agent, "niveau", lambda sub, org, t: niveaux.get(sub))
    monkeypatch.setattr(R, "_list_rows", lambda ctx, inp: rec["list"].append((ctx.sub, inp)) or {
        "rows": [{"_id": "r1", "_revision": "1", "name": "A", "status": "ok", "email": "x@y"}], "total": 1})
    monkeypatch.setattr(R, "_aggregate", lambda ctx, inp: rec["agg"].append(inp) or {"groups": [{"n": 1}]})
    monkeypatch.setattr(R, "_update_row", lambda ctx, inp: rec["update"].append(inp) or {
        "_id": inp.row_id, "_revision": "2", "status": inp.patch["status"], "email": "x@y"})
    monkeypatch.setattr(R, "_append_row", lambda ctx, inp: rec["append"].append(inp) or {"_id": "r9", **inp.row})
    import oto_mcp.capabilities.runner_triggers as T
    monkeypatch.setattr(T, "_triggers", lambda ctx, inp: rec["run"].append((ctx.sub, inp)) or {"job": {"id": 77}})
    monkeypatch.setattr(db, "list_jobs", lambda org, trigger_id=None, limit=50, **k: [
        {"id": 77, "status": "done", "run_id": "run-1", "created_at": "t0", "finished_at": "t1", "last_error": None}])
    monkeypatch.setattr(db.apps, "run_results", lambda ids: {"run-1": {"outcome": "done", "note": "Enriched 3 leads."}})
    monkeypatch.setattr(db.apps, "job_of_org", lambda org, jid: {
        "id": jid, "status": "done", "run_id": "run-1", "created_at": "t0", "finished_at": "t1",
        "last_error": None, "trigger_id": "9" if jid == 77 else "999"})
    rec["droits"], rec["niveaux"] = droits, niveaux
    return rec


def call(**kw):
    return C._call(CTX, C.AppCallInput(app_id=5, **kw))


def refus(**kw) -> AuthzDenied:
    with pytest.raises(AuthzDenied) as e:
        call(**kw)
    return e.value


# ── Qui ─────────────────────────────────────────────────────────────────────

def test_le_lecteur_lit_comme_lui_et_seulement_les_champs_declares(seams):
    out = call(action="read_rows", table="leads")
    (sub, inp), = seams["list"]
    assert sub == LECTEUR and inp.datastore == "12"
    assert out["rows"] == [{"_id": "r1", "_revision": "1", "name": "A", "status": "ok"}]


def test_app_illisible_pour_le_lecteur_meme_404_qu_inconnue(seams):
    seams["droits"][(LECTEUR, "project")] = False
    assert refus(action="read_rows", table="leads").code == "unknown_app"
    with pytest.raises(AuthzDenied) as e:
        C._call(CTX, C.AppCallInput(app_id=404, action="read_rows", table="leads"))
    assert e.value.code == "unknown_app"


def test_l_auteur_qui_perd_le_tableau_coupe_l_app(seams):
    seams["droits"][(AUTEUR, "12", "read")] = False
    e = refus(action="read_rows", table="leads")
    assert (e.status, e.code) == (403, "app_author_lost_access")
    assert seams["list"] == []


def test_ecrire_exige_que_l_auteur_puisse_ecrire(seams):
    seams["droits"][(AUTEUR, "12", "write")] = False
    e = refus(action="write_cell", table="leads", row_id="r1", field="status", value="ok",
              expected_revision="1")
    assert e.code == "app_author_lost_access" and seams["update"] == []


def test_auteur_inconnu_refuse(seams, monkeypatch):
    monkeypatch.setattr(db.apps, "get", lambda i: dict(APP, updated_by=None))
    assert refus(action="read_rows", table="leads").code == "app_author_unknown"


# ── Quoi : borné au manifeste ───────────────────────────────────────────────

def test_tableau_non_declare(seams):
    assert refus(action="read_rows", table="secret").code == "undeclared_table"


@pytest.mark.parametrize("options", [{"filter": {"email": "x"}}, {"order_by": "email"}, {"q": "x"}])
def test_lecture_ne_touche_pas_un_champ_non_declare(seams, options):
    assert refus(action="read_rows", table="leads", options=options).status in (400, 403)
    assert seams["list"] == []


def test_filtre_sur_les_dates_permis(seams):
    call(action="read_rows", table="leads", options={"filter": {"_created_at": {"gte": "2026-10-01"}}})
    assert seams["list"]


def test_recherche_permise_sans_liste_de_champs(seams):
    call(action="read_rows", table="all", options={"q": "acme"})
    assert seams["list"][0][1].q == "acme"


def test_agregat_borne(seams):
    call(action="read_aggregate", table="leads", options={"group_by": "status"})
    assert seams["agg"][0].group_by == "status"
    assert refus(action="read_aggregate", table="leads", options={"group_by": "email"}).code == "undeclared_field"
    assert refus(action="read_aggregate", table="leads",
                 options={"metrics": [{"op": "sum", "field": "email"}]}).code == "undeclared_field"


def test_ecriture_valeur_declaree_et_revision_exigee(seams):
    out = call(action="write_cell", table="leads", row_id="r1", field="status", value="no",
               expected_revision="1")
    assert seams["update"][0].expected_revision == "1" and "email" not in out["row"]
    assert refus(action="write_cell", table="leads", row_id="r1", field="status", value="maybe",
                 expected_revision="1").code == "undeclared_value"
    assert refus(action="write_cell", table="leads", row_id="r1", field="name", value="ok",
                 expected_revision="1").code == "undeclared_write"
    assert refus(action="write_cell", table="leads", row_id="r1", field="status",
                 value="ok").code == "revision_required"


def test_ajout_colonnes_declarees(seams):
    call(action="add_row", table="leads", values={"name": "B"})
    assert seams["append"][0].row == {"name": "B"}
    assert refus(action="add_row", table="leads", values={"status": "ok"}).code == "undeclared_write"
    assert refus(action="add_row", table="all", values={"name": "B"}).code == "undeclared_write"


# ── Agents et leurs résultats ───────────────────────────────────────────────

def test_lancer_comme_le_lecteur_avec_l_entree_marquee(seams):
    out = call(action="start_agent", agent="enrich", input="acme.com")
    (sub, inp), = seams["run"]
    assert out["job_id"] == 77 and sub == LECTEUR
    assert inp.op == "run_now" and inp.trigger_id == 9 and inp.handoff.endswith("acme.com")


def test_lancer_exige_run_et_entree_declares(seams):
    assert refus(action="start_agent", agent="watch").code == "agent_not_runnable"
    assert refus(action="start_agent", agent="enrich", input="x" * 301).code == "agent_input_too_long"


def test_lancer_exige_que_l_auteur_puisse_le_lancer(seams):
    seams["niveaux"][AUTEUR] = "viewer"
    assert refus(action="start_agent", agent="enrich").code == "app_author_lost_access"
    assert seams["run"] == []


def test_runs_et_ce_que_l_agent_a_conclu(seams):
    out = call(action="agent_runs", agent="watch")
    (run,) = out["runs"]
    assert run["status"] == "done" and run["note"] == "Enriched 3 leads." and run["outcome"] == "done"


def test_resultat_d_un_run_d_un_agent_declare_seulement(seams):
    assert call(action="run_result", job_id=77)["run"]["note"] == "Enriched 3 leads."
    assert refus(action="run_result", job_id=78).code == "unknown_run"


def test_agent_invisible_au_lecteur(seams):
    seams["niveaux"].pop(LECTEUR)
    assert refus(action="agent_runs", agent="watch").code == "unknown_agent"
