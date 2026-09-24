"""Une rafale d'enregistrements du dashboard ne fait qu'UNE version (oto#274).

Le dashboard enregistre une page après 4 s d'inactivité, avec `expected_rev`. Chaque
enregistrement passait par `update_doc`, qui archive l'état antérieur : l'historique
d'une page prenait une version par pause de frappe.

La règle : une écriture de `update`/`patch` par la face REST ne crée pas d'instantané si
le dernier de la page vient du MÊME compte, par la face REST, et a moins de 5 minutes
— fenêtre FIXE, comptée depuis l'instantané qui a ouvert la rafale. L'état d'avant la
rafale reste la version restaurable. Tout le reste crée une version : l'agent (MCP),
un autre compte, une fenêtre dépassée, une restauration.

Volet 2 : la page sert `updated_by`, et les « modifications récentes » le lisent — leur
ancien appariement à la révision de même horodatage n'aurait plus trouvé d'auteur
après une écriture regroupée.

Banc LIVE : ce qui est neuf est du SQL sous verrou (la lecture du dernier instantané,
la fenêtre en `interval`) et une révision Alembic. Un double ne prouverait rien.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from oto_mcp import db, ownership, session_org
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.docs import core as D

RACINE = Path(__file__).resolve().parent.parent
A, B = "user-a-274", "user-b-274"


@pytest.fixture
def page(live, monkeypatch):
    monkeypatch.setattr(ownership, "can_access", lambda sub, t, rid, want="read": True)
    pid = db.create_project("user", A, "Projet 274", created_by=A)
    did = db.create_doc(pid, "Page", body_md="v0", created_by=A)
    return {"pid": pid, "did": did}


def _ecrire(sub: str, did: int, corps: str, *, mcp: bool = False,
            expected_rev: str | None = None) -> dict:
    jeton = session_org.set_call_face(session_org.FACE_MCP) if mcp else None
    try:
        return D._doc(ResolvedCtx(sub=sub, org_id=None),
                      D.DocInput(op="update", doc_id=did, body_md=corps,
                                 expected_rev=expected_rev))
    finally:
        if jeton is not None:
            session_org.reset_call_face(jeton)


def _versions(did: int) -> list[dict]:
    return db.list_doc_revisions(did)


def _vieillir(did: int, minutes: int) -> None:
    """Recule le dernier instantané de la page, comme si `minutes` étaient passées."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("UPDATE doc_revisions SET created_at = created_at - %s::interval "
                     "WHERE doc_id = %s", (f"{minutes} minutes", did))


# ── 1. La rafale ─────────────────────────────────────────────────────────────

def test_une_rafale_du_dashboard_ne_fait_qu_une_version(page):
    for corps in ("v1", "v2", "v3", "v4"):
        _ecrire(A, page["did"], corps)
    versions = _versions(page["did"])
    # UNE version : l'état d'avant la rafale, restaurable. La page porte la dernière frappe.
    assert [v["body_md"] for v in versions] == ["v0"]
    assert versions[0]["edited_by"] == A
    assert db.get_doc_by_id(page["did"])["body_md"] == "v4"


def test_l_agent_puis_le_dashboard_font_deux_versions(page):
    _ecrire(A, page["did"], "v1-agent", mcp=True)
    _ecrire(A, page["did"], "v2-ecran")
    _ecrire(A, page["did"], "v3-ecran")
    # La version de l'agent reste dans l'historique : la rafale de l'écran s'ouvre après.
    assert [v["body_md"] for v in _versions(page["did"])] == ["v1-agent", "v0"]


def test_l_agent_ne_regroupe_jamais(page):
    for corps in ("v1", "v2", "v3"):
        _ecrire(A, page["did"], corps, mcp=True)
    assert [v["body_md"] for v in _versions(page["did"])] == ["v2", "v1", "v0"]


def test_un_autre_compte_cree_une_nouvelle_version(page):
    _ecrire(A, page["did"], "v1-a")
    _ecrire(B, page["did"], "v2-b")
    _ecrire(B, page["did"], "v3-b")
    versions = _versions(page["did"])
    assert [v["body_md"] for v in versions] == ["v1-a", "v0"]
    assert [v["edited_by"] for v in versions] == [B, A]


def test_la_fenetre_depassee_cree_une_nouvelle_version(page):
    _ecrire(A, page["did"], "v1")
    _vieillir(page["did"], 6)
    _ecrire(A, page["did"], "v2")
    assert [v["body_md"] for v in _versions(page["did"])] == ["v1", "v0"]


def test_la_fenetre_ne_glisse_pas(page):
    """Comptée depuis l'instantané qui ouvre la rafale, pas depuis la dernière frappe :
    une session continue fait une version toutes les 5 minutes, pas une pour l'heure."""
    _ecrire(A, page["did"], "v1")
    _vieillir(page["did"], 4)
    _ecrire(A, page["did"], "v2")      # 4 min : dans la fenêtre
    _vieillir(page["did"], 2)
    _ecrire(A, page["did"], "v3")      # 6 min après l'ouverture, 2 après v2
    assert [v["body_md"] for v in _versions(page["did"])] == ["v2", "v0"]


# ── 2. Concurrence ───────────────────────────────────────────────────────────

def test_le_rev_rendu_par_une_ecriture_regroupee_reste_juste(page):
    rev = _ecrire(A, page["did"], "v1")["rev"]
    rev = _ecrire(A, page["did"], "v2", expected_rev=rev)["rev"]
    rev = _ecrire(A, page["did"], "v3", expected_rev=rev)["rev"]
    assert rev == db.doc_rev("Page", "v3")
    assert [v["body_md"] for v in _versions(page["did"])] == ["v0"]


def test_un_rev_perime_est_refuse_meme_dans_la_rafale(page):
    perime = db.doc_rev("Page", "v0")
    _ecrire(A, page["did"], "v1")
    with pytest.raises(AuthzDenied) as e:
        _ecrire(A, page["did"], "v2", expected_rev=perime)
    assert (e.value.status, e.value.code) == (409, "conflict")
    assert db.get_doc_by_id(page["did"])["body_md"] == "v1"


# ── 3. Restauration ──────────────────────────────────────────────────────────

def test_une_restauration_cree_toujours_sa_version(page):
    _ecrire(A, page["did"], "v1")
    _ecrire(A, page["did"], "v2")
    v0 = _versions(page["did"])[0]
    assert v0["body_md"] == "v0"
    D._doc(ResolvedCtx(sub=A, org_id=None),
           D.DocInput(op="revert", doc_id=page["did"], revision_id=v0["id"]))
    # Au milieu de la rafale, même compte, face REST : l'état remplacé (v2) est gardé.
    assert [v["body_md"] for v in _versions(page["did"])] == ["v2", "v0"]
    assert db.get_doc_by_id(page["did"])["body_md"] == "v0"
    # L'id restauré n'a pas bougé : le regroupement n'efface ni ne réécrit rien.
    assert db.get_doc_revision(page["did"], v0["id"])["body_md"] == "v0"


# ── 4. L'auteur de la dernière modification ──────────────────────────────────

def test_la_page_sert_updated_by_sur_get_et_list(page):
    ctx = ResolvedCtx(sub=A, org_id=None)
    assert D._doc(ctx, D.DocInput(op="get", doc_id=page["did"]))["updated_by"] == A
    _ecrire(B, page["did"], "v1")
    assert D._doc(ctx, D.DocInput(op="get", doc_id=page["did"]))["updated_by"] == B
    liste = D._doc(ctx, D.DocInput(op="list", project_id=page["pid"]))
    lignes = liste.get("docs") or liste.get("items") or liste
    assert [d["updated_by"] for d in lignes if d["id"] == page["did"]] == [B]


def test_un_deplacement_efface_l_auteur_au_lieu_de_le_reconduire(page):
    _ecrire(B, page["did"], "v1")
    autre = db.create_doc(page["pid"], "Autre", created_by=A)
    db.move_doc(page["did"], autre)
    assert db.get_doc_by_id(page["did"])["updated_by"] is None


def test_les_modifications_recentes_gardent_l_auteur_apres_une_rafale(page):
    from oto_mcp.db import recent_changes as rc
    for corps in ("v1", "v2", "v3"):
        _ecrire(B, page["did"], corps)
    lignes = rc.recent_changes([page["pid"]], [], limit=5, base_slug="claude_md")
    assert [(l["id"], l["author_sub"]) for l in lignes] == [(page["did"], B)]


# ── 5. La révision Alembic ───────────────────────────────────────────────────

def _colonnes(dsn: str) -> set[tuple[str, str]]:
    import psycopg
    with psycopg.connect(dsn) as c:
        return {(r[0], r[1]) for r in c.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE (table_name, column_name) IN (('docs', 'updated_by'), "
            "('doc_revisions', 'face'))").fetchall()}


def test_la_revision_monte_remplit_et_descend(page, pg_module_dsn):
    import psycopg
    from alembic import command
    from alembic.config import Config

    from oto_mcp.db import init_db
    toutes = {("docs", "updated_by"), ("doc_revisions", "face")}
    assert _colonnes(pg_module_dsn) == toutes, "une base NEUVE les reçoit du CREATE TABLE"
    # L'état d'avant, écrit par l'ancien régime : une modification avec sa révision
    # appariée, une page jamais modifiée, une page déplacée.
    modifiee = page["did"]
    db.update_doc(modifiee, body_md="v1", edited_by=B)
    neuve = db.create_doc(page["pid"], "Neuve", created_by=A)
    deplacee = db.create_doc(page["pid"], "Déplacée", created_by=A)
    db.move_doc(deplacee, neuve)
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("ALTER TABLE docs DROP COLUMN updated_by")
        c.execute("ALTER TABLE doc_revisions DROP COLUMN face")
    init_db()
    assert _colonnes(pg_module_dsn) == set(), "le démarrage ne doit pas les poser"
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    command.stamp(cfg, "0012_partages_echeance")
    command.upgrade(cfg, "0013_pages_versions_regroupees")
    assert _colonnes(pg_module_dsn) == toutes, "la révision n'a rien écrit"
    auteurs = {d: db.get_doc_by_id(d)["updated_by"] for d in (modifiee, neuve, deplacee)}
    assert auteurs == {modifiee: B, neuve: A, deplacee: None}
    command.downgrade(cfg, "0012_partages_echeance")
    assert _colonnes(pg_module_dsn) == set(), "le retour arrière n'a rien retiré"
    command.upgrade(cfg, "head")
    assert _colonnes(pg_module_dsn) == toutes
