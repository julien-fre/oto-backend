"""Un schéma qui échoue au démarrage doit ARRÊTER le boot — les rattrapages, non.

La distinction est tout le test. `_prepare_database` est fail-open **par décision** :
un backfill de données qui casse ne doit pas empêcher le serveur de répondre, et ça
reste vrai. Mais `init_db` n'est pas un rattrapage : c'est la construction du schéma.
S'il échoue, le process sert des erreurs sur chaque appel, en ayant démarré vert.

Aujourd'hui les deux sont traités pareil — un `except Exception` et un avertissement.
Le premier test échoue donc, et c'est voulu : il décrit ce qui doit devenir vrai.

Pourquoi maintenant : l'ADR 0070 pose qu'une instance déclare ce qu'elle est et qu'un
défaut doit devenir un refus de démarrer. Un refus ne se prouve pas sur un boot qui
avale ses propres échecs — une base neuve chez un partenaire démarrerait verte avec un
schéma incomplet, et c'est exactement le cas que la préprod partagée masquait jusqu'ici.
"""
from __future__ import annotations

import pytest

from oto_mcp import server


class _SchemaCasse(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _boot_rejouable(monkeypatch):
    """`_prepare_database` ne s'exécute qu'une fois par process : on rearme le témoin."""
    monkeypatch.setattr(server, "_PREPARED", False, raising=False)


def test_un_schema_qui_echoue_arrete_le_boot(monkeypatch):
    monkeypatch.setattr(server.db, "init_db", lambda: (_ for _ in ()).throw(_SchemaCasse("pas de schéma")))
    with pytest.raises(_SchemaCasse):
        server._prepare_database()


def test_un_rattrapage_qui_echoue_laisse_le_serveur_repondre(monkeypatch):
    """Le fail-open des backfills est VOULU — ce test le garde."""
    monkeypatch.setattr(server.db, "init_db", lambda: None)
    from oto_mcp import org_store
    monkeypatch.setattr(org_store, "backfill_personal_orgs",
                        lambda: (_ for _ in ()).throw(RuntimeError("rattrapage cassé")))
    server._prepare_database()  # ne lève pas
