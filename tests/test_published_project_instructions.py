"""Ce qu'un endpoint de projet publié sert à son destinataire (feedback #308/#309).

Deux garde-fous : le tiers ne reçoit JAMAIS le socle plateforme (vocabulaire interne,
outils qu'il n'a pas), et publier depuis un environnement de test le DIT au lieu de
laisser distribuer une URL dont le certificat sera refusé.
"""
import pytest

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


def test_c_est_la_DECLARATION_qui_rend_une_URL_distribuable(monkeypatch):
    """Et plus le domaine. Une instance servie ailleurs que chez nous publie des URL
    parfaitement distribuables : son certificat est réel. L'ancienne règle comparait
    `project_domain()` à `oto.cx` et étiquetait donc « environnement de test » tout ce
    qu'un tiers publie sur son propre domaine."""
    monkeypatch.setenv("OTO_ENV", config.PROD)
    assert config.project_domain_is_production() is True
    monkeypatch.setenv("OTO_PROJECT_DOMAIN", "partenaire.example")
    assert config.project_domain_is_production() is True, "un autre domaine reste servi en prod"

    monkeypatch.setenv("OTO_ENV", config.PREPROD)
    assert config.project_domain_is_production() is False

    monkeypatch.delenv("OTO_ENV", raising=False)
    assert config.project_domain_is_production() is False, "sans déclaration, on avertit"


def test_le_domaine_des_projets_est_obligatoire(monkeypatch):
    """Décision du 15/09/2026 : plus de défaut muet vers `oto.cx` — chaque instance
    déclare son domaine, prod comprise (même raison que `project_domain_is_production`
    juste au-dessus : un défaut qui pointe chez nous engage une instance tierce sans
    le dire)."""
    monkeypatch.delenv("OTO_PROJECT_DOMAIN", raising=False)
    with pytest.raises(RuntimeError):
        config.project_domain()
    monkeypatch.setenv("OTO_PROJECT_DOMAIN", "oto.cx")
    assert config.project_domain() == "oto.cx"
    monkeypatch.setenv("OTO_PROJECT_DOMAIN", "oto.ninja")
    assert config.project_domain() == "oto.ninja"
