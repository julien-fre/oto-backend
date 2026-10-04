"""oto#127 — deux réglages de tête, un par axe : `unknown_columns` et `new_rows`.

Les anciens (`strict`, `unknown_fields`, `key_required`) sont REFUSÉS à la pose, au
patch et en paramètre, avec l'équivalent exact ; stockés, ils sont tolérés tant qu'on
n'y touche pas, ne sont plus lus, et la lecture les nomme.
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from oto_mcp import error_taxonomy as T
from oto_mcp.capabilities.datastore.columns import PatchSchemaInput
from oto_mcp.datastore import cles_inconnues
from oto_mcp.datastore import reglages as R
from oto_mcp.datastore import schema as S

CHAMPS = [{"key": "siren", "type": "text"},
          {"key": "statut", "type": "enum", "options": ["a", "b"]}]


# ── ce que la plateforme lit ─────────────────────────────────────────────────

def test_les_defauts_et_les_crans_se_lisent_par_les_nouveaux_reglages_seuls():
    assert R.effectifs(None) == {"unknown_columns": "create", "new_rows": "create"}
    assert R.effectifs({"unknown_columns": "reject", "new_rows": "reject",
                        "key": "siren", "fields": CHAMPS}) == {
        "unknown_columns": "reject", "new_rows": "reject"}
    assert not S.validation_active({"strict": True, "fields": CHAMPS}), \
        "un ancien réglage stocké n'est plus lu"
    assert not S.creation_refusee({"key": "siren", "key_required": True})


def test_new_rows_sans_cle_ne_s_arme_pas():
    assert R.lignes_nouvelles({"new_rows": "reject", "fields": CHAMPS}) == "create"


@pytest.mark.parametrize("mode,contrat", [("create", False), ("report", True),
                                          ("reject", True)])
def test_le_format_fait_contrat_hors_create(mode, contrat):
    schema = {"unknown_columns": mode, "fields": CHAMPS}
    assert S.validation_active(schema) is contrat
    assert bool(S.validate_row(schema, {"statut": "z"})) is contrat, "options de tête"
    assert bool(S.off_schema_keys(schema, {"inventee": 1})) is contrat
    assert bool(S.off_schema_refusal(schema, {"inventee": 1})[0]) is (mode == "reject")


# ── la pose ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("schema,attendu", [
    ({"unknown_columns": "refuse", "fields": CHAMPS}, '"create" | "report" | "reject"'),
    ({"new_rows": "closed", "key": "siren", "fields": CHAMPS}, '"create" | "reject"'),
    ({"unknown_columns": "reject", "fields": []}, "au moins une colonne déclarée"),
    ({"new_rows": "reject", "fields": CHAMPS}, "exige une clé métier"),
])
def test_une_valeur_ou_une_combinaison_inapplicable_est_refusee(schema, attendu):
    errs = S.validate_schema_def(schema)
    assert any(attendu in e for e in errs), errs


@pytest.mark.parametrize("anciens,equivalent", [
    ({"strict": True}, '`"unknown_columns": "report"`'),
    ({"strict": True, "unknown_fields": "reject"}, '`"unknown_columns": "reject"`'),
    ({"strict": False}, '`"unknown_columns": "create"`'),
    ({"key_required": True}, '`"new_rows": "reject"`'),
    ({"strict": True, "key_required": True},
     '`"unknown_columns": "report"` et `"new_rows": "reject"`'),
])
def test_un_ancien_reglage_pose_est_refuse_avec_son_equivalent_exact(anciens, equivalent):
    errs = S.validate_schema_def({"key": "siren", **anciens, "fields": CHAMPS})
    assert len(errs) == 1, errs
    assert "remplacé" in errs[0] and equivalent in errs[0], errs[0]
    for k in anciens:
        assert f"`{k}`" in errs[0]


def test_un_cran_inerte_est_dit_inerte_dans_le_refus():
    err = S.validate_schema_def({"unknown_fields": "reject", "fields": CHAMPS})[0]
    assert '`"unknown_columns": "create"`' in err and "ne refusait rien" in err, err


def test_un_ancien_reglage_DEJA_stocke_et_inchange_passe_et_se_dit_a_la_lecture():
    ancien = {"key": "siren", "strict": True, "fields": CHAMPS}
    patche = {**ancien, "fields": [*CHAMPS, {"key": "ville", "type": "text"}]}
    assert S.validate_schema_def(patche, ancien) == []
    assert S.validate_schema_def({**patche, "strict": False}, ancien), \
        "le MODIFIER est un geste : refusé"
    msg = cles_inconnues.residus_warning(ancien)
    assert "`strict`" in msg and '`unknown_columns: "report"`' in msg, msg


# ── les paramètres de `data_patch_schema` ────────────────────────────────────

@pytest.mark.parametrize("params,rejeu", [
    ({"strict": True}, 'unknown_columns="report"'),
    ({"strict": False}, 'unknown_columns="create"'),
    ({"unknown_fields": "reject"}, 'unknown_columns="reject"'),
    ({"strict": True, "unknown_fields": "reject"}, 'unknown_columns="reject"'),
    ({"key_required": True}, 'new_rows="reject"'),
    ({"key_required": False}, 'new_rows="create"'),
])
def test_un_ancien_parametre_est_refuse_avec_le_rejeu_exact(params, rejeu):
    msg = R.refus_parametres(params)
    assert f"Rejoue `data_patch_schema` avec {rejeu}" in msg, msg
    assert PatchSchemaInput.refus_champs_retires(params) == msg, "face REST, même texte"
    assert R.refus_parametres({"fields": []}) is None


_mcp = FastMCP("banc-127")


@_mcp.tool()
def data_patch_schema(datastore: str, unknown_columns: str | None = None,
                      new_rows: str | None = None) -> dict:
    return {"ok": True}


def test_la_face_MCP_sert_le_meme_refus():
    try:
        asyncio.run(_mcp.call_tool("data_patch_schema",
                                   {"datastore": "v", "strict": True,
                                    "unknown_fields": "reject"}))
    except Exception as exc:  # noqa: BLE001 — c'est elle qu'on examine
        msg = T._arg_error_message(exc)
    else:
        raise AssertionError("l'appel aurait dû être refusé")
    assert 'Rejoue `data_patch_schema` avec unknown_columns="reject"' in msg, msg
