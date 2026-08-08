"""Lot L1 (ADR 0052) — le tenant est NOMMÉ en base, rien ne le lit encore.

Deux modes de panne, et tous deux sont des **ordres** — donc vérifiables sans base,
là où un test SQL exigerait un PostgreSQL et ne dirait rien de plus :

1. `tenants` créée APRÈS `orgs` dans `_SCHEMA` → sur une base VIERGE, la FK
   `orgs.tenant_id → tenants(id)` échoue (`relation "tenants" does not exist`).
   C'est exactement le #151 déjà vécu sur `orgs`, dont le commentaire de `_schema`
   garde la trace.
2. La colonne ajoutée AVANT le seed du tenant 1 → `ALTER TABLE orgs … NOT NULL
   DEFAULT 1 REFERENCES tenants(id)` viole la FK dès qu'il existe une seule org.
   Sur une base vide le boot passerait, et casserait chez le premier qui a des
   données : le pire moment pour l'apprendre.

Le troisième test garde l'intention du lot : L1 **nomme** l'existant, il ne le
déplace pas. Le jour où un call-site lit `tenant_id`, ce n'est plus L1 — c'est un
autre lot, avec sa propre revue.
"""
from __future__ import annotations

import pathlib

_DB = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp" / "db"
_SCHEMA_SRC = (_DB / "_schema.py").read_text(encoding="utf-8")
_INIT_SRC = (_DB / "_init.py").read_text(encoding="utf-8")


def test_tenants_table_is_created_before_orgs():
    tenants = _SCHEMA_SRC.index("CREATE TABLE IF NOT EXISTS tenants")
    orgs = _SCHEMA_SRC.index("CREATE TABLE IF NOT EXISTS orgs")
    assert tenants < orgs, (
        "`tenants` doit être déclarée AVANT `orgs` dans _SCHEMA : PostgreSQL crée "
        "les tables dans l'ordre du DDL, et la FK `orgs.tenant_id` échouerait sur "
        "une base vierge (même panne que #151 sur `orgs`).")


def test_tenant_one_is_seeded_before_the_column_references_it():
    seed = _INIT_SRC.index("INSERT INTO tenants")
    setval = _INIT_SRC.index("pg_get_serial_sequence('tenants','id')")
    alter = _INIT_SRC.index("ALTER TABLE orgs ADD COLUMN IF NOT EXISTS tenant_id")
    assert seed < alter, (
        "le tenant 1 doit être semé AVANT l'ajout de `orgs.tenant_id` : la colonne "
        "naît `NOT NULL DEFAULT 1 REFERENCES tenants(id)`, donc la FK est violée "
        "par la première org existante si le tenant n'est pas là.")
    assert seed < setval < alter, (
        "le recalage de séquence doit suivre le seed : un INSERT à id explicite ne "
        "fait pas avancer la BIGSERIAL, et le prochain tenant naîtrait sur l'id 1.")


def test_nothing_reads_tenant_id_yet():
    """L1 nomme l'existant, il ne le déplace pas. Ce garde-fou n'interdit rien pour
    toujours — il force à ce que le premier LECTEUR de la colonne soit un acte
    délibéré (retirer ce test), pas un effet de bord."""
    root = _DB.parent
    readers = []
    for path in root.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for line in src.splitlines():
            if "tenant_id" not in line:
                continue
            # Ni la DDL (`_schema`), ni la migration (`_init`), ni un commentaire
            # — Python ou SQL — ne sont des LECTEURS de la colonne.
            if path.name in ("_init.py", "_schema.py"):
                continue
            if line.lstrip().startswith(("#", "--")):
                continue
            readers.append(f"{path.relative_to(root.parent)}: {line.strip()}")
    assert not readers, (
        "Quelqu'un lit `orgs.tenant_id`, ce qui déborde du lot L1 (« l'existant est "
        f"nommé, pas déplacé ») : {readers}. Si c'est voulu, retirer ce test dans le "
        "même commit — pour que la bascule soit visible en revue.")
