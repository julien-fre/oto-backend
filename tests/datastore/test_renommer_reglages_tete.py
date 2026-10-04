"""`scripts/renommer_reglages_tete.py` — traduire `strict`/`unknown_fields`/
`key_required` en `unknown_columns`/`new_rows`, une fois, sur l'existant (oto#127).

Deux moitiés : la TRADUCTION, pure (et son équivalence de comportement), et le PASSAGE
sur une base — tableaux, slots de procédure, entrées de bibliothèque.
"""
from __future__ import annotations

import copy
import pathlib
import sys
import uuid

import pytest

from oto_mcp.datastore import reglages as R
from oto_mcp.datastore import schema as S

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from scripts import renommer_reglages_tete as M  # noqa: E402

CHAMPS = [{"key": "siren", "type": "text"}, {"key": "statut", "type": "enum",
                                             "options": ["a", "b"]}]


# ── la traduction, pure ──────────────────────────────────────────────────────

@pytest.mark.parametrize("anciens,nouveaux", [
    ({"strict": False}, {"unknown_columns": "create"}),
    ({"unknown_fields": "reject"}, {"unknown_columns": "create"}),   # inerte sans strict
    ({"strict": False, "unknown_fields": "report"}, {"unknown_columns": "create"}),
    ({"strict": True}, {"unknown_columns": "report"}),
    ({"strict": True, "unknown_fields": "report"}, {"unknown_columns": "report"}),
    ({"strict": True, "unknown_fields": "reject"}, {"unknown_columns": "reject"}),
    ({"key_required": True}, {"new_rows": "reject"}),
    ({"key_required": False}, {"new_rows": "create"}),
    ({"strict": True, "unknown_fields": "reject", "key_required": True},
     {"unknown_columns": "reject", "new_rows": "reject"}),
])
def test_la_table_de_traduction(anciens, nouveaux):
    schema = {"key": "siren", **anciens, "fields": CHAMPS}
    assert R.traduire(schema) == nouveaux
    plan = M.renommer(schema)
    assert plan.anciens == anciens and plan.nouveaux == nouveaux
    assert not set(R.ANCIENS) & set(plan.schema)
    assert S.validate_schema_def(plan.schema) == []


def test_un_cran_inerte_se_traduit_par_ce_qu_il_faisait():
    sans_colonne = {"strict": True, "unknown_fields": "reject"}
    assert R.traduire(sans_colonne) == {"unknown_columns": "report"}
    assert R.traduire({"key_required": True, "fields": CHAMPS}) == {"new_rows": "create"}


@pytest.mark.parametrize("anciens", [
    {"strict": True}, {"strict": True, "unknown_fields": "reject"},
    {"key_required": True}, {"unknown_fields": "reject"}, {"strict": False},
])
def test_le_schema_traduit_s_applique_comme_le_dit_la_table(anciens):
    """Ce que la plateforme applique sur le schéma traduit est la traduction ; et sur
    l'ancien, plus rien — un ancien réglage stocké n'est plus lu (oto#127)."""
    avant = {"key": "siren", **anciens, "fields": copy.deepcopy(CHAMPS)}
    apres = M.renommer(avant).schema
    defauts = {"unknown_columns": "create", "new_rows": "create"}
    assert R.effectifs(apres) == {**defauts, **R.traduire(avant)}
    assert R.effectifs(avant) == defauts
    ligne = {"siren": "1", "statut": "z", "inventee": 1}
    contrat = R.traduire(avant).get("unknown_columns", "create") != "create"
    assert S.validation_active(apres) is contrat
    assert bool(S.off_schema_keys(apres, ligne)) is contrat


def test_les_nouveaux_prennent_la_place_des_anciens_et_c_est_idempotent():
    plan = M.renommer({"key": "siren", "strict": True, "fields": CHAMPS,
                       "key_required": True, "description": "d"})
    assert list(plan.schema) == ["key", "unknown_columns", "new_rows", "fields",
                                 "description"]
    assert M.renommer(plan.schema).vide


def test_un_nouveau_reglage_contradictoire_est_a_arbitrer():
    with pytest.raises(M.Inmigrable, match="unknown_columns"):
        M.renommer({"strict": True, "unknown_columns": "create", "fields": CHAMPS})
    deja = M.renommer({"strict": True, "unknown_columns": "report", "fields": CHAMPS})
    assert deja.schema == {"unknown_columns": "report", "fields": CHAMPS}


def test_sans_ancien_reglage_rien_a_faire():
    assert M.renommer({"fields": CHAMPS}).vide and M.renommer(None).vide


# ── le passage, sur une base ─────────────────────────────────────────────────

SUB = "u-renommer"
ANCIEN = {"key": "siren", "strict": True, "unknown_fields": "reject",
          "key_required": True, "fields": CHAMPS}
TRADUIT = {"key": "siren", "unknown_columns": "reject", "new_rows": "reject",
           "fields": CHAMPS}


def _tableau(schema) -> int:
    from oto_mcp import db
    ns_id = db.create_datastore("user", SUB, "renommer-" + uuid.uuid4().hex[:6])
    db.set_datastore_schema(ns_id, copy.deepcopy(schema))
    return ns_id


def _passage(appliquer, **filtres):
    lignes: list[str] = []
    bilan = M.executer(appliquer=appliquer, sortie=lignes.append, **filtres)
    return bilan, "\n".join(lignes)


def test_tableau_a_blanc_puis_ecrit_par_la_pose_journalise_et_idempotent(live):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    ns_id = _tableau(ANCIEN)
    bilan, texte = _passage(False, tableaux=[ns_id])
    assert db.get_datastore_by_id(ns_id)["schema"] == ANCIEN, "à blanc = rien d'écrit"
    assert bilan["tableaux"]["a_traduire"] == 1, texte
    # JSONB ne garde pas l'ordre des clés : l'équivalent se lit après la flèche.
    assert "→ unknown_columns: 'reject', new_rows: 'reject'" in texte
    assert "unknown_fields: 'reject'" in texte and "key_required: True" in texte
    bilan, texte = _passage(True, tableaux=[ns_id])
    assert bilan["tableaux"]["ecrits"] == 1, texte
    assert db.get_datastore_by_id(ns_id)["schema"] == TRADUIT
    with _connect() as conn:
        journal = conn.execute(
            "SELECT sub, args FROM tool_calls WHERE tool = 'data_set_schema' "
            "AND args->>'datastore' = %s", (str(ns_id),)).fetchall()
    assert len(journal) == 1 and journal[0]["sub"] is None
    assert journal[0]["args"]["migration_systeme"] == M.MIGRATION
    second, _ = _passage(False, tableaux=[ns_id])
    assert second["tableaux"]["a_traduire"] == 0


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
    assert db.get_datastore_by_id(ns_id)["schema"]["description"] == "bougé"


PROPRIO = "u-renommer-proc"
SLOTS = [{"name": "vivier", "type": "tableau", "schema": copy.deepcopy(ANCIEN)},
         {"name": "crm", "type": "connecteur", "connector": "attio"}]


def test_une_procedure_s_ecrit_en_nouvelle_version(live):
    from oto_mcp import org_store
    slug = "renommer-" + uuid.uuid4().hex[:4]
    org_store.set_instruction("user", PROPRIO, slug, "1. Qualifier.",
                              slots=copy.deepcopy(SLOTS), set_by="u-auteur")
    p = org_store.get_instruction("user", PROPRIO, slug)
    bilan, texte = _passage(True, procedures=[p["id"]])
    assert bilan["procedures"]["ecrits"] == 1, texte
    apres = org_store.get_instruction("user", PROPRIO, slug)
    assert apres["version"] == 2
    assert next(s for s in apres["slots"] if s["name"] == "vivier")["schema"] == TRADUIT
    assert org_store.get_instruction("user", PROPRIO, slug, 1)["slots"] == SLOTS
    assert _passage(False, procedures=[p["id"]])[0]["procedures"]["a_traduire"] == 0


def _entree(slug: str, slots) -> dict:
    """Une entrée publiée AVANT la bascule : elle entre par la table, comme le parc
    qu'on traduit — `publish_guide` valide désormais les slots qu'il reçoit."""
    import json

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


def test_une_entree_de_bibliotheque_est_republiee_par_son_auteur(live):
    from oto_mcp import org_store
    e = _entree("renommer-pub-" + uuid.uuid4().hex[:4], copy.deepcopy(SLOTS))
    bilan, texte = _passage(True, bibliotheque=[e["id"]])
    assert bilan["bibliotheque"]["ecrits"] == 1, texte
    apres = org_store.get_library_entry(entry_id=e["id"], include_unlisted=True)
    assert apres["version"] == e["version"] + 1
    assert (apres["title"], apres["visibility"], apres["tags"], apres["author_kind"]) \
        == ("T", "unlisted", ["x"], "otomata")
    assert apres["slots"][0]["schema"] == TRADUIT and apres["slots"][1] == SLOTS[1]
    assert _passage(False, bibliotheque=[e["id"]])[0]["bibliotheque"]["a_traduire"] == 0


def test_la_validation_des_slots_a_la_publication_laisse_passer_la_traduction(live):
    """`publish_guide` juge les slots contre ceux qu'il remplace (oto#34) : la
    traduction est une pose de réglages ADMIS, et une clé inconnue déjà stockée
    ailleurs dans le slot, inchangée, ne bloque pas la republication."""
    from oto_mcp import org_store
    vivier = copy.deepcopy(SLOTS[0])
    vivier["schema"]["fields"] = [{**CHAMPS[0], "editable": True}, CHAMPS[1]]
    e = _entree("renommer-val-" + uuid.uuid4().hex[:4], [vivier])
    bilan, texte = _passage(True, bibliotheque=[e["id"]])
    assert bilan["bibliotheque"]["ecrits"] == 1, texte
    schema = org_store.get_library_entry(entry_id=e["id"], include_unlisted=True)[
        "slots"][0]["schema"]
    assert schema["unknown_columns"] == "reject" and schema["new_rows"] == "reject"
    assert schema["fields"][0]["editable"] is True, "le résidu reste, intouché"
    assert not set(R.ANCIENS) & set(schema)
