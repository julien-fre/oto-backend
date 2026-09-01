"""`@claimed` après `run_finish` — le refus dit un MOMENT, pas un état (#645).

Huitième passage du palier, 30/08/2026 : **99 refus sur 200 écritures**, tous le même
texte — « `@claimed` en tableau : ton travail ne tient aucune ligne en ce moment
(aucune réservation active) ». Exact, et à côté de la question : les appels venaient
d'un harnais qui écrivait APRÈS `run_finish`, dont la clôture avait précisément libéré
les baux du run (#613). Le refus décrivait l'état constaté ; ce qu'il fallait dire est
**quand** la porte s'est fermée — rien, ni dans le nom de l'alias ni dans sa
description, n'annonçait qu'il en avait une. Deux heures perdues, sur un mécanisme
découvert deux fois à douze heures d'écart.

> **Un refus juste qui n'est pas le bon refus coûte autant qu'un refus faux** : il
> envoie chercher une réservation oubliée là où c'est l'ORDRE des gestes qui est en
> cause.

Ce que ce fichier fige, dans l'ordre de ce qui coûte :

① **le cas de production, bout en bout** — réserver sous `_run_id=R`, clore R, puis
  écrire sous `@claimed` : contre un vrai PostgreSQL, par les outils tels que le
  serveur les monte, chaque appel dans sa propre session (le chemin de la flotte) ;
② le refus lui-même, sur les DEUX formes de l'alias (`id=` et `namespace=`) ;
③ ce que le refus continue de dire quand le run est ouvert, et ce qu'il ne paie pas
  sur le chemin nominal ;
④ la borne dans la description SERVIE — mesurée sur les outils MONTÉS, jamais sur une
  docstring : c'est le texte que le modèle relit à chaque appel qui a produit les 99.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from oto_mcp.mcp_errors import McpError
from oto_mcp.datastore import core as D
from oto_mcp.datastore.errors import ClaimedRefUnresolved

SUB = "sub-flotte-645"
CLOS = "2026-08-30 21:08:53.417921+00:00"


# ── Outillage unitaire (le style de test_datastore_claimed_ref.py) ───────────

def _store(monkeypatch, *, run="run-8", noms=None):
    noms = noms or {7: "copie-eval-palier100"}
    s = D.DatastorePg("u1")
    monkeypatch.setattr(D, "_current_run", lambda: run)
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False:
                        next(i for i, n in noms.items() if n == ns))
    monkeypatch.setattr(s, "_ns_of", lambda i: {"namespace": noms[i]})
    return s


def _journal(monkeypatch, clos):
    """Ce que le journal répond sur la clôture du run — et QUI a été demandé."""
    vus: dict = {"appels": []}

    def _closed(run_id):
        vus["appels"].append(run_id)
        return clos

    monkeypatch.setattr(D.db, "run_closed_at", _closed, raising=False)
    return vus


def _baux(monkeypatch, baux):
    monkeypatch.setattr(D.db, "datastore_active_leases_of",
                        lambda *, run_id=None, worker=None: baux, raising=False)


# ── ② Le refus nomme la clôture, sur les deux formes de l'alias ──────────────

def test_un_run_clos_dit_sa_cloture_au_lieu_de_decrire_un_etat(monkeypatch):
    """LE texte des 99 refus, et celui qui le remplace. L'heure y est parce que c'est
    elle qui fait le lien avec le geste précédent : « depuis 21:08:53 » se reconnaît
    dans un journal, « aucune réservation active » ne se reconnaît pas."""
    s = _store(monkeypatch)
    _baux(monkeypatch, [])
    vus = _journal(monkeypatch, CLOS)

    with pytest.raises(ClaimedRefUnresolved) as e:
        s.resolve_claimed_ref("copie-eval-palier100")
    msg = str(e.value)

    assert "aucune réservation active" not in msg, (
        "le refus qui a coûté deux heures : vrai, et muet sur la cause")
    assert "CLOS" in msg and "21:08:53" in msg, msg
    assert "run_finish" in msg, "ce qui a fermé la porte se nomme"
    assert vus["appels"] == ["run-8"], "la clôture se lit sur le run de l'APPEL"


def test_la_meme_chose_quand_l_alias_est_passe_en_tableau(monkeypatch):
    """Les 99 refus du 30/08 sont arrivés par `namespace="@claimed"` (#599) : la forme
    qui a produit l'incident doit être celle qui rend le nouveau texte."""
    s = _store(monkeypatch)
    _baux(monkeypatch, [])
    _journal(monkeypatch, CLOS)

    with pytest.raises(ClaimedRefUnresolved) as e:
        s.resolve_claimed_target()
    msg = str(e.value)
    assert "en tableau" in msg, "la forme employée reste nommée"
    assert "CLOS" in msg and "21:08:53" in msg, msg


def test_le_refus_ne_prescrit_aucun_outil_que_l_appelant_pourrait_ne_pas_avoir():
    """La règle de `docs/conventions.md` (#613 → #632), appliquée au texte qu'on ajoute.

    `data_claim_next` n'y est cité que comme la RÉPONSE qui a déjà rendu l'identifiant
    — un pointeur vers une valeur que l'appelant tient, pas un geste à exécuter. Aucun
    autre outil n'est nommé : `run_start` ouvrirait une porte dont on ne sait pas, ici,
    si elle est servie."""
    msg = str(D._refus_run_clos("", CLOS))
    outils = {m for m in ("data_release", "data_rows", "run_start", "oto_call",
                          "data_claim_next") if m in msg}
    assert outils == {"data_claim_next"}, msg
    assert "t'avait rendu" in msg, "cité comme source d'une valeur déjà reçue"


# ── ③ Ce qui ne change pas : run ouvert, et le chemin nominal ────────────────

def test_un_run_ouvert_garde_le_texte_de_fin_de_file(monkeypatch):
    """Le cas NORMAL derrière ce refus reste la fin de file (#517) : ne pas le
    remplacer par une clôture qu'on n'a pas lue."""
    s = _store(monkeypatch)
    _baux(monkeypatch, [])
    _journal(monkeypatch, None)

    with pytest.raises(ClaimedRefUnresolved) as e:
        s.resolve_claimed_ref("copie-eval-palier100")
    msg = str(e.value)
    assert "aucune réservation active" in msg and "row: null" in msg, msg
    assert "CLOS" not in msg


def test_hors_run_le_journal_n_est_pas_interroge(monkeypatch):
    """Sans run, il n'y a pas de clôture à raconter — et c'est un autre refus, plus
    haut, qui parle (« passe `_run_id` »). Une requête ici serait payée pour rien."""
    s = _store(monkeypatch, run=None)
    _baux(monkeypatch, [])
    vus = _journal(monkeypatch, CLOS)

    with pytest.raises(ClaimedRefUnresolved) as e:
        s.resolve_claimed_ref("copie-eval-palier100")
    assert "_run_id" in str(e.value)
    assert vus["appels"] == [], "aucune lecture du journal hors run"


def test_un_journal_illisible_degrade_le_message_jamais_le_refus(monkeypatch, caplog):
    """La requête ajoutée ne peut pas emporter le refus.

    `_adresse_reservee` le dit : « une erreur interne l'effacerait au moment précis où
    elle sert ». Une base qui bronche doit coûter la PRÉCISION du message (on ne sait
    plus dire la clôture), jamais le message lui-même — et la dégradation se voit dans
    les logs, sinon c'est une divergence muette."""
    s = _store(monkeypatch)
    _baux(monkeypatch, [])

    def _boum(_run):
        raise RuntimeError("DATABASE_URL not set (managed PG connection string)")

    monkeypatch.setattr(D.db, "run_closed_at", _boum, raising=False)

    with caplog.at_level("WARNING"):
        with pytest.raises(ClaimedRefUnresolved) as e:
            s.resolve_claimed_ref("copie-eval-palier100")
    assert "data_claim_next" in str(e.value), "l'agent garde une conduite"
    assert any("run-8" in r.getMessage() for r in caplog.records), (
        "la perte de précision est journalisée, pas avalée")


def test_le_chemin_nominal_ne_paie_pas_la_requete(monkeypatch):
    """La question « ce run est-il clos ? » n'a de sens que sur le chemin d'ÉCHEC. Un
    appel de plus par écriture réussie, sur un serveur mono-loop, se paierait sur
    toute la flotte."""
    s = _store(monkeypatch)
    _baux(monkeypatch, [{"ns_id": 7, "row_id": "r1"}])
    vus = _journal(monkeypatch, CLOS)

    assert s.resolve_claimed_ref("copie-eval-palier100") == "r1"
    assert vus["appels"] == [], "l'alias résout sans interroger le journal"


# ── ④ La borne est dans la description SERVIE ────────────────────────────────

_MCP = None
_OUTILS: dict = {}


def _outil(nom: str):
    """Ce que charge le BOOT (`register_all`) — pas une docstring lue au module.

    Un seul montage pour tout le fichier, mais résolu outil PAR outil : une première
    version pré-remplissait une liste fermée et rendait `KeyError` sur le premier nom
    hors liste — une erreur qui, attrapée par un `pytest.raises(Exception)`, se lisait
    exactement comme le refus qu'on veut prouver."""
    global _MCP
    if _MCP is None:
        from fastmcp import FastMCP

        from oto_mcp.tools import register_all
        _MCP = FastMCP("t-645")
        register_all(_MCP)
    if nom not in _OUTILS:
        _OUTILS[nom] = asyncio.run(_MCP.get_tool(nom))
    return _OUTILS[nom]


def _servi(texte) -> str:
    return " ".join((texte or "").split())


def test_la_description_servie_de_data_write_borne_l_alias():
    """Le texte le plus près du geste gagne (#613) : la borne est dans la description
    des PARAMÈTRES, celle que le modèle relit en construisant l'appel — et sur les
    DEUX champs qui acceptent l'alias, parce que l'asymétrie entre eux est exactement
    ce qui a coûté deux écritures le 29/08 (#599)."""
    props = _outil("data_write").parameters["properties"]
    id_ = _servi(props["id"]["description"])
    ns = _servi(props["namespace"]["description"])
    assert "only while that run is OPEN" in id_, id_
    assert "run_finish" in id_, "ce qui ferme la porte se nomme là où l'alias se pose"
    assert "open run only" in ns, ns


def test_la_description_servie_de_data_release_borne_l_alias():
    """`data_release` porte déjà « closing the run frees everything it held » : ce qui
    manquait est que l'ADRESSE, elle, cesse de résoudre au même instant."""
    d = _servi(_outil("data_release").description)
    assert "only while that run is OPEN" in d, d


# ── ① Le cas de production, bout en bout ─────────────────────────────────────

@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_runclos_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture
def surface(live, monkeypatch):
    """Les outils `data_*` tels que le serveur les monte, l'acteur tenu."""
    from oto_mcp.datastore.core import make_store
    from oto_mcp.tools import datastore as T
    monkeypatch.setattr(T, "_acting_store", lambda: make_store(SUB))
    monkeypatch.setattr(T, "_ns", lambda ns: ns)
    monkeypatch.setattr(T, "_project_hint", lambda ns: None)


def _table():
    from oto_mcp import db
    ns = "palier-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", SUB, ns)
    db.datastore_insert_row(ns_id, "r0", {"siren": "551110001", "statut": "a_enrichir"})
    return ns, ns_id


def _appel(nom: str, args: dict):
    """L'appel comme il arrive en production : `_run_id=` lu des arguments BRUTS par le
    middleware, posé, retiré, puis l'outil dispatché. Chaque appel est sa propre
    session (le chemin de la flotte) : rien ne survit entre deux."""
    from oto_mcp.middleware.call_context import CallContextMiddleware

    outil = _outil(nom)

    class _Msg:
        pass

    class _Ctx:                     # comme le vrai MiddlewareContext : PAS de get_state
        pass

    msg = _Msg()
    msg.name, msg.arguments = nom, dict(args)
    ctx = _Ctx()
    ctx.message = msg

    async def _next(c):
        return await outil.run(c.message.arguments)

    async def _go():
        return await CallContextMiddleware(frozenset()).on_call_tool(ctx, _next)

    return asyncio.run(_go())


class _SessionCtx:
    """La pile de runs de la session, telle que `run_finish` la lit (`pop_run`)."""

    def __init__(self):
        self._state: dict = {}

    async def get_state(self, key):
        return self._state.get(key)

    async def set_state(self, key, value):
        self._state[key] = value


def _run_finish(run: str) -> dict:
    """`run_finish` tel que `register_all` le monte — c'est lui qui libère les baux.

    Le FAIT de journal, lui, est écrit par le sink du calllog (`db.insert_tool_call`),
    hors de cette chaîne de middlewares : on l'écrit par le MÊME writer que la
    production, et le test ci-dessous vérifie la forme obtenue avec un lecteur
    indépendant plutôt que de la supposer."""
    return asyncio.run(_outil("run_finish").fn(_SessionCtx(), run_id=run, outcome="done"))


def _fait(tool: str, run: str, args: dict) -> None:
    from oto_mcp import db
    db.insert_tool_call({"server": "oto", "kind": "mcp", "sub": SUB, "tool": tool,
                         "args": args, "ok": True, "run_id": run})


def test_apres_run_finish_l_ecriture_sous_l_alias_dit_la_cloture(surface):
    """Le scénario des 99 refus, joué en entier — et la preuve que le journal seul
    suffit à le dire.

    Un lecteur INDÉPENDANT (`db.my_runs(open_only=True)`, celui qui sert « refermer ce
    qu'on a ouvert ») atteste au passage que les faits écrits ici ont bien la forme
    d'une clôture : sans ce croisement, ce test ne prouverait que sa propre mise en
    scène."""
    from oto_mcp import db
    ns, ns_id = _table()
    run = uuid.uuid4().hex

    ligne = _appel("data_claim_next", {
        "namespace": ns, "worker": run, "filter": {"statut": "a_enrichir"},
        "lease_s": 600, "_run_id": run}).structured_content["row"]
    assert ligne, "une ligne était libre : le claim la rend"

    _fait("run_start", run, {"label": "palier 100"})
    out = _run_finish(run)
    assert out["ok"] and out.get("rows_released") == 1, out
    _fait("run_finish", run, {"run_id": run, "outcome": "done"})

    assert [r["run_id"] for r in db.my_runs(SUB, open_only=True)] == [], (
        "le lecteur indépendant voit un run CLOS — donc les faits écrits en ont la forme")

    # `McpError` et pas `Exception` : le refus doit traverser la surface en
    # INVALID_PARAMS — attraper large ferait passer une erreur de mise en scène pour
    # le message qu'on prouve (c'est arrivé à la première exécution de ce fichier).
    with pytest.raises(McpError) as e:
        _appel("data_write", {"namespace": "@claimed", "id": "@claimed",
                              "row": {"statut": "fait"}, "_run_id": run})
    msg = str(e.value)
    assert "aucune réservation active" not in msg, msg
    assert "CLOS" in msg and "run_finish" in msg, msg
    assert db.datastore_get_row(ns_id, ligne["_id"])["data"]["statut"] == "a_enrichir", (
        "rien n'a été écrit : le refus tient toujours")
