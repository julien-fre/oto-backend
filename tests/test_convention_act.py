"""Un connecteur sert DEUX outils : `<ns>` lit ou simule, `<ns>_act` agit (17/10/2026).

Décision d'Alexis, pour la sécurité : un client MCP autorise des OUTILS, pas des
arguments. `gmail_compose(mode="send")` envoyait sous l'autorisation du brouillon,
`gmail_message(op="trash")` vidait une boîte sous celle de la lecture. Cette garde rend la
règle mécanique, connecteur par connecteur :

- **nommage** : un connecteur migré ne sert que `<ns>` et `<ns>_act` ;
- **annotations** : `<ns>` est `LECTURE`, ou `SIMULATION` s'il prépare un brouillon ;
  `<ns>_act` écrit `destructiveHint` et `openWorldHint` — c'est lui que le client fait
  confirmer ;
- **ops** : aucune op de `<ns>` ne porte un verbe à effet (hors simulations nommées), et
  `<ns>_act` n'a que des ops à effet ; aucun paramètre qui déclenche (`mode`, `notify`,
  `send_*`) sur `<ns>` ;
- **retraits** : chaque ancien nom de `outils_retires.RENOMMAGES_ACT` n'est plus servi, et
  son remplaçant sert bien l'op annoncée.

`A_MIGRER` est la liste FERMÉE des connecteurs pas encore passés à la paire. Elle ne peut
que rétrécir : un connecteur qui devient conforme, ou qui n'existe plus, y rougit jusqu'à
ce qu'on l'en retire.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from oto_mcp import outils_retires, providers, tool_visibility

from _mcp_app import static_mcp

#: Les connecteurs pas encore migrés vers `<ns>` / `<ns>_act` (17/10/2026). Ne fait que
#: rétrécir.
A_MIGRER = frozenset({
    "affinity", "ahrefs", "aircall", "airtable", "amplitude", "apify", "apollo",
    "ashby", "attio", "bigquery", "bls", "bodacc", "boondmanager", "brevo", "brevoauto",
    "browser", "calendar", "ccn", "chat", "claap", "clay", "cloro", "cognism",
    "crunchbase", "culture", "dataforseo", "drive", "dropcontact", "figma", "finkare",
    "firecrawl", "fireflies", "folk", "foncier", "forager", "fr", "frenchtech",
    "fullenrich", "ga4", "github", "gocardless", "google", "google_ads", "gr", "grain",
    "granola", "greenhouse", "hellostock", "hithorizons", "http", "hubspot", "hunter",
    "infosec", "inqom", "instagram", "instagram_meta", "jev", "juris", "kaspr",
    "klaviyo", "leexi", "lemlist", "lever", "lightfield", "lighton", "linear",
    "linkedin_aiark", "linkedin_unipile", "loi", "lucca", "luma", "lusha", "mailpool",
    "make", "meta_ads", "microsoft", "minari", "modjo", "monid", "n8n", "nextmotion",
    "notion", "onet", "origami", "osm", "outlook_calendar", "payfit", "pennylane",
    "pennylane_firm", "pennylaneged", "phantombuster", "pipedrive", "planity",
    "posthog", "productlane", "promptwatch", "recruitee", "reddit", "resend", "routine",
    "salesforce", "sante", "searchapi", "sellsy", "serpapi", "serper", "sharepoint",
    "sheets", "signwell", "silae", "sivan", "slack", "snitcher", "spott", "stripe",
    "supabase", "tally", "tasks", "tavily", "teams", "teamtailor", "telegram",
    "theirstack", "threecx", "topograph", "transcription", "typeform", "ubersuggest",
    "unipile", "urba", "waalaxy", "web", "webflow", "whatsapp", "wordpress", "wttj",
    "yousign", "zapier", "zerobounce", "zoho", "zohoanalytics", "zohodesk",
})

#: Un verbe à effet dans le nom d'une op : il agit chez un tiers ou détruit.
VERBES_EFFET = re.compile(
    r"^(send|reply|forward|publish|broadcast|invite|remind|trash|archive|move|delete|"
    r"remove|rm|create|update|upsert|set|add|cancel|trigger|activate|post|react|"
    r"comment|review|push|upload|share|unshare|lend|revoke|disable|enable|call|request|"
    r"import)(_|$)")
#: Les ops qui SIMULENT : elles écrivent ce que personne ne voit et que rien ne détruit.
SIMULATIONS = frozenset({"draft"})
#: Les paramètres qui feraient agir l'outil qui lit.
PARAMETRES_DECLENCHEURS = re.compile(r"^(mode|notify|send_.*)$")


def _par_connecteur() -> dict[str, dict]:
    outils = asyncio.run(static_mcp().list_tools(run_middleware=False))
    par: dict[str, dict] = {}
    for t in outils:
        ns = tool_visibility.namespace_of(t.name)
        if providers.connector_for_namespace(ns) is not None:
            par.setdefault(ns, {})[t.name] = t
    return par


def _ops(outil) -> list[str]:
    op = (outil.parameters.get("properties") or {}).get("op") or {}
    if "enum" in op:
        return list(op["enum"])
    return [v for branche in op.get("anyOf", ()) for v in branche.get("enum", ())]


@pytest.fixture(scope="module")
def par_connecteur():
    return _par_connecteur()


def _migres(par_connecteur):
    return {ns: outils for ns, outils in par_connecteur.items() if ns not in A_MIGRER}


def test_un_connecteur_migre_ne_sert_que_sa_paire(par_connecteur):
    for ns, outils in _migres(par_connecteur).items():
        assert set(outils) <= {ns, f"{ns}_act"}, (ns, sorted(outils))


def test_a_migrer_ne_fait_que_retrecir(par_connecteur):
    for ns in A_MIGRER:
        assert ns in par_connecteur, f"`{ns}` n'existe plus : retire-le de A_MIGRER"
        assert not set(par_connecteur[ns]) <= {ns, f"{ns}_act"}, (
            f"`{ns}` sert sa paire : retire-le de A_MIGRER")


def test_les_annotations_disent_qui_lit_et_qui_agit(par_connecteur):
    for ns, outils in _migres(par_connecteur).items():
        if ns in outils:
            a = outils[ns].annotations
            assert a is not None, ns
            if a.readOnlyHint is not True:
                assert (a.readOnlyHint, a.destructiveHint, a.openWorldHint) == (
                    False, False, False), f"`{ns}` : LECTURE ou SIMULATION, rien d'autre"
                assert set(_ops(outils[ns])) & SIMULATIONS, (
                    f"`{ns}` n'est pas en lecture seule sans simuler quoi que ce soit")
        act = outils.get(f"{ns}_act")
        if act is not None:
            a = act.annotations
            assert a is not None and a.readOnlyHint is False, ns
            ecrits = a.model_dump(exclude_unset=True)
            assert "destructiveHint" in ecrits and "openWorldHint" in ecrits, (
                f"`{ns}_act` : destructiveHint et openWorldHint s'ÉCRIVENT")


def test_l_outil_qui_lit_n_a_aucune_op_ni_aucun_parametre_qui_agit(par_connecteur):
    for ns, outils in _migres(par_connecteur).items():
        if ns not in outils:
            continue
        for op in _ops(outils[ns]):
            assert op in SIMULATIONS or not VERBES_EFFET.match(op), (ns, op)
        for p in outils[ns].parameters.get("properties") or {}:
            assert not PARAMETRES_DECLENCHEURS.match(p), (ns, p)


def test_l_outil_qui_agit_n_a_que_des_ops_a_effet(par_connecteur):
    for ns, outils in _migres(par_connecteur).items():
        act = outils.get(f"{ns}_act")
        if act is None:
            continue
        ops = _ops(act)
        assert ops, f"`{ns}_act` sans `op` : son geste n'est pas nommé"
        for op in ops:
            assert VERBES_EFFET.match(op), (f"{ns}_act", op)


def test_les_anciens_noms_ne_sont_plus_servis_et_leur_remplacant_sert_l_op(par_connecteur):
    servis = {nom: t for outils in par_connecteur.values() for nom, t in outils.items()}
    for r in outils_retires.RENOMMAGES_ACT:
        assert r.ancien not in servis, r.ancien
        assert r.outil in servis, r
        assert r.op in _ops(servis[r.outil]), r
        assert r.ancien in outils_retires.RETIRES, r.ancien
        assert f'`{r.outil}(op="{r.op}"' in outils_retires.RETIRES[r.ancien].message, r


def test_un_ancien_nom_garde_tout_son_sens_dans_les_listes():
    """Ce qu'un ancien outil savait faire, ses remplaçants le savent : une liste
    d'outils autorisés (ou masqués) réécrite garde exactement son sens."""
    assert outils_retires.remplacants("gmail_compose") == ("gmail", "gmail_act")
    assert outils_retires.remplacants("gmail_message") == ("gmail", "gmail_act")
    assert outils_retires.remplacants("gmail_list_accounts") == ("gmail",)
    assert outils_retires.remplacants("outlook_compose") == ("outlook", "outlook_act")
    assert outils_retires.remplacants("inconnu") == ()


def test_l_annonce_se_sert_jusqu_a_sa_date_puis_se_tait():
    import datetime

    fin = outils_retires.ANNONCE_ACT_JUSQU_AU
    annonce = outils_retires.annonce_act_du_jour(fin)
    for r in outils_retires.RENOMMAGES_ACT:
        assert f"`{r.ancien}`" in annonce and f"`{r.outil}`" in annonce, r
    assert outils_retires.annonce_act_du_jour(fin + datetime.timedelta(days=1)) is None


def test_la_migration_reecrit_sur_place_sans_doublon():
    import importlib.util
    from pathlib import Path

    import oto_mcp

    chemin = (Path(oto_mcp.__file__).parent / "db" / "migrations" / "versions"
              / "20261017_0062_outils_act.py")
    spec = importlib.util.spec_from_file_location("revision_0062", chemin)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    table = m.correspondance()
    assert m.reecrire(["serper_search", "gmail_message", "gmail_compose", "outlook_compose"],
                      table) == ["serper_search", "gmail", "gmail_act", "outlook",
                                 "outlook_act"]
    with pytest.raises(ValueError):
        m.reecrire([{"name": "gmail_message"}], table)


#: Les connecteurs encore dans `A_MIGRER` dont la PAIRE est déjà née : leurs outils
#: d'avant restent, mais `<ns>` et `<ns>_act` ont la forme cible dès aujourd'hui.
PAIRES_NEES = ("http", "slack")


@pytest.mark.parametrize("ns", PAIRES_NEES)
def test_une_paire_nee_avant_la_migration_a_deja_la_forme_cible(par_connecteur, ns):
    outils = {n: t for n, t in par_connecteur[ns].items() if n in (ns, f"{ns}_act")}
    assert set(outils) == {ns, f"{ns}_act"}, (ns, sorted(par_connecteur[ns]))
    lecteur, acteur = outils[ns], outils[f"{ns}_act"]
    assert lecteur.annotations is not None and lecteur.annotations.readOnlyHint is True
    for op in _ops(lecteur):
        assert not VERBES_EFFET.match(op), (ns, op)
    for p in lecteur.parameters.get("properties") or {}:
        assert not PARAMETRES_DECLENCHEURS.match(p), (ns, p)
    a = acteur.annotations
    assert a is not None and a.readOnlyHint is False
    assert {"destructiveHint", "openWorldHint"} <= set(a.model_dump(exclude_unset=True))
    assert _ops(acteur) and all(VERBES_EFFET.match(op) for op in _ops(acteur)), (
        f"{ns}_act", _ops(acteur))
