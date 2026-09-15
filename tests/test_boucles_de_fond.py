"""Seule la production agit sur un tiers — la garde de `boucles_de_fond`.

Prod et préprod partagent la base : une boucle de fond qui prélève ou envoie des emails,
lancée en préprod, agit pour la production avec le code et les clés d'un autre
environnement (relevé le 10/09/2026 : prélèvement composé en préprod, clé Mollie de
TEST). Ces épreuves tiennent la règle par l'axe : la composition, l'environnement, la
déclaration, et le fait que `server.main` ne démarre rien qui ne passe par elle."""
import inspect
import re

import pytest

from oto_mcp import boucles_de_fond, config

_INTERRUPTEURS = ("OTO_SCHEDULER_ENABLED", "OTO_EMBED_WORKER_ENABLED",
                  "OTO_FILE_EXTRACT_WORKER_ENABLED", "OTO_RANK_BACKFILL_ENABLED",
                  "OTO_RUNNER_TICK_ENABLED", "OTO_BILLING_RUNNER_ENABLED",
                  "OTO_HANG_WATCH_ENABLED")
_PROD = "https://mcp.oto.cx"
_PREPROD = "https://mcp.oto.ninja"


@pytest.fixture
def env(monkeypatch):
    """Toutes les boucles armées, facturation comprise ; aucun environnement déclaré."""
    for var in _INTERRUPTEURS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OTO_BILLING_ENABLED", "1")
    monkeypatch.delenv("OTO_MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("OTO_SENTRY_ENV", raising=False)
    return monkeypatch


def _fonction(nom):
    return next(b for b in boucles_de_fond.BOUCLES if b.nom == nom).fonction()


def _toutes(tiers=None):
    return {b.fonction() for b in boucles_de_fond.BOUCLES
            if tiers is None or b.tiers is tiers}


def test_la_preprod_ne_compose_aucune_boucle_tierce(env):
    # La configuration relevée sur la préprod servie le 10/09/2026.
    env.setenv("OTO_MCP_PUBLIC_URL", _PREPROD)
    env.setenv("OTO_SENTRY_ENV", "canari")
    composees = boucles_de_fond.composer()
    from oto_mcp import billing_runner, scheduler
    assert billing_runner.run_billing_loop not in composees
    assert scheduler.run_scheduler_loop not in composees
    # Le reste tourne : la garde écarte ce qui touche un tiers, rien d'autre.
    assert set(composees) == _toutes(tiers=False)


def test_la_production_compose_les_sept(env):
    env.setenv("OTO_MCP_PUBLIC_URL", _PROD)
    env.setenv("OTO_SENTRY_ENV", "production")
    composees = boucles_de_fond.composer()
    assert len(composees) == len(boucles_de_fond.BOUCLES) == 7
    assert set(composees) == _toutes()


@pytest.mark.parametrize("url", [None, "", "mcp.oto.cx"],
                         ids=["absente", "vide", "sans-schema-donc-sans-hote"])
def test_un_environnement_indeterminable_refuse_de_demarrer(env, url):
    if url is not None:
        env.setenv("OTO_MCP_PUBLIC_URL", url)
    with pytest.raises(config.EnvironnementAmbigu):
        boucles_de_fond.composer()


@pytest.mark.parametrize("url, sentry", [(_PROD, "canari"), (_PREPROD, "production"),
                                         (_PREPROD, "PRODUCTION")])
def test_deux_declarations_qui_se_contredisent_refusent(env, url, sentry):
    env.setenv("OTO_MCP_PUBLIC_URL", url)
    env.setenv("OTO_SENTRY_ENV", sentry)
    with pytest.raises(config.EnvironnementAmbigu):
        boucles_de_fond.composer()


def test_sentry_absent_ne_vote_pas(env):
    # Son défaut côté Sentry (`production`) n'est pas une déclaration.
    env.setenv("OTO_MCP_PUBLIC_URL", _PREPROD)
    assert config.est_la_production() is False
    env.setenv("OTO_MCP_PUBLIC_URL", _PROD)
    assert config.est_la_production() is True


def test_sans_boucle_tierce_armee_la_question_nest_pas_posee(env):
    env.setenv("OTO_SCHEDULER_ENABLED", "0")
    env.setenv("OTO_BILLING_ENABLED", "0")
    assert set(boucles_de_fond.composer()) == _toutes(tiers=False)


def test_un_interrupteur_eteint_partout_et_n_allume_jamais_hors_production(env):
    env.setenv("OTO_MCP_PUBLIC_URL", _PROD)
    env.setenv("OTO_SCHEDULER_ENABLED", "0")
    assert _fonction("scheduler") not in boucles_de_fond.composer()
    env.setenv("OTO_MCP_PUBLIC_URL", _PREPROD)
    env.setenv("OTO_SCHEDULER_ENABLED", "1")
    env.setenv("OTO_BILLING_RUNNER_ENABLED", "1")
    composees = boucles_de_fond.composer()
    assert _fonction("scheduler") not in composees
    assert _fonction("billing_runner") not in composees


def test_la_declaration_tiers_est_obligatoire_et_booleenne():
    kw = dict(nom="x", armee=lambda: True, fonction=lambda: None)
    with pytest.raises(TypeError):
        boucles_de_fond.Boucle(**kw)
    for valeur in (None, "non", 0):
        with pytest.raises(TypeError):
            boucles_de_fond.Boucle(tiers=valeur, **kw)


def test_les_boucles_qui_touchent_un_tiers_le_declarent():
    noms = [b.nom for b in boucles_de_fond.BOUCLES]
    assert len(noms) == len(set(noms))
    # Faits de code, pas de goût : l'une envoie des emails, l'autre prélève.
    assert {"scheduler", "billing_runner"} <= {
        b.nom for b in boucles_de_fond.BOUCLES if b.tiers}


def test_server_main_ne_compose_rien_hors_du_module():
    from oto_mcp import server
    src = inspect.getsource(server.main)
    assert src.count("boucles_de_fond.composer(") == 1
    assert "_bg_loops.append" not in src
    assert re.findall(r"\brun_\w+_loop\b", src) == []


def test_la_composition_precede_toute_ecriture_en_base():
    # Un démarrage qui refuse APRÈS `_prepare_database` a déjà joué init_db et les
    # backfills sur la base partagée.
    from oto_mcp import server
    src = inspect.getsource(server.main)
    preparation = re.search(r"^\s*_prepare_database\(\)\s*$", src, re.M)
    assert preparation is not None
    assert src.index("boucles_de_fond.composer(") < preparation.start()
