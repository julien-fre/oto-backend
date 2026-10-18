"""L'index poussé à CHAQUE connexion cumule les trois paliers, et l'écho d'org ne
s'ajoute plus à une réponse qui déclare une portée personnelle.

**Le défaut que ce fichier ferme** (mesuré le 08-09/09/2026). Depuis l'ADR 0068
(04/09), `oto_procedure op=set` sans `scope` écrit au palier PERSONNEL, et `op=list`
cumule bien les trois paliers. Mais les deux textes que l'agent reçoit SANS les
demander ne lisaient que l'org :

- `instructions.skills_index_md`, appendé à la description d'`oto_procedure` au
  `tools/list` — appelé sans `sub` du tout, il n'en avait même pas le paramètre ;
- l'index du bundle de session (`_read_guide`, slug omis), qui cumulait `org` puis
  `group`, jamais `user`.

Conséquence : une procédure écrite à soi n'apparaissait dans RIEN de ce que l'agent
reçoit. Elle existait, la lecture par slug la rendait, et personne ne pouvait la
trouver sans en connaître le nom — deux campagnes ont été réécrites en croyant la
première perdue.

Le troisième volet est le TEXTE : l'accusé disait bien `scope: "user"`, mais
l'adaptateur MCP ajoutait `_org: {id, name}` à toute réponse dont l'org est
renseignée. À côté d'un `scope` discret, un écho d'organisation bien visible se lit
« écrit pour l'org ». C'est la lecture qui a été faite.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from oto_mcp import access, guide_store, org_store
from oto_mcp import instructions as instr
from oto_mcp.capabilities import _mcp_adapter
from oto_mcp.capabilities._types import Capability, RawCtx, ResolvedCtx
from oto_mcp.middleware import dynamic_instructions as mw


def _rows(*slugs):
    return [{"slug": s, "title": f"T-{s}", "description": f"D-{s}"} for s in slugs]


@pytest.fixture
def paliers(monkeypatch):
    """Un compte qui a UNE procédure à chaque palier — le seul monde où l'oubli d'un
    palier se voit."""
    contenu = {("user", "u1"): _rows("ma-passe"),
               ("org", 7): _rows("socle-org"),
               ("group", 3): _rows("compta")}
    monkeypatch.setattr(org_store, "list_instructions",
                        lambda otype, oid, **kw: contenu.get((otype, oid), []))
    monkeypatch.setattr(access, "current_group", lambda sub: 3 if sub == "u1" else None)
    return contenu


# ── ① l'index appendé au `tools/list` ───────────────────────────────────────

def test_index_cumule_les_trois_paliers(paliers):
    out = instr.skills_index_md("u1", 7)
    assert "- ma-passe — T-ma-passe : D-ma-passe [personal]" in out
    assert "- socle-org — T-socle-org : D-socle-org" in out
    assert "- compta — T-compta : D-compta [team]" in out
    # La sienne EN TÊTE, comme dans `op=list` : c'est le premier endroit où l'on
    # cherche ce qu'on vient d'écrire.
    assert out.index("ma-passe") < out.index("socle-org") < out.index("compta")


def test_le_palier_dorg_ne_porte_pas_de_marque(paliers):
    """La marque dit ce qui est RESTREINT. Marquer l'org aussi rendrait l'index plus
    long sans rien apprendre — et le texte part à chaque connexion de chaque agent."""
    out = instr.skills_index_md("u1", 7)
    ligne = next(l for l in out.splitlines() if l.startswith("- socle-org"))
    assert ligne.endswith("D-socle-org")


def test_la_marque_distingue_deux_paliers_qui_portent_le_meme_slug(monkeypatch):
    """Mesuré en production le 09/09/2026 : sur deux orgs, un même slug de campagne
    existe au palier `user` ET au palier `org`, avec le MÊME titre et la MÊME
    description. Sans marque, l'index rendrait deux lignes strictement identiques — et
    rien ne dirait laquelle `op=get` va charger (la cascade prend la sienne d'abord)."""
    meme = [{"slug": "slug-en-double", "title": "Passe 1", "description": "Registre."}]
    monkeypatch.setattr(org_store, "list_instructions",
                        lambda otype, oid, **kw: meme if otype in ("user", "org") else [])
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    lignes = [l for l in instr.skills_index_md("u1", 7).splitlines()
              if l.startswith("- slug-en-double")]
    assert len(lignes) == 2 and lignes[0] != lignes[1]
    assert lignes[0].endswith(" [personal]")


def test_index_sans_sub_reste_celui_de_lorg(paliers):
    """Le chemin stdio/boot n'a pas de compte : il garde l'index d'org, sans perso."""
    out = instr.skills_index_md(None, 7)
    assert "socle-org" in out and "ma-passe" not in out and "compta" not in out


def test_index_vide_quand_il_ny_a_rien(paliers):
    assert instr.skills_index_md(None, None) == ""
    assert instr.skills_index_md("inconnu", 99) == ""


def test_index_fail_open(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(org_store, "list_instructions", boom)
    assert instr.skills_index_md("u1", 7) == ""


# ── ① le CÂBLAGE : le middleware passe le compte, pas seulement l'org ───────

class _FakeTool:
    def __init__(self, name, description=""):
        self.name, self.description = name, description

    def model_copy(self, update):
        return _FakeTool(self.name, update.get("description", self.description))


def _list_tools(tools, sub, monkeypatch):
    monkeypatch.setattr(mw, "current_user_sub_from_token", lambda: sub)

    async def call_next(ctx):
        return tools
    return asyncio.run(mw.DynamicInstructionsMiddleware().on_list_tools(object(), call_next))


def test_le_tools_list_sert_bien_le_palier_perso(paliers, monkeypatch):
    """Le test de bout en bout du volet ①-a : ce que reçoit RÉELLEMENT un agent qui
    se connecte. Il mord aussi bien si `skills_index_md` oublie le palier perso que si
    son appelant oublie de lui passer le `sub` — les deux moitiés du même geste."""
    monkeypatch.setattr(access, "current_org", lambda sub: 7)
    monkeypatch.setattr(guide_store, "guides_index_md", lambda sub, org: "")
    servi = {t.name: t for t in
             _list_tools([_FakeTool("oto_procedure", "load")], "u1", monkeypatch)}
    assert "- ma-passe — T-ma-passe : D-ma-passe [personal]" in servi["oto_procedure"].description


def test_le_middleware_passe_le_sub_et_lorg(monkeypatch):
    """Le même fait, isolé : quand le câblage tombe, l'échec nomme le câblage."""
    vus: list[tuple] = []
    monkeypatch.setattr(access, "current_org", lambda sub: 7)
    monkeypatch.setattr(guide_store, "guides_index_md", lambda sub, org: "")
    monkeypatch.setattr(instr, "skills_index_md",
                        lambda *a, **kw: (vus.append((a, kw)), "INDEX")[1])
    _list_tools([_FakeTool("oto_procedure", "load")], "u1", monkeypatch)
    assert vus == [(("u1", 7), {})]


# ── ② l'écho d'org ne s'ajoute pas à une portée personnelle ─────────────────

class _NoInput(BaseModel):
    pass


def _cap(resultat: dict) -> Capability:
    return Capability(key="test.portee", handler=lambda ctx, inp: resultat,
                      Input=_NoInput, authz=lambda raw, inp: ResolvedCtx(sub="u1", org_id=7),
                      mcp="test_portee", refresh_visibility=False)


def _appel(resultat: dict, monkeypatch) -> dict:
    monkeypatch.setattr(_mcp_adapter, "current_user_sub_from_token", lambda: "u1")
    monkeypatch.setattr(access, "current_org", lambda sub: 7)
    monkeypatch.setattr(org_store, "get_org", lambda oid: {"name": f"org{oid}"})
    return asyncio.run(_mcp_adapter._make_tool(_cap(resultat))())


def test_pas_decho_dorg_sur_une_portee_perso(monkeypatch):
    out = _appel({"ok": True, "scope": "user", "user_id": "u1", "slug": "ma-passe"},
                 monkeypatch)
    assert out["scope"] == "user"
    assert "_org" not in out, "un écho d'org à côté d'un scope perso se lit « écrit pour l'org »"


def test_lecho_dorg_reste_partout_ailleurs(monkeypatch):
    for resultat in ({"ok": True, "scope": "org", "org_id": 7},
                     {"ok": True, "scope": "group", "group_id": 3},
                     {"ok": True, "rows": []}):
        out = _appel(dict(resultat), monkeypatch)
        assert out["_org"]["id"] == 7, resultat


def test_une_reponse_qui_porte_deja_son_echo_nest_pas_ecrasee(monkeypatch):
    out = _appel({"scope": "org", "_org": {"id": 99}}, monkeypatch)
    assert out["_org"] == {"id": 99}


# ── Garde de cohérence entre les deux index ────────────────────────────────

class _ListInput(BaseModel):
    query: str | None = None
    scope: str | None = None


def test_les_deux_index_lisent_les_memes_paliers(monkeypatch):
    """`skills_index_md` (poussé au `tools/list`) et `_list_guides` (`op=list`) doivent
    cumuler les MÊMES paliers : c'est l'écart entre eux qui a coûté le défaut —
    `op=list` avait été corrigé le 04/09/2026, l'index poussé non, et rien ne les
    reliait. On lit les paliers RÉELLEMENT demandés au store, pas le code source.

    ⚠️ Cette garde est le seul lien mécanique entre les deux surfaces. Ajouter un
    palier à l'une sans l'autre la fait tomber, et c'est le but : l'agent ne doit pas
    lire dans son index l'inverse de ce que `op=list` lui rendra."""
    from oto_mcp.capabilities.orgs import instructions as oi

    demandes: list[str] = []
    monkeypatch.setattr(org_store, "list_instructions",
                        lambda otype, oid, **kw: (demandes.append(otype), [])[1])
    monkeypatch.setattr(access, "current_group", lambda sub: 3)

    instr.skills_index_md("u1", 7)
    pousse = set(demandes)
    demandes.clear()
    oi._list_guides(ResolvedCtx(sub="u1", org_id=7, group_id=3), _ListInput())
    liste = set(demandes)

    assert pousse == liste == {"user", "org", "group"}
