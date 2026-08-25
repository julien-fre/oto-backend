"""Dispatch `op=` des 7 tools `airtable_*` + découpage en lots et pagination.

Airtable est un connecteur qui ÉCRIT dans une base de production d'un client (des
lignes, des colonnes, des tables). Trois familles de risques sont verrouillées ici :

1. **Routage** — une op mal câblée appelle silencieusement la mauvaise méthode du
   client et rien ne casse au boot. Chaque op a son cas : la méthode appelée, ET le
   mutisme des voisines destructrices.
2. **Défaut sûr** — le défaut de chaque tool à `op` est une lecture, et une op
   inconnue est refusée AVANT la résolution de la clé (donc avant tout appel réseau).
3. **Lots** — le plafond de 10 records par requête d'Airtable, le plafond de 200 par
   appel, le reçu partiel sur 429, l'abandon immédiat sur 401/403, et la pagination
   qui ANNONCE ce qu'elle n'a pas lu.

Le 4ᵉ invariant, propre à Airtable : `typecast` ne part JAMAIS tout seul. C'est une
mutation de schéma déclenchée par une écriture de donnée (il crée l'option manquante
d'un select) — un défaut à `True` élargirait en silence le schéma d'une base réelle.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import McpError

# Les 7 tools, avec les arguments que leur SCHÉMA rend obligatoires.
_TOOLS = {
    "airtable_record": {"base_id": "app1", "table": "tbl1"},
    "airtable_comment": {"base_id": "app1", "table": "tbl1", "record_id": "rec1"},
    "airtable_table": {"base_id": "app1"},
    "airtable_field": {"base_id": "app1", "table_id": "tbl1"},
    "airtable_base": {},
}
# `airtable_attachment` et `airtable_sync` n'ont PAS de paramètre `op` : un verbe
# unique n'a pas de verbe à choisir, et toute leur charge utile est obligatoire.
_OPLESS_TOOLS = ("airtable_attachment", "airtable_sync")

_WRITE_METHODS = (
    "create_records", "update_record", "update_records", "delete_record",
    "delete_records", "create_comment", "update_comment", "delete_comment",
    "create_table", "update_table", "create_field", "update_field",
    "create_base", "upload_attachment", "sync_csv",
)

_SCHEMA = {"tables": [
    {"id": "tbl1", "name": "Prospects", "primaryFieldId": "fld1",
     "fields": [{"id": "fld1", "name": "Nom", "type": "singleLineText"}],
     "views": []},
    {"id": "tbl2", "name": "Notes", "fields": [], "views": []},
]}


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import airtable as A

    m = FastMCP("t")
    A.register(m)
    return asyncio.run(m.get_tool(name)).fn


def _assert_no_stray_write(client, expected: str = ""):
    """Aucune écriture COLLATÉRALE : la seule méthode mutante touchée est l'attendue."""
    for name, _args, _kwargs in client.mock_calls:
        if name == expected:
            continue
        assert name.rsplit(".", 1)[-1] not in _WRITE_METHODS, (
            f"écriture collatérale : {name} (attendu : {expected or 'aucune'})")


@pytest.fixture
def client(monkeypatch):
    """Faux AirtableClient + clé résolue. `register()` importe la classe à l'appel,
    donc patcher le module oto-core AVANT suffit. `time.sleep` neutralisé : le délai
    de courtoisie est réel en prod, inutile en test."""
    import oto.tools.airtable.client as core

    inst = MagicMock()
    # Le plafond de lot est lu sur la CLASSE, pas sur l'instance mockée.
    inst.MAX_RECORDS_PER_REQUEST = core.AirtableClient.MAX_RECORDS_PER_REQUEST
    monkeypatch.setattr(core, "AirtableClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", False))
    monkeypatch.setattr("oto_mcp.tools.airtable.time.sleep", lambda *_: None)
    inst.list_records.return_value = {"records": []}
    inst.list_comments.return_value = {"comments": []}
    inst.list_bases.return_value = {"bases": [{"id": "app1", "name": "B"}]}
    inst.get_base_schema.return_value = _SCHEMA
    return inst


# ---------------------------------------------------------------------------
# Invariants de surface


def test_every_tool_has_a_description():
    """Un docstring écrit comme une f-string ne peuple pas `__doc__` : FastMCP
    publierait un tool SANS description, sans lever la moindre erreur."""
    from fastmcp import FastMCP
    from oto_mcp.tools import airtable as A

    m = FastMCP("t")
    A.register(m)
    for name in list(_TOOLS) + list(_OPLESS_TOOLS):
        tool = asyncio.run(m.get_tool(name))
        assert tool.description and len(tool.description) > 80, name


def test_default_op_never_writes(client):
    """Le défaut de CHAQUE tool à `op` est une lecture : un appel sans `op` ne peut
    ni écrire ni supprimer."""
    for name, required in _TOOLS.items():
        client.reset_mock()
        _tool(name)(**required)
        _assert_no_stray_write(client)


def test_unknown_op_refused_before_key_resolution(monkeypatch):
    """Une op inconnue est refusée AVANT `resolve_api_key` : elle n'atteint jamais le
    client, donc jamais, par un chemin dérivé, une écriture sur la base."""
    def _boom(*a, **k):
        raise AssertionError("resolve_api_key ne doit pas être atteint")

    monkeypatch.setattr("oto_mcp.access.resolve_api_key", _boom)
    for name, required in _TOOLS.items():
        with pytest.raises(McpError) as e:
            _tool(name)(op="destroy", **required)
        assert "op doit être" in str(e.value)


def test_opless_tools_have_no_op_parameter():
    """`airtable_attachment` / `airtable_sync` n'exposent pas de verbe : leur charge
    utile obligatoire EST l'intention explicite."""
    from fastmcp import FastMCP
    from oto_mcp.tools import airtable as A

    m = FastMCP("t")
    A.register(m)
    for name in _OPLESS_TOOLS:
        props = asyncio.run(m.get_tool(name)).parameters["properties"]
        assert "op" not in props, name


# ---------------------------------------------------------------------------
# airtable_record


def test_record_list_paginates_and_announces_the_rest(client):
    """La pagination suit l'`offset` opaque, et une liste incomplète le DIT.

    ⚠️ Le cœur du test est la TAILLE demandée à la dernière page. Avec un plafond de
    150 et des pages de 100, redemander 100 puis couper à 150 jetterait 50 lignes déjà
    lues — et l'`offset` rendu reprendrait APRÈS elles : la réponse annoncerait où
    reprendre tout en ayant fait disparaître ces 50 lignes. La 2ᵉ page doit donc
    demander 50, pas 100.
    """
    seq = {"n": 0}

    def _page(base_id, table, **kw):
        seq["n"] += 1
        size = kw["page_size"]
        return {"records": [{"id": f"rec{seq['n']}-{i}"} for i in range(size)],
                "offset": f"o{seq['n']}"}

    client.list_records.side_effect = _page
    out = _tool("airtable_record")(base_id="app1", table="tbl1", max_records=150)
    sizes = [c.kwargs["page_size"] for c in client.list_records.call_args_list]
    assert sizes == [100, 50], "la dernière page doit tomber PILE sur le plafond"
    assert out["count"] == 150 and len(out["records"]) == 150
    assert out["more"] is True and out["offset"] == "o2"
    assert client.list_records.call_args_list[1].kwargs["offset"] == "o1"
    _assert_no_stray_write(client)


def test_record_list_never_discards_a_page_it_already_read(client):
    """Aucune ligne lue n'est jetée : `count` == ce que le tool rend vraiment, et
    l'`offset` reprend juste après la dernière ligne rendue."""
    client.list_records.side_effect = [
        {"records": [{"id": f"rec{i}"} for i in range(40)]},  # pas d'offset : fini
    ]
    out = _tool("airtable_record")(base_id="app1", table="tbl1", max_records=150)
    assert out["count"] == 40 and "more" not in out and "offset" not in out
    assert client.list_records.call_args.kwargs["page_size"] == 100


def test_record_get_requires_record_id(client):
    with pytest.raises(McpError) as e:
        _tool("airtable_record")(base_id="app1", table="tbl1", op="get")
    assert "op='get' requiert record_id" in str(e.value)
    _assert_no_stray_write(client)


def test_record_create_solo_returns_the_record_directly(client):
    client.create_records.return_value = {"records": [{"id": "rec9"}]}
    out = _tool("airtable_record")(
        base_id="app1", table="tbl1", op="create", fields={"Nom": "Ada"})
    assert out == {"records": [{"id": "rec9"}]}
    assert client.create_records.call_args.args[2] == [{"fields": {"Nom": "Ada"}}]
    _assert_no_stray_write(client, "create_records")


def test_record_create_chunks_by_ten_and_returns_a_receipt(client):
    """21 records ⟹ 3 requêtes de 10/10/1, et un reçu qui compte ce qui est passé."""
    client.create_records.side_effect = lambda b, t, chunk, **k: {
        "records": [{"id": f"rec{i}"} for i in range(len(chunk))]}
    out = _tool("airtable_record")(
        base_id="app1", table="tbl1", op="create",
        records=[{"Nom": f"n{i}"} for i in range(21)])
    sizes = [len(c.args[2]) for c in client.create_records.call_args_list]
    assert sizes == [10, 10, 1]
    assert out["total"] == 21 and out["succeeded"] == 21 and out["failed"] == []
    _assert_no_stray_write(client, "create_records")


def test_record_create_rejects_both_or_neither(client):
    for kwargs in ({}, {"fields": {"a": 1}, "records": [{"a": 1}]}):
        with pytest.raises(McpError) as e:
            _tool("airtable_record")(base_id="app1", table="tbl1", op="create", **kwargs)
        assert "EXACTEMENT un" in str(e.value)
    _assert_no_stray_write(client)


def test_record_create_caps_items_per_call(client):
    with pytest.raises(McpError) as e:
        _tool("airtable_record")(
            base_id="app1", table="tbl1", op="create",
            records=[{"Nom": "x"} for _ in range(201)])
    assert "maximum de 200" in str(e.value)
    _assert_no_stray_write(client)


def test_record_update_many_requires_an_id_per_item(client):
    """Sans `id`, une ligne ne peut pas être rapprochée : on renvoie vers l'upsert
    plutôt que de deviner."""
    with pytest.raises(McpError) as e:
        _tool("airtable_record")(
            base_id="app1", table="tbl1", op="update", records=[{"Nom": "Ada"}])
    assert "op='upsert'" in str(e.value)
    _assert_no_stray_write(client)


def test_record_update_solo_patches_by_default(client):
    _tool("airtable_record")(
        base_id="app1", table="tbl1", op="update", record_id="rec1",
        fields={"Statut": "Signé"})
    assert client.update_record.call_args.kwargs["replace"] is False
    _assert_no_stray_write(client, "update_record")


def test_record_upsert_passes_fields_to_merge_on(client):
    client.update_records.return_value = {"records": [{"id": "rec1"}]}
    out = _tool("airtable_record")(
        base_id="app1", table="tbl1", op="upsert",
        records=[{"Email": "a@b.c", "Nom": "Ada"}], merge_on=["Email"])
    assert client.update_records.call_args.kwargs["perform_upsert"] == {
        "fieldsToMergeOn": ["Email"]}
    assert out["merged_on"] == ["Email"]
    _assert_no_stray_write(client, "update_records")


def test_record_upsert_refuses_more_than_three_merge_fields(client):
    with pytest.raises(McpError) as e:
        _tool("airtable_record")(
            base_id="app1", table="tbl1", op="upsert", records=[{"a": 1}],
            merge_on=["a", "b", "c", "d"])
    assert "1 à 3" in str(e.value)
    _assert_no_stray_write(client)


def test_record_delete_solo_and_batch(client):
    _tool("airtable_record")(base_id="app1", table="tbl1", op="delete", record_id="rec1")
    client.delete_record.assert_called_once_with("app1", "tbl1", "rec1")

    client.reset_mock()
    client.delete_records.side_effect = lambda b, t, chunk: {
        "records": [{"id": r, "deleted": True} for r in chunk]}
    out = _tool("airtable_record")(
        base_id="app1", table="tbl1", op="delete",
        record_ids=[f"rec{i}" for i in range(12)])
    assert [len(c.args[2]) for c in client.delete_records.call_args_list] == [10, 2]
    assert out["succeeded"] == 12


def test_typecast_is_off_unless_asked(client):
    """`typecast` est une mutation de schéma : il ne part jamais tout seul."""
    _tool("airtable_record")(
        base_id="app1", table="tbl1", op="create", fields={"Statut": "Signé"})
    assert client.create_records.call_args.kwargs["typecast"] is None

    client.reset_mock()
    _tool("airtable_record")(
        base_id="app1", table="tbl1", op="create", fields={"Statut": "Signé"},
        typecast=True)
    assert client.create_records.call_args.kwargs["typecast"] is True


# ---------------------------------------------------------------------------
# Lots : les trois régimes d'échec


def test_batch_aborts_on_rate_limit_with_a_partial_receipt(client):
    """429 : Airtable veut 30 s, le budget d'invoke est de 45 s. On s'arrête et on
    DIT ce qui est passé, au lieu de mourir en timeout sans reddition de comptes."""
    from oto.tools.common.errors import UpstreamHTTPError

    calls = {"n": 0}

    def _side(b, t, chunk, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise UpstreamHTTPError(429, "Too Many Requests", service="airtable")
        return {"records": [{"id": f"rec{i}"} for i in range(len(chunk))]}

    client.create_records.side_effect = _side
    out = _tool("airtable_record")(
        base_id="app1", table="tbl1", op="create",
        records=[{"Nom": f"n{i}"} for i in range(30)])
    assert out["aborted"] == "rate_limit"
    assert out["succeeded"] == 10 and out["total"] == 30
    assert "resume_hint" in out
    assert calls["n"] == 2, "on ne continue pas après un 429"


def test_batch_aborts_immediately_on_auth_error(client):
    """401/403 : la clé est mauvaise pour toute la suite — une seule requête, pas N."""
    from oto.tools.common.errors import UpstreamHTTPError

    client.create_records.side_effect = UpstreamHTTPError(
        403, "not authorized", service="airtable")
    with pytest.raises(McpError) as e:
        _tool("airtable_record")(
            base_id="app1", table="tbl1", op="create",
            records=[{"Nom": f"n{i}"} for i in range(30)])
    assert "scope" in str(e.value)
    assert client.create_records.call_count == 1


def test_batch_records_a_per_chunk_failure_and_continues(client):
    """Un 422 est propre à ce lot : on l'enregistre, on continue les suivants."""
    from oto.tools.common.errors import UpstreamHTTPError

    calls = {"n": 0}

    def _side(b, t, chunk, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise UpstreamHTTPError(422, "unknown field", service="airtable")
        return {"records": [{"id": f"rec{i}"} for i in range(len(chunk))]}

    client.create_records.side_effect = _side
    out = _tool("airtable_record")(
        base_id="app1", table="tbl1", op="create",
        records=[{"Nom": f"n{i}"} for i in range(25)])
    assert out["total"] == 25 and out["succeeded"] == 15
    assert out["failed"] == [{"items": [0, 9], "error": out["failed"][0]["error"]}]
    assert "aborted" not in out
    assert calls["n"] == 3


# ---------------------------------------------------------------------------
# airtable_comment


def test_comment_ops_route(client):
    t = _tool("airtable_comment")
    base = {"base_id": "app1", "table": "tbl1", "record_id": "rec1"}

    t(**base)
    client.list_comments.assert_called_once()
    _assert_no_stray_write(client)

    client.reset_mock()
    t(op="create", text="hello", **base)
    client.create_comment.assert_called_once()
    _assert_no_stray_write(client, "create_comment")

    client.reset_mock()
    t(op="update", comment_id="com1", text="edited", **base)
    client.update_comment.assert_called_once_with("app1", "tbl1", "rec1", "com1", "edited")
    _assert_no_stray_write(client, "update_comment")

    client.reset_mock()
    t(op="delete", comment_id="com1", **base)
    client.delete_comment.assert_called_once()
    _assert_no_stray_write(client, "delete_comment")


def test_comment_write_requires_its_arguments(client):
    base = {"base_id": "app1", "table": "tbl1", "record_id": "rec1"}
    for op, missing in (("create", "text"), ("update", "comment_id"), ("delete", "comment_id")):
        with pytest.raises(McpError) as e:
            _tool("airtable_comment")(op=op, **base)
        assert missing in str(e.value)
    _assert_no_stray_write(client)


# ---------------------------------------------------------------------------
# airtable_table / airtable_field


def test_table_schema_filters_to_one_table(client):
    out = _tool("airtable_table")(base_id="app1", table_id="tbl2")
    assert out["count"] == 1 and out["tables"][0]["id"] == "tbl2"
    _assert_no_stray_write(client)


def test_table_schema_names_what_exists_when_the_table_is_unknown(client):
    with pytest.raises(McpError) as e:
        _tool("airtable_table")(base_id="app1", table_id="tblNOPE")
    assert "aucune table" in str(e.value)
    _assert_no_stray_write(client)


def test_table_update_refuses_a_no_op(client):
    """`update` sans `name` ni `description` serait un PATCH qui ne change rien et
    passerait pour un succès."""
    with pytest.raises(McpError) as e:
        _tool("airtable_table")(base_id="app1", op="update", table_id="tbl1")
    assert "name" in str(e.value) and "description" in str(e.value)
    _assert_no_stray_write(client)


def test_field_list_returns_the_fields_of_that_table(client):
    out = _tool("airtable_field")(base_id="app1", table_id="Prospects")
    assert out["table"]["id"] == "tbl1"
    assert out["fields"][0]["name"] == "Nom"
    _assert_no_stray_write(client)


def test_field_list_lists_the_known_tables_on_a_miss(client):
    with pytest.raises(McpError) as e:
        _tool("airtable_field")(base_id="app1", table_id="tblNOPE")
    assert "Prospects (tbl1)" in str(e.value)
    _assert_no_stray_write(client)


def test_field_create_and_update(client):
    _tool("airtable_field")(
        base_id="app1", table_id="tbl1", op="create", name="Statut",
        type="singleSelect", options={"choices": [{"name": "Fait"}]})
    client.create_field.assert_called_once()
    _assert_no_stray_write(client, "create_field")

    client.reset_mock()
    with pytest.raises(McpError) as e:
        _tool("airtable_field")(
            base_id="app1", table_id="tbl1", op="update", field_id="fld1")
    assert "type" in str(e.value), "le refus doit dire que le type n'est pas modifiable"
    _assert_no_stray_write(client)


# ---------------------------------------------------------------------------
# airtable_base


def test_base_list_explains_an_empty_grant(client):
    """Un PAT valide sans base accordée rend 200 + liste vide : le mode d'échec le
    plus fréquent d'Airtable, et le seul qui ne ressemble pas à une erreur."""
    client.list_bases.return_value = {"bases": []}
    out = _tool("airtable_base")()
    assert out["count"] == 0
    assert "Access" in out["hint"]
    _assert_no_stray_write(client)


def test_base_whoami_and_create(client):
    _tool("airtable_base")(op="whoami")
    client.whoami.assert_called_once()
    _assert_no_stray_write(client)

    client.reset_mock()
    with pytest.raises(McpError) as e:
        _tool("airtable_base")(op="create", name="B")
    assert "workspace_id" in str(e.value)
    _assert_no_stray_write(client)


# ---------------------------------------------------------------------------
# airtable_attachment / airtable_sync


def test_attachment_upload(client):
    _tool("airtable_attachment")(
        base_id="app1", record_id="rec1", field="Fichiers",
        filename="d.pdf", content_type="application/pdf", file_base64="Zm9v")
    client.upload_attachment.assert_called_once_with(
        "app1", "rec1", "Fichiers",
        filename="d.pdf", content_type="application/pdf", file_b64="Zm9v")


def test_sync_csv_wraps_an_empty_response(client):
    """L'endpoint de sync peut répondre un corps vide : on rend un objet, pas None."""
    client.sync_csv.return_value = None
    out = _tool("airtable_sync")(
        base_id="app1", table="tbl1", sync_id="syn1", csv_data="a,b\n1,2\n")
    assert out == {"ok": True, "response": None}


# ---------------------------------------------------------------------------
# Garde du client (oto-core) exercée à travers le tool


def test_cell_format_string_requires_timezone_and_locale(monkeypatch):
    """La garde vit dans le client oto-core ; le tool la traduit en refus actionnable
    au lieu de laisser partir un 422 Airtable."""
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", False))
    with pytest.raises(McpError) as e:
        _tool("airtable_record")(base_id="app1", table="tbl1", cell_format="string")
    assert "time_zone" in str(e.value) and "user_locale" in str(e.value)


def test_batch_size_matches_the_core_client():
    """Le tool recopie le plafond de lot d'Airtable ; ce test est le fil qui casse si
    oto-core change la valeur sans que le découpage suive."""
    from oto.tools.airtable.client import AirtableClient
    from oto_mcp.tools import airtable as A

    assert A._BATCH_SIZE == AirtableClient.MAX_RECORDS_PER_REQUEST


def test_long_formula_switches_to_the_post_form(client):
    """Une `filterByFormula` énorme ne tient pas dans une query string : Airtable
    expose la même liste en POST. Le basculement est automatique — un agent n'a pas à
    connaître cette limite."""
    from oto_mcp.tools import airtable as A

    client.list_records_post.return_value = {"records": [{"id": "rec1"}]}
    formula = "OR(" + ",".join(f"{{Id}}='{i}'" for i in range(1500)) + ")"
    assert len(formula) > A._FORMULA_URL_LIMIT
    out = _tool("airtable_record")(
        base_id="app1", table="tbl1", filter_by_formula=formula)
    assert out["count"] == 1
    client.list_records.assert_not_called()
    assert client.list_records_post.call_args.args[2]["filterByFormula"] == formula
    _assert_no_stray_write(client)


def test_upsert_receipt_says_what_was_created_versus_matched(client):
    """La question qu'on pose à un upsert est « lesquelles étaient nouvelles ? ».
    Airtable y répond par `createdRecords`/`updatedRecords` ; un reçu de lot qui ne
    garderait que `records` compterait juste et ne répondrait pas."""
    def _side(b, t, chunk, **k):
        n = len(chunk)
        return {"records": [{"id": f"rec{i}"} for i in range(n)],
                "createdRecords": [f"rec{i}" for i in range(n // 2)],
                "updatedRecords": [f"rec{i}" for i in range(n // 2, n)]}

    client.update_records.side_effect = _side
    out = _tool("airtable_record")(
        base_id="app1", table="tbl1", op="upsert", merge_on=["Email"],
        records=[{"Email": f"{i}@b.c"} for i in range(12)])
    assert out["succeeded"] == 12
    # 2 lots (10 + 2) : 5+1 créées, 5+1 rapprochées — les DEUX lots sont cumulés.
    assert out["created"] == 6 and out["updated"] == 6
    assert len(out["createdRecords"]) == 6 and len(out["updatedRecords"]) == 6


def test_plain_batch_receipt_carries_no_empty_upsert_keys(client):
    """Un create ordinaire ne doit pas traîner des clés d'upsert vides."""
    client.create_records.side_effect = lambda b, t, chunk, **k: {
        "records": [{"id": f"rec{i}"} for i in range(len(chunk))]}
    out = _tool("airtable_record")(
        base_id="app1", table="tbl1", op="create", records=[{"Nom": "a"}])
    assert "createdRecords" not in out and "updatedRecords" not in out
