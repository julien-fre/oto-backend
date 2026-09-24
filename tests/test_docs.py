"""Capacité `oto_doc` — pages markdown arborescentes d'un projet (incrément 3).

Les Docs héritent de l'accès du PROJET (ownership.can_access sur le projet) ; on
monkeypatche db + ownership.
"""
import pytest

from oto_mcp import db, ownership
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.docs import core as D

CTX = ResolvedCtx(sub="u1", org_id=None)
DOC = {"id": 3, "project_id": 7, "parent_id": None, "title": "Page", "body_md": "x",
       "kind": "doc", "created_at": "2026-06-30", "updated_at": "2026-06-30"}


@pytest.fixture
def seams(monkeypatch):
    rec = {"create": [], "update": [], "delete": [], "move": []}
    monkeypatch.setattr(ownership, "can_access", lambda sub, t, rid, want="read": True)
    monkeypatch.setattr(db, "get_doc_by_id", lambda i: dict(DOC, id=i) if i in (3, 9) else None)
    monkeypatch.setattr(db, "create_doc",
                        lambda pid, title, parent_id=None, body_md="", kind="doc", created_by=None, description=None, trace=None:
                        rec["create"].append((pid, title, parent_id, kind, created_by)) or 3)
    monkeypatch.setattr(db, "list_docs_for_project", lambda pid: [DOC])
    monkeypatch.setattr(db, "update_doc",
                        lambda did, title=None, body_md=None, kind=None, edited_by=None, description=None, expected_rev=None, face=None, regroupable=False, trace=None: rec["update"].append((did, title, body_md, kind, edited_by, expected_rev)))
    monkeypatch.setattr(db, "list_doc_revisions",
                        lambda did, limit=50: [{"id": 1, "title": "v0", "body_md": "old", "edited_by": "u1", "created_at": "2026-06-30"}])
    # `delete_doc` rend le nombre de DESCENDANTS emportés (#657) : le double le rend
    # aussi, sinon il ment sur la signature qu'il remplace et l'accusé porterait `None`.
    monkeypatch.setattr(db, "delete_doc",
                        lambda did: rec["delete"].append(did) or 0)
    monkeypatch.setattr(db, "move_doc", lambda did, p, position=None: rec["move"].append((did, p)))
    monkeypatch.setattr(db, "log_project_activity", lambda *a, **k: None)
    rec["set_public"] = []
    monkeypatch.setattr(db, "set_doc_public",
                        lambda did, public: rec["set_public"].append((did, public)) or ("tok123" if public else None))
    return rec


def test_set_public_on(seams):
    out = D._doc(CTX, D.DocInput(op="set_public", doc_id=3, public=True))
    assert seams["set_public"] == [(3, True)]
    assert out["public"] is True and out["public_url"].endswith("/p/d/tok123")


def test_set_public_off(seams):
    out = D._doc(CTX, D.DocInput(op="set_public", doc_id=3, public=False))
    assert seams["set_public"] == [(3, False)]
    assert out["public"] is False and out["public_url"] is None


def test_create(seams):
    out = D._doc(CTX, D.DocInput(op="create", project_id=7, title=" Page "))
    assert seams["create"] == [(7, "Page", None, "doc", "u1")]
    assert out["id"] == 3


def test_create_forbidden(seams, monkeypatch):
    monkeypatch.setattr(ownership, "can_access", lambda sub, t, rid, want="read": False)
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="create", project_id=7, title="X"))
    assert e.value.code == "forbidden"


def test_un_lecteur_qui_cree_prend_403_et_rien_n_est_ecrit(seams, monkeypatch):
    """oto#191 : « les lecteurs proposent » est retiré. Un compte qui LIT le projet sans
    pouvoir y écrire n'obtient plus une proposition à la place de la page : il est
    refusé, et rien n'est écrit."""
    monkeypatch.setattr(ownership, "can_access",
                        lambda sub, t, rid, want="read": want == "read")
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="create", project_id=7, title="Idée"))
    assert (e.value.status, e.value.code) == (403, "forbidden")
    assert seams["create"] == []


def test_create_missing_title(seams):
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="create", project_id=7, title="  "))
    assert e.value.code == "missing_title"


def test_list(seams):
    out = D._doc(CTX, D.DocInput(op="list", project_id=7))
    assert out["project_id"] == 7 and [d["id"] for d in out["docs"]] == [3]


def test_get_unknown(seams):
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="get", doc_id=999))
    assert e.value.code == "unknown_doc"


def test_update(seams):
    D._doc(CTX, D.DocInput(op="update", doc_id=3, body_md="new"))
    assert seams["update"] == [(3, None, "new", None, "u1", None)]   # edited_by = ctx.sub


def test_patch_replaces_only_target_section(seams, monkeypatch):
    body = "# T\n\n## A\n\nvieux A.\n\n## B\n\ngarder B.\n"
    monkeypatch.setattr(db, "get_doc_by_id", lambda i: dict(DOC, id=i, body_md=body))
    D._doc(CTX, D.DocInput(op="patch", doc_id=3, section="A", body_md="NEUF A."))
    # update_doc reçoit le corps COMPLET patché : A remplacé, B intact
    new_body = seams["update"][0][2]
    assert "NEUF A." in new_body and "vieux A." not in new_body and "garder B." in new_body


def test_patch_unknown_section_is_404(seams, monkeypatch):
    body = "# T\n\n## A\n\nx\n"
    monkeypatch.setattr(db, "get_doc_by_id", lambda i: dict(DOC, id=i, body_md=body))
    with pytest.raises(AuthzDenied) as ei:
        D._doc(CTX, D.DocInput(op="patch", doc_id=3, section="Zzz", body_md="x"))
    assert ei.value.status == 404 and ei.value.code == "unknown_section"


def test_patch_passes_expected_rev_for_conflict(seams, monkeypatch):
    body = "# T\n\n## A\n\nx\n"
    monkeypatch.setattr(db, "get_doc_by_id", lambda i: dict(DOC, id=i, body_md=body))
    D._doc(CTX, D.DocInput(op="patch", doc_id=3, section="A", body_md="y", expected_rev="r1"))
    assert seams["update"][0][5] == "r1"   # le conflit optimiste s'applique aussi au patch


def test_update_passes_expected_rev(seams):
    D._doc(CTX, D.DocInput(op="update", doc_id=3, body_md="new", expected_rev="abc123"))
    assert seams["update"][0][5] == "abc123"   # le token de conflit optimiste est transmis


def test_update_conflict_is_409(seams, monkeypatch):
    # Le doc a changé depuis la lecture → DocConflict → erreur actionnable 409, pas d'écrasement.
    def _boom(*a, **k):
        raise db.DocConflict("newrev99")
    monkeypatch.setattr(db, "update_doc", _boom)
    with pytest.raises(AuthzDenied) as ei:
        D._doc(CTX, D.DocInput(op="update", doc_id=3, body_md="x", expected_rev="stale"))
    assert ei.value.status == 409 and ei.value.code == "conflict"


def test_revisions(seams):
    out = D._doc(CTX, D.DocInput(op="revisions", doc_id=3))
    assert out["doc_id"] == 3 and out["revisions"][0]["title"] == "v0"


def test_bulk_create_builds_tree_via_parent_index(seams, monkeypatch):
    # A4 (#6) : N pages en un appel ; parent_index référence une page plus tôt dans le lot.
    made = []
    def _create(pid, title, parent_id=None, body_md="", kind="doc", created_by=None, description=None):
        made.append((title, parent_id))
        return 100 + len(made)              # ids séquentiels 101, 102, 103…
    monkeypatch.setattr(db, "create_doc", _create)
    out = D._doc(CTX, D.DocInput(op="bulk_create", project_id=7, pages=[
        {"title": "Racine"},
        {"title": "Enfant", "parent_index": 0},     # sous « Racine » (id 101)
        {"title": "Autre racine"},
    ]))
    assert out["count"] == 3 and out["created"] == [101, 102, 103]
    assert made == [("Racine", None), ("Enfant", 101), ("Autre racine", None)]


def test_bulk_create_requires_titles(seams):
    with pytest.raises(AuthzDenied) as ei:
        D._doc(CTX, D.DocInput(op="bulk_create", project_id=7, pages=[{"body_md": "x"}]))
    assert ei.value.code == "missing_title"


def test_move_to_another_project(seams, monkeypatch):
    # A4 (#6) : op=move avec to_project déplace la page + sous-arbre vers le projet cible
    # (écriture requise sur source ET cible ; cible doit exister).
    rec = {}
    monkeypatch.setattr(db, "get_project_by_id", lambda i: {"id": i} if i in (3, 8) else None)
    monkeypatch.setattr(db, "move_doc_to_project",
                        lambda did, tgt, parent=None, position=None: rec.update(
                            did=did, tgt=tgt, parent=parent) or 3)
    out = D._doc(CTX, D.DocInput(op="move", doc_id=3, to_project=8))
    assert rec == {"did": 3, "tgt": 8, "parent": None}
    assert out["moved_count"] == 3
    assert seams["move"] == []          # PAS le move intra-projet


def test_move_to_unknown_project_404(seams, monkeypatch):
    monkeypatch.setattr(db, "get_project_by_id", lambda i: dict(DOC, id=i) if i == 3 else None)
    with pytest.raises(AuthzDenied) as ei:
        D._doc(CTX, D.DocInput(op="move", doc_id=3, to_project=999))
    assert ei.value.status == 404 and ei.value.code == "unknown_project"


def test_delete(seams):
    out = D._doc(CTX, D.DocInput(op="delete", doc_id=3))
    assert seams["delete"] == [3] and out["deleted"] is True
    # #657 : l'accusé DIT ce que la cascade a emporté. Une feuille rend 0, pas rien —
    # un champ absent se lit « la question n'a pas de réponse ».
    assert out["descendants"] == 0 and "warning" not in out


def test_move_self_parent_rejected(seams):
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="move", doc_id=3, parent_id=3))
    assert e.value.code == "bad_parent"


def test_move_top_level(seams):
    D._doc(CTX, D.DocInput(op="move", doc_id=3, parent_id=None))
    assert seams["move"] == [(3, None)]


def test_capability_registered():
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next((c for c in CAPABILITIES if c.key == "me.doc"), None)
    assert cap is not None and cap.mcp == "oto_doc" and cap.rest.path == "/api/me/docs"
