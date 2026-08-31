"""Lemlist lead-lifecycle tools (create/launch/variables) added on top of the
native read-only surface. Locks in: the tool↔client join (version-skew guard),
that `lemlist_create_lead` filters None fields and merges custom variables into
the lead payload before calling the client, that `lemlist_launch_lead` is
masked by default (it pushes a lead into a live send — a bad LLM call
shouldn't do that by accident) while the other new tools stay visible, and
platform-usage recording on all three.
"""
import asyncio
from unittest.mock import patch

import pytest

from oto_mcp.tool_visibility import DEFAULT_HIDDEN_TOOLS, namespace_of

EXPECTED_NEW_TOOLS = {
    "lemlist_create_lead", "lemlist_launch_lead", "lemlist_add_lead_variables",
}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    tools = asyncio.run(m._list_tools())
    return {t.name for t in tools}


def _tool(name):
    from fastmcp import FastMCP
    from oto_mcp.tools import lemlist

    m = FastMCP("t")
    lemlist.register(m)
    return asyncio.run(m.get_tool(name))


def _with_fake_client():
    key = patch("oto_mcp.access.resolve_api_key", return_value=("fake-key", False))
    cls = patch("oto.tools.lemlist.LemlistClient")
    return key, cls


# --- surface MCP --------------------------------------------------------------

def test_new_lemlist_tools_registered_under_namespace(all_tools):
    assert EXPECTED_NEW_TOOLS <= all_tools
    assert all(namespace_of(t) == "lemlist" for t in EXPECTED_NEW_TOOLS)


# --- jointure tool <-> client oto-core (garde version-skew) -------------------

def test_client_exposes_methods_called_by_new_tools():
    from oto.tools.lemlist import LemlistClient
    for meth in ("create_lead", "launch_lead", "add_lead_variables"):
        assert callable(getattr(LemlistClient, meth, None)), f"LemlistClient.{meth} manquant"


# --- visibilité : seul launch_lead est masqué par défaut ----------------------

def test_only_launch_lead_is_hidden_by_default():
    assert "lemlist_launch_lead" in DEFAULT_HIDDEN_TOOLS
    assert "lemlist_create_lead" not in DEFAULT_HIDDEN_TOOLS
    assert "lemlist_add_lead_variables" not in DEFAULT_HIDDEN_TOOLS


# --- lemlist_create_lead : shaping du payload ---------------------------------

def test_create_lead_drops_none_fields_and_merges_custom_variables():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_create_lead")

        tool.fn(
            campaign_id="camp_1", email="a@acme.fr", first_name="A",
            custom_variables={"industry": "SaaS"},
        )

        args, kwargs = inst.create_lead.call_args
        assert args[0] == "camp_1"
        lead = args[1]
        assert lead == {"email": "a@acme.fr", "firstName": "A", "industry": "SaaS"}
        assert kwargs == {
            "deduplicate": False, "linkedin_enrichment": False,
            "find_email": False, "verify_email": False, "find_phone": False,
        }


def test_create_lead_forwards_enrichment_flags():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_create_lead")

        tool.fn(campaign_id="camp_1", email="a@acme.fr", find_email=True, deduplicate=True)

        kwargs = inst.create_lead.call_args.kwargs
        assert kwargs["find_email"] is True
        assert kwargs["deduplicate"] is True
        assert kwargs["find_phone"] is False


# --- lemlist_launch_lead / lemlist_add_lead_variables : passthrough -----------

def test_launch_lead_delegates_to_client():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        inst.launch_lead.return_value = {"ok": True}
        tool = _tool("lemlist_launch_lead")

        out = tool.fn(lead_id="lea_1")

        inst.launch_lead.assert_called_once_with("lea_1")
        assert out == {"ok": True}


def test_add_lead_variables_delegates_to_client():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        inst.add_lead_variables.return_value = {"ok": True}
        tool = _tool("lemlist_add_lead_variables")

        out = tool.fn(lead_id="lea_1", variables={"customField1": "x"})

        inst.add_lead_variables.assert_called_once_with("lea_1", {"customField1": "x"})
        assert out == {"ok": True}


# --- enrichissement : surface async + garde-fous -----------------------------
#
# L'enrichissement n'envoie rien mais DÉPENSE des crédits lemlist, d'où deux
# invariants verrouillés ici : aucune action n'est demandée par défaut (un appel
# sans action échoue localement, sans round-trip ni crédit), et rien n'attend
# in-process (signal #252 : au-delà de ~60s le client MCP raccroche, le résultat
# est perdu et les crédits, eux, sont consommés).

ENRICH_TOOLS = {
    "lemlist_enrich", "lemlist_enrich_lead",
    "lemlist_enrich_result", "lemlist_enrich_bulk",
}


def test_enrich_tools_registered_under_namespace(all_tools):
    assert ENRICH_TOOLS <= all_tools
    assert all(namespace_of(t) == "lemlist" for t in ENRICH_TOOLS)


def test_enrich_tools_visible_by_default():
    # Contrairement à launch_lead, enrichir ne déclenche aucun envoi.
    assert not (ENRICH_TOOLS & DEFAULT_HIDDEN_TOOLS)


def test_client_exposes_enrichment_methods():
    from oto.tools.lemlist import LemlistClient
    for meth in ("enrich", "get_enrichment", "enrich_lead", "bulk_enrich"):
        assert callable(getattr(LemlistClient, meth, None)), f"LemlistClient.{meth} manquant"


def test_enrich_forwards_identity_and_actions():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        inst.enrich.return_value = {"id": "enr_1"}
        tool = _tool("lemlist_enrich")

        out = tool.fn(
            first_name="John", last_name="Lempire",
            company_domain="lempire.com", find_email=True,
        )

        kwargs = inst.enrich.call_args.kwargs
        assert kwargs["first_name"] == "John"
        assert kwargs["company_domain"] == "lempire.com"
        assert kwargs["find_email"] is True
        assert kwargs["find_phone"] is False
        assert out["enrichment_id"] == "enr_1"


def test_enrich_without_any_action_fails_locally():
    from oto_mcp.mcp_errors import McpError
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_enrich")

        with pytest.raises(McpError):
            tool.fn(email="a@acme.fr")

        # Aucun appel amont : pas de crédit brûlé sur le 400 documenté de lemlist.
        inst.enrich.assert_not_called()


def test_enrich_lead_without_any_action_fails_locally():
    from oto_mcp.mcp_errors import McpError
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_enrich_lead")

        with pytest.raises(McpError):
            tool.fn(lead_id="lea_1")

        inst.enrich_lead.assert_not_called()


def test_enrich_result_reports_in_progress_without_waiting():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        inst.get_enrichment.return_value = {
            "enrichmentId": "enr_1", "enrichmentStatus": "in-progress",
            "input": {"firstName": "John"}, "data": {},
        }
        tool = _tool("lemlist_enrich_result")

        out = tool.fn(enrichment_id="enr_1")

        # Un seul relevé, pas de boucle d'attente.
        assert inst.get_enrichment.call_count == 1
        assert out["all_done"] is False
        assert out["results"][0]["done"] is False
        assert out["results"][0]["status"] == "in-progress"


def test_enrich_result_marks_not_found_as_terminal():
    # lemlist répond 404 avec un corps légitime : re-poller ne le fera pas
    # apparaître, donc c'est un état terminal, pas une attente.
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        inst.get_enrichment.return_value = {
            "enrichmentId": "enr_x", "enrichmentStatus": "not-found",
            "error": "Enrichment not found", "data": {},
        }
        tool = _tool("lemlist_enrich_result")

        out = tool.fn(enrichment_id="enr_x")

        assert out["all_done"] is True
        assert out["results"][0]["done"] is True


def test_enrich_result_accepts_a_list_of_ids():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        inst.get_enrichment.side_effect = [
            {"enrichmentId": "enr_1", "enrichmentStatus": "done",
             "input": {}, "data": {"email": {"email": "a@acme.fr", "notFound": False}}},
            {"enrichmentId": "enr_2", "enrichmentStatus": "in-progress",
             "input": {}, "data": {}},
        ]
        tool = _tool("lemlist_enrich_result")

        out = tool.fn(enrichment_id=["enr_1", "enr_2"])

        assert [r["enrichment_id"] for r in out["results"]] == ["enr_1", "enr_2"]
        assert out["results"][0]["data"]["email"]["email"] == "a@acme.fr"
        assert out["all_done"] is False
        assert "enr_2" not in out["results"][0]["enrichment_id"]


def test_enrich_bulk_maps_actions_to_the_v2_vocabulary():
    # Piège de l'API : en v2 la vérification d'email s'appelle `verify`, PAS
    # `verify_email` — un snake_case mécanique des flags v1 enverrait du faux.
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        inst.bulk_enrich.return_value = [{"id": "enr_1", "metadata": {"index": "0"}}]
        tool = _tool("lemlist_enrich_bulk")

        out = tool.fn(people=[{
            "linkedin_url": "https://www.linkedin.com/in/lempire",
            "actions": ["verify_email", "find_phone"],
        }])

        items = inst.bulk_enrich.call_args.args[0]
        assert items[0]["enrichmentRequests"] == ["verify", "find_phone"]
        assert items[0]["input"] == {"linkedinUrl": "https://www.linkedin.com/in/lempire"}
        assert out["enrichment_ids"] == ["enr_1"]


def test_enrich_bulk_rejects_unknown_or_missing_actions():
    from oto_mcp.mcp_errors import McpError
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("lemlist_enrich_bulk")

        with pytest.raises(McpError):
            tool.fn(people=[{"email": "a@acme.fr", "actions": ["find_everything"]}])
        with pytest.raises(McpError):
            tool.fn(people=[{"email": "a@acme.fr"}])

        inst.bulk_enrich.assert_not_called()


def test_enrich_bulk_surfaces_per_entry_errors():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        inst.bulk_enrich.return_value = [
            {"id": "enr_1", "metadata": {"index": "0"}},
            {"error": "MISSING_INPUTS", "metadata": {"index": "1"}},
        ]
        tool = _tool("lemlist_enrich_bulk")

        out = tool.fn(people=[
            {"email": "a@acme.fr", "actions": ["find_phone"]},
            {"actions": ["find_phone"]},
        ])

        assert out["enrichment_ids"] == ["enr_1"]
        assert out["submitted"][1]["error"] == "MISSING_INPUTS"


def test_enrich_records_platform_usage():
    key = patch("oto_mcp.access.resolve_api_key", return_value=("platform-key", True))
    cls = patch("oto.tools.lemlist.LemlistClient")
    rec = patch("oto_mcp.access.record_platform_usage")
    with key, cls as client_cls, rec as record:
        client_cls.return_value.enrich.return_value = {"id": "enr_1"}
        _tool("lemlist_enrich").fn(email="a@acme.fr", find_phone=True)
        record.assert_called_once_with("lemlist")


def test_bulk_action_vocabulary_matches_the_client():
    # Garde version-skew : le tool écrit le vocabulaire v2 en dur (pour rester
    # validable sous mock), le client le porte aussi. Les deux doivent coïncider.
    from oto.tools.lemlist import LemlistClient
    from oto_mcp.tools.lemlist import BULK_ACTIONS

    assert BULK_ACTIONS == LemlistClient.ENRICH_BULK_ACTIONS
    assert BULK_ACTIONS["verify_email"] == "verify"


def test_enrich_bulk_records_one_platform_usage_per_person():
    # Un bulk est facturé à la personne : compter 1 pour l'appel sous-facturerait
    # un lot de 40 d'un facteur 40.
    key = patch("oto_mcp.access.resolve_api_key", return_value=("platform-key", True))
    cls = patch("oto.tools.lemlist.LemlistClient")
    rec = patch("oto_mcp.access.record_platform_usage")
    with key, cls as client_cls, rec as record:
        client_cls.return_value.bulk_enrich.return_value = [
            {"id": "enr_1"}, {"id": "enr_2"}, {"id": "enr_3"},
        ]
        _tool("lemlist_enrich_bulk").fn(people=[
            {"email": f"a{i}@acme.fr", "actions": ["find_email"]} for i in range(3)
        ])
        record.assert_called_once_with("lemlist", 3)


def test_enrich_result_union_survives_the_mcp_validation_boundary():
    # Les autres tests appellent `tool.fn` — la fonction brute, sans la
    # validation pydantic de FastMCP. Ici on passe par `tool.run`, le vrai
    # chemin d'un client MCP, pour que `str | list[str]` soit prouvé des deux
    # côtés et pas seulement dans la boucle Python.
    def _run(payload, side_effect):
        key, cls = _with_fake_client()
        with key, cls as client_cls:
            client_cls.return_value.get_enrichment.side_effect = side_effect
            tool = _tool("lemlist_enrich_result")
            res = asyncio.run(tool.run(payload))
            return res.structured_content

    def _done(eid):
        return {"enrichmentId": eid, "enrichmentStatus": "done", "input": {}, "data": {}}

    out = _run({"enrichment_id": ["enr_1", "enr_2"]}, [_done("enr_1"), _done("enr_2")])
    assert [r["enrichment_id"] for r in out["results"]] == ["enr_1", "enr_2"]

    out = _run({"enrichment_id": "enr_1"}, [_done("enr_1")])
    assert [r["enrichment_id"] for r in out["results"]] == ["enr_1"]


def test_enrich_bulk_falls_back_to_position_when_metadata_is_unusable():
    # lemlist renvoie `metadata` dans deux formes différentes dans sa propre
    # doc : la lire aveuglément casserait l'appariement.
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        inst.bulk_enrich.return_value = [
            {"id": "enr_1", "metadata": "some_id"},   # chaîne nue
            {"id": "enr_2", "metadata": {"index": "1"}},
        ]
        out = _tool("lemlist_enrich_bulk").fn(people=[
            {"email": "a@acme.fr", "actions": ["find_email"]},
            {"email": "b@acme.fr", "actions": ["find_email"]},
        ])
        assert [r["index"] for r in out["submitted"]] == [0, 1]


# --- ce que le live a appris (2026-08-25, clé réelle Folk GTM) ----------------


def test_enrich_result_flags_done_but_empty_as_not_settled():
    # Relevé en live : lemlist bascule parfois sur `done` avant d'avoir posé la
    # charge utile (`data` vide, peuplé au relevé suivant). Compter ça comme
    # terminé ferait conclure « pas trouvé » sur une donnée qui arrive juste après.
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        client_cls.return_value.get_enrichment.return_value = {
            "enrichmentId": "enr_1", "enrichmentStatus": "done", "input": {}, "data": {},
        }
        out = _tool("lemlist_enrich_result").fn(enrichment_id="enr_1")

        assert "warning" in out["results"][0]
        assert out["recheck_suggested"] == ["enr_1"]
        # …mais `all_done` reste vrai : un résultat légitimement vide le
        # resterait pour toujours, et une boucle `while not all_done` ne
        # terminerait jamais. Le re-relevé est une suggestion, pas une attente.
        assert out["all_done"] is True


def test_enrich_result_digests_only_what_carries_a_value():
    # `data` porte la clé de l'axe demandé même vide, et `notFound: false` a été
    # vu sur une charge sans numéro : seule la valeur fait foi.
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        client_cls.return_value.get_enrichment.return_value = {
            "enrichmentId": "enr_1", "enrichmentStatus": "done", "input": {},
            "data": {
                "email": {"email": "aaron.levie@box.com", "notFound": False,
                          "status": "deliverable"},
                "phone": {"notFound": False},        # pas de numéro malgré notFound=false
                "linkedin": {},                       # profil non résolu
            },
        }
        out = _tool("lemlist_enrich_result").fn(enrichment_id="enr_1")

        found = out["results"][0]["found"]
        assert found["email"] == "aaron.levie@box.com"
        assert found["email_status"] == "deliverable"
        assert "phone" not in found
        assert "linkedin" not in found
        assert out["all_done"] is True
        assert "warning" not in out["results"][0]


def test_enrich_result_digests_a_verify_only_payload():
    # `verify_email` seul ne rend qu'un statut, sans adresse.
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        client_cls.return_value.get_enrichment.return_value = {
            "enrichmentId": "enr_1", "enrichmentStatus": "done", "input": {},
            "data": {"email": {"status": "undeliverable"}},
        }
        out = _tool("lemlist_enrich_result").fn(enrichment_id="enr_1")

        assert out["results"][0]["found"] == {"email_status": "undeliverable"}
        assert out["all_done"] is True


def test_enrich_result_digests_a_linkedin_profile():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        client_cls.return_value.get_enrichment.return_value = {
            "enrichmentId": "enr_1", "enrichmentStatus": "done", "input": {},
            "data": {"linkedin": {"firstName": "Bill", "lastName": "Gates",
                                  "tagline": "Chair, Gates Foundation",
                                  "linkedinMemberId": "251749025"}},
        }
        out = _tool("lemlist_enrich_result").fn(enrichment_id="enr_1")

        li = out["results"][0]["found"]["linkedin"]
        assert li["firstName"] == "Bill"
        assert "linkedinMemberId" not in li  # digest, pas recopie du profil entier
