"""L'environnement se DÉCLARE, le domaine ne vote plus.

Une instance sert le cœur ailleurs que chez nous : sur son domaine, elle serait classée
« préproduction » en silence par la seule comparaison d'un hôte à une chaîne écrite dans
le code. Conséquences mesurées : aucun prélèvement, aucune boucle sortante, tout paiement
refusé — et rien n'échoue, ce qui est le pire état.

Ce banc décrit ce qui doit devenir vrai : `OTO_ENV` déclare l'environnement, et plus
aucun nom d'hôte n'entre dans la réponse. Il couvre les DEUX comparaisons dures, parce
que les livrer séparément donnerait deux vérités sur la même question :
`origine_du_process()` et `project_domain_is_production()`.
"""
from __future__ import annotations

import pytest

from oto_mcp import config


@pytest.fixture(autouse=True)
def _table_rase(monkeypatch):
    for v in ("OTO_ENV", "OTO_MCP_PUBLIC_URL", "OTO_SENTRY_ENV", "OTO_PROJECT_DOMAIN"):
        monkeypatch.delenv(v, raising=False)


def test_une_instance_sur_un_domaine_tiers_se_sait_en_production(monkeypatch):
    monkeypatch.setenv("OTO_ENV", "prod")
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.partenaire.example")
    assert config.origine_du_process() == config.PROD
    assert config.est_la_production() is True


def test_notre_domaine_ne_suffit_plus_a_declarer_la_production(monkeypatch):
    monkeypatch.setenv("OTO_ENV", "preprod")
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.oto.cx")
    assert config.origine_du_process() == config.PREPROD
    assert config.est_la_production() is False


def test_l_url_publique_n_entre_plus_dans_la_reponse(monkeypatch):
    monkeypatch.setenv("OTO_ENV", "prod")
    assert config.origine_du_process() == config.PROD


def test_une_valeur_inconnue_est_un_refus_pas_un_silence(monkeypatch):
    monkeypatch.setenv("OTO_ENV", "staging")
    with pytest.raises(config.EnvironnementAmbigu):
        config.origine_du_process()


def test_l_avertissement_de_publication_suit_la_declaration(monkeypatch):
    """La MÊME question ne se pose pas deux fois : `project_domain_is_production` lisait
    le domaine de son côté, ce qui étiquetait « test » les URL d'une instance tierce."""
    monkeypatch.setenv("OTO_ENV", "prod")
    monkeypatch.setenv("OTO_PROJECT_DOMAIN", "partenaire.example")
    assert config.project_domain_is_production() is True


def test_absente_elle_ne_repond_pas_et_ne_laisse_rien_engager():
    """Le poste de développement et les tests : aucune déclaration, donc aucune réponse.
    `origine_du_process` rend None — une écriture le note « origine inconnue » plutôt que
    de l'attribuer à quelqu'un — et tout ce qui engage un tiers refuse de démarrer."""
    assert config.origine_du_process() is None
    assert config.project_domain_is_production() is False
    with pytest.raises(config.EnvironnementAmbigu):
        config.est_la_production()


def test_deux_declarations_qui_se_contredisent_levent(monkeypatch):
    """Garde-fou existant : il doit survivre au changement de source."""
    monkeypatch.setenv("OTO_ENV", "preprod")
    monkeypatch.setenv("OTO_SENTRY_ENV", "production")
    with pytest.raises(config.EnvironnementAmbigu):
        config.est_la_production()
