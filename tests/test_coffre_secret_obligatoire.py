"""`connector_credentials.secret_enc` NOT NULL (#521) : une ligne du coffre DÉTIENT un
secret, et « détenir une clé » n'a plus qu'une définition — la ligne existe.

```
base NEUVE       la contrainte naît du CREATE TABLE (`db/schema/connectors.py`)
base EXISTANTE   le CREATE TABLE est sauté ; elle vient de la révision
                 `0009_coffre_secret_obligatoire`, jouée À LA MAIN (ADR 0065)
```

Le test de #518 ne pouvait pas attraper la divergence `has_credential` /
`list_credentials` : les deux lectures dérivaient de la même fixture. Ce banc-ci ne
teste pas les lectures, il teste ce que la BASE accepte — le seul endroit où l'écart
se fermait vraiment.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
_LIGNE_SANS_CHIFFRE = ("INSERT INTO connector_credentials (entity_type, entity_id, connector) "
                       "VALUES ('org', '1', 'serper')")


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


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


@pytest.fixture
def base_d_avant(live, pg_module_dsn):
    """La base ramenée à l'état de la production avant ce lot : colonne nullable."""
    import psycopg
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("ALTER TABLE connector_credentials ALTER COLUMN secret_enc DROP NOT NULL")
    assert _nullable(pg_module_dsn)
    return pg_module_dsn


def test_le_boot_ne_pose_pas_la_contrainte(base_d_avant):
    """Hors du démarrage : un NOT NULL sur une base servie est un ACTE (ADR 0065)."""
    from oto_mcp.db import init_db
    init_db()
    assert _nullable(base_d_avant)


def test_la_revision_pose_la_contrainte_et_se_defait(base_d_avant):
    from alembic import command
    cfg = _alembic()
    command.upgrade(cfg, "0009_coffre_secret_obligatoire")
    assert not _nullable(base_d_avant), "la révision n'a rien écrit"
    command.downgrade(cfg, "0008_billing_contracts")
    assert _nullable(base_d_avant), "le retour arrière n'a rien retiré"
    command.upgrade(cfg, "head")
    assert not _nullable(base_d_avant)


def test_la_revision_echoue_sur_une_ligne_sans_chiffre(base_d_avant):
    """Une ligne nulle surgie entre la vérification et la migration : la révision
    ÉCHOUE, sans rien écrire — jamais une contrainte posée à moitié, jamais un
    nettoyage silencieux de la ligne fautive."""
    import psycopg
    from alembic import command
    cfg = _alembic()
    command.downgrade(cfg, "0008_billing_contracts")
    with psycopg.connect(base_d_avant, autocommit=True) as c:
        c.execute(_LIGNE_SANS_CHIFFRE)
    try:
        with pytest.raises(Exception, match="null"):
            command.upgrade(cfg, "0009_coffre_secret_obligatoire")
        assert _nullable(base_d_avant)
    finally:
        with psycopg.connect(base_d_avant, autocommit=True) as c:
            c.execute("DELETE FROM connector_credentials WHERE secret_enc IS NULL")
