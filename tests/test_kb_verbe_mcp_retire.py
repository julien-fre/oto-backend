"""Le verbe MCP `oto_kb` est RETIRÉ (10/09/2026) : il refuse en nommant le geste qui aboutit.

Décision d'Alexis : la « base de connaissance » n'existe plus comme concept — il n'y a que
projet, doc et procédure. La DONNÉE ne bouge pas : c'était déjà un projet ordinaire. La
route REST `POST /api/me/kb` RESTE, le tableau de bord la consomme (étape d'accueil, écran
Documents, sélecteur de documents) : seule la face MCP s'en va.

Pourquoi retirer plutôt que laisser dormir : la description disait « LEGACY, n'écris pas
ici », mais la RÉPONSE de `op=get` rendait le numéro du projet et le résumé « The org-wide
knowledge base: shared reference pages » — et dans 4 des 6 orgs où des agents ont écrit
dans une KB après la coupure du recrutement (08/09), l'agent venait d'appeler ce verbe. Le
verbe recrutait par sa réponse ; `oto_whoami` faisait de même avec son champ `knowledge`.

Trois chemins mènent au nom retiré, et chacun doit rendre LE MÊME refus :
1. `tools/call name=oto_kb` direct — fastmcp lève, l'enveloppe d'erreur classe ;
2. `oto_call(name="oto_kb")` — le chemin RÉEL : la notice prescrit `oto_call` pour un
   outil absent de la liste, et des procédures d'org (contenu client, qu'on ne corrige
   pas) nomment encore `oto_kb`. Avant ce lot il répondait « outil méta/spine —
   appelle-le directement », qui renvoie au chemin 1 : une boucle ;
3. `oto_tool_schema(name="oto_kb")` — ce qu'on lit avant d'appeler.
"""
from __future__ import annotations

import asyncio
import json
import re

import fastmcp.server.context as _fc
import pytest
from mcp.types import INVALID_PARAMS

from oto_mcp import error_taxonomy, guide_store, instructions, openapi, outils_retires, providers
from oto_mcp.capabilities import registry
from oto_mcp.mcp_errors import McpError
from oto_mcp.middleware.error_envelope import ErrorEnvelopeMiddleware
from oto_mcp.tools import meta as _meta

from _mcp_app import static_mcp

RETIRE = "oto_kb"


def _dit_le_geste(message: str) -> None:
    """Ce que le refus DOIT porter, quel que soit le chemin qui y mène : le texte déclaré
    (aucun chemin ne se met à dire autre chose), et ce que ce texte doit contenir."""
    assert outils_retires.KB.message in message, message
    assert "retired" in message
    assert "ordinary PROJECT" in message, "une base de connaissance EST un projet"
    assert "`oto_project op=list`" in message, "le geste pour TROUVER le projet"
    assert "`oto_doc`" in message and "`project_id`" in message, "le geste pour ses pages"


def _catalogue_monte():
    return asyncio.run(static_mcp().list_tools(run_middleware=False))


def test_la_capacite_quitte_le_MCP_et_reste_servie_en_REST():
    cap = registry.by_key("me.kb")
    assert cap.mcp is None
    assert cap not in registry.caps_with_mcp()
    assert [(b.verb, b.path) for b in cap.rest_bindings()] == [("POST", "/api/me/kb")]
    assert "post" in openapi.build()["paths"]["/api/me/kb"], "le tableau de bord la consomme"


def test_le_catalogue_MONTE_ne_porte_plus_oto_kb():
    noms = {t.name for t in _catalogue_monte()}
    assert RETIRE not in noms
    # Contrôle positif : c'est bien le vrai catalogue (sinon « absent » ne prouve rien).
    assert {"oto_doc", "oto_project", "oto_search", "oto_call", "oto_tool_schema"} <= noms


class _Ctx:
    class _Msg:
        name = RETIRE
    message = _Msg()


def test_appel_direct__refuse_en_nommant_le_geste():
    """`tools/call` sur le serveur RÉELLEMENT monté, puis l'enveloppe d'erreur — celle
    qui répond à l'agent — sur l'exception que ce montage a réellement levée."""
    with pytest.raises(Exception) as leve:
        asyncio.run(static_mcp().call_tool(RETIRE, {"op": "get"}, run_middleware=False))
    info = error_taxonomy.classify(leve.value)
    _dit_le_geste(info.message)
    assert info.code == "unknown_tool"
    assert error_taxonomy.jsonrpc_code(info) == INVALID_PARAMS, "changer d'appel, pas réessayer"
    assert info.hint == outils_retires.KB.hint

    async def _suivant(_ctx):
        raise leve.value

    with pytest.raises(McpError) as servi:
        asyncio.run(ErrorEnvelopeMiddleware().on_call_tool(_Ctx(), _suivant))
    _dit_le_geste(servi.value.error.message)


async def _appelle(nom_du_tool: str, args: dict):
    """Le tool RÉELLEMENT monté, exécuté dans un contexte fastmcp (patron de
    `test_tool_alias_par_tenant`) — pas la closure recopiée."""
    tool = await static_mcp().get_tool(nom_du_tool)
    async with _fc.Context(fastmcp=static_mcp()):
        return await tool.run(args)


@pytest.mark.parametrize("nom_du_tool, args", [
    ("oto_call", {"name": RETIRE, "arguments": {"op": "get"}}),
    ("oto_tool_schema", {"name": RETIRE}),
])
def test_oto_call_et_oto_tool_schema_refusent_en_nommant_le_geste(nom_du_tool, args,
                                                                  monkeypatch):
    monkeypatch.setattr(_meta, "current_user_sub_from_token", lambda: "u-retrait")
    with pytest.raises(McpError) as ei:
        asyncio.run(_appelle(nom_du_tool, args))
    _dit_le_geste(ei.value.error.message)


def test_oto_whoami_ne_rend_plus_le_projet_de_l_ex_base(monkeypatch):
    """Le même recrutement, par la réponse d'un autre outil : « ta KB est le projet N »."""
    from fastmcp import FastMCP
    from oto_mcp import access
    from oto_mcp.tools import whoami as whoami_tool

    monkeypatch.setattr(whoami_tool, "current_user_sub_from_token", lambda: "u-retrait")
    monkeypatch.setattr(access, "status_for", lambda sub: {"providers": {}})
    m = FastMCP("t")
    whoami_tool.register(m)
    tool = asyncio.run(m.get_tool("oto_whoami"))
    out = tool.fn(ctx=None)
    assert "account" in out and "connectors" in out, "contrôle positif : la vraie réponse"
    assert "knowledge" not in out
    assert "kb_project_id" not in json.dumps(out, default=str)
    assert "kb_project_id" not in (tool.description or "")


def test_oto_doc_dit_la_portee_des_liens_en_projets():
    """La résolution des `[[…]]` se fait parmi les projets que possède l'org
    (`db/backlinks.resolution_scope`, #888/#890) — plus d'ancre, donc plus de « projet
    historique » : le texte dit ce mécanisme, en projets."""
    desc = next(t for t in _catalogue_monte() if t.name == "oto_doc").description or ""
    assert "every project the ORGANIZATION owns" in desc
    assert "historical documents project" not in desc
    assert "org KB" not in desc and "the KB" not in desc


#: Ce qui PORTE le concept — et ce qui ne le porte pas. Mesuré le 10/09/2026 sur le
#: corpus réellement servi : le jeton `KB` SEUL y apparaît 8 fois et pas une pour notre
#: concept — c'est une taille (« ≤256 KB », « ~160 KB », « ~220 KB ») ou le vocabulaire
#: d'un tiers (Zoho Desk : « Help Center (KB) articles »). Le bannir viserait le mot et
#: raterait le sens. Le concept, lui, se dit en DEUX mots, ou se nomme par un
#: identifiant à nous — ce sont ces quatre-là qu'on refuse.
CONCEPT = ("knowledge base", "base de connaissance", "oto_kb", "kb_project_id")

#: La seule surface qui a le DROIT de nommer l'ancien concept : la route qui en porte
#: encore le nom dans son chemin. Un lecteur de `/api/openapi.json` qui cherche « la KB »
#: doit pouvoir la retrouver — mais au PASSÉ, jamais comme une chose qui existe.
CHEMIN_LEGACY = "/api/me/kb"
_AU_PASSE = re.compile(r"used to be called|is retired|historical|seeded as", re.I)


def test_la_face_REST_ne_parle_du_concept_QUE_sur_la_route_qui_en_porte_le_nom():
    """Le catalogue MCP n'est qu'un tiers du texte servi : 222 capacités sur 327 sont
    REST-only et ne se lisent QUE dans `/api/openapi.json` — servi sans authentification.
    Balayer le document ENTIER attrape en prime les docstrings des modèles de sortie,
    qui y partent en descriptions de schéma (c'est par là que `KbView` affirmait encore
    « Ancre de la base de connaissance de l'org active »)."""
    doc = openapi.build()
    doc["paths"].pop(CHEMIN_LEGACY)                   # jugée à part, juste en dessous
    reste = json.dumps(doc, ensure_ascii=False).lower()
    assert "/api/me/docs" in json.dumps(doc), "contrôle positif : c'est bien le document"
    fautifs = [m for m in CONCEPT if m in reste]
    assert not fautifs, f"le concept revient hors de {CHEMIN_LEGACY} : {fautifs}"


def test_la_route_legacy_ne_nomme_l_ancien_concept_qu_au_passe():
    """Elle garde le nom (son chemin le porte), donc elle doit l'EXPLIQUER — pas le
    poser au présent. Chaque occurrence doit voisiner un marqueur de passé."""
    legacy = json.dumps(openapi.build()["paths"][CHEMIN_LEGACY], ensure_ascii=False)
    trouve = 0
    for motif in CONCEPT:
        for m in re.finditer(re.escape(motif), legacy, re.I):
            trouve += 1
            fenetre = legacy[max(0, m.start() - 150):m.end() + 150]
            assert _AU_PASSE.search(fenetre), (
                f"« {motif} » posé sans marqueur de passé : …{fenetre}…")
    assert trouve, "contrôle positif : cette route DOIT encore nommer l'ancien concept"


def test_le_brief_seme_dans_le_projet_ne_pose_plus_la_base_comme_un_lieu():
    """`KB_BRIEF` n'est pas un commentaire : `op=create` l'écrit dans `brief_md`, et
    `oto_project` le SERT ensuite à tout agent qui liste les projets de l'org. Le
    10/09/2026, 41 briefs vivants disaient encore « The org-wide knowledge base:
    shared reference pages… », et c'est un des chemins par lesquels des agents ont
    continué d'écrire « dans la KB » après le retrait du recrutement.
    ⚠️ `KB_NAME` reste « Knowledge base » : c'est le NOM d'une donnée déjà posée dans
    43 orgs, et le motif du rapport `scripts/archive_empty_kb_projects.py`. Il ne
    produit plus rien — mesuré le 10/09/2026, AUCUN client de la flotte n'appelle
    `op=create` (le seul appelant de la route est le tableau de bord, en `op=get`, et
    il n'y lit que `project_id`) ; le verbe MCP qui créait est retiré."""
    from oto_mcp.capabilities import kb as kb_cap
    fautifs = [m for m in CONCEPT if m in kb_cap.KB_BRIEF.lower()]
    assert not fautifs, f"le brief semé pose encore le concept : {fautifs}"
    assert "`oto_doc`" in kb_cap.KB_BRIEF, "il doit nommer le geste qui aboutit"


def test_aucun_guide_seme_ne_pose_le_concept():
    """Les 11 guides plateforme sont la consigne la plus lue (la notice dit « lis-moi
    d'abord »). ⚠️ Ce banc ne voit que les SEEDS du dépôt : ce qui est réellement servi
    vient de la table — un fichier corrigé n'atteint personne tant que la ligne en base
    n'est pas réécrite. Cf. le compte rendu du 10/09."""
    corpus = {g["slug"]: (str(g.get("title", "")) + str(g.get("description", ""))
                          + str(g.get("body_md", ""))).lower()
              for g in guide_store.list_file_guides()}
    assert "notice" in corpus and "oto_doc" in corpus["notice"], "contrôle positif"
    fautifs = {s: [m for m in CONCEPT if m in c] for s, c in corpus.items()
               if any(m in c for m in CONCEPT)}
    # La notice a le droit de DIRE que la base n'existe pas — mais pas de nommer le
    # verbe retiré, ni de la présenter comme un lieu où aller.
    for slug, motifs in fautifs.items():
        # Les seeds plateforme sont écrits en anglais (docs/guides.md) : la phrase
        # permise y est « there is no separate "knowledge base" ».
        assert motifs in (["base de connaissance"], ["knowledge base"]), f"{slug} : {motifs}"
        assert ("il n'y a pas de « base de connaissance »" in corpus[slug]
                or 'there is no separate "knowledge base"' in corpus[slug]), slug


def test_aucun_texte_servi_ne_nomme_plus_le_verbe_retire():
    """Un nom retiré qu'un texte servi continue de citer envoie l'agent droit sur le refus.
    Balaye description ET schéma : le bloc `Args:` d'une docstring part dans le schéma."""
    servis = {f"outil `{t.name}`": (t.description or "")
              + json.dumps(t.parameters or {}, ensure_ascii=False)
              for t in _catalogue_monte()}
    servis["carte des namespaces"] = providers.render_namespace_catalog()
    servis["socle de boot"] = instructions.render()
    servis["guide `notice` (fichier)"] = guide_store.file_guide("notice")["body_md"]
    assert "oto_doc" in servis["carte des namespaces"], "contrôle positif du balayage"
    citants = sorted(k for k, v in servis.items() if RETIRE in v)
    assert not citants, f"`{RETIRE}` encore cité par : {citants}"
