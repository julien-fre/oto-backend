"""`connector_credentials.secret_enc` NOT NULL (#521) : une ligne du coffre DÉTIENT un
secret, et « détenir une clé » n'a plus qu'une définition — la ligne existe.

La contrainte naît du CREATE TABLE (`db/schema/connectors.py`) ; toute base vivante la
porte (posée par une révision antérieure à la référence du registre,
docs/migrations-versionnees.md §5.4).

Le test de #518 ne pouvait pas attraper la divergence `has_credential` /
`list_credentials` : les deux lectures dérivaient de la même fixture. Ce banc-ci ne
teste pas les lectures, il teste ce que la BASE accepte — le seul endroit où l'écart
se fermait vraiment.
"""
from __future__ import annotations

import pytest

_LIGNE_SANS_CHIFFRE = ("INSERT INTO connector_credentials (entity_type, entity_id, connector) "
                       "VALUES ('org', '1', 'serper')")


def _nullable(dsn: str) -> bool:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'connector_credentials' AND column_name = 'secret_enc'"
        ).fetchone()[0] == "YES"


def test_une_base_neuve_refuse_une_ligne_sans_chiffre(live, pg_module_dsn):
    import psycopg
    assert not _nullable(pg_module_dsn), "le CREATE TABLE doit poser le NOT NULL"
    with psycopg.connect(pg_module_dsn) as c, pytest.raises(psycopg.errors.NotNullViolation):
        c.execute(_LIGNE_SANS_CHIFFRE)
