"""What the approval card says, and what the person may change on it: the sentence is
written from the call and never says LESS than what runs; an edit lands only on a field
the card offered."""
from __future__ import annotations

import pytest

from oto_mcp.capabilities.copilot import carte


@pytest.mark.parametrize("name,args", [
    ("data_write", {"datastore": 7, "id": "r1", "row": {"a": 1}, "upsert": True,
                    "key": "email"}),
    ("data_update_where", {"datastore": 7, "set": {"s": "x"}, "dry_run": False}),
    ("oto_trigger", {"op": "update", "trigger_id": 3, "cron": "0 9 * * *",
                     "tools": ["a"], "procedure": "p"}),
    ("oto_doc", {"op": "create", "title": "T", "body_md": "b", "visibility": "org"}),
    ("someconnector_act", {"op": "send", "to": "a", "subject": "s", "body": "b"}),
    ("oto_call", {"name": "someconnector_act", "arguments": {"op": "send", "to": "a",
                                                             "_account": "two"}}),
])
def test_la_phrase_nomme_chaque_argument(name, args):
    """Every argument of the TARGET, except the server's own axes, appears in the title:
    named by the sentence, or listed after it."""
    phrase = carte.phrase(name, args)
    _, a = carte.cible(name, args)
    connus = {"op", "datastore", "trigger_id", "doc_id", "id", "row", "rows", "set",
              "filter", "filters", "cron", "tz", "label", "title", "body_md", "enabled"}
    for k in a:
        if k in ("_org", "_project", "_run_id", "_group"):
            continue
        if k in connus and k not in ("upsert", "key"):
            continue
        assert k in phrase, (k, phrase)


def test_phrase_dit_tout_le_tableau_sans_filtre():
    assert "ALL rows" in carte.phrase("data_update_where",
                                      {"datastore": 2, "set": {"statut": "x"}})


def test_detail_sans_les_axes_du_serveur():
    d = carte.detail("data_write", {"datastore": 1, "row": {"a": 1}, "_org": 9,
                                    "_run_id": "r", "_account": "b"})
    assert "_org" not in d and "_run_id" not in d and "_account" in d


def test_modifications_seulement_sur_les_champs_offerts():
    args = {"op": "update", "trigger_id": 3, "cron": "0 9 * * *", "tz": "Europe/Paris",
            "procedure": "p"}
    offerts = {f["key"] for f in carte.champs_modifiables("oto_trigger", args)}
    assert offerts == {"cron", "tz"}
    out = carte.appliquer_modifications("oto_trigger", args, {
        "cron": "0 10 * * *", "procedure": "autre", "trigger_id": "999", "enabled": "true"})
    assert out["cron"] == "0 10 * * *"
    assert out["procedure"] == "p" and out["trigger_id"] == 3 and "enabled" not in out
    assert args["cron"] == "0 9 * * *"          # the original is left untouched


def test_modification_garde_le_type_et_la_couche():
    args = {"datastore": 1, "set": {"n": 3, "s": {"valeur": "a", "comment": "c"}}}
    out = carte.appliquer_modifications("data_update_where", args,
                                        {"set.n": "4", "set.s": "b", "set.z": "x"})
    assert out["set"]["n"] == 4
    assert out["set"]["s"] == {"valeur": "b", "comment": "c"}
    assert "z" not in out["set"]


def test_modification_dans_la_cible_d_oto_call():
    args = {"name": "data_create_datastore", "arguments": {"name": "t", "schema": {}}}
    out = carte.appliquer_modifications("oto_call", args, {"name": "t2"})
    assert out["arguments"]["name"] == "t2" and out["name"] == "data_create_datastore"


def test_une_valeur_non_texte_est_ignoree():
    args = {"op": "create", "title": "a"}
    out = carte.appliquer_modifications("oto_doc", args, {"title": {"x": 1}})
    assert out["title"] == "a"


def test_resume():
    assert carte.resume('{"rows": [1, 2], "total": 2}') == "2 rows"
    assert carte.resume('{"error": "nope"}') == "nope"
    assert carte.resume('{"total": 5}') == "5 total"
    assert carte.resume("plain\ntext") == "plain text"


def test_portee():
    assert carte.portee("data_write") == "data"
    assert carte.portee("oto_procedure") == "processes"
    assert carte.portee("oto_trigger") == "agents"
    assert carte.portee("fr_search") == "all"
