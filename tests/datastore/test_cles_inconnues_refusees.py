"""Une clé de schéma qu'aucun niveau n'admet est REFUSÉE (01/10/2026 — oto#34/#35/#127).

Le vocabulaire était ouvert : on SIGNALAIT. Mesuré le 01/10 sur 442 tableaux à schéma,
45 portaient des clés que personne ne lisait. Désormais chaque niveau (tête, colonne,
sous-champ, élément de liste, bloc `lifecycle`) déclare ce qu'il admet, et le reste se
refuse — à la pose comme au patch, sur les deux faces, puisque les deux passent par
`validate_schema_def`.

Ce banc garde les trois propriétés du lot :

1. le refus NOMME le chemin, la clé, et où elle va (la plus proche, l'autre niveau, le
   paramètre d'appel, `description` pour un texte d'aide, `meta` pour le reste) ;
2. `meta` est la porte : un objet, transporté, jamais lu, borné en taille ;
3. ⚠️ le refus porte sur ce que le GESTE pose ou modifie, jamais sur ce qui est déjà
   stocké : le patch d'une autre colonne reste possible sur un tableau pas migré.
"""
from __future__ import annotations

import copy
import uuid

import pytest

from oto_mcp.datastore import cles_inconnues as C
from oto_mcp.datastore import schema_keys as K
from oto_mcp.datastore.definition import validate_schema_def


def _un_refus(schema, ancien=None) -> str:
    errs = validate_schema_def(schema, ancien)
    assert len(errs) == 1, errs
    return errs[0]


# ── ① par niveau : le refus, son chemin, et la clé proche ────────────────────

@pytest.mark.parametrize("schema,chemin,cle,proche", [
    ({"unknown_column": "report", "fields": [{"key": "a"}]}, "tête", "unknown_column",
     "unknown_columns"),
    ({"fields": [{"key": "a", "read_only": True}]}, "fields.a", "read_only", "readonly"),
    ({"fields": [{"key": "o", "type": "object",
                  "fields": [{"key": "y", "requird": True}]}]},
     "fields.o.fields.y", "requird", "required"),
    ({"fields": [{"key": "l", "type": "list", "of": {"type": "enum", "enum": ["a"]}}]},
     "fields.l.of", "enum", "options"),
    ({"fields": [{"key": "s", "lifecycle": {"states": ["a"], "transition": {}}}]},
     "fields.s.lifecycle", "transition", "transitions"),
])
def test_chaque_niveau_refuse_et_nomme_la_cle_proche(schema, chemin, cle, proche):
    msg = _un_refus(schema)
    assert msg.startswith(f"{chemin} : `{cle}` n'est pas admise"), msg
    assert f"voulais-tu `{proche}` ?" in msg, msg


def test_une_cle_d_un_AUTRE_niveau_est_renvoyee_a_son_domicile():
    """« Inconnue » serait faux : elle existe, ailleurs. Le refus dit où."""
    assert "`lifecycle.states`" in _un_refus(
        {"fields": [{"key": "s", "states": ["a"]}]})
    assert "réglage de TÊTE" in _un_refus(
        {"fields": [{"key": "s", "unknown_columns": "report"}]})
    assert "attribut de COLONNE" in _un_refus({"readonly": True})
    msg = _un_refus({"fields": [{"key": "o", "type": "object",
                                 "fields": [{"key": "y", "readonly": True}]}]})
    assert "ne se pose qu'au premier niveau" in msg and "`readonly`" in msg


def test_un_PARAMETRE_d_appel_range_dans_le_schema_a_sa_phrase():
    msg = _un_refus({"semantic_search": True})
    assert "PARAMÈTRE de l'appel" in msg and "data_set_schema(semantic_search=" in msg


def test_origine_est_refusee_comme_cle_RETIREE():
    msg = _un_refus({"fields": [{"key": "a", "origine": "system"}]})
    assert "retirée le 08/09/2026" in msg and "donnees_d_origine" in msg


def test_sans_parente_le_refus_liste_le_niveau_et_ouvre_meta():
    msg = _un_refus({"fields": [{"key": "a", "explained_by": "b"}]})
    assert "les clés admises ici sont" in msg and "`meta`" in msg


@pytest.mark.parametrize("aide", K.TEXTES_D_AIDE)
def test_les_textes_d_aide_sont_refuses_et_renvoient_a_description(aide):
    for schema in ({"fields": [{"key": "a", aide: "x"}]},
                   {"fields": [{"key": "o", "type": "object",
                                "fields": [{"key": "y", aide: "x"}]}]}):
        msg = _un_refus(schema)
        assert f"`{aide}`" in msg and "`description`" in msg, msg
    assert validate_schema_def({"fields": [{"key": "a", "description": "x"}]}) == []


def test_ce_que_le_dashboard_lit_est_admis():
    """Vérifié sur `oto-dashboard` (origin/main, 01/10/2026) avant de fermer : ce
    qu'un écran lit n'est pas inconnu."""
    propre = {"key": "k", "unknown_columns": "report", "description": "d", "fields": [
        {"key": "k", "type": "text", "label": "K", "hidden": True, "width": "half",
         "display": "title", "role": "badge", "description": "aide"},
        {"key": "s", "type": "enum", "options": ["a", "b"], "role": "status",
         "required_when": {"k": "x"},
         "lifecycle": {"states": ["a", "b"], "transitions": {"a": ["b"]},
                       "terminal": ["b"], "labels": {"a": "À faire"}}},
        {"key": "o", "type": "object", "fields": [
            {"key": "n", "type": "text", "label": "N", "width": "full"}]},
        {"key": "l", "type": "list", "of": {"type": "object", "key": "n", "fields": [
            {"key": "n", "type": "text", "label": "N", "required": True}]}},
    ]}
    assert validate_schema_def(propre) == []


# ── ② `meta`, la zone libre ──────────────────────────────────────────────────

def test_meta_est_admis_a_chaque_niveau_et_transporte_tel_quel():
    m = {"depends_on": ["x"], "read_only": True}
    schema = {"meta": m, "fields": [
        {"key": "a", "meta": m},
        {"key": "o", "type": "object", "fields": [{"key": "y", "meta": m}]},
        {"key": "l", "type": "list", "of": {"type": "text", "meta": m}},
        {"key": "s", "lifecycle": {"states": ["a"], "meta": m}}]}
    assert validate_schema_def(schema) == []


def test_meta_doit_etre_un_objet_borne():
    assert "doit être un objet" in _un_refus({"fields": [{"key": "a", "meta": [1]}]})
    trop = {"x": "é" * K.META_MAX_OCTETS}
    msg = _un_refus({"meta": trop})
    assert f"au plus {K.META_MAX_OCTETS}" in msg
    juste = {"x": "a" * (K.META_MAX_OCTETS - len('{"x":""}'))}
    assert K.taille_json(juste) == K.META_MAX_OCTETS
    assert validate_schema_def({"meta": juste}) == []


def test_meta_ne_rend_rien_actif():
    """`read_only` rangé dans `meta` reste inerte : aucun verrou ne naît de là."""
    from oto_mcp.datastore import schema as dsv2
    s = {"fields": [{"key": "a", "meta": {"readonly": True}}]}
    assert "a" not in dsv2.readonly_fields(s)


# ── ③ ce qui est STOCKÉ n'est pas posé par le geste ─────────────────────────

ANCIEN = {"fields": [
    {"key": "a", "type": "text", "note": "aide d'avant", "editable": True},
    {"key": "b", "type": "text"}]}


def test_une_cle_stockee_INCHANGEE_passe():
    nouveau = copy.deepcopy(ANCIEN)
    nouveau["fields"][1]["max_length"] = 20          # un AUTRE champ bouge
    assert validate_schema_def(nouveau, ANCIEN) == []


def test_une_cle_stockee_MODIFIEE_est_refusee():
    nouveau = copy.deepcopy(ANCIEN)
    nouveau["fields"][0]["note"] = "aide d'après"
    assert "`note`" in _un_refus(nouveau, ANCIEN)


def test_la_meme_cle_ailleurs_est_un_geste():
    """Le chemin compte : `note` stockée sur `a` n'autorise pas `note` sur `b`."""
    nouveau = copy.deepcopy(ANCIEN)
    nouveau["fields"][1]["note"] = "aide d'avant"
    assert _un_refus(nouveau, ANCIEN).startswith("fields.b : `note`")


def test_le_residu_stocke_se_DIT():
    w = C.residus_warning({"fields": [{"key": "s", "type": "enum",
                                       "enum": ["a"], "options": ["a", "b"]}]})
    assert "`fields.s.enum`" in w and "Ce qui fait foi : `options`" in w
    assert C.residus_warning({"fields": [{"key": "s", "options": ["a"]}]}) is None


# ── la pose et le patch RÉELS, sur une base ─────────────────────────────────

def _store(monkeypatch, ns_id):
    from oto_mcp.datastore.core import DatastorePg
    st = DatastorePg("u-cles")
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: ns_id)
    return st


def _tableau(schema) -> int:
    from oto_mcp import db
    ns_id = db.create_datastore("user", "u-cles", "cles-" + uuid.uuid4().hex[:6])
    db.set_datastore_schema(ns_id, schema)        # posé AVANT la fermeture
    return ns_id


def test_le_patch_d_un_AUTRE_champ_passe_sur_un_schema_ancien(live, monkeypatch):
    from oto_mcp import db
    ns_id = _tableau(ANCIEN)
    out = _store(monkeypatch, ns_id).patch_schema(
        str(ns_id), fields=[{"key": "b", "label": "Bé"}])
    stocke = db.get_datastore_by_id(ns_id)["schema"]
    assert stocke["fields"][0]["note"] == "aide d'avant", "le résidu n'est pas touché"
    assert stocke["fields"][1]["label"] == "Bé"
    assert "`fields.a.note`" in out["warning"], "et il se dit à l'auteur"


def test_le_patch_qui_POSE_une_cle_inconnue_est_refuse(live, monkeypatch):
    from oto_mcp import db
    from oto_mcp.datastore.errors import SchemaDefinitionError
    ns_id = _tableau(ANCIEN)
    st = _store(monkeypatch, ns_id)
    with pytest.raises(SchemaDefinitionError, match="fields.b : `hint`"):
        st.patch_schema(str(ns_id), fields=[{"key": "b", "hint": "h"}])
    with pytest.raises(SchemaDefinitionError, match="fields.a : `note`"):
        st.patch_schema(str(ns_id), fields=[{"key": "a", "note": "autre"}])
    assert db.get_datastore_by_id(ns_id)["schema"] == ANCIEN, "rien n'est écrit"
    # Le geste qui range : retirer l'attribut, poser `meta`.
    st.patch_schema(str(ns_id), remove_attrs={"a": ["note", "editable"]},
                    fields=[{"key": "a", "description": "aide d'avant",
                             "meta": {"editable": True}}])
    assert C.residus_warning(db.get_datastore_by_id(ns_id)["schema"]) is None


def test_la_face_REST_rend_le_refus_en_400(live, monkeypatch):
    from _datastore_rest import call, stub_authz
    from oto_mcp.datastore import core as dsm
    stub_authz(monkeypatch)
    ns_id = _tableau(None)
    monkeypatch.setattr(dsm.DatastorePg, "_resolve", lambda self, ns, write=False: ns_id)
    code, corps = call("me.datastore.set_schema", path_params={"datastore": str(ns_id)},
                       body={"schema": {"fields": [{"key": "a", "read_only": True}]}})
    assert code == 400 and corps["error"] == "invalid_schema", corps
    assert "fields.a : `read_only`" in corps["detail"]
    assert "voulais-tu `readonly` ?" in corps["detail"]
    code, corps = call("me.datastore.patch_schema", path_params={"datastore": str(ns_id)},
                       body={"fields": [{"key": "a", "editable": True}]})
    assert code == 400 and "fields.a : `editable`" in corps["detail"], corps


def test_la_route_des_cles_sert_les_CINQ_niveaux(monkeypatch):
    from _datastore_rest import call, stub_authz
    stub_authz(monkeypatch)
    code, corps = call("datastore.schema_keys")
    assert code == 200
    assert set(corps["levels"]) == {"head", "field", "subfield", "item", "lifecycle"}
    assert corps["keys"] == corps["levels"]["field"]
    assert corps["meta_max_bytes"] == K.META_MAX_OCTETS
    for niveau, entrees in corps["levels"].items():
        assert "meta" in {e["key"] for e in entrees}, niveau
    assert not {e["key"] for e in corps["keys"]} & (set(K.TEXTES_D_AIDE) | {"origine"})


# ── le schéma cible d'un slot de procédure suit la même règle ────────────────

SLOT_ANCIEN = [{"name": "vivier", "type": "tableau",
                "schema": {"fields": [{"key": "a", "note": "aide d'avant"},
                                      {"key": "b"}]}}]


def test_un_slot_neuf_porteur_d_une_cle_inconnue_est_refuse():
    from oto_mcp.slots import validate_slots
    with pytest.raises(ValueError, match="fields.a : `note`"):
        validate_slots(copy.deepcopy(SLOT_ANCIEN))


def test_un_slot_deja_stocke_reste_enregistrable_tant_qu_on_n_y_touche_pas():
    """Une procédure pas encore migrée se réenregistre (corps modifié, slot repris
    tel quel) ; modifier la clé inconnue, ou en poser une, reste refusé."""
    from oto_mcp.slots import validate_slots
    repris = copy.deepcopy(SLOT_ANCIEN)
    repris[0]["schema"]["fields"][1]["label"] = "Bé"
    assert validate_slots(repris, SLOT_ANCIEN)[0]["schema"] == repris[0]["schema"]
    change = copy.deepcopy(SLOT_ANCIEN)
    change[0]["schema"]["fields"][0]["note"] = "autre"
    with pytest.raises(ValueError, match="fields.a : `note`"):
        validate_slots(change, SLOT_ANCIEN)
    autre_slot = [{**copy.deepcopy(SLOT_ANCIEN[0]), "name": "autre"}]
    with pytest.raises(ValueError, match="`note`"):
        validate_slots(autre_slot, SLOT_ANCIEN)


def test_la_surface_procedure_juge_le_slot_contre_la_procedure_STOCKEE(monkeypatch):
    """Le câblage : `_write_instruction` passe les slots en place à `validate_slots`
    — sans quoi la règle du slot existerait et ne servirait jamais."""
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.orgs import instructions as oi
    ecrit: dict = {}
    monkeypatch.setattr(oi.org_store, "get_instruction",
                        lambda o, i, s, version=None: (
                            {"slots": SLOT_ANCIEN, "body_md": "x", "title": "",
                             "description": ""} if version is None
                            else {"body_md": ecrit.get("body_md", "")}))

    def _set_instruction(otype, oid, slug, body_md, **kw):
        ecrit.update(body_md=body_md, slots=kw.get("slots"))
        return 2
    monkeypatch.setattr(oi.org_store, "set_instruction", _set_instruction)
    oi._write_instruction(ResolvedCtx(sub="u-proc", org_id=3),
                          oi.ConsoleInstrSetInput(slug="p", scope="org",
                                                  body_md="1. Corps modifié.",
                                                  slots=copy.deepcopy(SLOT_ANCIEN)))
    assert ecrit["slots"][0]["schema"] == SLOT_ANCIEN[0]["schema"]
