"""L'empreinte d'un run : ce qu'il a réellement exécuté (chantier du run, lot J2).

Un run n'enregistrait que le SLUG de sa procédure. Or les procédures sont versionnées
(`org_instructions.version`, un snapshot par version dans `org_instruction_revisions`) :
rejouer « la même procédure » trois semaines plus tard, c'est en jouer une autre sans
que rien ne le dise. ADR 0055-D10 / 0058-D1 : *le gel des versions EST l'empreinte du
run*, et chaque jour de runs sans empreinte est un jour non reproductible.

Deux moitiés, une seule livrée ici :
- la **version de procédure**, résolue par `run_start` et versée dans le JOURNAL (le
  domicile cohérent avec le verdict du 12/08 : le run est ses faits, pas sa ligne
  d'index) — testée de bout en bout ci-dessous ;
- la **clé d'instance** (quel credential a réellement servi), dont l'allowlist du relevé
  est ouverte mais dont le point d'écriture est `access.resolve_credential`.

Le chemin est exercé POUR DE VRAI : `run_start` réel + `CallContextMiddleware`, et le
relevé relu comme le fait le sink du calllog. Ne sont stubbés que la DB et l'identité.
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from oto_mcp import access, auth_hooks, call_axes, db, group_store, org_store, server, session_org
from oto_mcp.middleware import CallContextMiddleware
from oto_mcp.tools import doctrine_run as drt


class _SessionCtx:
    def __init__(self):
        self._state: dict = {}

    async def get_state(self, key):
        return self._state.get(key)

    async def set_state(self, key, value):
        self._state[key] = value


class _Msg:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _Ctx:
    def __init__(self, msg):
        self.message = msg


@pytest.fixture(scope="module")
def run_start_fn():
    mcp = FastMCP("test")
    drt.register(mcp)
    return asyncio.run(mcp.get_tool("run_start")).fn


def _wire(monkeypatch, *, sub="u1", org=35, group=None,
          org_procedures=None, group_procedures=None):
    monkeypatch.setattr(call_axes, "current_user_sub_from_token", lambda: sub)
    monkeypatch.setattr(auth_hooks, "current_user_sub_from_token", lambda: sub)
    monkeypatch.setattr(org_store, "get_active_org", lambda s: org)
    monkeypatch.setattr(access, "current_group", lambda s=None, **kw: group)
    monkeypatch.setattr(org_store, "get_instruction",
                        lambda oid, slug: (org_procedures or {}).get(slug))
    monkeypatch.setattr(group_store, "get_group_instruction",
                        lambda gid, slug: (group_procedures or {}).get(slug))
    monkeypatch.setattr(db, "insert_run", lambda run_id, **kw: None)


async def _start(run_start_fn, **args) -> tuple[dict, dict]:
    """Ouvre un run par le chemin réel. Rend (résultat, relevé d'appel).

    ⚠️ Le relevé est posé par `CallContextMiddleware` lui-même (holder neuf par appel) :
    on le relit DEPUIS L'INTÉRIEUR de l'appel, exactement là où le sink du calllog le
    relit pour le verser dans les args de la ligne. Le poser depuis le test donnerait un
    holder que le middleware remplace — et un test qui n'observerait rien."""
    seen: dict = {}
    mw = CallContextMiddleware(reserved_org_tools=set())

    async def _next(ctx):
        out = await run_start_fn(_SessionCtx(), **ctx.message.arguments)
        seen.update(session_org.current_call_trace() or {})
        return out

    out = await mw.on_call_tool(_Ctx(_Msg("run_start", dict(args))), _next)
    return out, seen


# ── La version de procédure atterrit dans le journal ────────────────────────

@pytest.mark.asyncio
async def test_le_run_enregistre_la_version_de_la_procedure(monkeypatch, run_start_fn):
    _wire(monkeypatch, org_procedures={"prospection": {"version": 7}})
    out, trace = await _start(run_start_fn, label="prospection Q3", doctrine="prospection")
    # Dans la ligne de journal (via le relevé) …
    assert trace["doctrine_version"] == 7
    # … et rendue à l'agent, qui sait donc ce qu'il déroule.
    assert out["doctrine"] == "prospection" and out["doctrine_version"] == 7


@pytest.mark.asyncio
async def test_la_procedure_dequipe_compte_comme_celle_de_lorg(monkeypatch, run_start_fn):
    """`oto_procedure(op='get')` sert l'org PUIS l'équipe active : l'empreinte doit
    suivre le même ordre, sinon une procédure d'équipe se déroule sans version."""
    _wire(monkeypatch, group=12, group_procedures={"relance": {"version": 2}})
    _, trace = await _start(run_start_fn, label="relances", doctrine="relance")
    assert trace["doctrine_version"] == 2


@pytest.mark.asyncio
async def test_lorg_prime_sur_lequipe(monkeypatch, run_start_fn):
    _wire(monkeypatch, group=12, org_procedures={"relance": {"version": 9}},
          group_procedures={"relance": {"version": 2}})
    _, trace = await _start(run_start_fn, label="relances", doctrine="relance")
    assert trace["doctrine_version"] == 9


@pytest.mark.asyncio
async def test_un_run_ad_hoc_ne_prétend_à_aucune_version(monkeypatch, run_start_fn):
    _wire(monkeypatch, org_procedures={"prospection": {"version": 7}})
    out, trace = await _start(run_start_fn, label="un truc vite fait")
    assert out["doctrine_version"] is None and "doctrine_version" not in trace


@pytest.mark.asyncio
async def test_un_slug_inconnu_ouvre_le_run_sans_version(monkeypatch, run_start_fn):
    """Un slug qui ne désigne rien de lisible ici (doctrine d'un autre foyer, faute de
    frappe) : pas de version, mais le run s'ouvre — l'empreinte n'est pas un gate."""
    _wire(monkeypatch)
    out, trace = await _start(run_start_fn, label="?", doctrine="ce-slug-nexiste-pas")
    assert out["run_id"] and out["doctrine_version"] is None
    assert "doctrine_version" not in trace


@pytest.mark.asyncio
async def test_une_lecture_de_version_en_panne_nempeche_pas_le_run(monkeypatch,
                                                                   run_start_fn):
    """Best-effort, comme tout ce qui entoure un run : la base indisponible au moment
    de résoudre la version ne doit pas faire échouer l'ouverture du déroulé."""
    def _boom(*a, **kw):
        raise RuntimeError("pool épuisé")

    _wire(monkeypatch)
    monkeypatch.setattr(org_store, "get_instruction", _boom)
    out, trace = await _start(run_start_fn, label="prospection", doctrine="prospection")
    assert out["run_id"] and out["doctrine_version"] is None
    assert trace == {}


# ── Le relevé d'appel, et ce qu'il laisse passer ────────────────────────────

def test_lempreinte_passe_lallowlist_du_journal():
    """Le relevé est filtré à l'écriture (`server._TRACED_ARGS`) : une clé absente de
    l'allowlist est silencieusement jetée. Les deux moitiés de l'empreinte doivent y
    être — sinon `run_start` calcule une version que personne n'écrit."""
    assert "doctrine_version" in server._TRACED_ARGS
    assert "instance" in server._TRACED_ARGS


def test_seule_lallowlist_atteint_les_args_de_la_ligne():
    """Reproduit le filtre du sink : ce qui est déclaré passe, le reste tombe."""
    trace = {"doctrine_version": 7, "instance": "org:35:pennylane", "ns_id": 160,
             "secret_interne": "à ne jamais journaliser"}
    args = {**{"label": "prospection Q3"},
            **{k: v for k, v in trace.items() if k in server._TRACED_ARGS}}
    assert args == {"label": "prospection Q3", "doctrine_version": 7,
                    "instance": "org:35:pennylane", "ns_id": 160}
