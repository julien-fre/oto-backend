"""`_claimed_run` : la ligne servie dit POUR QUEL RUN elle est réservée.

Le bail servi disait à QUI (`_claimed_by`) et JUSQU'À QUAND (`_claimed_until`), jamais
POUR QUEL RUN — alors que `datastore_rows.claimed_run` le porte depuis le verrou natif.
Conséquence côté surveillance : on voyait qu'un agent tenait une ligne, jamais laquelle
tenait laquelle. Le serveur savait pourtant déjà répondre — l'alias `@claimed` résout
run → ligne par cette même colonne — mais seulement au run LUI-MÊME (il faut porter son
jeton), donc jamais à un tiers qui regarde la file.

Ce que ces tests verrouillent :

1. **La couture servie**, sur TOUS les chemins qui rendent une ligne réservée
   (liste, fiche par id, curseur keyset, curseur trié, file de supervision, et le
   claim lui-même) : contre un vrai PostgreSQL, avec la réservation posée comme en
   production (middleware `_run_id=` + outil monté par `register_all`).
2. **Les trois états, pas deux** : `_claimed_run` = le run ; `null` = le bail a été
   pris SANS run (une personne sur la file du dashboard) ; ABSENT = pas de bail du
   tout. Un `null` fabriqué par un SELECT qui aurait oublié la colonne dirait « ce
   bail n'a pas de run » — un fait faux, pas un trou visible.
3. **La garde mécanique** qui empêche le point 2 de retomber : toute requête de
   `oto_mcp/db/` qui projette une ligne pour l'API et nomme `claimed_by` doit nommer
   `claimed_run`. `_row_to_dict` lit la colonne par CLÉ (pas `.get`) : un chemin qui
   l'oublie LÈVE au lieu de servir un faux « sans run ».

⚠️ Hors périmètre, et volontairement : `datastore_release` n'efface pas `claimed_run`
(oto-backend#664), donc la colonne peut rester garnie sur une ligne libre. Rien n'en
sort : la projection ne parle du run que sous `claimed_by IS NOT NULL`, et le test
`test_une_ligne_liberee_ne_dit_plus_rien_du_run` le fige.
"""
from __future__ import annotations

import asyncio
import ast
import pathlib
import uuid

import pytest

SUB = "sub-claimed-run"
ORG = 664


# ── ① la garde mécanique : elle ne demande à personne de se souvenir ────────────

# La signature d'une requête qui construit une ligne pour `_row_to_dict` : ces quatre
# colonnes, dans cet ordre, sont son en-tête. On ne cherche pas « une requête qui parle
# de baux » (il y en a qui libèrent, qui comptent, qui sondent) mais « une requête qui
# RÉPOND une ligne ».
_ENTETE_LIGNE = "row_id, created_at, updated_at, data"


def _requetes_de_ligne() -> list[tuple[str, str]]:
    """(fichier, littéral SQL) des requêtes de `db/` qui projettent une ligne servie.

    Par l'AST, jamais par `grep` : un littéral SQL est écrit en plusieurs morceaux
    concaténés, et une recherche textuelle sur le fichier verrait des colonnes qui
    n'appartiennent pas à la même requête.
    """
    racine = pathlib.Path(__file__).resolve().parents[2] / "oto_mcp" / "db"
    trouvees = []
    for f in sorted(racine.glob("*.py")):
        arbre = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(arbre):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                sql = n.value
            elif isinstance(n, ast.BinOp) or isinstance(n, ast.JoinedStr):
                continue
            else:
                continue
            if _ENTETE_LIGNE in sql and "claimed_by" in sql:
                trouvees.append((f.name, sql))
    return trouvees


def test_toute_projection_de_ligne_porte_claimed_run():
    """Une requête qui rend une ligne AVEC son bail rend les trois champs du bail.

    Sans cette garde, ajouter un chemin de lecture de plus ferait servir
    `_claimed_run: null` sur des lignes qui ONT un run — un mensonge silencieux, la
    forme de défaut la plus coûteuse qu'on connaisse ici. Avec elle, l'oubli est
    rouge à l'écriture du SELECT.
    """
    requetes = _requetes_de_ligne()
    assert requetes, ("aucune requête de projection trouvée : la signature "
                      f"{_ENTETE_LIGNE!r} a changé, la garde ne garde plus rien")
    manquantes = [(f, sql) for f, sql in requetes if "claimed_run" not in sql]
    assert not manquantes, (
        "ces requêtes projettent une ligne avec `claimed_by` sans `claimed_run` — "
        "`_row_to_dict` lit la colonne par clé, elles lèveraient en production :\n"
        + "\n".join(f"  {f}: {sql[:120]}…" for f, sql in manquantes))


def test_claimed_run_est_une_colonne_de_plateforme():
    """`_claimed_run` ne doit pas pouvoir être écrasé par une colonne utilisateur."""
    from oto_mcp.datastore.columns import _META_COLS
    assert "_claimed_run" in _META_COLS


def test_la_projection_leve_si_la_colonne_na_pas_ete_lue():
    """Le refus est le comportement VOULU, il se prouve : une ligne réservée dont le
    SELECT a oublié `claimed_run` ne se sert pas à moitié, elle lève."""
    from oto_mcp.datastore.core import DatastorePg
    with pytest.raises(KeyError):
        DatastorePg._row_to_dict({"row_id": "r1", "created_at": "2026-09-01",
                                  "updated_at": "2026-09-01", "data": {},
                                  "claimed_by": "w", "claimed_until": "2026-09-02"})


def test_le_champ_est_declare_au_contrat_servi():
    """Servi n'est pas déclaré : un client typé ne voit que ce que l'OpenAPI nomme."""
    from oto_mcp.capabilities.datastore.rows import Row
    champ = Row.model_fields["claimed_run"]
    assert champ.serialization_alias == "_claimed_run"
    assert champ.description and "null" in champ.description, \
        "le contrat doit dire ce que `null` veut dire, sinon il sera lu « aucun run »"


# ── ② la couture servie, contre un vrai PostgreSQL ─────────────────────────────

@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_claimedrun_" + uuid.uuid4().hex[:8]
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


_OUTILS: dict = {}


def _outil(nom: str):
    """Ce que charge le BOOT (`register_all`), pas un module seul."""
    if nom not in _OUTILS:
        from fastmcp import FastMCP

        from oto_mcp.tools import register_all
        m = FastMCP("t-claimed-run")
        register_all(m)
        _OUTILS[nom] = asyncio.run(m.get_tool(nom))
    return _OUTILS[nom]


def _run() -> str:
    from oto_mcp import db
    run_id = uuid.uuid4().hex
    db.insert_run(run_id, sub=SUB, org_id=ORG, label="surveillance")
    return run_id


def _table(n: int = 1):
    from oto_mcp import db
    ns = "file-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", SUB, ns)
    for i in range(n):
        db.datastore_insert_row(ns_id, f"r{i}", {"siren": f"5511100{i}",
                                                 "statut": "a_enrichir"})
    return ns, ns_id


def _claim(ns: str, run: str | None) -> dict:
    """La réservation comme elle arrive en production : `_run_id=` lu des arguments
    BRUTS par le middleware, posé, retiré, puis l'outil dispatché. `run=None` = la
    file pilotée à la main, sans jeton de run."""
    from oto_mcp.middleware.call_context import CallContextMiddleware

    outil = _outil("data_claim_next")

    class _Msg:
        pass

    class _Ctx:                     # comme le vrai MiddlewareContext : PAS de get_state
        pass

    msg = _Msg()
    msg.name = "data_claim_next"
    msg.arguments = {"namespace": ns, "worker": "poste-1",
                     "filter": {"statut": "a_enrichir"}, "lease_s": 600}
    if run is not None:
        msg.arguments["_run_id"] = run
    ctx = _Ctx()
    ctx.message = msg

    async def _next(c):
        return await outil.run(c.message.arguments)

    async def _go():
        return await CallContextMiddleware(frozenset()).on_call_tool(ctx, _next)

    return asyncio.run(_go()).structured_content


def _store():
    from oto_mcp.datastore.core import make_store
    return make_store(SUB)


def test_tous_les_chemins_servis_disent_le_run(surface):
    """Le claim, la liste, la fiche, les deux curseurs et la file de supervision : la
    même ligne, le même run. Un seul chemin qui l'oublierait suffirait à rendre la
    vue incohérente selon l'écran ouvert."""
    ns, ns_id = _table(1)
    run = _run()

    pris = _claim(ns, run)["row"]
    assert pris["_claimed_run"] == run, "le claim rend déjà l'adresse du travail"
    rid = pris["_id"]

    store = _store()
    lignes = {
        # `list_rows` → `datastore_list_rows` ; `cursor_rows` sans `order_by` →
        # `datastore_list_rows_after` (keyset) ; avec `order_by` → la voie triée.
        # Les trois SELECT sont distincts : les couvrir tous les trois est le sujet.
        "liste": next(r for r in store.list_rows(ns) if r["_id"] == rid),
        "par_id": store.get_row(ns, rid),
        "curseur": next(r for r in store.cursor_rows(ns, limit=10)["rows"]
                        if r["_id"] == rid),
        "curseur_trie": next(r for r in store.cursor_rows(
            ns, limit=10, order_by="_created_at")["rows"] if r["_id"] == rid),
        "file": next(r for r in store.queue(ns) if r["_id"] == rid),
    }
    for nom, ligne in lignes.items():
        assert ligne["_claimed_run"] == run, f"{nom} ne dit pas quel run tient la ligne"
        assert ligne["_claimed_by"] == "poste-1", f"{nom} : le bail est incomplet"


def test_un_bail_sans_run_vaut_null_et_pas_une_absence(surface):
    """Réserver sans jeton de run est un cas NORMAL (la file pilotée par une
    personne). La réponse doit le dire : la clé est là, sa valeur est `null` — « ce
    bail n'appartient à aucun run », pas « je ne sais pas »."""
    ns, _ = _table(1)
    pris = _claim(ns, None)["row"]
    assert pris["_claimed_by"] == "poste-1", "le bail est bien posé"
    assert "_claimed_run" in pris, "la clé manquante se lirait « pas de bail »"
    assert pris["_claimed_run"] is None


def test_une_ligne_libre_ne_porte_aucune_des_trois_cles(surface):
    """Absent, pas nul : sur les millions de lignes jamais réservées, trois `null`
    par ligne seraient du bruit dans toutes les lectures."""
    ns, _ = _table(1)
    ligne = _store().list_rows(ns)[0]
    for cle in ("_claimed_by", "_claimed_until", "_claimed_run"):
        assert cle not in ligne


def test_une_ligne_liberee_ne_dit_plus_rien_du_run(surface):
    """`datastore_release` laisse `claimed_run` en base (oto-backend#664) — la
    projection, elle, se tait dès que le bail tombe. La colonne périmée ne sort pas."""
    ns, ns_id = _table(1)
    run = _run()
    rid = _claim(ns, run)["row"]["_id"]

    from oto_mcp import db
    assert db.datastore_release_claim(ns_id, rid, "poste-1") is True
    ligne = _store().get_row(ns, rid)
    assert "_claimed_run" not in ligne, \
        "servir le run d'un bail mort ferait croire qu'un travail tient la ligne"


def test_rendre_les_lignes_du_run_efface_l_adresse(surface):
    """La contrepartie à écrire noir sur blanc : `_claimed_run` répond « sur quelle
    ligne ce run est-il MAINTENANT », jamais « quelle ligne ce run a-t-il
    travaillée ». Un run conclu n'a plus d'adresse."""
    ns, _ = _table(1)
    run = _run()
    rid = _claim(ns, run)["row"]["_id"]

    from oto_mcp import db
    assert db.datastore_release_by_run(run) == 1
    assert "_claimed_run" not in _store().get_row(ns, rid)
