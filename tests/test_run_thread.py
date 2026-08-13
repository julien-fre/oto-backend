"""Le fil d'un run — ce que la capacité PROMET et qu'une réécriture casserait.

Quatre invariants, dans l'ordre du danger :
1. le fil hérite des droits de son run, et un refus est INDISTINGUABLE d'un run
   inexistant (pas d'oracle d'existence) ;
2. le segment provider ne sort JAMAIS vers un non-propriétaire — il porte les
   blocs de thinking du modèle, pas une donnée d'équipe ;
3. un tour hors plafond est refusé À L'ÉCRITURE, avant tout accès base ;
4. l'org_admin lit, il n'écrit pas — un fil à deux plumes n'est l'état
   d'exécution de personne.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import run_thread as RT
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx


def _ctx(sub="worker-1", org_id=226):
    return ResolvedCtx(sub=sub, org_id=org_id)


@pytest.fixture
def base(monkeypatch):
    """Un run possédé par worker-1 dans l'org 226, et un espion sur les écritures."""
    etat = {"head": {"sub": "worker-1", "org_id": 226}, "appended": [],
            "read_kwargs": None, "admin_de": set()}
    monkeypatch.setattr(RT.db, "get_run_head",
                        lambda run_id: etat["head"] if run_id == "run-A" else None)
    monkeypatch.setattr(RT.db, "append_run_message",
                        lambda run_id, role, content, raw=None:
                        (etat["appended"].append((run_id, role)), {"seq": 1})[1])
    def _get(run_id, after_seq=0, limit=200, include_raw=False):
        etat["read_kwargs"] = {"include_raw": include_raw}
        return [{"seq": 1, "role": "assistant", "content": {"text": "t"}}]
    monkeypatch.setattr(RT.db, "get_run_messages", _get)
    monkeypatch.setattr(RT.roles, "is_org_admin",
                        lambda sub, org: (sub, org) in etat["admin_de"])
    return etat


def _appel(sub, **kw):
    return RT._thread(_ctx(sub=sub), RT.ThreadInput(**kw))


# ── 1. l'héritage des droits, sans oracle ──────────────────────────────────────

def test_le_proprietaire_ecrit_et_lit(base):
    out = _appel("worker-1", op="append", run_id="run-A",
                 role="assistant", content={"text": "bonjour"})
    assert out["seq"] == 1 and base["appended"] == [("run-A", "assistant")]
    out = _appel("worker-1", op="read", run_id="run-A")
    assert out["messages"][0]["seq"] == 1


def test_un_tiers_recoit_le_meme_404_quun_run_inexistant(base):
    with pytest.raises(AuthzDenied) as a:
        _appel("intrus", op="read", run_id="run-A")
    with pytest.raises(AuthzDenied) as b:
        _appel("worker-1", op="read", run_id="run-INEXISTANT")
    assert (a.value.status, a.value.code) == (b.value.status, b.value.code) == \
        (404, "run_not_found"), "un refus distinguable est un oracle d'existence"


def test_lorg_admin_lit_la_projection_neutre(base):
    base["admin_de"].add(("chef", 226))
    out = _appel("chef", op="read", run_id="run-A")
    assert out["messages"], "l'org_admin de l'org du run lit le fil"
    assert base["read_kwargs"] == {"include_raw": False}


# ── 2. le brut reste au propriétaire ───────────────────────────────────────────

def test_le_segment_provider_est_refuse_a_ladmin(base):
    base["admin_de"].add(("chef", 226))
    with pytest.raises(AuthzDenied) as e:
        _appel("chef", op="read", run_id="run-A", include_raw=True)
    assert e.value.status == 403 and e.value.code == "raw_is_owner_only"


def test_le_proprietaire_obtient_le_brut(base):
    _appel("worker-1", op="read", run_id="run-A", include_raw=True)
    assert base["read_kwargs"] == {"include_raw": True}


# ── 3. le plafond, à l'écriture, avant la base ─────────────────────────────────

def test_un_tour_hors_plafond_est_refuse_avant_tout_acces_base(base, monkeypatch):
    def _jamais(*a, **k):
        raise AssertionError("aucune écriture ne doit partir hors plafond")
    monkeypatch.setattr(RT.db, "append_run_message", _jamais)
    with pytest.raises(AuthzDenied) as e:
        _appel("worker-1", op="append", run_id="run-A", role="tool",
               content={"text": "x" * (RT._MAX_MESSAGE_CHARS + 10)})
    assert e.value.code == "message_too_large"
    assert str(RT._MAX_MESSAGE_CHARS) in str(e.value.message), \
        "le refus nomme le plafond — sinon l'appelant tâtonne"


def test_le_plafond_compte_le_brut_avec_la_projection(base, monkeypatch):
    monkeypatch.setattr(RT.db, "append_run_message",
                        lambda *a, **k: pytest.fail("hors plafond, rien ne part"))
    moitie = "x" * (RT._MAX_MESSAGE_CHARS // 2)
    with pytest.raises(AuthzDenied):
        _appel("worker-1", op="append", run_id="run-A", role="assistant",
               content={"text": moitie}, provider_raw={"text": moitie, "pad": "yyyy"})


# ── 4. l'org_admin n'écrit pas ─────────────────────────────────────────────────

def test_ladmin_ne_peut_pas_apposer(base):
    base["admin_de"].add(("chef", 226))
    with pytest.raises(AuthzDenied) as e:
        _appel("chef", op="append", run_id="run-A",
               role="user", content={"text": "je m'incruste"})
    assert e.value.status == 404, \
        "écrire dans le fil d'autrui est refusé comme s'il n'existait pas"


def test_append_sans_role_ou_contenu_est_un_400_clair(base):
    with pytest.raises(AuthzDenied) as e:
        _appel("worker-1", op="append", run_id="run-A")
    assert e.value.status == 400 and e.value.code == "missing_fields"
