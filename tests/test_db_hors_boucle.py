"""Garde « pas de SQL dans la boucle » : le balayage statique, puis la garde d'exécution.

Le serveur est mono-loop : un `async def` qui atteint la base — directement, ou par une
chaîne d'assistants synchrones — tient TOUT le monde le temps de la requête (gel de prod du
21/09/2026, ~140 s, `me.agent_context`). Deux détecteurs, qui se recouvrent :

- le **balayage statique** (`_appels_db_hors_boucle.py`) suit les appels par nom et par
  module importé ; il est exact sur ce qu'il voit, et il fige un STOCK (`_stock_db_hors_boucle.py`)
  qui ne fait que rétrécir ;
- la **garde d'exécution** (`oto_mcp/db/_hors_boucle.py`, dans `_connect()`) ne voit que ce
  qui s'exécute, mais tout ce qui s'exécute — répartition dynamique comprise.

Cf. `docs/event-loop-perf.md`, « un accès base depuis la boucle ».
"""
from __future__ import annotations

import ast
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from starlette.concurrency import run_in_threadpool

from _appels_db_hors_boucle import RACINE, Balayage
from _stock_db_hors_boucle import STOCK
from oto_mcp.db import _conn, _hors_boucle


# ── 1. Le balayage statique : un cliquet à double sens ───────────────────────────────


def test_le_stock_gele_ne_fait_que_retrecir():
    sites = set(Balayage().sites_bloquants())
    nouveaux = sorted(sites - set(STOCK))
    assert not nouveaux, (
        "un `async def` atteint la base DANS la boucle (mono-loop : il gèle tout le "
        "serveur le temps de la requête) :\n  " + "\n  ".join(nouveaux) +
        "\nDécharge-le — `await run_in_threadpool(...)` autour de l'appel sync, ou une "
        "fonction synchrone qui porte tout le SQL. N'ajoute PAS la ligne au stock : il "
        "ne fait que rétrécir (`docs/event-loop-perf.md`).")
    corriges = sorted(set(STOCK) - sites)
    assert not corriges, (
        "ces sites du stock gelé ne touchent plus la base dans la boucle — retire leur "
        "ligne de `tests/_stock_db_hors_boucle.py`, sinon le stock tolérerait leur "
        "régression :\n  " + "\n  ".join(corriges))


def test_le_balayage_voit_les_chemins_indirects_et_coupe_le_threadpool():
    """Une garde qui ne balaye rien serait verte pour la mauvaise raison — on la prend
    sur trois formes connues, dont les deux qui se ressemblent et divergent."""
    sites = Balayage().sites_bloquants()
    # indirect : l'`async def` n'appelle pas `db` lui-même, un assistant synchrone le fait
    assert "oto_mcp.middleware.alias::ToolAliasMiddleware.on_initialize" in sites
    assert not any(k.startswith("oto_mcp.capabilities.agent_context::") for k in sites), (
        "`me.agent_context` a été déchargé (21/09/2026) : le balayage ne doit plus le voir")


# ── 2. La garde d'exécution ──────────────────────────────────────────────────────────

_SOURCE_FAUSSE = '''
from oto_mcp.db import _conn
from starlette.concurrency import run_in_threadpool

def assistant_sync():
    with _conn._connect() as c:
        return c

async def direct():
    with _conn._connect() as c:
        return c

async def indirect():
    return assistant_sync()

async def decharge():
    return await run_in_threadpool(assistant_sync)

async def ddl():
    with _conn._connect_autocommit() as c:
        return c

async def avale():
    # La forme fail-soft courante (`subdomain_org._resolve_slug`, le journal d'appels) :
    # la levée n'en sort pas.
    try:
        return assistant_sync()
    except Exception:
        return None
'''


@pytest.fixture(autouse=True)
def _releve_isole(monkeypatch):
    """Les bancs de ce module PROVOQUENT des violations exprès : elles vont dans un relevé
    propre au banc, jamais dans celui que `conftest.py` juge en fin de session."""
    monkeypatch.setattr(_hors_boucle, "_violations", [])


@pytest.fixture
def faux_site():
    """Des fonctions dont le fichier d'origine est SOUS `oto_mcp/` : la garde ne juge
    que le code de la maison, pas la coroutine d'un test."""
    ns = {"__name__": "oto_mcp._faux_site"}
    code = compile(_SOURCE_FAUSSE, str(RACINE / "oto_mcp" / "_faux_site.py"), "exec")
    exec(code, ns)                                              # noqa: S102
    return ns


@pytest.fixture
def base_factice(monkeypatch):
    class _Pool:
        @contextmanager
        def connection(self):
            yield object()

    @contextmanager
    def _connexion_ddl(*_a, **_k):
        yield object()

    monkeypatch.setattr(_conn, "_get_pool", lambda: _Pool())
    monkeypatch.setattr(_conn, "_database_url", lambda: "postgresql://factice")
    monkeypatch.setattr(_conn.psycopg, "connect", _connexion_ddl)


@pytest.fixture
def garde_stricte():
    _hors_boucle.configurer(strict=True, tolere=())
    yield
    from _stock_db_hors_boucle import STOCK as stock
    _hors_boucle.configurer(strict=True, tolere=stock)


SITE = "oto_mcp._faux_site::"


@pytest.mark.asyncio
async def test_un_acces_direct_depuis_la_boucle_est_refuse_en_test(faux_site, base_factice, garde_stricte):
    with pytest.raises(_hors_boucle.HorsBoucle) as e:
        await faux_site["direct"]()
    assert SITE + "direct" in str(e.value)


@pytest.mark.asyncio
async def test_un_chemin_indirect_nomme_l_async_def_a_decharger(faux_site, base_factice, garde_stricte):
    """Le cœur : l'`async def` n'appelle pas la base, un assistant synchrone le fait — la
    pile dit quand même QUI décharger."""
    with pytest.raises(_hors_boucle.HorsBoucle) as e:
        await faux_site["indirect"]()
    assert SITE + "indirect" in str(e.value)
    assert "assistant_sync" in str(e.value), "la pile compacte doit montrer l'assistant"


@pytest.mark.asyncio
async def test_le_ddl_a_chaud_est_garde_lui_aussi(faux_site, base_factice, garde_stricte):
    with pytest.raises(_hors_boucle.HorsBoucle):
        await faux_site["ddl"]()


@pytest.mark.asyncio
async def test_un_thread_du_threadpool_ne_declenche_jamais_la_garde(faux_site, base_factice, garde_stricte):
    """Le remède lui-même ne doit pas être refusé : le threadpool n'a pas de boucle."""
    await faux_site["decharge"]()


def test_sans_boucle_aucune_detection(faux_site, base_factice, garde_stricte):
    """Le démarrage (`init_db`, migrations), les timers, les scripts : pas de boucle."""
    faux_site["assistant_sync"]()


@pytest.mark.asyncio
async def test_la_coroutine_d_un_test_n_est_pas_un_site(base_factice, garde_stricte):
    """Un banc qui pilote lui-même un assistant sync n'est pas un chemin de production."""
    with _conn._connect():
        pass


@pytest.mark.asyncio
async def test_un_site_du_stock_est_tolere(faux_site, base_factice):
    _hors_boucle.configurer(strict=True, tolere={SITE + "direct"})
    try:
        await faux_site["direct"]()
        with pytest.raises(_hors_boucle.HorsBoucle):
            await faux_site["indirect"]()
    finally:
        _hors_boucle.configurer(strict=True, tolere=STOCK)


@pytest.mark.asyncio
async def test_en_production_on_avertit_une_fois_par_site_sans_lever(faux_site, base_factice, caplog):
    _hors_boucle.configurer(strict=False)
    try:
        with caplog.at_level(logging.WARNING, logger="oto_mcp.db._hors_boucle"):
            for _ in range(3):
                await faux_site["indirect"]()
            await faux_site["direct"]()
        lignes = [r.getMessage() for r in caplog.records if "db.hors_boucle" in r.getMessage()]
        assert len(lignes) == 2, lignes                    # un par SITE, pas par appel
        assert any(SITE + "indirect" in m and "assistant_sync" in m for m in lignes)
        assert any(SITE + "direct" in m for m in lignes)
    finally:
        _hors_boucle.configurer(strict=True, tolere=STOCK)


@pytest.mark.asyncio
async def test_le_pret_partage_de_reuse_connection_est_garde_aussi(faux_site, base_factice, garde_stricte):
    """Chaque requête d'un `reuse_connection()` s'exécute dans le thread appelant : la
    garde est en tête de `_connect()`, AVANT le raccourci du prêt partagé."""
    with _conn.reuse_connection():
        with pytest.raises(_hors_boucle.HorsBoucle):
            await faux_site["direct"]()


# ── 3. Une garde n'est qu'un instrument : Python 3.10, et jamais de panne ───────────
#
# Le 21/09/2026 (v1.325.0), la garde lisait `code.co_qualname` — Python 3.11+ — alors que la
# prod tourne en 3.10 : `AttributeError` à CHAQUE accès base depuis la boucle, 186 en 3 min,
# et `redact_payload` retombait en passe-through. La CI (3.12) n'avait rien vu. Ces bancs
# ferment les deux portes : l'API minimale, et l'exception qui sort de l'observateur.


def _trame(nom, chemin, suivante=None, *, coroutine=False, module="oto_mcp.x"):
    """Une trame qui n'offre QUE ce que Python 3.10 offre (`co_filename`, `co_name`,
    `co_flags`) : pas de `co_qualname`, `AttributeError` si `_site` le réclame."""
    flags = _hors_boucle._CO_COROUTINE if coroutine else 0
    return SimpleNamespace(
        f_code=SimpleNamespace(co_filename=chemin, co_name=nom, co_flags=flags),
        f_globals={"__name__": module}, f_lineno=1, f_back=suivante)


def test_le_site_ne_demande_que_l_api_de_python_3_10():
    chemin = str(RACINE / "oto_mcp" / "x.py")
    haut = _trame("handler", chemin, coroutine=True)
    bas = _trame("assistant", chemin, haut)
    site, pile = _hors_boucle._site(bas)
    assert site == "oto_mcp.x::handler"
    assert len(pile) == 2


def test_la_garde_n_emploie_aucune_api_posterieure_a_python_3_10():
    """Le plancher du repo est `>=3.10` (pyproject, `syntaxe-plancher`, la box). Garde
    statique, valable sous toute version : elle nomme ce qui casserait en prod."""
    source = (RACINE / "oto_mcp" / "db" / "_hors_boucle.py").read_text()
    interdits = {"co_qualname", "ExceptionGroup", "TaskGroup", "tomllib", "StrEnum",
                 "add_note", "Self"}
    arbre = ast.parse(source)
    vus = set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.Attribute):
            vus.add(n.attr)
        elif isinstance(n, ast.Name):
            vus.add(n.id)
        elif isinstance(n, ast.alias):
            vus.add(n.name.split(".")[0])
        elif n.__class__.__name__ == "TryStar":
            vus.add("except*")
    assert not (vus & (interdits | {"except*"})), (
        f"API de Python 3.11+ dans la garde : {sorted(vus & (interdits | {'except*'}))} — "
        "la prod tourne en 3.10.")


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_un_defaut_interne_de_la_garde_ne_casse_jamais_l_acces_base(
        faux_site, base_factice, caplog, monkeypatch, strict):
    """Test rouge de la reprise : une exception dans le calcul du site ne sort pas — en
    production comme en mode test (où seule la VIOLATION lève, pas un défaut de l'outil)."""
    def _boum(_frame):
        raise AttributeError("'code' object has no attribute 'co_qualname'")

    _hors_boucle.configurer(strict=strict, tolere=())
    monkeypatch.setattr(_hors_boucle, "_site", _boum)
    try:
        with caplog.at_level(logging.ERROR, logger="oto_mcp.db._hors_boucle"):
            for _ in range(3):
                await faux_site["direct"]()            # l'accès base réussit malgré tout
        lignes = [r for r in caplog.records if "db.hors_boucle.defaut" in r.getMessage()]
        assert len(lignes) == 1, "le défaut est journalisé UNE fois par process, pas à chaque accès"
        assert lignes[0].exc_info, "avec sa pile : c'est ce qui permet de le réparer"
    finally:
        _hors_boucle.configurer(strict=True, tolere=STOCK)


@pytest.mark.asyncio
async def test_la_violation_n_est_pas_prise_pour_un_defaut_interne(
        faux_site, base_factice, caplog, garde_stricte):
    with caplog.at_level(logging.ERROR, logger="oto_mcp.db._hors_boucle"):
        with pytest.raises(_hors_boucle.HorsBoucle):
            await faux_site["direct"]()
    assert not [r for r in caplog.records if "db.hors_boucle.defaut" in r.getMessage()]


def test_le_stock_se_ramene_a_la_cle_portable():
    assert _hors_boucle._nom("oto_mcp.middleware.alias::ToolAliasMiddleware.on_initialize") \
        == "oto_mcp.middleware.alias::on_initialize"
    assert _hors_boucle._nom("oto_mcp.tools.meta::register.<locals>.oto_call") \
        == "oto_mcp.tools.meta::oto_call"
    assert _hors_boucle._nom("oto_mcp.call_axes::_pin_group") == "oto_mcp.call_axes::_pin_group"


@pytest.mark.asyncio
async def test_une_violation_avalee_par_un_except_est_notee(faux_site, base_factice, garde_stricte):
    """La levée seule ne suffit pas : un `except Exception` en chemin la transforme en
    `None` et le banc passe. Le relevé, lui, la garde — et fait échouer la session."""
    assert await faux_site["avale"]() is None                  # la levée a bien été avalée
    with pytest.raises(_hors_boucle.HorsBoucle) as e:
        _hors_boucle.exiger_aucune_violation()
    assert SITE + "avale" in str(e.value)
    assert "assistant_sync" in str(e.value), "le relevé doit porter la pile"


@pytest.mark.asyncio
async def test_le_releve_nomme_chaque_site_une_fois_puis_se_vide(faux_site, base_factice, garde_stricte):
    for _ in range(3):
        await faux_site["avale"]()
    with pytest.raises(_hors_boucle.HorsBoucle):
        await faux_site["direct"]()
    with pytest.raises(_hors_boucle.HorsBoucle) as e:
        _hors_boucle.exiger_aucune_violation()
    assert "2 site(s)" in str(e.value)
    assert f"`{SITE}avale` (3×)" in str(e.value)
    _hors_boucle.exiger_aucune_violation()                     # relevé vidé : plus rien à juger


@pytest.mark.asyncio
async def test_un_site_tolere_ou_hors_mode_strict_n_est_pas_note(faux_site, base_factice):
    try:
        _hors_boucle.configurer(strict=True, tolere={SITE + "avale"})
        await faux_site["avale"]()
        _hors_boucle.configurer(strict=False)
        await faux_site["direct"]()
        _hors_boucle.exiger_aucune_violation()
    finally:
        _hors_boucle.configurer(strict=True, tolere=STOCK)
