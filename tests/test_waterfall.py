"""Les outils waterfall (`tools/waterfall.py`) — verrouille : six connecteurs
(`waterfall_email|phone|company|person|people|tech`) qui ne portent qu'un réglage, chargés
par deux modules (`waterfall` les lit, `waterfall_lignes` écrit dans un tableau) ; un outil
`<ns>` en lecture et un `<ns>_act` par connecteur ; des étapes qui ne nomment que des
outils montés ; un ordre par défaut neutre — les étapes synchrones avant les asynchrones,
qu'une étape `pending` arrête. La cascade elle-même : `tests/waterfall/`."""
import asyncio

import pytest
from fastmcp import FastMCP

from oto_mcp import providers
from oto_mcp.tool_visibility import namespace_of
from oto_mcp.tools import waterfall as outils
from oto_mcp.tools import waterfall_lignes
from oto_mcp.waterfall import email, ordre, personne, plan, societe, telephone

from _waterfall_faux import NAMESPACES


# --- registre & surface -----------------------------------------------------------

@pytest.mark.parametrize("ns", NAMESPACES)
def test_six_connecteurs_qui_ne_portent_qu_un_reglage(ns):
    c = providers.REGISTRY[ns]
    assert c.kind == "tools" and c.modules == ("waterfall", "waterfall_lignes")
    assert c.auth_modes == frozenset({"byo_user", "byo_org"}) and not c.keyed
    assert [f.name for f in c.credential_fields] == ["steps"]
    assert c.credential_fields[0].secret is False and c.default_active is False
    # Le nom entier EST le namespace : sans quoi l'outil tomberait hors de tout
    # connecteur, donc hors gate d'activation.
    assert namespace_of(ns) == ns and namespace_of(f"{ns}_act") == ns


def test_un_outil_par_connecteur_decrit_et_en_lecture():
    m = FastMCP("t")
    outils.register(m)
    waterfall_lignes.register(m)
    tools = {t.name: t for t in asyncio.run(m._list_tools())}
    # `<ns>` lit ; `<ns>_act` écrit dans un tableau (`waterfall_lignes.py`).
    assert set(tools) == set(NAMESPACES) | {f"{ns}_act" for ns in NAMESPACES}
    for name in NAMESPACES:
        assert tools[f"{name}_act"].annotations.destructiveHint is True
    tools = {n: t for n, t in tools.items() if n in NAMESPACES}
    for t in tools.values():
        assert t.description and t.annotations.readOnlyHint is True
        props = t.parameters["properties"]
        assert props["providers"].get("description") and props["dry_run"].get("description")


def test_les_etapes_ne_nomment_que_des_outils_montes():
    from oto_mcp.tools import register_all
    m = FastMCP("t")
    register_all(m)
    names = {t.name for t in asyncio.run(m._list_tools())}
    for steps in plan.STEPS_BY_NAMESPACE.values():
        for s in steps:
            assert s.tool in names, s.tool
            assert not s.tool.startswith("waterfall")
            if s.collect:
                assert s.collect.tool in names, s.collect.tool


def test_pas_d_ai_ark_pour_le_telephone_mais_pour_societe_et_personne():
    assert "aiark" not in {s.key for s in telephone.PHONE_STEPS}
    assert "aiark" in {s.key for s in societe.COMPANY_STEPS}
    assert "aiark" in {s.key for s in personne.PERSON_STEPS}
    for steps in (email.EMAIL_STEPS, telephone.PHONE_STEPS, personne.PERSON_STEPS):
        assert "lemlist" in {s.key for s in steps}


@pytest.mark.parametrize("ns", NAMESPACES)
def test_une_carte_de_reglages_se_pose_et_se_relit_par_le_coffre(ns):
    """Le front pose ce réglage par la MÊME route qu'une clé (champ unique : `api_key`
    côté org, le nom du champ côté membre). Ce qui revient du coffre doit être
    `{"steps": …}`, sans espace (copier-coller), sinon la cascade lirait le défaut."""
    from oto_mcp import credentials_store
    c = providers.REGISTRY[ns]
    assert c.secret_kind == "settings" and c.auth_method == "secret"
    kept = credentials_store.validate_fields(ns, {"steps": "lemlist, hunter , apollo"})
    stored = credentials_store.pack_secret(ns, kept)
    assert credentials_store.unpack_secret(ns, stored) == {"steps": "lemlist,hunter,apollo"}
    assert ordre._parse_steps("lemlist,hunter,apollo") == ["lemlist", "hunter", "apollo"]


@pytest.mark.parametrize("ns", NAMESPACES)
def test_la_carte_liste_les_etapes_dans_l_ordre_par_defaut(ns):
    """Le réglage `steps` annonce ses clés dans l'ordre par défaut : un ordre changé dans
    le code sans la carte la ferait mentir (vu une fois)."""
    aide = providers.REGISTRY[ns].credential_fields[0].help
    annonce = [k.strip(" .") for k in aide.split("Available:")[1].split(",")]
    assert annonce == [s.key for s in plan.STEPS_BY_NAMESPACE[ns]]


# --- ordre par défaut : neutre ----------------------------------------------------

@pytest.mark.parametrize("ns", NAMESPACES)
def test_les_etapes_synchrones_passent_avant_les_asynchrones(ns):
    """Une étape `pending` arrête la cascade : une étape synchrone rangée après elle ne
    tournerait jamais. L'ordre par défaut ne doit rien à une grille de prix d'instance
    (une instance pose le sien par le réglage `steps`). Une étape qui, en attente, passe
    la main (`falls_through` : Pipecorn en tête du téléphone, Julien 29/10/2026)
    n'arrête rien : elle peut précéder les synchrones, avec sa propre attente bornée."""
    bloquantes = [s.collect is not None for s in plan.STEPS_BY_NAMESPACE[ns]
                  if not s.falls_through]
    assert bloquantes == sorted(bloquantes), ns
    for s in plan.STEPS_BY_NAMESPACE[ns]:
        if s.falls_through:
            assert s.collect is not None and s.poll is not None, (ns, s.key)


def test_fullenrich_la_plus_lente_ferme_la_marche():
    assert [s.key for s in email.EMAIL_STEPS][-1] == "fullenrich"
    assert [s.key for s in telephone.PHONE_STEPS][-1] == "fullenrich"
