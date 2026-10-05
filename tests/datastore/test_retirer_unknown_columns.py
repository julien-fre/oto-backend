"""`scripts/retirer_unknown_columns.py` — retirer `unknown_columns` des schémas
stockés (oto#124) : tableaux, slots de procédure, entrées de bibliothèque.

À blanc par défaut, `--appliquer` écrit par le chemin normal de chaque famille,
idempotent, et un schéma qui a bougé depuis l'inventaire est sauté.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import uuid

import pytest

from oto_mcp.datastore import schema as S

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from scripts import retirer_unknown_columns as M  # noqa: E402

CHAMPS = [{"key": "siren", "type": "text"},
          {"key": "statut", "type": "enum", "options": ["a", "b"]}]
ANCIEN = {"key": "siren", "unknown_columns": "reject", "new_rows": "reject",
          "fields": CHAMPS}
RETIRE = {"key": "siren", "new_rows": "reject", "fields": CHAMPS}
SUB = "u-retirer-uc"


# ── le retrait, pur ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("valeur", ["create", "report", "reject"])
def test_le_retrait_ne_touche_que_le_reglage(valeur):
    plan = M.retirer({**ANCIEN, "unknown_columns": valeur})
    assert plan.retire == {"unknown_columns": valeur}
    assert plan.schema == RETIRE and list(plan.schema) == list(RETIRE), "ordre gardé"
    assert S.validate_schema_def(plan.schema) == []


@pytest.mark.parametrize("schema", [None, {}, RETIRE, {"fields": []}])
def test_sans_le_reglage_rien_a_faire(schema):
    assert M.retirer(schema).vide


def test_le_reglage_pose_est_refuse_le_stocke_inchange_passe():
    """Ce qui rend le passage sûr à tout moment : le stocké inchangé est toléré à la
    pose comme au patch — seule une valeur posée ou modifiée est refusée."""
    assert S.validate_schema_def(ANCIEN, ANCIEN) == []
    refus = S.validate_schema_def(ANCIEN, RETIRE)
    assert len(refus) == 1 and "plus aucun réglage" in refus[0], refus


# ── le passage sur une base ──────────────────────────────────────────────────

def _tableau(schema) -> int:
    from oto_mcp import db
    ns_id = db.create_datastore("user", SUB, "retirer-" + uuid.uuid4().hex[:6])
    db.set_datastore_schema(ns_id, copy.deepcopy(schema))
    return ns_id


def _passage(appliquer, **filtres):
    lignes: list[str] = []
    bilan = M.executer(appliquer=appliquer, sortie=lignes.append, **filtres)
    return bilan, "\n".join(lignes)


def test_tableau_a_blanc_puis_ecrit_journalise_et_idempotent(live):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    ns_id = _tableau(ANCIEN)
    bilan, texte = _passage(False, tableaux=[ns_id])
    assert db.get_datastore_by_id(ns_id)["schema"] == ANCIEN, "à blanc = rien d'écrit"
    assert bilan["tableaux"]["a_retirer"] == 1, texte
    assert "unknown_columns: 'reject' retiré" in texte
    bilan, texte = _passage(True, tableaux=[ns_id])
    assert bilan["tableaux"]["ecrits"] == 1, texte
    assert db.get_datastore_by_id(ns_id)["schema"] == RETIRE
    with _connect() as conn:
        journal = conn.execute(
            "SELECT sub, args FROM tool_calls WHERE tool = 'data_set_schema' "
            "AND args->>'datastore' = %s", (str(ns_id),)).fetchall()
    assert len(journal) == 1 and journal[0]["sub"] is None
    assert journal[0]["args"]["migration_systeme"] == M.MIGRATION
    assert "'unknown_columns': 'reject'" in str(journal[0]["args"]["retire"])
    second, _ = _passage(True, tableaux=[ns_id])
    assert second["tableaux"]["a_retirer"] == 0 and second["tableaux"]["ecrits"] == 0


def test_un_tableau_qui_a_bouge_est_saute(live, monkeypatch):
    from oto_mcp import db
    ns_id = _tableau(ANCIEN)
    vrai = M.inventaire

    def _perime(tableaux=None):
        lus = vrai(tableaux)
        db.set_datastore_schema(ns_id, {**ANCIEN, "description": "bougé"})
        return lus
    monkeypatch.setattr(M, "inventaire", _perime)
    bilan, texte = _passage(True, tableaux=[ns_id])
    assert bilan["tableaux"]["bouges"] == [ns_id] and "SAUTÉ" in texte
    apres = db.get_datastore_by_id(ns_id)["schema"]
    assert apres["description"] == "bougé" and apres["unknown_columns"] == "reject"
    assert M.main(["--tableau", str(ns_id)]) == 0, "à blanc : rien d'échoué"


PROPRIO = "u-retirer-proc"
SLOTS = [{"name": "vivier", "type": "tableau", "schema": copy.deepcopy(ANCIEN)},
         {"name": "crm", "type": "connecteur", "connector": "attio"}]


def _procedure_stockee(slug: str) -> dict:
    """Une procédure dont le slot porte le réglage : posée AVANT son retrait, elle
    entre par la table (la pose le refuse désormais)."""
    from oto_mcp import org_store
    from oto_mcp.db._conn import _connect
    org_store.set_instruction("user", PROPRIO, slug, "1. Qualifier.",
                              slots=copy.deepcopy([{**SLOTS[0], "schema": RETIRE},
                                                   SLOTS[1]]), set_by="u-auteur")
    p = org_store.get_instruction("user", PROPRIO, slug)
    with _connect() as conn:
        conn.execute("UPDATE org_instructions SET slots = %s WHERE id = %s",
                     (json.dumps(SLOTS), p["id"]))
    return org_store.get_instruction("user", PROPRIO, slug)


def test_une_procedure_s_ecrit_en_nouvelle_version(live):
    from oto_mcp import org_store
    slug = "retirer-" + uuid.uuid4().hex[:4]
    p = _procedure_stockee(slug)
    assert p["slots"] == SLOTS
    bilan, texte = _passage(True, procedures=[p["id"]])
    assert bilan["procedures"]["ecrits"] == 1, texte
    apres = org_store.get_instruction("user", PROPRIO, slug)
    assert apres["version"] == p["version"] + 1
    assert next(s for s in apres["slots"] if s["name"] == "vivier")["schema"] == RETIRE
    assert _passage(False, procedures=[p["id"]])[0]["procedures"]["a_retirer"] == 0


def _entree(slug: str, slots) -> dict:
    from oto_mcp import org_store
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(
            "INSERT INTO guide_library (slug, title, description, body_md, slots, "
            "author_kind, author_display, category, tags, visibility, version, "
            "published_by) VALUES (%s, 'T', '', '1. Faire.', %s, 'otomata', 'Otomata', "
            "'', ARRAY['x'], 'unlisted', 1, 'u-publieur')",
            (slug, json.dumps(slots)))
    return org_store.get_library_entry(slug=slug, include_unlisted=True)


def test_une_entree_de_bibliotheque_est_republiee(live):
    from oto_mcp import org_store
    e = _entree("retirer-pub-" + uuid.uuid4().hex[:4], copy.deepcopy(SLOTS))
    bilan, texte = _passage(True, bibliotheque=[e["id"]])
    assert bilan["bibliotheque"]["ecrits"] == 1, texte
    apres = org_store.get_library_entry(entry_id=e["id"], include_unlisted=True)
    assert apres["version"] == e["version"] + 1
    assert (apres["title"], apres["visibility"], apres["tags"], apres["author_kind"]) \
        == ("T", "unlisted", ["x"], "otomata")
    assert apres["slots"][0]["schema"] == RETIRE and apres["slots"][1] == SLOTS[1]
    assert _passage(False, bibliotheque=[e["id"]])[0]["bibliotheque"]["a_retirer"] == 0
