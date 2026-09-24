"""La FORME de ce qui a été servi, dans le journal (oto-backend#644).

`result_size` (#340) dit combien, pas quoi : un 0 ne sépare pas une liste vide d'un
refus rendu poliment, et un non-zéro ne sépare pas un résultat d'un `{"error": …}`
rendu sous `ok=true`. C'est précisément l'ambiguïté qui empêche de trancher « l'agent
a appelé l'outil et mal rapporté » contre « il ne l'a pas appelé ».

`result_shape` est un vocabulaire FERMÉ — `empty` | `non_empty` | `refused(<code>)` —
jamais le contenu. Le banc suit les quatre relais de #340 : le middleware la calcule
sur la forme que fastmcp fait RÉELLEMENT passer, le sink l'écrit, la colonne
l'accueille (révision 0008, pas le démarrage), la liste et la fiche la rendent.
"""
from __future__ import annotations

import asyncio
import types
import uuid
from pathlib import Path

import pytest

from oto_mcp import calllog

RACINE = Path(__file__).resolve().parent.parent


async def _drain():
    while calllog._PENDING:
        await asyncio.gather(*list(calllog._PENDING), return_exceptions=True)


def _serveur(rows):
    from fastmcp import FastMCP

    mcp = FastMCP("t644")

    @mcp.tool
    def liste_vide() -> list:
        return []

    @mcp.tool
    def objet_vide() -> dict:
        return {}

    @mcp.tool
    def rien() -> None:
        return None

    @mcp.tool
    def texte_vide() -> str:
        return ""

    @mcp.tool
    def trouve() -> dict:
        return {"items": [{"k": 1}]}

    @mcp.tool
    def refus_poli() -> dict:
        return {"error": "not_found", "siren": "552100554"}

    @mcp.tool
    def refus_en_texte_libre() -> dict:
        return {"ok": False, "error": "TypeError: le siren 552100554 est invalide"}

    @mcp.tool
    def casse() -> str:
        raise RuntimeError("boom")

    async def sink(row):
        rows.append(row)

    async def identite():
        return {"sub": "u1"}

    mcp.add_middleware(calllog.ToolCallLogger(sink, server="oto", identity=identite))
    return mcp


# ── 1. le chemin : un vrai serveur, un vrai client ────────────────────────────

@pytest.mark.asyncio
async def test_un_appel_REEL_ecrit_la_forme_de_ce_qui_a_ete_servi():
    """⚠️ Le seul banc qui prouve que la mesure lit la forme que fastmcp fait passer
    (une sortie non-objet rangée sous `{"result": …}`, un `None` sans rien), et pas
    celle qu'on suppose en lisant le code."""
    from fastmcp import Client

    rows: list = []
    async with Client(_serveur(rows)) as c:
        for outil in ("liste_vide", "objet_vide", "rien", "texte_vide", "trouve",
                      "refus_poli", "refus_en_texte_libre"):
            await c.call_tool(outil, {})
        with pytest.raises(Exception):
            await c.call_tool("casse", {})
    await _drain()

    formes = {r["tool"]: r.get("result_shape") for r in rows if r["tool"] != "initialize"}
    assert formes == {
        "liste_vide": "empty",
        "objet_vide": "empty",
        "rien": "empty",
        "texte_vide": "empty",
        "trouve": "non_empty",
        "refus_poli": "refused(not_found)",
        # le message n'est pas un code : il ne passe pas, le refus reste nommé comme tel
        "refus_en_texte_libre": "refused(unnamed)",
        # un appel en échec n'a rien servi : non mesurée, comme `result_size`
        "casse": None,
    }


# ── 2. la fonction : ce qu'elle refuse d'écrire ───────────────────────────────

@pytest.mark.parametrize("sc,attendu", [
    ({"error": "identity_unavailable"}, "refused(identity_unavailable)"),
    ({"ok": False, "code": "quota_exceeded", "error": "Quota dépassé"}, "refused(quota_exceeded)"),
    ({"error": {"code": "not_found", "message": "…"}}, "refused(not_found)"),
    ({"error": "domaine invalide"}, "refused(unnamed)"),          # texte libre
    ({"error": "tok_4f9a8b7c6d5e"}, "refused(unnamed)"),          # des chiffres : pas un code
    ({"error": "x" * 41}, "refused(unnamed)"),                    # trop long pour un code
    ({"error": None, "items": [1]}, "non_empty"),                 # une clé `error` vide
    ({"ok": True, "items": []}, "non_empty"),
    ({"result": [1, 2]}, "non_empty"),
    ({"result": ""}, "empty"),
])
def test_le_vocabulaire_est_ferme_et_ne_porte_jamais_de_contenu(sc, attendu):
    assert calllog.forme_servie(types.SimpleNamespace(structured_content=sc,
                                                      content=[])) == attendu


@pytest.mark.parametrize("objet,attendu", [
    (types.SimpleNamespace(structured_content=None, content=[]), "empty"),
    (types.SimpleNamespace(structured_content=None,
                           content=[types.SimpleNamespace(text="[]")]), "empty"),
    (types.SimpleNamespace(structured_content=None,
                           content=[types.SimpleNamespace(text="abc")]), "non_empty"),
    (types.SimpleNamespace(structured_content=None,
                           content=[types.SimpleNamespace(data="img")]), "non_empty"),
    (types.SimpleNamespace(), None),          # ni donnée ni blocs : non mesurée
    ("une chaîne nue", None),                 # la forme des bancs stubbés
    (None, None),
])
def test_sans_donnee_structuree_la_forme_se_lit_sur_les_blocs(objet, attendu):
    assert calllog.forme_servie(objet) == attendu


def test_la_mesure_ne_casse_JAMAIS_l_appel_qu_elle_observe():
    class Piege:
        @property
        def structured_content(self):
            raise RuntimeError("forme hostile")

    assert calllog.forme_servie(Piege()) is None


# ── 3. la base : la colonne accueille, la liste et la fiche rendent ──────────

def test_la_forme_traverse_le_sink_et_ressort_de_la_liste_et_de_la_fiche(live):
    from oto_mcp import db

    run = uuid.uuid4().hex
    db.insert_tool_call({"tool": "t644_refus", "sub": "u-644", "kind": "mcp", "ok": True,
                         "run_id": run, "result_size": 30,
                         "result_shape": "refused(not_found)"})
    db.insert_tool_call({"tool": "t644_historique", "sub": "u-644", "kind": "mcp",
                         "ok": True, "run_id": run})

    liste = {r["tool_name"]: r for r in db.list_tool_calls(run_id=run)}
    assert liste["t644_refus"]["result_shape"] == "refused(not_found)"
    assert liste["t644_historique"]["result_shape"] is None, "non mesurée : NULL"

    fiche = db.get_tool_call(liste["t644_refus"]["id"])
    assert fiche["result_shape"] == "refused(not_found)"


@pytest.mark.parametrize("valeur", ["sk-ant-api03-abc", "refused(TypeError: boom)",
                                    "non_empty ", "refused(tok_4f9a)"])
def test_la_BASE_refuse_toute_valeur_hors_du_vocabulaire(live, valeur):
    """Le vocabulaire n'est pas fermé par politesse du code : une valeur libre — une
    clé, un message — est refusée par la contrainte, sur toute base."""
    import psycopg
    from oto_mcp.db import _connect

    with pytest.raises(psycopg.errors.CheckViolation):
        with _connect() as conn:
            conn.execute("INSERT INTO tool_calls (tool, result_shape) VALUES (%s, %s)",
                         ("t644_libre", valeur))


# ── 4. la révision : une base existante la reçoit d'Alembic, jamais du boot ──

def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _a_la_colonne(dsn: str) -> bool:
    import psycopg
    with psycopg.connect(dsn) as c:
        return bool(c.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'tool_calls' AND column_name = 'result_shape'").fetchone())


def test_la_revision_pose_la_colonne_que_le_boot_ne_pose_pas(live, pg_module_dsn):
    """⚠️ Le piège de #340 : une colonne ajoutée au seul `CREATE TABLE` n'arrive jamais
    sur la table de production, qui existe déjà. Mesuré, pas affirmé : on la retire, le
    démarrage ne la rend pas (par décision — table énorme, où chaque appel écrit), la
    révision la rend, et le retour arrière la retire."""
    import psycopg
    from alembic import command
    from oto_mcp.db import init_db

    assert _a_la_colonne(pg_module_dsn), "une base NEUVE la reçoit du CREATE TABLE"
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("ALTER TABLE tool_calls DROP COLUMN result_shape")

    init_db()
    assert not _a_la_colonne(pg_module_dsn), "le démarrage ne pose pas cette colonne"

    cfg = _alembic()
    # Le registre à la révision d'avant : une base neuve naît à la tête (#969).
    command.stamp(cfg, "0009_coffre_secret_obligatoire")
    command.upgrade(cfg, "0010_tool_calls_result_shape")
    assert _a_la_colonne(pg_module_dsn), "la révision n'a rien écrit"
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO tool_calls (tool, result_shape) VALUES ('t', 'libre')")
    command.downgrade(cfg, "0009_coffre_secret_obligatoire")
    assert not _a_la_colonne(pg_module_dsn), "le retour arrière n'a rien retiré"
    command.upgrade(cfg, "head")
    assert _a_la_colonne(pg_module_dsn)
