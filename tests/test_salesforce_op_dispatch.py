"""Dispatch `op=` des 4 tools `salesforce_*` (ADR 0047 §Amendement, appliqué au
connecteur salesforce le 2026-08-11 : 13 tools → 4).

Ce que ce fichier verrouille, et que les autres tests salesforce ne couvraient PAS :
ils exercent des helpers purs (`_project_describe`, `_validate_bulk_items`,
`_bulk_receipt`, `_rotation_writer`) ou le flux OAuth — aucun ne touchait la
SURFACE. Une consolidation par `op=` déplace précisément le risque là : une op mal
câblée appelle silencieusement la mauvaise méthode du client, et rien ne casse au
boot.

⚠️ Ici la mauvaise méthode ÉCRIT dans le CRM d'un client. D'où trois exigences, en
plus du routage de chaque op :

- **aucune écriture atteignable par défaut** — le défaut d'`op` de chaque tool est
  une lecture, vérifié sur la signature réelle ;
- **chaque op d'écriture a son cas**, qui vérifie la méthode appelée ET le mutisme
  des voisines dangereuses (`assert_not_called`) — un `delete` câblé sur `update`
  passerait tous les tests « la bonne méthode a été appelée » pris isolément ;
- **arguments obligatoires** → refus qui NOMME l'op et l'argument, jamais un
  fallback (un `record_id` absorbé en silence viserait la collection entière).
"""
import asyncio
import inspect
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import salesforce as S

# Toutes les méthodes du client qui MODIFIENT le CRM. Sert de témoin de mutisme :
# chaque cas d'écriture prouve que ses voisines sont restées silencieuses.
_WRITE_METHODS = ("create_record", "update_record", "delete_record",
                  "upsert_record", "create_records", "update_records",
                  "create_note")


def _tool(name: str):
    from fastmcp import FastMCP

    m = FastMCP("t")
    S.register(m)
    return asyncio.run(m.get_tool(name)).fn


def _only_write(client, method: str) -> None:
    """La méthode attendue a été appelée UNE fois, et aucune autre écriture."""
    getattr(client, method).assert_called_once()
    for autre in _WRITE_METHODS:
        if autre != method:
            getattr(client, autre).assert_not_called()


@pytest.fixture
def client(monkeypatch):
    """Faux `SalesforceClient` + credential résolu.

    `register()` importe la classe DANS son corps (`from oto.tools.salesforce.client
    import SalesforceClient`) : patcher l'attribut du module amont AVANT `_tool()`
    suffit, et évite de dupliquer la plomberie de `_client()` (résolution de
    l'entité gagnante + branchement de la rotation)."""
    import oto.tools.salesforce.client as sf_client

    from oto_mcp import access

    inst = MagicMock()
    monkeypatch.setattr(sf_client, "SalesforceClient", lambda **kw: inst)

    class _RC:
        # `entity_type=None` = pas de ligne de coffre à réécrire : le writer de
        # rotation reste inerte, ce test ne parle pas de persistance.
        entity_type, entity_id, account = None, None, ""
        fields = {"client_id": "ci", "client_secret": "cs", "refresh_token": "rt",
                  "login_url": "https://x.my.salesforce.com"}

    monkeypatch.setattr(access, "resolve_credential", lambda *a, **k: _RC())
    return inst


# --- l'enregistrement : ce que le connecteur expose ----------------------------

def test_la_surface_est_exactement_ces_quatre_tools(client):
    """Tripwire de consolidation : un tool ressuscité (ou perdu) casse ici, pas en
    prod. `salesforce_describe` reste seul — découverte d'un TYPE, pas d'une ligne."""
    from fastmcp import FastMCP

    m = FastMCP("t")
    S.register(m)
    assert {t.name for t in asyncio.run(m.list_tools())} == {
        "salesforce_describe", "salesforce_record", "salesforce_query",
        "salesforce_note"}


# --- INVARIANT ÉCRITURE : rien de destructeur n'est atteignable par défaut ------

def test_aucune_ecriture_nest_atteignable_par_defaut(client):
    """Un appel qui OMET `op` ne doit pouvoir ni écrire, ni supprimer. Vérifié sur
    la signature réelle plutôt que sur la doc : c'est la signature qui décide."""
    from fastmcp import FastMCP

    m = FastMCP("t")
    S.register(m)
    for nom, lectures in (("salesforce_record", S._RECORD_READ_OPS),
                          ("salesforce_query", S._QUERY_OPS),
                          ("salesforce_note", S._NOTE_READ_OPS)):
        fn = asyncio.run(m.get_tool(nom)).fn
        defaut = inspect.signature(fn).parameters["op"].default
        assert defaut in lectures, f"{nom} : le défaut d'op n'est plus une lecture"


def test_le_defaut_de_record_liste_et_ne_touche_a_rien_dautre(client):
    _tool("salesforce_record")(sobject="Contact")
    client.list_records.assert_called_once()
    for dangereuse in _WRITE_METHODS:
        getattr(client, dangereuse).assert_not_called()


def test_le_defaut_de_note_liste(client):
    client.list_notes.return_value = [{"Id": "069a"}]
    assert _tool("salesforce_note")(record_id="003a") == {"notes": [{"Id": "069a"}]}
    client.create_note.assert_not_called()


def test_le_defaut_de_query_est_soql(client):
    _tool("salesforce_query")(query="SELECT Id FROM Account")
    client.query.assert_called_once()
    client.search.assert_not_called()


# --- salesforce_record : les lectures ------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_records"),
    ("get", {"record_id": "003a"}, "get_record"),
])
def test_record_read_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("salesforce_record")(sobject="Contact", op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_record_list_transmet_le_faconnage_soql(client):
    """`fields` / `where` / `limit` ne servent qu'ici : la fusion ne doit pas les
    avoir décrochés du seul op qui les consomme."""
    _tool("salesforce_record")(sobject="Account", where="Industry = 'Technology'",
                               fields="Id,Name", limit=5)
    assert client.list_records.call_args.args == ("Account",)
    assert client.list_records.call_args.kwargs == {
        "fields": "Id,Name", "where": "Industry = 'Technology'", "limit": 5}


def test_record_get_transmet_lid_et_la_projection(client):
    _tool("salesforce_record")(sobject="Account", op="get", record_id="001a",
                               fields="Id,Name")
    assert client.get_record.call_args.args == ("Account", "001a")
    assert client.get_record.call_args.kwargs == {"fields": "Id,Name"}


# --- salesforce_record : les écritures, une par une -----------------------------

def test_record_create_necrit_que_create(client):
    _tool("salesforce_record")(sobject="Contact", op="create",
                               data={"LastName": "Lovelace"})
    _only_write(client, "create_record")
    assert client.create_record.call_args.args == ("Contact", {"LastName": "Lovelace"})


def test_record_update_necrit_que_update(client):
    _tool("salesforce_record")(sobject="Contact", op="update", record_id="003a",
                               data={"Email": "ada@example.com"})
    _only_write(client, "update_record")
    assert client.update_record.call_args.args == (
        "Contact", "003a", {"Email": "ada@example.com"})


def test_record_delete_necrit_que_delete(client):
    """Irréversible : c'est l'op où une erreur de câblage coûte le plus cher."""
    _tool("salesforce_record")(sobject="Contact", op="delete", record_id="003a")
    _only_write(client, "delete_record")
    assert client.delete_record.call_args.args == ("Contact", "003a")


def test_record_upsert_necrit_que_upsert_et_dans_le_bon_ordre(client):
    """L'ordre `(champ, valeur)` est le piège de cette op : inversé, l'upsert
    chercherait la clé externe dans un champ qui porte sa valeur — et créerait
    des doublons au lieu de mettre à jour, sans jamais échouer bruyamment."""
    _tool("salesforce_record")(sobject="Contact", op="upsert",
                               external_id_field="External_Id__c",
                               external_id="abc-123", data={"LastName": "X"})
    _only_write(client, "upsert_record")
    assert client.upsert_record.call_args.args == (
        "Contact", "External_Id__c", "abc-123", {"LastName": "X"})


def test_record_bulk_create_necrit_que_bulk_create_et_rend_un_recu(client):
    client.create_records.return_value = [
        {"id": "003a", "success": True, "errors": []},
        {"success": False, "errors": [{"statusCode": "REQUIRED_FIELD_MISSING"}]},
    ]
    out = _tool("salesforce_record")(
        sobject="Contact", op="bulk_create",
        items=[{"LastName": "A"}, {"LastName": "B"}], all_or_none=True)
    _only_write(client, "create_records")
    assert client.create_records.call_args.args == (
        "Contact", [{"LastName": "A"}, {"LastName": "B"}])
    assert client.create_records.call_args.kwargs == {"all_or_none": True}
    assert out["total"] == 2 and out["succeeded"] == 1
    assert out["results"][0]["index"] == 0 and out["results"][1]["index"] == 1


def test_record_bulk_update_necrit_que_bulk_update(client):
    client.update_records.return_value = [{"id": "003a", "success": True, "errors": []}]
    out = _tool("salesforce_record")(sobject="Contact", op="bulk_update",
                                     items=[{"Id": "003a", "LastName": "X"}])
    _only_write(client, "update_records")
    assert client.update_records.call_args.kwargs == {"all_or_none": False}
    assert out["succeeded"] == 1


def test_all_or_none_defaut_false_comme_salesforce(client):
    """Le défaut Salesforce : les succès sont GARDÉS, les échecs rapportés ligne à
    ligne. Le basculer par mégarde rendrait un lot partiel silencieusement nul."""
    _tool("salesforce_record")(sobject="Contact", op="bulk_create",
                               items=[{"LastName": "A"}])
    assert client.create_records.call_args.kwargs == {"all_or_none": False}


# --- salesforce_record : les gardes des bulk restent branchées SUR LE TOOL ------

def test_bulk_refuse_un_lot_vide_sans_appeler_salesforce(client):
    with pytest.raises(McpError, match="au moins un"):
        _tool("salesforce_record")(sobject="Contact", op="bulk_create", items=[])
    client.create_records.assert_not_called()


def test_bulk_refuse_au_dela_de_200_sans_appeler_salesforce(client):
    """Plafond DUR de sObject Collections : on échoue tôt côté tool plutôt que de
    laisser Salesforce rendre un 400."""
    with pytest.raises(McpError, match="200"):
        _tool("salesforce_record")(sobject="Contact", op="bulk_create",
                                   items=[{"LastName": str(i)} for i in range(201)])
    client.create_records.assert_not_called()


def test_bulk_update_exige_un_id_sur_chaque_item(client):
    with pytest.raises(McpError, match=r"items\[1\]"):
        _tool("salesforce_record")(sobject="Contact", op="bulk_update",
                                   items=[{"Id": "003a"}, {"LastName": "Y"}])
    client.update_records.assert_not_called()


def test_bulk_create_naccepte_pas_le_controle_did_de_bulk_update(client):
    """Symétrie inverse : `create` ne DOIT pas exiger d'Id (sinon plus aucune
    création en lot ne passe). Le contrôle appartient à `bulk_update` seul."""
    _tool("salesforce_record")(sobject="Contact", op="bulk_create",
                               items=[{"LastName": "A"}])
    client.create_records.assert_called_once()


# --- salesforce_query : deux langages, un paramètre ----------------------------

@pytest.mark.parametrize("op,method", [("soql", "query"), ("sosl", "search")])
def test_query_ops_route_to_the_right_client_method(client, op, method):
    _tool("salesforce_query")(query="X", op=op)
    getattr(client, method).assert_called_once_with("X")


def test_query_ne_croise_jamais_les_deux_langages(client):
    """SOQL et SOSL partagent la MÊME forme de paramètre — c'est ce qui autorise la
    fusion — mais pas le même endpoint : les croiser rendrait un `MALFORMED_QUERY`
    opaque."""
    _tool("salesforce_query")(query="FIND {Acme} IN ALL FIELDS RETURNING Account(Id)",
                              op="sosl")
    client.search.assert_called_once()
    client.query.assert_not_called()


# --- salesforce_note ------------------------------------------------------------

def test_note_create_necrit_que_la_note(client):
    _tool("salesforce_note")(record_id="003a", op="create", title="T", body="B")
    _only_write(client, "create_note")
    assert client.create_note.call_args.args == ("003a", "T", "B")
    client.list_notes.assert_not_called()


def test_note_list_enveloppe_la_liste_du_client(client):
    """Le client rend une LISTE, le tool un dict `{notes: [...]}` — l'enveloppe
    est un contrat de surface, pas un détail."""
    client.list_notes.return_value = [{"Id": "069a"}, {"Id": "069b"}]
    assert _tool("salesforce_note")(record_id="003a", op="list") == {
        "notes": [{"Id": "069a"}, {"Id": "069b"}]}


# --- salesforce_describe : resté seul, et toujours projetant --------------------

def test_describe_projette_et_verbose_reste_la_porte_de_sortie(client):
    client.describe.return_value = {"name": "Account", "childRelationships": [1] * 5,
                                    "fields": [{"name": "Id", "type": "id"}]}
    projete = _tool("salesforce_describe")(sobject="Account")
    assert projete["name"] == "Account" and projete["field_count"] == 1
    assert "childRelationships" not in projete
    brut = _tool("salesforce_describe")(sobject="Account", verbose=True)
    assert brut["childRelationships"] == [1] * 5


# --- refus d'une op inconnue ----------------------------------------------------

@pytest.mark.parametrize("tool,base", [
    ("salesforce_record", {"sobject": "Contact"}),
    ("salesforce_query", {"query": "SELECT Id FROM Account"}),
    ("salesforce_note", {"record_id": "003a"}),
])
def test_une_op_inconnue_est_refusee_en_nommant_les_ops_valides(client, tool, base):
    """Jamais de repli silencieux sur le défaut : l'agent croirait sa demande
    honorée alors qu'il a obtenu autre chose."""
    with pytest.raises(McpError, match="op doit être"):
        _tool(tool)(op="nope", **base)
    assert not client.method_calls, "une op inconnue a atteint le client"


def test_une_op_inconnue_ne_resout_meme_pas_le_credential(monkeypatch):
    """La garde est AVANT `_client()`. Ça n'est pas de l'optimisation : c'est ce
    qui garantit qu'aucune op inconnue ne peut atteindre, par un chemin dérivé,
    une méthode d'écriture."""
    from oto_mcp import access

    def _boum(*a, **k):
        raise AssertionError("credential résolu pour une op inconnue")

    monkeypatch.setattr(access, "resolve_credential", _boum)
    with pytest.raises(McpError, match="op doit être"):
        _tool("salesforce_record")(sobject="Contact", op="destroy")


@pytest.mark.parametrize("op", S._RECORD_OPS)
def test_le_message_de_refus_nomme_chaque_op_de_record(op):
    """Source unique : la garde d'entrée et le message dérivent du même tuple. Une
    op acceptée mais non annoncée serait invisible à l'agent."""
    assert f"'{op}'" in S._RECORD_OPS_ERROR


@pytest.mark.parametrize("op", S._QUERY_OPS)
def test_le_message_de_refus_nomme_chaque_op_de_query(op):
    assert f"'{op}'" in S._QUERY_OPS_ERROR


@pytest.mark.parametrize("op", S._NOTE_OPS)
def test_le_message_de_refus_nomme_chaque_op_de_note(op):
    assert f"'{op}'" in S._NOTE_OPS_ERROR


# --- arguments obligatoires : refus qui NOMME l'op et l'argument ----------------

@pytest.mark.parametrize("tool,base,op,kwargs,manquant", [
    ("salesforce_record", {"sobject": "Contact"}, "get", {}, "record_id"),
    ("salesforce_record", {"sobject": "Contact"}, "create", {}, "data"),
    ("salesforce_record", {"sobject": "Contact"}, "update",
     {"record_id": "003a"}, "data"),
    ("salesforce_record", {"sobject": "Contact"}, "update",
     {"data": {"X": 1}}, "record_id"),
    ("salesforce_record", {"sobject": "Contact"}, "delete", {}, "record_id"),
    ("salesforce_record", {"sobject": "Contact"}, "upsert",
     {"external_id": "a", "data": {}}, "external_id_field"),
    ("salesforce_record", {"sobject": "Contact"}, "upsert",
     {"external_id_field": "F__c", "data": {}}, "external_id"),
    ("salesforce_record", {"sobject": "Contact"}, "upsert",
     {"external_id_field": "F__c", "external_id": "a"}, "data"),
    ("salesforce_record", {"sobject": "Contact"}, "bulk_create", {}, "items"),
    ("salesforce_record", {"sobject": "Contact"}, "bulk_update", {}, "items"),
    ("salesforce_note", {"record_id": "003a"}, "create", {"body": "B"}, "title"),
    ("salesforce_note", {"record_id": "003a"}, "create", {"title": "T"}, "body"),
])
def test_un_argument_obligatoire_manquant_nomme_lop_et_largument(
        client, tool, base, op, kwargs, manquant):
    with pytest.raises(McpError, match=f"op='{op}' requiert {manquant}"):
        _tool(tool)(op=op, **base, **kwargs)
    assert not client.method_calls, "l'appel est parti malgré l'argument manquant"


def test_un_record_id_vide_est_refuse_pas_transmis(client):
    """`record_id=""` viserait l'URL de COLLECTION : la suppression manquerait sa
    cible au lieu d'échouer franchement."""
    with pytest.raises(McpError, match="requiert record_id"):
        _tool("salesforce_record")(sobject="Contact", op="delete", record_id="   ")
    client.delete_record.assert_not_called()
