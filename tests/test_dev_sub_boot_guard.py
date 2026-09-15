"""Constat basse sévérité n°1 de la revue de sécurité du 2026-08-29 (oto-backend#572) :
`OTO_MCP_DEV_SUB` (repli d'identité pour un run local sans authentification réelle,
`auth/hooks.py`) est opt-in, documenté, et sans effet tant qu'il n'est pas posé — mais
rien n'empêchait MÉCANIQUEMENT qu'il soit posé sur un environnement servi sous un vrai
émetteur Logto (`LOGTO_ENDPOINT`). `config.verifier_repli_identite_dev` refuse de
démarrer sur ce croisement ; ce test prouve le refus, que chaque variable SEULE reste
inerte, et que `server.main()` atteint la garde AVANT tout autre sous-système."""
from __future__ import annotations

import pytest

from oto_mcp import config, server


@pytest.fixture(autouse=True)
def _env_propre(monkeypatch):
    monkeypatch.delenv("OTO_MCP_DEV_SUB", raising=False)
    monkeypatch.delenv("LOGTO_ENDPOINT", raising=False)


def test_aucune_des_deux_ne_leve(monkeypatch):
    config.verifier_repli_identite_dev()  # ne lève pas


def test_dev_sub_seul_ne_leve_pas(monkeypatch):
    monkeypatch.setenv("OTO_MCP_DEV_SUB", "sub-dev-de-secours")
    config.verifier_repli_identite_dev()  # ne lève pas


def test_emetteur_reel_seul_ne_leve_pas(monkeypatch):
    monkeypatch.setenv("LOGTO_ENDPOINT", "https://auth.oto.ninja")
    config.verifier_repli_identite_dev()  # ne lève pas


def test_croisement_refuse_de_demarrer(monkeypatch):
    monkeypatch.setenv("OTO_MCP_DEV_SUB", "sub-dev-de-secours")
    monkeypatch.setenv("LOGTO_ENDPOINT", "https://auth.oto.ninja")
    with pytest.raises(config.ReplIdentiteDevDangereux):
        config.verifier_repli_identite_dev()


def test_server_main_refuse_avant_toute_autre_preparation(monkeypatch):
    """`main()` atteint le refus AVANT `_prepare_database` (et donc avant Sentry, les
    boucles de fond, uvicorn) : rien de ce qui suit la garde ne doit tourner."""
    monkeypatch.setenv("OTO_MCP_DEV_SUB", "sub-dev-de-secours")
    monkeypatch.setenv("LOGTO_ENDPOINT", "https://auth.oto.ninja")

    def _ne_doit_pas_tourner():
        raise AssertionError(
            "_prepare_database a tourné : le refus doit précéder toute préparation")

    monkeypatch.setattr(server, "_prepare_database", _ne_doit_pas_tourner)
    with pytest.raises(config.ReplIdentiteDevDangereux):
        server.main()
