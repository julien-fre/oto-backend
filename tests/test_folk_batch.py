"""folk_record en lot par les endpoints batch natifs de Folk (`…/batch/create|update`,
octobre 2026) : UNE requête pour le lot, un résultat par fiche envoyée, dans l'ordre.

Verrouille : la validation par item AVANT l'envoi (un item refusé n'est pas envoyé et
garde son index d'origine), le report des résultats de Folk sur les bons index, la
relecture + fusion des `customFieldValues` / groupes avant l'écriture (Folk remplace
les deux), le refus d'une requête entière (422 → tout le lot envoyé échoue, 401 →
lève), `dry_run` qui reste la boucle sans écriture, et la cadence laissée au client
(oto-core), que le serveur ne fixe pas.
"""
import asyncio
from unittest.mock import patch

import pytest

from oto_mcp.mcp_errors import McpError


def _appel(**kwargs):
    from fastmcp import FastMCP
    from oto_mcp.tools import folk as folk_tool

    m = FastMCP("t")
    folk_tool.register(m)
    return asyncio.run(m.get_tool("folk_record")).fn(**kwargs)


@pytest.fixture(autouse=True)
def _cle(monkeypatch):
    monkeypatch.setattr(
        "oto_mcp.access.resolve_api_key", lambda provider, account=None: ("k", False))


@pytest.fixture
def inst():
    with patch("oto.tools.folk.client.FolkClient") as cls:
        c = cls.return_value
        for corps in ("person_body", "company_body", "deal_body"):
            getattr(c, corps).side_effect = lambda **kw: dict(kw)
        yield c


def _ok(recs, **extra):
    return [{"status": "succeeded", "data": {"id": f"id_{k}"}, **extra}
            for k, _ in enumerate(recs)]


# --- create -----------------------------------------------------------------------

def test_un_lot_de_creation_est_une_seule_requete(inst):
    inst.create_people_batch.side_effect = _ok
    r = _appel(op="create", entity="person",
               items=[{"first_name": "A"}, {"first_name": "B"}])
    inst.create_people_batch.assert_called_once_with(
        [{"first_name": "A"}, {"first_name": "B"}])
    inst.create_person.assert_not_called()
    assert r == {"total": 2, "succeeded": 2, "failed": [],
                 "created": [{"index": 0, "id": "id_0"}, {"index": 1, "id": "id_1"}]}


def test_un_item_refuse_n_est_pas_envoye_et_garde_son_index(inst):
    """L'item 1 est refusé ici (champ inconnu) : seuls 0 et 2 partent, et le
    résultat de Folk pour le 2e enregistrement ENVOYÉ revient sur l'index 2."""
    inst.create_people_batch.side_effect = lambda recs: [
        {"status": "succeeded", "data": {"id": "per_A"}},
        {"status": "failed", "error": {"code": "invalid", "message": "bad email"}}]
    r = _appel(op="create", entity="person", items=[
        {"first_name": "A"}, {"firstName": "B"}, {"first_name": "C", "emails": ["x"]}])
    envoyes = inst.create_people_batch.call_args.args[0]
    assert [e["first_name"] for e in envoyes] == ["A", "C"]
    assert r["created"] == [{"index": 0, "id": "per_A"}]
    assert [f["index"] for f in r["failed"]] == [1, 2]
    assert "firstName" in r["failed"][0]["error"]
    assert r["failed"][1]["error"] == "bad email"


def test_une_societe_cree_porte_ses_groupes_et_champs_et_dit_quand_folk_fusionne(inst):
    inst.create_companies_batch.side_effect = lambda recs: [
        {"status": "succeeded", "data": {"id": "com_1"}, "operation": "created"},
        {"status": "succeeded", "data": {"id": "com_2"}, "operation": "updated"}]
    r = _appel(op="create", entity="company", items=[
        {"name": "Acme", "urls": ["https://acme.test"],
         "custom_fields": {"grp_1": {"Tier": "A"}}},
        {"name": "Beta", "group_ids": ["grp_2"]}])
    envoyes = inst.create_companies_batch.call_args.args[0]
    # Le groupe d'un champ personnalisé rejoint `groups` : Folk l'exige.
    assert envoyes[0] == {"name": "Acme", "urls": ["https://acme.test"],
                          "groups": [{"id": "grp_1"}],
                          "customFieldValues": {"grp_1": {"Tier": "A"}}}
    assert envoyes[1] == {"name": "Beta", "groups": [{"id": "grp_2"}]}
    assert r["created"] == [{"index": 0, "id": "com_1"},
                            {"index": 1, "id": "com_2", "operation": "updated"}]


def test_la_creation_solo_d_une_societe_prend_aussi_groupes_et_champs(inst):
    inst.create_company.return_value = {"id": "com_1"}
    _appel(op="create", entity="company",
           item={"name": "Acme", "group_ids": ["grp_1"],
                 "custom_fields": {"grp_1": {"Status": "New"}}})
    inst.create_company.assert_called_once_with(
        name="Acme", groups=[{"id": "grp_1"}],
        customFieldValues={"grp_1": {"Status": "New"}})


def test_des_custom_fields_mal_formes_sont_refuses_en_nom(inst):
    with pytest.raises(McpError, match="custom_fields"):
        _appel(op="create", entity="company",
               item={"name": "Acme", "custom_fields": {"grp_1": "A"}})
    inst.create_company.assert_not_called()


def test_un_lot_de_deals_vise_son_groupe(inst):
    inst.create_deals_batch.side_effect = lambda gid, recs, object_type: _ok(recs)
    _appel(op="create", entity="deal", group_id="grp_d", items=[{"name": "D1"}])
    inst.create_deals_batch.assert_called_once_with(
        "grp_d", [{"name": "D1"}], object_type="deals")


def test_un_refus_de_la_requete_entiere_fait_echouer_tout_le_lot_envoye(inst):
    from oto.tools.common.errors import UpstreamHTTPError
    inst.create_people_batch.side_effect = UpstreamHTTPError(
        422, {"message": "malformed"}, service="folk")
    r = _appel(op="create", entity="person",
               items=[{"first_name": "A"}, {"first_name": "B"}])
    assert r["succeeded"] == 0 and [f["index"] for f in r["failed"]] == [0, 1]


def test_une_erreur_d_auth_leve(inst):
    from oto.tools.common.errors import UpstreamHTTPError
    inst.create_people_batch.side_effect = UpstreamHTTPError(
        401, {"message": "bad key"}, service="folk")
    with pytest.raises(UpstreamHTTPError):
        _appel(op="create", entity="person", items=[{"first_name": "A"}])


def test_le_dry_run_reste_la_boucle_et_n_ecrit_rien(inst):
    r = _appel(op="create", entity="person", items=[{"first_name": "A"}],
               dry_run=True)
    inst.create_people_batch.assert_not_called()
    assert r["would_create"] == [{"index": 0, "would_create": {"first_name": "A"}}]


def test_plus_de_50_items_est_refuse_avant_tout_appel(inst):
    with pytest.raises(McpError, match="max 50"):
        _appel(op="create", entity="person",
               items=[{"first_name": str(i)} for i in range(51)])
    inst.create_people_batch.assert_not_called()


# --- update / add_to_group --------------------------------------------------------

def test_un_lot_de_maj_relit_fusionne_puis_ecrit_en_une_requete(inst):
    """`Tier` change, `Status` (non fourni) survit : la fusion du PATCH solo vaut
    pour le lot."""
    inst.get_company.side_effect = lambda cid: {
        "id": cid, "groups": [{"id": "g1"}],
        "customFieldValues": {"g1": {"Tier": "C", "Status": "New"}}}
    inst.update_companies_batch.side_effect = _ok
    r = _appel(op="update", entity="company", items=[
        {"id": "com_1", "fields": {"customFieldValues": {"g1": {"Tier": "A"}}}},
        {"id": "com_2", "fields": {"customFieldValues": {"g1": {"Tier": "B"}}}}])
    assert r == {"total": 2, "succeeded": 2, "failed": []}
    inst.update_company.assert_not_called()
    envoyes = inst.update_companies_batch.call_args.args[0]
    assert envoyes == [
        {"id": "com_1", "customFieldValues": {"g1": {"Tier": "A", "Status": "New"}}},
        {"id": "com_2", "customFieldValues": {"g1": {"Tier": "B", "Status": "New"}}}]


def test_des_champs_ordinaires_ne_relisent_rien(inst):
    inst.update_people_batch.side_effect = _ok
    _appel(op="update", entity="person",
           items=[{"id": "per_1", "fields": {"jobTitle": "CTO"}}])
    inst.get_person.assert_not_called()
    inst.update_people_batch.assert_called_once_with(
        [{"id": "per_1", "jobTitle": "CTO"}])


def test_un_item_sans_id_ou_illisible_echoue_seul(inst):
    inst.get_company.side_effect = lambda cid: None
    inst.update_companies_batch.side_effect = _ok
    r = _appel(op="update", entity="company", items=[
        {"fields": {"name": "x"}},
        {"id": "com_2", "fields": {"customFieldValues": {"g1": {"Tier": "A"}}}},
        {"id": "com_3", "fields": {"name": "y"}}])
    assert [(f["index"], f["id"]) for f in r["failed"]] == [(0, None), (1, "com_2")]
    assert inst.update_companies_batch.call_args.args[0] == [{"id": "com_3", "name": "y"}]
    assert r["succeeded"] == 1


def test_un_echec_par_fiche_de_folk_revient_sur_son_id(inst):
    inst.update_people_batch.side_effect = lambda recs: [
        {"status": "failed", "error": {"message": "not found"}},
        {"status": "succeeded", "data": {"id": "per_2"}}]
    r = _appel(op="update", entity="person", items=[
        {"id": "per_404", "fields": {"jobTitle": "X"}},
        {"id": "per_2", "fields": {"jobTitle": "Y"}}])
    assert r == {"total": 2, "succeeded": 1,
                 "failed": [{"index": 0, "id": "per_404", "error": "not found"}]}


def test_add_to_group_en_lot_unit_les_groupes_et_ecrit_une_fois(inst):
    inst.get_person.side_effect = lambda pid: {"id": pid, "groups": [{"id": "g1"}]}
    inst.update_people_batch.side_effect = _ok
    r = _appel(op="add_to_group", entity="person", ids=["per_1", "per_2"],
               group_id="g2")
    assert r == {"total": 2, "succeeded": 2, "failed": []}
    assert inst.update_people_batch.call_args.args[0] == [
        {"id": "per_1", "groups": [{"id": "g1"}, {"id": "g2"}]},
        {"id": "per_2", "groups": [{"id": "g1"}, {"id": "g2"}]}]
    inst.update_person.assert_not_called()


def test_une_note_reste_la_boucle(inst):
    inst.update_note.return_value = {"id": "nte_1"}
    r = _appel(op="update", entity="note",
               items=[{"id": "nte_1", "fields": {"content": "x"}}])
    assert r["succeeded"] == 1
    inst.update_note.assert_called_once_with("nte_1", content="x")


# --- cadence ----------------------------------------------------------------------

def test_le_serveur_garde_la_cadence_par_defaut_du_client():
    """La cadence est celle du client (oto-core `oto/tools/folk/pacing.py`) : le
    serveur construit `FolkClient` sans fixer `min_interval_s`, donc avec le défaut
    de la lib, partagé par clé entre threads."""
    with patch("oto.tools.folk.client.FolkClient") as cls:
        cls.return_value.list_groups.return_value = []
        from fastmcp import FastMCP
        from oto_mcp.tools import folk as folk_tool

        m = FastMCP("t")
        folk_tool.register(m)
        asyncio.run(m.get_tool("folk_group")).fn(op="list")
    assert cls.call_args is not None
    assert "min_interval_s" not in cls.call_args.kwargs
    assert "pacer" not in cls.call_args.kwargs


def test_le_lot_ne_remplace_plus_la_methode_du_client():
    """Plus de régulateur côté serveur : `_bulk_run` n'enveloppe plus `_request`."""
    import inspect

    from oto_mcp.tools import folk

    assert "client" not in inspect.signature(folk._bulk_run).parameters
    assert not hasattr(folk, "_RateLimiter")
    assert not hasattr(folk, "_lot_disponible")
