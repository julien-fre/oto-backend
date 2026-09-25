"""Connecteur Clay : une carte, deux sortes d'entrées (api / table).

Tools appelés en direct sur un FastMCP nu, clients oto-core remplacés par des faux.
Les entrées visibles (`_entries`) et la résolution d'une entrée sont simulées :
ni base, ni coffre, ni réseau.

Porte `exige_pin_oto_core` : le client `oto.tools.clay` n'existe qu'à partir du tag
oto-core qui l'ajoute — sous un venv en retard sur le pin, non concluant, pas rouge.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.exige_pin_oto_core

from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import clay as C

HOOK = "https://api.clay.com/v3/sources/webhook/pull-in-data-from-a-webhook-abc"
CURL = f"curl -X POST '{HOOK}' -H 'x-clay-webhook-auth: tok' -d '{{}}'"


class _FauxHook:
    instances: list = []

    def __init__(self, url, auth_token=None):
        self.url, self.token, self.pushed = url, auth_token, []
        self.boom_at: dict = {}
        _FauxHook.instances.append(self)

    def push(self, row):
        i = len(self.pushed)
        self.pushed.append(row)
        if i in self.boom_at:
            raise self.boom_at[i]
        return {"ok": True}


class _FauxApi:
    def __init__(self, api_key=None):
        self.api_key = api_key

    def get_me(self):
        return {"user": {"id": "u"}}

    def get_credit_balance(self):
        return {"balance": 10}

    def run_routine(self, routine_id, items):
        return {"routine_run_id": "r1", "status": "in_progress"}

    def query_tables(self, query, cursor=None, limit=None):
        from oto.tools.common import UpstreamHTTPError
        raise UpstreamHTTPError(403, {"message": "forbidden"}, service="clay")


@pytest.fixture
def env(monkeypatch):
    import oto.tools.clay as pkg

    _FauxHook.instances = []
    monkeypatch.setattr(pkg, "ClayTableWebhook", _FauxHook)
    monkeypatch.setattr(pkg, "ClayClient", _FauxApi)
    monkeypatch.setattr(C.access, "current_user_sub_or_raise", lambda: "sub-1")
    monkeypatch.setattr(C.egress, "check_url", lambda *a, **k: None)

    entries = {
        "Leads": {"kind": "table", "scope": "org", "entity": ("org", "1"),
                  "meta": {"kind": "table"}},
        "Perso": {"kind": "api", "scope": "member", "entity": ("member", "1:sub-1"),
                  "meta": {"kind": "api"}},
    }
    secrets = {"Leads": {"webhook": CURL}, "Perso": {"api_key": "k-perso"}}
    monkeypatch.setattr(C, "_entries", lambda sub: entries)
    monkeypatch.setattr(C.access, "resolve_credential_fields",
                        lambda prov, account=None: secrets[account])
    metas = []
    from oto_mcp import credentials_store
    monkeypatch.setattr(credentials_store, "update_meta",
                        lambda *a, **k: metas.append(a) or True)

    from fastmcp import FastMCP
    m = FastMCP("t")
    C.register(m)

    def fn(name):
        return asyncio.run(m.get_tool(name)).fn

    return {"fn": fn, "entries": entries, "metas": metas}


def test_chaque_tool_a_une_description():
    from fastmcp import FastMCP
    m = FastMCP("t")
    C.register(m)
    tools = asyncio.run(m.list_tools())
    assert {t.name for t in tools} >= {"clay_push_rows", "clay_list_tables",
                                       "clay_run_routine", "clay_search"}
    assert all((t.description or "").strip() for t in tools)


def test_liste_les_tables_sans_la_cle(env):
    out = env["fn"]("clay_list_tables")()
    assert [t["name"] for t in out["tables"]] == ["Leads"]
    assert out["api_key_set"] is True


def test_push_une_ligne_lit_le_curl(env):
    out = env["fn"]("clay_push_rows")(table="Leads", row={"name": "Acme"})
    assert out["sent"] is True
    h = _FauxHook.instances[0]
    assert (h.url, h.token) == (HOOK, "tok")
    # compteur posé sur l'entité où vit l'entrée, lié à l'URL
    ent_type, ent_id, conn, acct, patch = env["metas"][0]
    assert (ent_type, ent_id, conn, acct) == ("org", "1", "clay", "Leads")
    assert patch["submissions"] == 1 and patch["submissions_url"]


def test_row_et_rows_exclusifs(env):
    with pytest.raises(McpError):
        env["fn"]("clay_push_rows")(table="Leads")
    with pytest.raises(McpError):
        env["fn"]("clay_push_rows")(table="Leads", row={"a": 1}, rows=[{"a": 1}])


def test_table_inconnue_liste_les_connues(env):
    with pytest.raises(McpError) as e:
        env["fn"]("clay_push_rows")(table="Nope", row={"a": 1})
    assert "Leads" in str(e.value)


def test_une_entree_api_nest_pas_une_table(env):
    with pytest.raises(McpError):
        env["fn"]("clay_push_rows")(table="Perso", row={"a": 1})


def test_dry_run_nenvoie_rien(env):
    out = env["fn"]("clay_push_rows")(table="Leads", rows=[{"a": 1}, {"a": 2}], dry_run=True)
    assert out["dry_run"] is True and out["would_send"] == 2
    assert _FauxHook.instances == [] or _FauxHook.instances[0].pushed == []
    assert env["metas"] == []


def test_plafond_par_appel(env):
    with pytest.raises(McpError):
        env["fn"]("clay_push_rows")(table="Leads", rows=[{"a": i} for i in range(C.MAX_ROWS + 1)])


def test_lot_continue_sur_422_sarrete_sur_401(env, monkeypatch):
    from oto.tools.common import UpstreamHTTPError
    monkeypatch.setattr(C, "PACE_S", 0)
    orig = _FauxHook.__init__

    def init(self, url, auth_token=None):
        orig(self, url, auth_token)
        self.boom_at = {1: UpstreamHTTPError(422, "bad", service="clay"),
                        2: UpstreamHTTPError(401, "no", service="clay")}
    monkeypatch.setattr(_FauxHook, "__init__", init)
    out = env["fn"]("clay_push_rows")(table="Leads", rows=[{"a": i} for i in range(5)])
    assert out["succeeded"] == 1
    assert [f["index"] for f in out["failed"]] == [1, 2, 3, 4]
    assert len(_FauxHook.instances[0].pushed) == 3  # rien après le 401


def test_mur_des_50000(env):
    env["entries"]["Leads"]["meta"].update({"submissions": C.WEBHOOK_LIMIT,
                                            "submissions_url": C._url_mark(HOOK)})
    with pytest.raises(McpError) as e:
        env["fn"]("clay_push_rows")(table="Leads", row={"a": 1})
    assert "nouveau webhook" in str(e.value)


def test_nouveau_webhook_remet_le_compteur_a_zero(env):
    env["entries"]["Leads"]["meta"].update({"submissions": C.WEBHOOK_LIMIT,
                                            "submissions_url": "autre-url"})
    assert env["fn"]("clay_push_rows")(table="Leads", row={"a": 1})["sent"] is True


def test_api_prend_lentree_api(env):
    out = env["fn"]("clay_account")()
    assert out["credits"] == {"balance": 10}


def test_api_absente_message_actionnable(env):
    del env["entries"]["Perso"]
    with pytest.raises(McpError) as e:
        env["fn"]("clay_account")()
    assert "api" in str(e.value)


def test_routine_borne_1_100(env):
    with pytest.raises(McpError):
        env["fn"]("clay_run_routine")(routine_id="function:t_1", items=[])
    with pytest.raises(McpError):
        env["fn"]("clay_run_routine")(routine_id="function:t_1", items=[{"inputs": {}}])
    out = env["fn"]("clay_run_routine")(
        routine_id="function:t_1", items=[{"id": "a", "inputs": {"x": 1}}])
    assert out["routine_run_id"] == "r1"


def test_tables_403_dit_enterprise(env):
    with pytest.raises(McpError) as e:
        env["fn"]("clay_tables_query")(query={})
    assert "Enterprise" in str(e.value)


# --- sonde de connexion -------------------------------------------------------

def test_sonde_api_appelle_me(monkeypatch):
    import oto.tools.clay as pkg
    appels = []

    class F:
        def __init__(self, api_key=None):
            appels.append(api_key)

        def get_me(self):
            return {}
    monkeypatch.setattr(pkg, "ClayClient", F)
    C._verify({"api_key": "k"}, {"kind": "api"})
    assert appels == ["k"]


def test_sonde_table_forme_seule(monkeypatch):
    monkeypatch.setattr(C.egress, "check_url", lambda *a, **k: None)
    C._verify({"webhook": CURL}, {"kind": "table"})
    with pytest.raises(ValueError):
        C._verify({"webhook": "https://example.com/x"}, {"kind": "table"})


def test_registre_declare_une_carte_a_deux_sortes():
    from oto_mcp import providers
    c = providers.REGISTRY["clay"]
    assert c.field_discriminator == "kind"
    assert {f.name for f in c.fields_for({"kind": "table"})} == {"kind", "webhook", "auth_token"}
    assert {f.name for f in c.fields_for({"kind": "api"})} == {"kind", "api_key"}
    assert "platform" not in c.auth_modes


def test_validation_ecarte_les_champs_de_lautre_sorte():
    from oto_mcp import credentials_store
    kept = credentials_store.validate_fields(
        "clay", {"kind": "table", "webhook": CURL, "api_key": "stray"})
    assert "api_key" not in kept and kept["webhook"] == CURL
    with pytest.raises(credentials_store.CredentialFieldsInvalid):
        credentials_store.validate_fields("clay", {"kind": "api"})


def test_budget_dhorloge_rend_un_recu_partiel(env, monkeypatch):
    """Passé le budget, le reste part en `failed` avec la consigne de renvoyer — et le
    compteur compte ce qui est parti."""
    monkeypatch.setattr(C, "PACE_S", 0)
    monkeypatch.setattr(C, "BATCH_BUDGET_S", 0.0)
    out = env["fn"]("clay_push_rows")(table="Leads", rows=[{"a": i} for i in range(4)])
    assert out["succeeded"] == 1
    assert [f["index"] for f in out["failed"]] == [1, 2, 3]
    assert "time budget" in out["failed"][0]["error"]
    assert env["metas"][-1][-1]["submissions"] == 1


_REF = "\n".join([
    "# Clay search query reference", "intro",
    "## Grammar", "g" * 10,
    "## Operators", "o" * 10,
    "## Field docs", "### People fields", "p" * 10, "### Companies fields", "c" * 50,
    "## Examples", "e" * 70_000,
])


def test_reference_sans_section_sommaire_et_essentiel():
    out = C._reference_view(_REF, None, 0)
    assert [s["title"] for s in out["sections"]][:3] == [
        "Clay search query reference", "Grammar", "Operators"]
    assert "## Grammar" in out["content"] and "## Operators" in out["content"]
    assert "## Examples" not in out["content"]


def test_reference_section_inclut_ses_sous_sections():
    out = C._reference_view(_REF, "field docs", 0)
    assert "### People fields" in out["content"] and "### Companies fields" in out["content"]
    assert "## Examples" not in out["content"]
    assert "next_offset" not in out


def test_reference_paginee():
    first = C._reference_view(_REF, "Examples", 0)
    assert len(first["content"]) == C.REFERENCE_CHUNK and first["next_offset"] == C.REFERENCE_CHUNK
    last = C._reference_view(_REF, "Examples", 60_000)
    assert "next_offset" not in last


def test_reference_section_inconnue_liste_les_titres():
    with pytest.raises(McpError) as e:
        C._reference_view(_REF, "nope", 0)
    assert "Grammar" in str(e.value)
