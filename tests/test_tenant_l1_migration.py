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

> **Première lecture admise, et bornée (suivi des tenants).** Le garde-fou refusait
> TOUTE lecture de `orgs.tenant_id` et demandait, le jour venu, qu'on le retire —
> le retirer rendrait alors muet ce qu'il protège vraiment : qu'aucun chemin de
> **résolution** (identité, credential, visibilité, autz) ne se mette à dépendre du
> rattachement d'org. L'écran de suivi plateforme lit la colonne pour la COMPTER, et
> ne décide de rien avec. Le garde-fou passe donc d'une interdiction totale à une
> **allowlist nommée** (patron `test_org_seam_tripwire.py`) : les deux fichiers du
> suivi, et eux seuls. Un lecteur ailleurs casse toujours — c'est le cas qu'on veut
> voir en revue.
"""
from __future__ import annotations

import pathlib

from oto_mcp.db import _schema

_DB = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp" / "db"
# Le DDL n'est plus un fichier mais un ASSEMBLAGE (`db/schema/<domaine>.py`
# concaténés dans un ordre figé) : on lit la chaîne SERVIE, seule chose dont les
# ordres et les formes ci-dessous soient des propriétés.
_SCHEMA_SRC = _schema._SCHEMA
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


# Les SEULS lecteurs admis de `orgs.tenant_id` : le suivi (il COMPTE le
# rattachement et n'en dérive aucune décision). Tout ajout à cette liste est un
# changement de nature — la colonne cesserait d'être un nom pour devenir une entrée
# de résolution — et doit être argumenté en revue, pas glissé dans un diff.
_LECTEURS_ADMIS = {
    "db/tenants.py",              # les compteurs de suivi (lecture seule)
    "capabilities/tenants_admin.py",  # la capacité qui les sert (PLATFORM_ADMIN)
    # L'audience d'une relance de plateforme (2026-09-02). Entrée DÉLIBÉRÉE, et deux
    # précisions qui la bornent :
    #  - ce module ne lit pas `orgs.tenant_id` lui-même — il réutilise
    #    `tenants._ORG_TENANT_EXPR`, déjà admise ci-dessus ; ce que le grep attrape est
    #    le join sur le tenant du SUB (`tenants.id`), plus la prose qui l'explique ;
    #  - le rattachement n'y sert qu'à REFUSER (écarter les comptes d'un partenaire
    #    d'un envoi), jamais à accorder quoi que ce soit. Aucune identité, aucun
    #    credential, aucune visibilité n'en dépend — ce que ce garde-fou protège.
    "db/outreach.py",
}


def test_only_the_tracking_read_touches_tenant_id():
    """L1 nomme l'existant, il ne le déplace pas.

    Ce que le garde-fou protège n'est pas « personne ne lit la colonne » mais
    « aucune RÉSOLUTION n'en dépend » : identité, credential, visibilité, autz
    continuent d'ignorer le rattachement d'org. Un lecteur hors allowlist tombe.
    """
    root = _DB.parent
    readers = []
    for path in root.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for line in src.splitlines():
            if "tenant_id" not in line:
                continue
            # Ni la DDL (`_schema` et les fragments de `db/schema/`), ni la
            # migration (`_init`), ni un commentaire — Python ou SQL — ne sont des
            # LECTEURS de la colonne.
            if path.name in ("_init.py", "_schema.py"):
                continue
            if path.parent.name == "schema" and path.parent.parent.name == "db":
                continue
            if line.lstrip().startswith(("#", "--")):
                continue
            rel = path.relative_to(root).as_posix()
            if rel in _LECTEURS_ADMIS:
                continue
            readers.append(f"{path.relative_to(root.parent)}: {line.strip()}")
    assert not readers, (
        "Quelqu'un lit `orgs.tenant_id` hors du suivi, ce qui déborde du lot L1 "
        f"(« l'existant est nommé, pas déplacé ») : {readers}. Un chemin de "
        "résolution qui dépend du rattachement d'org est un LOT, avec sa revue : "
        f"l'ajouter à _LECTEURS_ADMIS doit être un acte délibéré.")
