"""`oto_project op=update` corrige `mcp_instructions_md` SANS republier (oto-backend#597).

Corriger l'instruction servie par un endpoint publié imposait de rejouer `publish_mcp`,
donc de redéclarer accès et outils : une session prudente refusait de le faire pour une
phrase, et l'instruction périmée restait servie (cas vécu : une clause retirée du brief,
encore servie au destinataire).

Même patron que `test_project_url_perimeter_option.py` : les seams db/ownership sont
monkeypatchés. Ce que ce fichier fige : l'instruction SEULE bouge (aucune écriture de
publication), sous `can_govern`, journalisée `project.update_mcp_instructions` ; le
reste de la publication est REFUSÉ sur `update` plutôt qu'ignoré.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import projects as P
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="u1", org_id=99)
ROW = {"id": 7, "owner_type": "org", "owner_id": "99", "name": "Campagne", "brief_md": "b",
       "created_by": "u1", "archived_at": None, "created_at": "2026-08-29",
       "updated_at": "2026-08-29", "mcp_slug": "campagne-3f2a9c1d7e4b",
       "mcp_access": "secret", "mcp_tools": ["oto_doc"],
       "mcp_instructions_md": "Ancienne consigne."}


@pytest.fixture
def seams(monkeypatch):
    store = {"row": dict(ROW), "instr": [], "activite": [], "maj": []}
    monkeypatch.setattr(P.db, "get_project_by_id",
                        lambda pid: dict(store["row"], id=pid) if pid == 7 else None)
    monkeypatch.setattr(P.db, "update_project",
                        lambda pid, **kw: store["maj"].append((pid, kw)))
    monkeypatch.setattr(P.db, "list_project_links", lambda pid: [])

    def _instr(pid, md):
        store["instr"].append((pid, md))
        store["row"]["mcp_instructions_md"] = (md or "").strip() or None
    monkeypatch.setattr(P.db, "set_project_mcp_instructions", _instr)

    def _jamais(*a, **k):
        raise AssertionError("l'update ne doit rien republier")
    monkeypatch.setattr(P.db, "set_project_mcp_publication", _jamais)
    monkeypatch.setattr(P, "publish_project_mcp", _jamais)
    monkeypatch.setattr(P.db, "log_project_activity",
                        lambda pid, sub, action, detail=None: store["activite"].append(action))
    monkeypatch.setattr(P.ownership, "can_access", lambda sub, t, rid, want="read": True)
    monkeypatch.setattr(P.ownership, "can_govern", lambda sub, t, rid: True)
    monkeypatch.setattr(P.ownership, "accessor_scope", lambda sub, rt, rid: True)
    monkeypatch.setattr("oto_mcp.project_audit.audit_project",
                        lambda pid, links=None, *, light=False: {"dead_links": [],
                                                                 "unbound_slots": [],
                                                                 "inert_procedures": []})
    return store


def test_update_corrige_l_instruction_seule(seams):
    out = P._project(CTX, P.ProjectInput(op="update", project_id=7,
                                         mcp_instructions_md="Nouvelle consigne."))
    assert seams["instr"] == [(7, "Nouvelle consigne.")]
    assert out["mcp_instructions_md"] == "Nouvelle consigne."
    # La publication n'a pas bougé : même adresse, même accès, mêmes outils.
    assert (out["mcp_slug"], out["mcp_access"], out["mcp_tools"]) == (
        ROW["mcp_slug"], "secret", ["oto_doc"])
    assert "project.update_mcp_instructions" in seams["activite"]


def test_une_chaine_vide_efface_l_instruction(seams):
    out = P._project(CTX, P.ProjectInput(op="update", project_id=7, mcp_instructions_md=""))
    assert seams["instr"] == [(7, "")] and out["mcp_instructions_md"] == ""


def test_sans_le_champ_l_instruction_ne_bouge_pas(seams):
    P._project(CTX, P.ProjectInput(op="update", project_id=7, name="Renommé"))
    assert seams["instr"] == []
    assert "project.update_mcp_instructions" not in seams["activite"]


def test_corriger_l_instruction_exige_can_govern(seams, monkeypatch):
    """Un simple droit d'écriture ne suffit pas : c'est ce qu'un TIERS lit au branchement."""
    monkeypatch.setattr(P.ownership, "can_govern", lambda sub, t, rid: False)
    with pytest.raises(AuthzDenied) as e:
        P._project(CTX, P.ProjectInput(op="update", project_id=7,
                                       mcp_instructions_md="Nouvelle consigne."))
    assert e.value.status == 403
    assert seams["instr"] == [] and seams["maj"] == []


@pytest.mark.parametrize("champ,valeur", [
    ("mcp_slug", "autre"), ("mcp_access", "org"), ("mcp_tools", ["oto_doc"]),
    ("mcp_expose_datastore", False), ("mcp_expose_datastore_write", True),
    ("mcp_expose_docs", True),
])
def test_le_reste_de_la_publication_est_refuse_sur_update(seams, champ, valeur):
    """Ignoré, il ferait croire à l'agent que l'accès ou les outils ont changé (#1007)."""
    with pytest.raises(AuthzDenied) as e:
        P._project(CTX, P.ProjectInput(op="update", project_id=7,
                                       mcp_instructions_md="x", **{champ: valeur}))
    assert e.value.code == "publication_field_on_update" and champ in e.value.message
    assert seams["instr"] == [] and seams["maj"] == []


def test_la_description_servie_nomme_le_geste():
    cap = next(c for c in P.CAPABILITIES if c.mcp == "oto_project")
    assert "mcp_instructions_md = the prose" in cap.description
