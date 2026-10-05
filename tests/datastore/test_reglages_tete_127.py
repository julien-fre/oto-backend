"""oto#127 puis oto#124 — un seul réglage de tête reste : `new_rows`.

Les anciens (`strict`, `unknown_fields`, `key_required`) sont REFUSÉS à la pose, au
patch et en paramètre ; `key_required` avec son équivalent exact (`new_rows`).
`unknown_columns`, qui les avait remplacés le 02/10, est RETIRÉ le 05/10/2026 : plus
aucun réglage, les colonnes et les valeurs sont toujours vérifiées. Refusé à la pose et
au patch ; STOCKÉ, il est toléré tant qu'on n'y touche pas, dit à la lecture, et lu
jusqu'au 21/10/2026 seulement (`validation_complete`).
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

PLUS_AUCUN = "plus aucun réglage : les colonnes et les valeurs sont toujours vérifiées"


# ── ce que la plateforme lit ─────────────────────────────────────────────────

def test_le_seul_reglage_servi_est_new_rows():
    assert R.effectifs(None) == {"new_rows": "create"}
    assert R.effectifs({"unknown_columns": "reject", "new_rows": "reject",
                        "key": "siren", "fields": CHAMPS}) == {"new_rows": "reject"}
    assert not S.validation_active({"strict": True, "fields": CHAMPS}), \
        "un ancien réglage stocké n'est plus lu"
    assert not S.creation_refusee({"key": "siren", "key_required": True})


def test_new_rows_sans_cle_ne_s_arme_pas():
    assert R.lignes_nouvelles({"new_rows": "reject", "fields": CHAMPS}) == "create"


@pytest.mark.parametrize("mode,contrat", [("create", False), ("report", True),
                                          ("reject", True)])
def test_avant_la_date_le_reglage_STOCKE_garde_son_effet(mode, contrat):
    """Pendant le préavis, un tableau réglé `report`/`reject` garde la validation
    complète qu'il avait — sans quoi il retomberait au préavis."""
    schema = {"unknown_columns": mode, "fields": CHAMPS}
    assert S.validation_active(schema) is contrat
    assert bool(S.validate_row(schema, {"statut": "z"})) is contrat, "options de tête"


@pytest.mark.parametrize("mode", ["create", "report", "reject"])
def test_apres_la_date_le_reglage_stocke_n_est_plus_lu(mode, validation_complete_partout):
    schema = {"unknown_columns": mode, "fields": CHAMPS}
    assert S.validation_active(schema) is True
    assert S.validate_row(schema, {"statut": "z"}), "la liste fait contrat, partout"


# ── la pose ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("valeur", ["create", "report", "reject", "refuse"])
def test_unknown_columns_pose_est_refuse_avec_son_explication(valeur):
    errs = S.validate_schema_def({"unknown_columns": valeur, "fields": CHAMPS})
    assert len(errs) == 1, errs
    assert "`unknown_columns`" in errs[0] and PLUS_AUCUN in errs[0], errs[0]


def test_unknown_columns_STOCKE_et_inchange_passe_et_se_dit_a_la_lecture():
    ancien = {"key": "siren", "unknown_columns": "report", "fields": CHAMPS}
    patche = {**ancien, "fields": [*CHAMPS, {"key": "ville", "type": "text"}]}
    assert S.validate_schema_def(patche, ancien) == []
    assert S.validate_schema_def({**patche, "unknown_columns": "reject"}, ancien), \
        "le MODIFIER est un geste : refusé"
    msg = cles_inconnues.residus_warning(ancien)
    assert "`unknown_columns: 'report'`" in msg and PLUS_AUCUN in msg, msg
    assert "fermeture du vocabulaire" not in msg, "sa phrase propre, pas celle des résidus"


@pytest.mark.parametrize("schema,attendu", [
    ({"new_rows": "closed", "key": "siren", "fields": CHAMPS}, '"create" | "reject"'),
    ({"new_rows": "reject", "fields": CHAMPS}, "exige une clé métier"),
])
def test_une_valeur_ou_une_combinaison_inapplicable_est_refusee(schema, attendu):
    errs = S.validate_schema_def(schema)
    assert any(attendu in e for e in errs), errs


@pytest.mark.parametrize("anciens,dit", [
    ({"strict": True}, PLUS_AUCUN),
    ({"strict": True, "unknown_fields": "reject"}, PLUS_AUCUN),
    ({"key_required": True}, '`"new_rows": "reject"`'),
    ({"strict": True, "key_required": True}, '`"new_rows": "reject"`'),
])
def test_un_ancien_reglage_pose_est_refuse_avec_ce_qui_le_remplace(anciens, dit):
    errs = S.validate_schema_def({"key": "siren", **anciens, "fields": CHAMPS})
    assert len(errs) == 1, errs
    assert "remplacé" in errs[0] and dit in errs[0], errs[0]
    assert "unknown_columns\": " not in errs[0], "jamais conseiller le réglage retiré"
    for k in anciens:
        assert f"`{k}`" in errs[0]


def test_un_cran_inerte_est_dit_inerte_dans_le_refus():
    err = S.validate_schema_def({"key_required": True, "fields": CHAMPS})[0]
    assert '`"new_rows": "create"`' in err and "ne fermait rien" in err, err


def test_un_ancien_reglage_DEJA_stocke_et_inchange_passe_et_se_dit_a_la_lecture():
    ancien = {"key": "siren", "key_required": True, "fields": CHAMPS}
    patche = {**ancien, "fields": [*CHAMPS, {"key": "ville", "type": "text"}]}
    assert S.validate_schema_def(patche, ancien) == []
    assert S.validate_schema_def({**patche, "key_required": False}, ancien)
    msg = cles_inconnues.residus_warning(ancien)
    assert "`key_required`" in msg and 'new_rows: "reject"' in msg, msg


# ── les paramètres de `data_patch_schema` ────────────────────────────────────

@pytest.mark.parametrize("params,rejeu", [
    ({"unknown_columns": "report"}, "rejoue `data_patch_schema` sans lui"),
    ({"strict": True}, "rejoue `data_patch_schema` sans lui"),
    ({"strict": True, "unknown_fields": "reject"}, "rejoue `data_patch_schema` sans eux"),
    ({"key_required": True}, 'rejoue `data_patch_schema` avec new_rows="reject"'),
    ({"key_required": False}, 'rejoue `data_patch_schema` avec new_rows="create"'),
])
def test_un_parametre_retire_est_refuse_avec_le_rejeu(params, rejeu):
    msg = R.refus_parametres(params)
    assert rejeu in msg and "rien n'a été écrit" in msg, msg
    if set(params) - {"key_required"}:
        assert PLUS_AUCUN in msg
    assert PatchSchemaInput.refus_champs_retires(params) == msg, "face REST, même texte"
    assert R.refus_parametres({"fields": []}) is None


def test_unknown_columns_n_est_plus_un_parametre_du_patch():
    assert "unknown_columns" not in PatchSchemaInput.model_fields


_mcp = FastMCP("banc-124")


@_mcp.tool()
def data_patch_schema(datastore: str, new_rows: str | None = None) -> dict:
    return {"ok": True}


def test_la_face_MCP_sert_le_meme_refus():
    try:
        asyncio.run(_mcp.call_tool("data_patch_schema",
                                   {"datastore": "v", "unknown_columns": "reject"}))
    except Exception as exc:  # noqa: BLE001 — c'est elle qu'on examine
        msg = T._arg_error_message(exc)
    else:
        raise AssertionError("l'appel aurait dû être refusé")
    assert PLUS_AUCUN in msg and "sans lui" in msg, msg
