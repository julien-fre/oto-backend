"""Dispatch `op=` des tools `silae_*` (ADR 0047 §Amendement, appliqué au connecteur
silae le 2026-08-11 : 12 tools → 4).

Ce que ce fichier verrouille, et que rien ne couvrait avant : la SURFACE. Les autres
tests qui nomment « silae » (`test_cascade_walker`, `test_connector_rbac`,
`test_credential_fields_shared`, `test_reachable_team_key`) s'en servent comme
EXEMPLE de connecteur multi-champs byo_user — aucun ne touche `tools/silae.py`. Or la
consolidation par `op=` déplace précisément le risque là : une op mal câblée appelle
silencieusement la mauvaise méthode du client, et rien ne casse au boot.

Trois familles de garanties :
  1. chaque op → la méthode client attendue, avec les arguments dans le bon ORDRE
     (Silae prend `(dossier, matricule, periode)` positionnellement — une inversion
     matricule/periode passe le typage et rend un bulletin faux) ;
  2. les refus : op inconnue nommant les ops valides, argument obligatoire nommant
     l'op ET l'argument, argument fourni mais non utilisé par l'op (le mode de panne
     propre à `op=` : un résultat crédible à côté de la demande) ;
  3. **le mutisme des écritures** : `SilaeClient` porte 4 méthodes qui MODIFIENT la
     paie (`ajouter_element_variable`, `ajouter_prime`, `ajouter_heures`,
     `confirmer_saisies`). Aucune n'est exposée — on joue toutes les ops et on exige
     qu'aucune ne soit touchée, plus un contrôle statique du module.
"""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import McpError

# Les 4 méthodes du client qui ÉCRIVENT dans la paie. Rien ici ne doit les atteindre.
_WRITE_METHODS = ("ajouter_element_variable", "ajouter_prime", "ajouter_heures",
                  "confirmer_saisies")

# Toute la surface, op par op — sert au dispatch ET au contrôle de mutisme.
_ALL_OPS = [
    ("silae_dossier", {"op": "list"}),
    ("silae_dossier", {"op": "numbers"}),
    ("silae_dossier", {"op": "info", "numero_dossier": "001"}),
    ("silae_dossier", {"op": "current_period", "numero_dossier": "001"}),
    ("silae_employee", {"numero_dossier": "001", "op": "list"}),
    ("silae_employee", {"numero_dossier": "001", "op": "get",
                        "matricule_salarie": "0001"}),
    ("silae_employee", {"numero_dossier": "001", "op": "jobs"}),
    ("silae_payslip", {"numero_dossier": "001", "periode": "2026-05", "op": "list"}),
    ("silae_payslip", {"numero_dossier": "001", "periode": "2026-05", "op": "header",
                       "matricule_salarie": "0001"}),
    ("silae_payslip", {"numero_dossier": "001", "periode": "2026-05", "op": "lines",
                       "matricule_salarie": "0001"}),
    ("silae_payslip", {"numero_dossier": "001", "periode": "2026-05", "op": "totals",
                       "matricule_salarie": "0001"}),
    ("silae_variables_to_enter", {"numero_dossier": "001"}),
]


@pytest.fixture
def client(monkeypatch):
    """Faux `SilaeClient` + credential résolu.

    `register()` fait son `from oto.tools.silae import SilaeClient` À L'INTÉRIEUR de la
    fonction : patcher l'attribut du package AVANT `_tool()` suffit, sans toucher au
    module. Le credential est stubbé pour que `_client()` n'aille pas lire le coffre.
    """
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.silae.SilaeClient", lambda **kw: inst)
    monkeypatch.setattr(
        "oto_mcp.access.resolve_credential_fields",
        lambda provider: {"client_id": "id", "client_secret": "sec",
                          "subscription_key": "sub"},
    )
    return inst


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import silae as S

    m = FastMCP("t")
    S.register(m)
    return asyncio.run(m.get_tool(name)).fn


def test_the_surface_is_exactly_the_four_consolidated_tools(client):
    """12 → 4. Un tool en plus (ou un ancien nom ressuscité) doit se voir ici."""
    from fastmcp import FastMCP
    from oto_mcp.tools import silae as S

    m = FastMCP("t")
    S.register(m)
    assert sorted(t.name for t in asyncio.run(m.list_tools())) == [
        "silae_dossier", "silae_employee", "silae_payslip",
        "silae_variables_to_enter",
    ]


# --- dossiers -----------------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_dossiers"),
    ("numbers", {}, "list_numeros_dossiers"),
    ("info", {"numero_dossier": "001"}, "dossier_infos"),
    ("current_period", {"numero_dossier": "001"}, "dossier_periode_en_cours"),
])
def test_dossier_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("silae_dossier")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_dossier_defaults_to_the_reachable_list(client):
    """Défaut = LECTURE la plus large, jamais une op qui a besoin d'un argument."""
    _tool("silae_dossier")()
    client.list_dossiers.assert_called_once()


def test_dossier_list_refuses_a_dossier_filter(client):
    """`silae_dossier(numero_dossier="001")` sans op rendrait la liste COMPLÈTE : un
    résultat crédible à côté de la demande. Refus nommant l'op qui l'honore."""
    with pytest.raises(McpError, match="op='list' n'utilise pas numero_dossier"):
        _tool("silae_dossier")(numero_dossier="001")
    client.list_dossiers.assert_not_called()

    with pytest.raises(McpError, match="numero_dossier"):
        _tool("silae_dossier")(op="numbers", numero_dossier="001")


# --- salariés -----------------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_salaries"),
    ("get", {"matricule_salarie": "0001"}, "salarie_matricule"),
    ("jobs", {}, "list_salarie_emplois"),
])
def test_employee_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("silae_employee")(numero_dossier="001", op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_employee_get_passes_dossier_then_matricule(client):
    """Ordre positionnel du client — l'inverser interrogerait un matricule inexistant
    dans un dossier inexistant, sans erreur de typage."""
    _tool("silae_employee")(numero_dossier="001", op="get", matricule_salarie="0001")
    assert client.salarie_matricule.call_args.args == ("001", "0001")


def test_employee_jobs_defaults_to_all_employees_and_current_jobs(client):
    """Sémantique héritée de `silae_employee_jobs` : matricule vide = tous les
    salariés, `type_emplois=0` = emplois courants seulement."""
    _tool("silae_employee")(numero_dossier="001", op="jobs")
    assert client.list_salarie_emplois.call_args.args == ("001", "", 0)


def test_employee_jobs_honours_matricule_and_type(client):
    _tool("silae_employee")(numero_dossier="001", op="jobs",
                            matricule_salarie="0001", type_emplois=1)
    assert client.list_salarie_emplois.call_args.args == ("001", "0001", 1)


def test_employee_list_refuses_arguments_it_would_ignore(client):
    """L'ancien tool s'appelait `silae_employee(numero_dossier, matricule_salarie)` :
    un appelant qui garde ce réflexe doit recevoir une erreur, pas le trombinoscope
    complet du dossier."""
    with pytest.raises(McpError, match="op='list' n'utilise pas matricule_salarie"):
        _tool("silae_employee")(numero_dossier="001", matricule_salarie="0001")
    client.list_salaries.assert_not_called()

    with pytest.raises(McpError, match="type_emplois"):
        _tool("silae_employee")(numero_dossier="001", op="list", type_emplois=1)

    with pytest.raises(McpError, match="type_emplois"):
        _tool("silae_employee")(numero_dossier="001", op="get",
                                matricule_salarie="0001", type_emplois=1)


# --- bulletins ----------------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "bulletins"),
    ("header", {"matricule_salarie": "0001"}, "bulletin_entete"),
    ("lines", {"matricule_salarie": "0001"}, "bulletin_lignes"),
    ("totals", {"matricule_salarie": "0001"}, "bulletin_cumuls"),
])
def test_payslip_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("silae_payslip")(numero_dossier="001", periode="2026-05", op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_payslip_list_scopes_to_the_whole_dossier_by_default(client):
    """`bulletins` attend `(dossier, periode, matricule)` — matricule vide = tous."""
    _tool("silae_payslip")(numero_dossier="001", periode="2026-05")
    assert client.bulletins.call_args.args == ("001", "2026-05", "")


def test_payslip_list_filters_on_one_employee_when_asked(client):
    _tool("silae_payslip")(numero_dossier="001", periode="2026-05",
                           matricule_salarie="0001")
    assert client.bulletins.call_args.args == ("001", "2026-05", "0001")


@pytest.mark.parametrize("op,method", [
    ("header", "bulletin_entete"), ("lines", "bulletin_lignes"),
    ("totals", "bulletin_cumuls"),
])
def test_payslip_detail_passes_dossier_matricule_periode_in_that_order(
        client, op, method):
    """⚠️ Le détail du bulletin prend `(dossier, MATRICULE, PERIODE)` alors que la
    liste prend `(dossier, PERIODE, matricule)` — deux ordres pour trois arguments du
    même type. Une inversion rendrait un bulletin d'une autre période sans lever."""
    _tool("silae_payslip")(numero_dossier="001", periode="2026-05", op=op,
                           matricule_salarie="0001")
    assert getattr(client, method).call_args.args == ("001", "0001", "2026-05")


# --- variables (resté seul) ---------------------------------------------------

def test_variables_to_enter_stays_a_single_capability(client):
    """Pas d'`op` : une seule capacité. Le tool est resté INCHANGÉ par la
    consolidation (même nom, même signature) — ses refs doctrine survivent."""
    import inspect

    fn = _tool("silae_variables_to_enter")
    assert list(inspect.signature(fn).parameters) == ["numero_dossier"]
    fn(numero_dossier="001")
    client.list_variables_a_saisir.assert_called_once_with("001")


# --- refus --------------------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,expected", [
    ("silae_dossier", {}, "'list', 'numbers', 'info' ou 'current_period'"),
    ("silae_employee", {"numero_dossier": "001"}, "'list', 'get' ou 'jobs'"),
    ("silae_payslip", {"numero_dossier": "001", "periode": "2026-05"},
     "'list', 'header', 'lines' ou 'totals'"),
])
def test_unknown_op_is_refused_with_the_allowed_list(client, tool, kwargs, expected):
    """Une op inconnue doit lever en nommant les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être") as e:
        _tool(tool)(op="nope", **kwargs)
    assert expected in str(e.value)   # les ops valides sont NOMMÉES dans le message


@pytest.mark.parametrize("tool,kwargs,missing", [
    ("silae_dossier", {"op": "info"}, "numero_dossier"),
    ("silae_dossier", {"op": "current_period"}, "numero_dossier"),
    ("silae_employee", {"numero_dossier": "001", "op": "get"}, "matricule_salarie"),
    ("silae_payslip", {"numero_dossier": "001", "periode": "2026-05",
                       "op": "header"}, "matricule_salarie"),
    ("silae_payslip", {"numero_dossier": "001", "periode": "2026-05",
                       "op": "lines"}, "matricule_salarie"),
    ("silae_payslip", {"numero_dossier": "001", "periode": "2026-05",
                       "op": "totals"}, "matricule_salarie"),
])
def test_missing_required_arg_names_the_op_and_the_arg(client, tool, kwargs, missing):
    with pytest.raises(McpError, match=missing) as e:
        _tool(tool)(**kwargs)
    assert f"op='{kwargs['op']}'" in str(e.value)


def test_empty_matricule_counts_as_missing_on_a_single_payslip_op(client):
    """Silae traite `""` comme « tous les salariés » : la laisser passer sur un op
    mono-bulletin rendrait un résultat plausible et faux."""
    with pytest.raises(McpError, match="matricule_salarie"):
        _tool("silae_payslip")(numero_dossier="001", periode="2026-05", op="header",
                               matricule_salarie="")
    client.bulletin_entete.assert_not_called()


# --- lecture seule : le mutisme des écritures ---------------------------------

@pytest.mark.parametrize("tool,kwargs", _ALL_OPS)
def test_no_op_ever_reaches_a_write_method(client, tool, kwargs):
    """Invariant central de ce connecteur : la surface est en LECTURE SEULE. Chaque op
    est jouée, puis les 4 méthodes qui modifient la paie sont vérifiées muettes — une
    op mal câblée sur `ajouter_prime`/`confirmer_saisies` toucherait des bulletins
    réels."""
    _tool(tool)(**kwargs)
    for m in _WRITE_METHODS:
        getattr(client, m).assert_not_called()


@pytest.mark.parametrize("tool,kwargs", [
    ("silae_dossier", {}),
    ("silae_employee", {"numero_dossier": "001"}),
    ("silae_payslip", {"numero_dossier": "001", "periode": "2026-05"}),
])
def test_every_default_op_is_a_read(client, tool, kwargs):
    """« Aucune op d'écriture atteignable par défaut » : appelé SANS `op`, chaque tool
    fait une lecture de liste."""
    _tool(tool)(**kwargs)
    assert client.method_calls, "le défaut doit appeler le client"
    called = {c[0] for c in client.method_calls}
    assert called <= {"list_dossiers", "list_salaries", "bulletins"}
    for m in _WRITE_METHODS:
        getattr(client, m).assert_not_called()


def test_the_module_never_names_a_write_method():
    """Contrôle STATIQUE, complémentaire du dynamique : les tests ci-dessus ne
    couvrent que les ops qu'ils connaissent. Si quelqu'un ajoute demain une op
    d'écriture, ce test tombe — et c'est le signal qu'il faut alors un cas de test
    dédié (méthode appelée + voisines muettes), pas un simple vert."""
    src = (Path(__file__).resolve().parent.parent
           / "oto_mcp" / "tools" / "silae.py").read_text()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    body = code.split('"""', 2)[2] if code.count('"""') >= 2 else code
    for m in _WRITE_METHODS:
        assert f".{m}(" not in body, (
            f"{m} est une ÉCRITURE Silae : l'exposer demande un cas de test dédié "
            "et une décision produit (cf. docstring du module).")
