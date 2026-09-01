"""`hubspot_list op='members'` + `properties` : la page enrichie en 2 appels.

Sans `properties`, l'op rendait — et rend toujours — des ids d'enregistrements
nus. Une procédure qui veut sept colonnes par membre enchaînait donc un
`hubspot_object op='get'` PAR membre : un N+1 qui tape le plafond d'une private
app (190 requêtes / 10 s) vers la quarantième fiche. `properties` compose la
page d'appartenances avec UN batch read.

⚠️ **LA FORME DU RETOUR D'oto-core EST LE SUJET DE CE FICHIER.**
`batch_read_objects` rend une ENVELOPPE — `{"results": [...], "missing_ids":
[...]}` — et non une liste. Une première version de ces tests posait
`inst.batch_read_objects.return_value = [ … ]` sur un `MagicMock()` NU : 29
tests verts contre un client qui n'existe pas, et un `AttributeError` sur le
premier appel réel (itérer un dict rend ses CLÉS, donc `"results"`, une chaîne).
C'est très exactement le test qui décrit l'intention de son auteur au lieu du
système (`docs/conventions.md`). Deux garde-fous sont posés ici contre le
retour de cette panne :

- `_enveloppe()` ci-dessous est le SEUL endroit de ce dépôt où cette forme est
  écrite : tous les mocks passent par lui, donc ils ne peuvent plus diverger
  entre eux ;
- `test_a_bare_list_from_the_client_is_refused_by_name` fait passer au tool la
  forme de l'ANCIEN client et exige un refus NOMMÉ. C'est le seul test qui
  survivrait à un pin en arrière : sans lui, un client qui régresse redonne un
  `AttributeError` opaque en prod.

Le mock est conformé (`spec=`) à la vraie classe : un `MagicMock()` nu accepte
n'importe quel nom de méthode, donc une faute de frappe sur `get_lst` passerait.

Le reste de ce qui est testé ici est ce qui peut casser SILENCIEUSEMENT :

1. **Le chemin sans `properties`.** C'est le contrat déjà servi. S'il se mettait
   à ré-emballer sa réponse, à faire un GET de plus ou à ajouter une clé, aucun
   appelant ne lèverait — ils liraient juste autre chose. D'où une assertion
   d'IDENTITÉ (`is`), pas d'égalité, et un comptage d'appels.
2. **Le recollage appartenance ↔ enregistrement.** Le batch read peut rendre
   MOINS d'objets qu'on n'en demande (supprimé entre les deux appels, hors des
   droits de la clé). Recoller par index, ou itérer les enregistrements plutôt
   que les appartenances, perdrait des membres sans un mot — dans une population
   de prospection, une ligne muette est pire qu'un refus.
3. **Le type d'objet du batch read.** Une appartenance ne dit pas de quel objet
   elle parle. Le deviner (« contacts ») ne lève pas : ça lit le mauvais objet
   et rend une population plausible et fausse.
4. **Le découpage à 100.** Il appartient au client oto-core. Un second
   découpeur ici serait un miroir que rien ne relie.
5. **La description SERVIE.** FastMCP tronque la `description` au bloc `Args:` :
   la phrase qui fait choisir 2 appels plutôt que 101 doit vivre AVANT.

Le client oto-core est mocké : ces tests ne dépendent pas du pin
(`batch_read_objects` n'existe pas encore sur le tag épinglé — c'est
`tests/test_tools_client_methods_exist.py` qui porte cette dépendance, et il
doit rester rouge jusqu'au bump).
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError


def _enveloppe(results, missing_ids=()) -> dict:
    """La forme que `batch_read_objects` rend, écrite UNE fois.

    oto-core : `{"results": [...], "missing_ids": [...]}`. `missing_ids` porte
    les ids demandés que HubSpot n'a pas rendus (son batch read répond 207 sans
    nommer les absents) ; le client est le seul endroit où cet écart se calcule,
    donc le seul à pouvoir le dire.

    Aucun mock de ce fichier n'écrit cette forme à la main : la faire passer
    toute par ici est ce qui empêche quatre mocks de dériver l'un de l'autre —
    et le jour où oto-core change de forme, il y a UNE ligne à changer, qui fera
    tomber tout ce qui en dépend au lieu de rien.
    """
    return {"results": list(results), "missing_ids": list(missing_ids)}


def _spec_du_client() -> list[str]:
    """Les noms de méthodes auxquels le mock est CONFORMÉ.

    Un `MagicMock()` nu répond à tout : `inst.get_lst.return_value = …` passerait
    sans un mot, et le mock ne pourrait alors RIEN réfuter — c'est la moitié de
    ce qui a laissé passer la divergence de forme réparée par ce lot.

    Le spec est l'union de deux ensembles, et la répartition est délibérée :

    - **la vraie classe `HubSpotClient`** — ce qu'oto-core sait faire ;
    - **les méthodes que `oto_mcp/tools/hubspot.py` appelle sur son client**,
      relues à l'AST par la sonde version-skew elle-même.

    Le second terme est là parce qu'un `dir(HubSpotClient)` SEUL couplerait ce
    fichier au core INSTALLÉ, pas au core épinglé : un venv en retard (le cas
    ordinaire en local, cf. `docs/commands.md` §Pin oto-core) retirerait du spec
    des méthodes qui existent bel et bien sur le tag, et ces tests deviendraient
    rouges pour une raison d'environnement — un garde-fou qui crie à tort finit
    ignoré. La question « ces méthodes existent-elles sur le PIN ? » a déjà son
    propriétaire, `tests/test_tools_client_methods_exist.py`, qui la tranche
    statiquement contre le tag ; la poser deux fois n'ajouterait rien et
    brouillerait la lecture des rouges.

    Ce qui reste donc gardé ICI : un mock qui configure une méthode que ni la
    classe ni le tool ne connaissent — une faute de frappe dans ce fichier, ou
    un mock survivant à une méthode disparue des deux côtés.
    """
    import ast
    import importlib.util
    from pathlib import Path

    from oto.tools.hubspot.client import HubSpotClient

    sonde = Path(__file__).with_name("test_tools_client_methods_exist.py")
    spec = importlib.util.spec_from_file_location("_sonde_skew", sonde)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    outil = Path(__file__).resolve().parent.parent / "oto_mcp" / "tools" / "hubspot.py"
    appelees = mod._methods_called_on_client(ast.parse(outil.read_text()))

    return sorted({n for n in dir(HubSpotClient) if not n.startswith("_")}
                  | appelees)


@pytest.fixture
def client(monkeypatch):
    import oto.tools.hubspot.client as hs

    inst = MagicMock(spec=_spec_du_client())
    inst.get_list_memberships.return_value = {
        "results": [{"recordId": "1", "membershipTimestamp": "t1"},
                    {"recordId": "2", "membershipTimestamp": "t2"}],
        "paging": {"next": {"after": "cur"}},
        "total": 2,
    }
    # forme réelle de GET /crm/v3/lists/{listId} : la liste est enveloppée
    inst.get_list.return_value = {
        "list": {"listId": "9", "name": "ICP France", "processingType": "MANUAL",
                 "objectTypeId": "0-1"}}
    inst.batch_read_objects.return_value = _enveloppe([
        {"id": "1", "properties": {"email": "a@b.test"}},
        {"id": "2", "properties": {"email": "d@e.test"}},
    ])
    monkeypatch.setattr(hs, "HubSpotClient", lambda *a, **k: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", False))
    return inst


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import hubspot as H

    m = FastMCP("t")
    H.register(m)
    return asyncio.run(m.get_tool(name)).fn


def _described(name: str) -> str:
    from fastmcp import FastMCP
    from oto_mcp.tools import hubspot as H

    m = FastMCP("t")
    H.register(m)
    return asyncio.run(m.get_tool(name)).description or ""


# --- la FORME du retour d'oto-core ----------------------------------------------

def test_the_client_mock_is_conformed_to_the_real_class(client):
    """Sans `spec=`, le mock répond à tout et ne peut donc rien réfuter."""
    with pytest.raises(AttributeError):
        client.get_lst  # faute de frappe : le mock doit la refuser


def test_the_batch_read_envelope_is_consumed_as_oto_core_serves_it(client):
    """oto-core rend `{"results": [...], "missing_ids": [...]}`, PAS une liste.

    Le seul test qui exerce l'enveloppe COMPLÈTE — résultats partiels ET verdict
    du client — de bout en bout, de la valeur rendue par le client à la valeur
    servie à l'agent.
    """
    client.batch_read_objects.return_value = _enveloppe(
        [{"id": "2", "properties": {"email": "d@e.test"}}], missing_ids=["1"])

    out = _tool("hubspot_list")(op="members", list_id="9", properties=["email"])

    assert [r["recordId"] for r in out["results"]] == ["1", "2"]
    assert out["results"][0]["properties"] is None
    assert "missing" in out["results"][0]
    assert out["results"][1]["properties"] == {"email": "d@e.test"}
    assert out["missing_ids"] == ["1"]
    assert out["missing_count"] == 1
    assert "missing_mismatch" not in out


def test_a_bare_list_from_the_client_is_refused_by_name(client):
    """La forme de l'ANCIEN client (une liste nue) doit lever un refus NOMMÉ.

    C'est le garde-fou qui survit au mock : si le pin oto-core repart en arrière,
    l'appelant reçoit une phrase qui dit quoi faire, pas
    `AttributeError: 'str' object has no attribute 'get'` levé cinq frames plus
    bas dans une compréhension de dict.
    """
    client.batch_read_objects.return_value = [
        {"id": "1", "properties": {"email": "a@b.test"}}]

    with pytest.raises(TypeError, match="batch_read_objects"):
        _tool("hubspot_list")(op="members", list_id="9", properties=["email"])


def test_the_clients_missing_ids_is_surfaced_not_re_derived(client):
    """Le relevé du client est SERVI tel quel — le jeter le rendrait indérivable.

    HubSpot ne nomme pas les absents de son batch read ; ce relevé n'existe que
    parce que le client compare demandé et rendu. Le remplacer par un comptage
    local, c'est perdre le seul énoncé qui vient de l'amont.
    """
    client.get_list_memberships.return_value = {
        "results": [{"recordId": "1"}, {"recordId": "2"}, {"recordId": "3"}]}
    client.batch_read_objects.return_value = _enveloppe(
        [{"id": "2", "properties": {}}], missing_ids=["1", "3"])

    out = _tool("hubspot_list")(op="members", list_id="9", properties=["email"])

    assert out["missing_ids"] == ["1", "3"]
    assert out["missing_count"] == 2


def test_no_missing_key_when_the_client_reports_nothing_missing(client):
    out = _tool("hubspot_list")(op="members", list_id="9", properties=["email"])
    assert "missing_ids" not in out
    assert "missing_count" not in out
    assert "missing_mismatch" not in out


def test_the_two_verdicts_disagreeing_is_named_not_silently_reconciled(client):
    """Le client dit « il manque 3 », la jointure dit « il manque 1 ».

    Fondre les deux en un chiffre choisirait un gagnant en silence. On sert les
    deux et on nomme le désaccord : c'est ce qui rend la dérive future visible.
    """
    client.batch_read_objects.return_value = _enveloppe(
        [{"id": "2", "properties": {"email": "d@e.test"}}], missing_ids=["3"])

    out = _tool("hubspot_list")(op="members", list_id="9", properties=["email"])

    assert out["missing_ids"] == ["3"]
    assert out["missing_count"] == 1
    assert out["missing_mismatch"]["reported_by_client"] == ["3"]
    assert out["missing_mismatch"]["absent_from_join"] == ["1"]


# --- le chemin historique reste OCTET pour OCTET --------------------------------

def test_members_without_properties_still_returns_ids_only(client):
    """Le contrat déjà servi : un appel, sa réponse RENDUE TELLE QUELLE.

    `is` et pas `==` : une égalité passerait encore si le tool reconstruisait
    un dict équivalent — et c'est justement la reconstruction qui, un lot plus
    tard, se met à ajouter ou à renommer une clé.
    """
    attendu = client.get_list_memberships.return_value
    out = _tool("hubspot_list")(op="members", list_id="9", limit=50, after="cur")

    assert out is attendu
    assert client.get_list_memberships.call_args.args == ("9",)
    assert client.get_list_memberships.call_args.kwargs == {
        "limit": 50, "after": "cur"}
    client.batch_read_objects.assert_not_called()
    client.get_list.assert_not_called()


def test_members_without_properties_makes_exactly_one_call(client):
    _tool("hubspot_list")(op="members", list_id="9")
    assert len(client.mock_calls) == 1


# --- la composition -------------------------------------------------------------

def test_members_with_properties_composes_memberships_then_batch_read(client):
    _tool("hubspot_list")(op="members", list_id="9", properties=["email"])

    client.get_list_memberships.assert_called_once()
    client.batch_read_objects.assert_called_once()
    assert client.batch_read_objects.call_args.args[1] == ["1", "2"]


def test_the_projection_is_forwarded_to_the_batch_read(client):
    _tool("hubspot_list")(op="members", list_id="9",
                          properties=["email", "firstname"])
    assert client.batch_read_objects.call_args.kwargs["properties"] == [
        "email", "firstname"]


def test_rows_keep_the_hubspot_membership_keys(client):
    """`recordId` n'est PAS renommé en `id` : une procédure qui le lit déjà
    continue de marcher le jour où elle passe `properties`."""
    out = _tool("hubspot_list")(op="members", list_id="9", properties=["email"])

    assert out["results"] == [
        {"recordId": "1", "membershipTimestamp": "t1",
         "properties": {"email": "a@b.test"}},
        {"recordId": "2", "membershipTimestamp": "t2",
         "properties": {"email": "d@e.test"}},
    ]


def test_paging_and_total_survive_verbatim(client):
    """C'est sur `paging` que la procédure boucle : le déplacer casse en silence."""
    out = _tool("hubspot_list")(op="members", list_id="9", properties=["email"])
    assert out["paging"] == {"next": {"after": "cur"}}
    assert out["total"] == 2


def test_chunking_is_the_clients_job_not_the_tool(client):
    """HubSpot plafonne le batch à 100 ; découper est au CLIENT (un découpeur de
    plus ici serait un miroir que rien ne relie, et qui dériverait)."""
    client.get_list_memberships.return_value = {
        "results": [{"recordId": str(i), "membershipTimestamp": "t"}
                    for i in range(250)]}
    client.batch_read_objects.return_value = _enveloppe(
        [{"id": str(i), "properties": {"email": f"{i}@x.test"}}
         for i in range(250)])

    out = _tool("hubspot_list")(op="members", list_id="9", limit=250,
                                properties=["email"])

    client.batch_read_objects.assert_called_once()
    assert len(client.batch_read_objects.call_args.args[1]) == 250
    assert len(out["results"]) == 250


# --- ne JAMAIS perdre un membre -------------------------------------------------

def test_a_member_whose_record_is_missing_is_a_named_row_not_a_dropped_one(client):
    client.batch_read_objects.return_value = _enveloppe(
        [{"id": "2", "properties": {"email": "d@e.test"}}], missing_ids=["1"])

    out = _tool("hubspot_list")(op="members", list_id="9", properties=["email"])

    assert [r["recordId"] for r in out["results"]] == ["1", "2"]
    assert out["results"][0]["properties"] is None
    assert "missing" in out["results"][0]
    assert out["results"][1]["properties"] == {"email": "d@e.test"}


def test_missing_count_appears_only_when_something_is_missing(client):
    tool = _tool("hubspot_list")

    assert "missing_count" not in tool(op="members", list_id="9",
                                       properties=["email"])

    client.batch_read_objects.return_value = _enveloppe(
        [{"id": "2", "properties": {"email": "d@e.test"}}], missing_ids=["1"])
    assert tool(op="members", list_id="9", properties=["email"])["missing_count"] == 1


def test_a_requested_property_absent_from_the_record_is_named(client):
    """Sinon un nom interne mal orthographié se lit comme une colonne vide."""
    out = _tool("hubspot_list")(op="members", list_id="9",
                                properties=["email", "firstnme"])
    assert out["results"][0]["missing_properties"] == ["firstnme"]
    assert "missing_properties" not in _tool("hubspot_list")(
        op="members", list_id="9", properties=["email"])["results"][0]


# --- le type d'objet : dérivé, jamais deviné ------------------------------------

def test_the_object_type_is_derived_from_the_list_when_not_given(client):
    client.get_list.return_value = {"list": {"listId": "9", "objectTypeId": "0-2"}}

    _tool("hubspot_list")(op="members", list_id="9", properties=["name"])

    client.get_list.assert_called_once_with("9")
    assert client.batch_read_objects.call_args.args[0] == "companies"


def test_an_explicit_object_type_skips_the_extra_read(client):
    _tool("hubspot_list")(op="members", list_id="9", object_type="Deals",
                          properties=["dealstage"])

    client.get_list.assert_not_called()
    assert client.batch_read_objects.call_args.args[0] == "deals"
    assert len(client.mock_calls) == 2


def test_a_custom_object_type_id_is_passed_through(client):
    client.get_list.return_value = {"list": {"listId": "9", "objectTypeId": "2-7"}}
    _tool("hubspot_list")(op="members", list_id="9", properties=["x"])
    assert client.batch_read_objects.call_args.args[0] == "2-7"


def test_a_list_without_a_readable_object_type_is_refused_not_guessed(client):
    """Retomber sur « contacts » lirait le mauvais objet et rendrait une
    population plausible et fausse — le pire mode d'échec disponible."""
    client.get_list.return_value = {"list": {"listId": "9", "name": "L"}}

    with pytest.raises(McpError, match="object_type"):
        _tool("hubspot_list")(op="members", list_id="9", properties=["email"])
    client.batch_read_objects.assert_not_called()


def test_the_object_type_used_is_reported_in_the_answer(client):
    out = _tool("hubspot_list")(op="members", list_id="9", properties=["email"])
    assert out["object_type"] == "contacts"


def test_an_unknown_object_type_is_refused(client):
    with pytest.raises(McpError, match="object_type"):
        _tool("hubspot_list")(op="members", list_id="9", object_type="prospects",
                              properties=["email"])
    client.batch_read_objects.assert_not_called()


# --- refus ----------------------------------------------------------------------

@pytest.mark.parametrize("value", [{"email": "x"}, "email", [1, 2]])
def test_properties_must_be_a_list_of_names(client, value):
    with pytest.raises(McpError, match="properties"):
        _tool("hubspot_list")(op="members", list_id="9", properties=value)
    client.get_list_memberships.assert_not_called()
    client.batch_read_objects.assert_not_called()


def test_an_empty_properties_list_is_refused_by_name(client):
    """`properties=[]` demande zéro colonne, et HubSpot répondrait ses colonnes
    PAR DÉFAUT : trois appels pour ce que personne n'a demandé. Omettre
    l'argument et le remplir ont déjà chacun leur sens ; la troisième forme se
    refuse plutôt que de se deviner."""
    with pytest.raises(McpError, match="NON VIDE"):
        _tool("hubspot_list")(op="members", list_id="9", properties=[])

    client.get_list_memberships.assert_not_called()
    client.get_list.assert_not_called()
    client.batch_read_objects.assert_not_called()


@pytest.mark.parametrize("op,kwargs,method", [
    ("get", {"list_id": "9"}, "get_list"),
    ("add_members", {"list_id": "9", "record_ids": ["1"]}, "add_list_memberships"),
    ("clear_members", {"list_id": "9"}, "delete_all_list_memberships"),
    ("copy_from", {"list_id": "9", "source_list_id": "8"},
     "add_memberships_from_list"),
])
def test_properties_on_a_non_members_op_is_refused(client, op, kwargs, method):
    """Ignorer l'argument serait la divergence MUETTE que le dépôt refuse :
    l'appelant croirait avoir demandé des colonnes."""
    with pytest.raises(McpError, match="members"):
        _tool("hubspot_list")(op=op, properties=["email"], **kwargs)
    getattr(client, method).assert_not_called()


def test_an_empty_page_does_not_call_the_batch_read(client):
    """`POST /batch/read` avec `inputs: []` est un 400 chez HubSpot."""
    client.get_list_memberships.return_value = {"results": [], "total": 0}

    out = _tool("hubspot_list")(op="members", list_id="9", properties=["email"])

    client.batch_read_objects.assert_not_called()
    assert out["results"] == []
    assert out["total"] == 0
    assert "missing_ids" not in out


# --- la surface SERVIE ----------------------------------------------------------

def test_the_new_arguments_are_documented_in_args():
    """Un paramètre sans entrée `Args:` est servi avec un schéma nu."""
    src = _tool("hubspot_list").__doc__ or ""
    args = src.split("Args:", 1)[1]
    assert "properties:" in args
    assert "object_type:" in args


def test_the_empty_list_refusal_is_documented_where_it_is_read():
    """Un refus non écrit dans `Args:` se découvre par l'échec, en production."""
    args = (_tool("hubspot_list").__doc__ or "").split("Args:", 1)[1]
    assert "EMPTY list is refused" in args


def test_the_fast_path_is_advertised_before_the_args_block():
    """FastMCP tronque la `description` au bloc `Args:` : la phrase qui fait
    choisir 2 appels plutôt que 101 doit vivre AVANT, sinon l'agent ne la voit
    jamais et refait le N+1."""
    servie = _described("hubspot_list")
    assert "Args:" not in servie
    tete = servie.split('- **"add_members"**', 1)[0]
    assert "properties" in tete
    assert "190 requests per" in tete


# --- l'enveloppe et le recollage, exercés directement ----------------------------

def test_the_envelope_reader_refuses_a_list_by_name():
    from oto_mcp.tools.hubspot import _batch_read_envelope

    with pytest.raises(TypeError, match="batch_read_objects"):
        _batch_read_envelope([{"id": "1"}])


def test_the_envelope_reader_tolerates_an_absent_missing_ids():
    """Une enveloppe sans `missing_ids` reste lisible : c'est `results` qui porte
    la donnée, `missing_ids` n'est qu'un relevé."""
    from oto_mcp.tools.hubspot import _batch_read_envelope

    assert _batch_read_envelope({"results": [{"id": "1"}]}) == ([{"id": "1"}], [])


def test_rows_from_memberships_refuses_the_envelope_itself():
    """Le helper prend la LISTE `results`, pas l'enveloppe. Lui passer le dict
    l'itérerait sur ses CLÉS — `'str' object has no attribute 'get'`, cinq
    frames plus bas. Il refuse, et il dit quoi."""
    from oto_mcp.tools.hubspot import _rows_from_memberships

    with pytest.raises(TypeError, match="_rows_from_memberships"):
        _rows_from_memberships([{"recordId": "1"}],
                               {"results": [], "missing_ids": []})


def test_rows_from_memberships_keeps_the_page_order():
    from oto_mcp.tools.hubspot import _rows_from_memberships

    membres = [{"recordId": "3"}, {"recordId": "1"}, {"recordId": "2"}]
    records = [{"id": "1"}, {"id": "2"}, {"id": "3"}]  # HubSpot ne garantit rien

    rows = _rows_from_memberships(membres, records)
    assert [r["recordId"] for r in rows] == ["3", "1", "2"]


def test_rows_from_memberships_matches_on_id_not_on_position():
    from oto_mcp.tools.hubspot import _rows_from_memberships

    membres = [{"recordId": "1"}, {"recordId": "2"}]
    records = [{"id": "2", "properties": {"email": "deux@x.test"}}]

    rows = _rows_from_memberships(membres, records, ["email"])
    assert rows[0]["properties"] is None
    assert rows[1]["properties"] == {"email": "deux@x.test"}


def test_rows_from_memberships_stringifies_the_record_id():
    """HubSpot rend l'id en chaîne côté objet et l'appartenance peut, elle,
    porter un entier : comparer sans normaliser perdrait TOUTE la page."""
    from oto_mcp.tools.hubspot import _rows_from_memberships

    rows = _rows_from_memberships([{"recordId": 1}],
                                  [{"id": "1", "properties": {"a": "b"}}])
    assert rows[0]["properties"] == {"a": "b"}


def test_the_missing_report_is_empty_when_nothing_is_missing():
    from oto_mcp.tools.hubspot import _missing_report

    assert _missing_report([{"recordId": "1", "properties": {}}], []) == {}
