"""Ce qu'un endpoint de projet publié sert à son destinataire (feedback #308/#309).

Deux garde-fous : le tiers ne reçoit JAMAIS le socle plateforme (vocabulaire interne,
outils qu'il n'a pas), et publier depuis un environnement de test le DIT au lieu de
laisser distribuer une URL dont le certificat sera refusé.
"""
import importlib

from oto_mcp import config, instructions


def test_published_project_serves_its_own_prose(monkeypatch):
    import oto_mcp.db as db
    monkeypatch.setattr(db, "get_project_by_id", lambda pid: {
        "id": pid, "name": "Marché — accords dormants",
        "mcp_instructions_md": "Ce vivier liste les entreprises dont l'accord dort.",
    })
    out = instructions.compose_published_project(169)
    assert "Marché" in out
    assert "accord dort" in out
    # Le socle plateforme n'a rien à faire chez un tiers.
    assert "run_start" not in out and "connecteur" not in out


def test_published_project_without_prose_stays_minimal(monkeypatch):
    import oto_mcp.db as db
    monkeypatch.setattr(db, "get_project_by_id", lambda pid: {
        "id": pid, "name": "Vivier", "mcp_instructions_md": None})
    out = instructions.compose_published_project(169)
    assert "Vivier" in out
    assert len(out) < 500          # un minimum, pas les ~12 Ko du socle
    assert "run_start" not in out


def test_unknown_project_falls_open(monkeypatch):
    import oto_mcp.db as db
    monkeypatch.setattr(db, "get_project_by_id", lambda pid: None)
    assert instructions.compose_published_project(999999) is None


def test_production_domain_is_the_only_distributable_one(monkeypatch):
    monkeypatch.delenv("OTO_PROJECT_DOMAIN", raising=False)
    importlib.reload(config)
    assert config.project_domain() == "oto.cx"
    assert config.project_domain_is_production() is True

    monkeypatch.setenv("OTO_PROJECT_DOMAIN", "oto.ninja")
    importlib.reload(config)
    assert config.project_domain_is_production() is False
    monkeypatch.delenv("OTO_PROJECT_DOMAIN", raising=False)
    importlib.reload(config)
