"""L'alias `@claimed` après une réservation — le cas de production du 29/08, 15:24 (#517).

Trois appels d'un même travail, même `_run_id` à l'octet :

    15:24:08  data_claim_next                                  ok
    15:24:32  data_write(namespace="@claimed")                 « ton travail ne tient aucune ligne »
    15:24:43  data_write(id="6738f4c2-57c0-43b9-9d78-XXXXXXXXXXXX")  « introuvable »

Lu d'abord comme « le claim pose une identité que l'alias ne retrouve pas ». C'est
faux, et le premier test le fige : la réservation s'écrit sur le RUN (`claimed_run`)
et l'alias se lit sur le RUN — `worker` n'est qu'un libellé, et `data_write` n'en
passe aucun. Le fait réel, relu dans le journal : **le claim avait rendu `row: null`**
(la dernière ligne de la file était sous le bail actif d'un pair, qui l'a écrite
71 ms plus tard). Le travail ne tenait rien, et le refus le disait juste.

Ce qui a coûté, c'est la SUITE de chaque refus : « … ou écris avec un identifiant
explicite », puis « un identifiant a la forme `01a04aef-…` ». Deux invitations que
l'agent a suivies à la lettre — il a fabriqué un identifiant sur le gabarit, douze X
à la place du dernier groupe. Rien n'a été écrit (le second refus a tenu), mais
c'est la plateforme qui lui avait soufflé le geste.

D'où les autres tests : un claim à vide suivi de l'alias refuse EN NOMMANT la file
vide, sans inviter à fournir un identifiant ; « introuvable » décrit la forme sans
en montrer une qui se recopie ; et un libellé de worker qui ne correspond pas est
nommé comme tel, au lieu de passer pour « aucune réservation ».

⚠️ Chemin RÉEL : les appels traversent `CallContextMiddleware.on_call_tool` (qui pose
`_run_id=` AVANT le dispatch) puis l'outil enregistré, contre un vrai PostgreSQL. Le
contexte passé au middleware n'a PAS de `get_state`, comme le vrai `MiddlewareContext`.
"""
from __future__ import annotations

import asyncio
import re
import uuid

import pytest

RUN_R = "7096ddacf50e4542b7155ab089a7ab41"      # le travail du 15:24
RUN_Q = "de5f081ac7e84d5598c348bf203f1fe8"      # le pair qui tenait la dernière ligne


def _run(modele: str) -> str:
    """Un jeton de run PROPRE à ce test. Les baux d'un run se lisent sur toute la base
    (c'est le sens de `@claimed` en tableau) : deux tests sous le même jeton se
    verraient l'un l'autre — vécu à la première exécution de ce fichier, où ② résolvait
    la ligne que ① tenait encore dans un autre tableau."""
    return modele[:24] + uuid.uuid4().hex[:8]


SUB = "sub-flotte"
GABARIT = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_claimvide_" + uuid.uuid4().hex[:8]
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


def _table(n: int):
    """Un tableau de `n` lignes « à enrichir », comme la file du palier."""
    from oto_mcp import db
    ns = "palier-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", SUB, ns)
    for i in range(n):
        db.datastore_insert_row(ns_id, f"r{i}", {"siren": f"5511100{i}", "statut": "a_enrichir"})
    return ns, ns_id


@pytest.fixture
def surface(live, monkeypatch):
    """Les outils `data_*` tels que le serveur les monte, l'acteur tenu."""
    from oto_mcp.datastore.core import make_store
    from oto_mcp.tools import datastore as T
    monkeypatch.setattr(T, "_acting_store", lambda: make_store(SUB))
    monkeypatch.setattr(T, "_ns", lambda ns: ns)
    monkeypatch.setattr(T, "_project_hint", lambda ns: None)


_OUTILS: dict = {}


def _outil(nom: str):
    if not _OUTILS:
        from fastmcp import FastMCP

        from oto_mcp.tools import datastore as T
        m = FastMCP("t")
        T.register(m)
        for n in ("data_claim_next", "data_write", "data_release"):
            _OUTILS[n] = asyncio.run(m.get_tool(n))
    return _OUTILS[nom]


def _appel(nom: str, args: dict) -> dict:
    """L'appel comme il arrive en production : `_run_id=` lu des arguments BRUTS par
    le middleware, posé, retiré, puis l'outil dispatché. Chaque appel est sa propre
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

    return asyncio.run(_go()).structured_content


def _bail(ns_id, row_id) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return dict(conn.execute(
            "SELECT claimed_by, claimed_until, claimed_run, claims FROM datastore_rows "
            "WHERE ns_id = %s AND row_id = %s", (ns_id, row_id)).fetchone() or {})


def _vieillir(ns_id, row_id, secondes: int) -> None:
    """`secondes` se sont écoulées depuis la réservation — le bail est déplacé, pas
    attendu."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(
            "UPDATE datastore_rows SET claimed_until = claimed_until - (%s || ' seconds')::interval "
            "WHERE ns_id = %s AND row_id = %s", (int(secondes), ns_id, row_id))


def _claim(ns: str, run: str, *, worker: str | None = None, lease_s: int = 600) -> dict:
    return _appel("data_claim_next", {
        "namespace": ns, "worker": worker or run, "filter": {"statut": "a_enrichir"},
        "lease_s": lease_s, "_run_id": run})


# ── ① La réservation et l'alias se lisent sur le MÊME run ──────────────────────

def test_la_reservation_s_ecrit_sur_le_run_et_l_alias_se_lit_sur_le_run(surface):
    """Le scénario exact du 15:24, avec une ligne dans la file : claim sous `_run_id=R`
    (le `worker` est le run lui-même, comme la flotte l'a passé), puis 24 s plus tard
    `data_write(namespace="@claimed", id="@claimed", _run_id=R)` — SANS worker, l'outil
    n'en a pas. Doit résoudre : l'identité stable d'un travail est son run."""
    from oto_mcp import db
    ns, ns_id = _table(3)
    run_r = _run(RUN_R)

    rendu = _claim(ns, run_r)
    ligne = rendu["row"]
    assert ligne, "une ligne était libre : le claim la rend"
    bail = _bail(ns_id, ligne["_id"])
    assert bail["claimed_run"] == run_r, "la réservation porte le run de l'appel"
    assert bail["claimed_by"] == run_r, "et le libellé, tel quel"

    _vieillir(ns_id, ligne["_id"], 24)

    out = _appel("data_write", {"namespace": "@claimed", "id": "@claimed",
                                "row": {"statut": "en_cours"}, "_run_id": run_r})
    assert out["_id"] == ligne["_id"], "l'alias désigne la ligne que le run tient"
    assert db.datastore_get_row(ns_id, ligne["_id"])["data"]["statut"] == "en_cours"


# ── ② Le claim à vide, puis l'alias : le refus nomme la file vide ───────────────

def test_un_claim_a_vide_puis_l_alias_refuse_en_nommant_la_file_vide(surface):
    """Le fait du 15:24 : la dernière ligne « à enrichir » était sous le bail ACTIF d'un
    pair. Le claim du travail R rend `row: null` — il ne tient rien, et c'est vrai.

    Le refus qui suit ne doit plus dire « ou écris avec un identifiant explicite » :
    c'est la phrase que l'agent a exécutée en fabriquant un identifiant. Il doit dire
    ce qui s'est passé (la file est vide pour ce filtre) et ce qu'il reste à faire
    (rien à écrire, terminer) — et le rendu du claim à vide doit déjà le dire."""
    from mcp.shared.exceptions import McpError
    ns, ns_id = _table(1)
    run_q, run_r = _run(RUN_Q), _run(RUN_R)

    assert _claim(ns, run_q, lease_s=900)["row"], "le pair prend la dernière ligne"
    vide = _claim(ns, run_r)
    assert vide["row"] is None, "plus rien de libre pour ce filtre"
    assert "run_finish" in vide["hint"] and "@claimed" in vide["hint"], (
        "le rendu du claim à vide dit déjà qu'on ne tient rien et qu'on n'écrit pas")

    for adresse in ({"namespace": "@claimed", "id": "@claimed"},
                    {"namespace": ns, "id": "@claimed"}):
        with pytest.raises(McpError) as e:
            _appel("data_write", {**adresse, "row": {"statut": "en_cours"}, "_run_id": run_r})
        msg = str(e.value)
        assert "identifiant explicite" not in msg, (
            "l'invitation à fournir un identifiant est ce qui a fait fabriquer le faux")
        assert "row: null" in msg and "vide" in msg, "le refus nomme le claim à vide"
        assert "invente" in msg and "run_finish" in msg, "et dit quoi faire : rien, terminer"

    # Et rien n'a été écrit chez le pair.
    assert _bail(ns_id, "r0")["claimed_run"] == run_q


# ── ③ « introuvable » décrit la forme, sans gabarit à recopier ──────────────────

def test_introuvable_decrit_la_forme_sans_montrer_de_gabarit(surface):
    """L'ancien refus montrait `01a04aef-26c0-7c16-9c58-42f8af87e80c` « (cinq groupes
    hexadécimaux) ». L'agent y a lu un modèle à remplir et a rendu
    `6738f4c2-57c0-43b9-9d78-XXXXXXXXXXXX`. La forme se DÉCRIT (36 caractères, rendu
    par `data_write`/`data_claim_next`, on ne l'invente pas) ; elle ne se montre pas."""
    from mcp.shared.exceptions import McpError
    ns, _ = _table(1)
    faux = "6738f4c2-57c0-43b9-9d78-XXXXXXXXXXXX"

    with pytest.raises(McpError) as e:
        _appel("data_write", {"namespace": ns, "id": faux,
                              "row": {"statut": "en_cours"}, "_run_id": _run(RUN_R)})
    msg = str(e.value)
    assert faux in msg, "l'identifiant refusé se cite"
    assert not GABARIT.search(msg), "aucun exemple qui ressemble à un modèle à remplir"
    assert "36 caractères" in msg and "invente" in msg, "la forme se décrit"
    assert 'id="@claimed"' in msg, "et la sortie sans recopie se rappelle"
    assert "pas la forme" in msg, "douze X : ce n'est pas un identifiant, on le dit"


# ── ④ Un libellé de worker qui ne correspond pas est nommé, pas maquillé ────────

def test_un_autre_libelle_sur_le_relachement_nomme_le_libelle_tenu(surface):
    """`data_release` porte un `worker` de garde. Rejoué avec un autre libellé, le
    refus disait « aucune réservation active » — faux : le run tient bien une ligne,
    sous un autre libellé. Le refus doit dire lequel, sinon l'agent va réserver une
    seconde ligne pour retrouver la première."""
    from mcp.shared.exceptions import McpError
    ns, ns_id = _table(1)
    run_r = _run(RUN_R)

    assert _claim(ns, run_r, worker="w-au-claim")["row"]
    with pytest.raises(McpError) as e:
        _appel("data_release", {"namespace": ns, "id": "@claimed",
                                "worker": "w-autre", "_run_id": run_r})
    msg = str(e.value)
    assert "w-au-claim" in msg and "w-autre" in msg, "les deux libellés, pour que l'écart se voie"
    assert "aucune réservation active" not in msg
    assert _bail(ns_id, "r0")["claimed_by"] == "w-au-claim", "et rien n'a bougé"
