"""La portée personne des droits déclarés sur une base EXISTANTE : révisions 0014 et 0015
(#1066), jouées pour de vrai.

```
base d'avant      table de la 0004 : PK (org_id, right_key, source), value nullable,
                  des lignes à value NULL posées par l'ancienne réconciliation
0014 (avant la fusion)   + sub, NULL → 1, UNIQUE NULLS NOT DISTINCT — l'ancien code
                         (ON CONFLICT sur la PK, value NULL) passe ENCORE
0015 (après le tag)      NULL → 1, value NOT NULL, PK retirée
```

Le banc part d'une base bootée (forme cible), la RAMÈNE à la forme d'avant, au registre
de la 0013, et joue les révisions — l'essai à blanc a déjà menti une fois
(`docs/migrations-versionnees.md` §5). Puis : le démarrage après les révisions est un
no-op, et le retour arrière se défait dans l'ordre.
"""
from __future__ import annotations

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent

# La table telle que la 0004 la pose — une ligne vivante d'avant, à `value` NULL.
_AVANT = """
CREATE TABLE org_entitlements (
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    right_key TEXT NOT NULL,
    value INTEGER,
    source TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    granted_by TEXT,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, right_key, source)
)"""
# L'écriture de l'ANCIEN code (`db/entitlements.grant` d'avant #1066).
_ANCIEN_GRANT = (
    "INSERT INTO org_entitlements (org_id, right_key, source, value) "
    "VALUES (%s, 'unipile', %s, NULL) ON CONFLICT (org_id, right_key, source) "
    "DO UPDATE SET value = EXCLUDED.value")


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _forme(dsn: str) -> dict:
    import psycopg
    with psycopg.connect(dsn) as c:
        cols = {r[0]: r[1] for r in c.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'org_entitlements'").fetchall()}
        contraintes = {r[0] for r in c.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'org_entitlements'::regclass AND contype IN ('p', 'u')"
        ).fetchall()}
        nulles = c.execute("SELECT count(*) FROM org_entitlements WHERE value IS NULL"
                           ).fetchone()[0]
    return {"sub": "sub" in cols, "value_nullable": cols.get("value") == "YES",
            "pk": "org_entitlements_pkey" in contraintes,
            "une_ligne": "org_entitlements_une_ligne" in contraintes, "nulles": nulles}


CIBLE = {"sub": True, "value_nullable": False, "pk": False, "une_ligne": True, "nulles": 0}


@pytest.fixture(scope="module")
def base_d_avant(live, pg_module_dsn):
    import psycopg
    from alembic import command
    assert _forme(pg_module_dsn) == CIBLE, "une base NEUVE a la forme cible"
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("DROP TABLE org_entitlements")
        c.execute(_AVANT)
        org = c.execute("INSERT INTO orgs (name) VALUES ('org-avant') RETURNING id"
                        ).fetchone()[0]
        c.execute(_ANCIEN_GRANT, (org, "subscription"))
    command.stamp(_alembic(), "0013_pages_versions_regroupees")
    assert _forme(pg_module_dsn) == {"sub": False, "value_nullable": True, "pk": True,
                                     "une_ligne": False, "nulles": 1}
    return pg_module_dsn, org


def test_0014_ajoute_sans_casser_l_ancien_code_puis_0015_retire(base_d_avant):
    import psycopg
    from alembic import command
    dsn, org = base_d_avant
    cfg = _alembic()

    command.upgrade(cfg, "0014_droits_portee_personne")
    assert _forme(dsn) == {"sub": True, "value_nullable": True, "pk": True,
                           "une_ligne": True, "nulles": 0}
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(_ANCIEN_GRANT, (org, "subscription"))   # rejoué : ne lève pas
        c.execute(_ANCIEN_GRANT, (org, "offered"))        # neuf, NULL : accepté
    assert _forme(dsn)["nulles"] == 2, "l'ancien code écrit encore après la 0014"

    command.upgrade(cfg, "0015_droits_valeur_obligatoire")
    assert _forme(dsn) == CIBLE

    from oto_mcp.access.entitlements import value_for
    from oto_mcp.db import entitlements as E
    E.grant(org, "unipile", "subscription", value=1, sub="u-personne")
    assert value_for("u-personne", org, "unipile") == 1
    assert value_for(None, org, "unipile") == 1, "les lignes d'avant valent « oui »"

    # Le retour arrière de la 0015 repose la PK : il échoue DE LUI-MÊME tant qu'une ligne
    # de personne double une ligne d'org (en-tête de la révision).
    with pytest.raises(Exception, match="unique|dupliqu|duplicate"):
        command.downgrade(cfg, "0014_droits_portee_personne")
    E.revoke(org, "unipile", "subscription", sub="u-personne")
    command.downgrade(cfg, "0014_droits_portee_personne")
    assert _forme(dsn)["pk"] and _forme(dsn)["value_nullable"]
    command.downgrade(cfg, "0013_pages_versions_regroupees")
    assert _forme(dsn) == {"sub": False, "value_nullable": True, "pk": True,
                           "une_ligne": False, "nulles": 0}
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT count(*) FROM org_entitlements").fetchone()[0] == 2, (
            "les lignes d'org restent")

    command.upgrade(cfg, "head")
    assert _forme(dsn) == CIBLE


def test_le_demarrage_apres_les_revisions_est_un_no_op(base_d_avant):
    from alembic import command
    from oto_mcp.db import init_db
    dsn, _ = base_d_avant
    command.upgrade(_alembic(), "head")
    init_db()
    assert _forme(dsn) == CIBLE
