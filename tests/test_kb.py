"""Capacité `oto_kb` — base de connaissance d'org, ANCRÉE PAR ID (lot 3, chantier 0.3).

`orgs.kb_project_id` = la source de vérité (plus le nom) : renommer ne casse rien,
deux appels concurrents ne créent plus deux KB (claim optimiste, le perdant archive
son doublon), une ancre pendouillante (transfert/archive) s'auto-répare.
Logique pure — seams org_store/db stubés.
"""
import pytest

from oto_mcp.capabilities import kb as K
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx


def _proj(pid, org="7", *, archived=None, owner_type="org"):
    return {"id": pid, "name": K.KB_NAME, "brief_md": K.KB_BRIEF,
            "owner_type": owner_type, "owner_id": org, "archived_at": archived}


@pytest.fixture
def seams(monkeypatch):
    rec = {"created": [], "archived": [], "cleared": [], "anchor": None,
           "claim_ok": True, "projects": {}}

    monkeypatch.setattr(K.org_store, "get_kb_project_id",
                        lambda org: rec["anchor"])
    def _claim(org, pid):
        if rec["claim_ok"] and rec["anchor"] is None:
            rec["anchor"] = pid
            return True
        return False
    monkeypatch.setattr(K.org_store, "claim_kb_project", _claim)
    monkeypatch.setattr(K.org_store, "clear_kb_project",
                        lambda org, pid: rec["cleared"].append(pid) or
                        rec.update(anchor=None) if rec["anchor"] == pid else None)
    def _create(ot, oid, name, brief, created_by=None):
        pid = 42 + len(rec["created"])
        rec["created"].append((ot, oid, name))
        rec["projects"][pid] = _proj(pid, oid)
        return pid
    monkeypatch.setattr(K.db, "create_project", _create)
    monkeypatch.setattr(K.db, "get_project_by_id",
                        lambda pid: rec["projects"].get(pid))
    monkeypatch.setattr(K.db, "archive_project",
                        lambda pid: rec["archived"].append(pid))
    monkeypatch.setattr(K.db, "log_project_activity", lambda *a, **k: None)
    return rec


def test_anchored_kb_returned_without_creation(seams):
    seams["anchor"] = 9
    seams["projects"][9] = _proj(9)
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="get"))
    assert out["project_id"] == 9 and seams["created"] == []


def test_renamed_kb_still_resolves(seams):
    # Le nom n'est plus un marqueur : une KB renommée reste LA KB (l'ancre tient).
    seams["anchor"] = 9
    seams["projects"][9] = {**_proj(9), "name": "Wiki interne"}
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="get"))
    assert out["project_id"] == 9 and out["name"] == "Wiki interne"
    assert seams["created"] == []


def test_get_on_an_org_without_kb_creates_nothing(seams):
    """LE test de ce lot : une lecture ne pose pas de projet.

    `op="get"` était créant, et ce endpoint est monté à la racine des fronts —
    ouvrir l'app suffisait donc à planter une « Base de connaissance » vide dans
    l'org de chaque client (remonté par un client, 19/08)."""
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="get"))
    assert out["project_id"] is None
    assert seams["created"] == [] and seams["anchor"] is None
    K.KbView(**out)          # project_id nullable : la sortie déclarée tient


def test_get_does_not_repair_a_dangling_anchor(seams):
    # Réparer, c'est écrire (clear + create + claim) : réservé à `ensure`.
    seams["anchor"] = 9
    seams["projects"][9] = _proj(9, archived="2026-07-01")
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="get"))
    assert out["project_id"] is None
    assert seams["cleared"] == [] and seams["created"] == []


def test_no_anchor_creates_and_claims(seams):
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="ensure"))
    assert seams["created"] == [("org", "7", K.KB_NAME)]
    assert out["project_id"] == 42 and seams["anchor"] == 42


def test_dangling_anchor_transferred_project_repairs(seams):
    # Le projet ancré a été transféré hors org → clear + recréation + re-claim.
    seams["anchor"] = 9
    seams["projects"][9] = _proj(9, org="99")   # owner ≠ org active
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="ensure"))
    assert seams["cleared"] == [9]
    assert out["project_id"] == 42 and seams["anchor"] == 42


def test_dangling_anchor_archived_project_repairs(seams):
    seams["anchor"] = 9
    seams["projects"][9] = _proj(9, archived="2026-07-01")
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="ensure"))
    assert out["project_id"] == 42


def test_lost_claim_archives_duplicate_and_returns_winner(seams):
    # Un appel concurrent a posé l'ancre entre ma création et mon claim → mon
    # doublon est archivé, je renvoie LA KB du gagnant.
    winner = 9
    seams["projects"][winner] = _proj(winner)
    real_claim = K.org_store.claim_kb_project
    def _racing_claim(org, pid):
        seams["anchor"] = winner        # le concurrent gagne juste avant moi
        return False
    K.org_store.claim_kb_project = _racing_claim
    try:
        out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="ensure"))
    finally:
        K.org_store.claim_kb_project = real_claim
    assert seams["archived"] == [42]          # mon doublon archivé
    assert out["project_id"] == winner


def test_no_active_org(seams):
    with pytest.raises(AuthzDenied) as e:
        K._kb(ResolvedCtx(sub="u1", org_id=None), K.KbInput(op="get"))
    assert e.value.code == "no_active_org"


def test_capability_registered():
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next((c for c in CAPABILITIES if c.key == "me.kb"), None)
    assert cap is not None and cap.mcp == "oto_kb"


def test_kb_output_holds_for_every_op(seams):
    """`KbView` doit décrire la réponse de TOUTES les `op` de la surface, pas de
    celle qu'on avait sous les yeux en l'écrivant.

    C'est le seul garde-fou qui compte sur une surface consolidée (le verbe vit
    dans le corps) : une `op` ajoutée demain qui rendrait une autre forme ferait
    générer des types FAUX chez l'intégrateur qui dérive son client d'`/openapi.json`.
    Ici l'énumération tient dans une ligne — `op` n'a qu'une valeur — et c'est
    précisément pourquoi cette surface-là est déclarable, quand ses trois voisines
    (me.project, me.doc, resources.govern) ne le sont pas (#269)."""
    from typing import get_args

    ops = get_args(K.KbInput.model_fields["op"].annotation)
    assert ops, "aucune `op` énumérée : le test ne prouverait rien."
    declared = set(K.KbView.model_fields)
    for op in ops:
        seams["anchor"] = 9
        seams["projects"][9] = _proj(9)
        out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op=op))
        assert set(out) == declared, (
            f"op={op!r} rend {sorted(set(out) ^ declared)} en écart de `KbView`. "
            "Soit la nouvelle op rend les mêmes champs, soit `KbView` retombe sur "
            "l'INTERSECTION commune à toutes les op (et le reste passe en prose).")
        K.KbView(**out)          # les TYPES tiennent aussi, pas seulement les noms


def test_kb_output_reaches_the_openapi_document():
    """Une déclaration qui n'atteint pas le document est décorative."""
    from oto_mcp import openapi

    schema = (openapi.build()["paths"]["/api/me/kb"]["post"]["responses"]["200"]
              .get("content", {}).get("application/json", {}).get("schema", {}))
    assert set(schema.get("properties", {})) == set(K.KbView.model_fields)
    assert sorted(schema.get("required", [])) == sorted(K.KbView.model_fields)


# ── 04/09/2026 : « ma » base de connaissance est celle de l'ORG ────────────────

def test_ensure_DIT_que_la_base_est_partagee_et_qu_il_vient_de_la_creer(seams):
    """Vécu ce matin-là. Une DG demande à son agent de « mettre à jour sa knowledge
    base » ; il appelle `ensure`, qui CRÉE un projet possédé par l'ORG — visible de
    tous ses membres — et y dépose un document stratégique marqué « non diffusable ».
    Il s'en aperçoit 3 minutes plus tard, déplace la page vers un projet perso et
    archive la base. Le contenu aura été exposé 3 min 18 s.

    La réponse rendait `{project_id, name, brief_md}` : ni la portée, ni le fait
    qu'une ressource PARTAGÉE venait de naître. « Ma base » se comprend comme « la
    mienne » ; celle-ci est celle de l'org, et c'est le mot qui manquait."""
    seams["anchor"] = None                      # aucune base : `ensure` va la CRÉER
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="ensure"))
    assert out["created"] is True, "l'appelant doit savoir qu'il vient de CRÉER"
    assert "TOUS les membres" in out["visible_to"]
    assert "n'est pas personnelle" in out["visible_to"]
    # Et le message dit le GESTE de repli, sinon il ne fait qu'inquiéter.
    assert "owner_type='user'" in out["visible_to"]


def test_une_base_qui_existait_deja_ne_se_dit_pas_creee(seams):
    """`created` distingue « je viens de la faire naître » de « elle était là ».
    Sans lui, un appelant ne peut pas savoir lequel des deux il a fait — et c'est
    précisément la question qui décide s'il doit s'inquiéter."""
    seams["anchor"] = 9
    seams["projects"][9] = _proj(9)
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="ensure"))
    assert out["created"] is False
    assert "TOUS les membres" in out["visible_to"]


def test_meme_une_LECTURE_dit_la_portee(seams):
    """`op=get` sur une org sans base : la portée est dite AVANT qu'on crée quoi que
    ce soit. L'apprendre après coup, c'est l'apprendre trop tard."""
    seams["anchor"] = None
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="get"))
    assert out["project_id"] is None and out["created"] is False
    assert "TOUS les membres" in out["visible_to"]
