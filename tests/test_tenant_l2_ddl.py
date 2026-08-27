"""Lot L2 (ADR 0052) — la table `tenants` porte l'émetteur du tenant.

Le lot livre un registre `issuer → (tenant, verifier)` ; sa source de vérité pour
les tenants TIERS est la base. Trois modes de panne, tous vérifiables sans
PostgreSQL parce que ce sont des propriétés du DDL, pas du moteur :

1. Colonnes déclarées dans le `CREATE TABLE` mais SANS `ALTER … IF NOT EXISTS`
   correspondant : `CREATE TABLE IF NOT EXISTS` ne propage rien sur une table
   déjà créée (L1 a créé `tenants`) → une base neuve aurait les colonnes, la
   prod jamais. Le registre y lirait une colonne inexistante à chaque boot.
2. `issuer` sans UNIQUE : deux tenants sur le même émetteur ⟹ la sélection par
   `iss` devient ambiguë, et cette ambiguïté décide de QUI est l'appelant. Le
   registre a sa propre garde côté Python ; l'unicité en base est la seconde.
3. `hosts` nullable : L3 lira cette colonne pour le binding host → tenant. Une
   liste NULL et une liste vide n'y voudront pas dire la même chose si personne
   ne l'a interdit au départ.

⚠️ prod et preprod partagent la MÊME base : ces trois ordres doivent rester
purement additifs (colonnes nullables ou defaultées, aucun backfill).
"""
from __future__ import annotations

import pathlib
import re

from oto_mcp.db import _schema

_DB = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp" / "db"
# Le DDL n'est plus un fichier mais un ASSEMBLAGE (`db/schema/<domaine>.py`
# concaténés dans un ordre figé) : on lit la chaîne SERVIE, seule chose dont les
# ordres et les formes ci-dessous soient des propriétés.
_SCHEMA_SRC = _schema._SCHEMA
_INIT_SRC = (_DB / "_init.py").read_text(encoding="utf-8")
# Les ordres SQL de `_init` sont écrits en littéraux Python adjacents ("…" "…") pour
# tenir la largeur : on les recolle une fois, sinon chaque motif devrait connaître la
# coupure — c'est-à-dire l'indentation du fichier.
_INIT_SQL = re.sub(r'"\s*\n\s*"', "", _INIT_SRC)

_TENANT_COLUMNS = ("issuer", "jwks_uri", "hosts")


def _tenants_create_block() -> str:
    m = re.search(r"CREATE TABLE IF NOT EXISTS tenants \((.*?)\n\);", _SCHEMA_SRC, re.S)
    assert m, "bloc CREATE TABLE tenants introuvable — test à réparer"
    return m.group(1)


def test_les_colonnes_du_registre_sont_declarees_et_ajoutables():
    """Déclarées dans le CREATE (base vierge) ET ajoutées en ALTER (base existante).

    L1 a déjà créé `tenants` : sans l'ALTER, la prod n'aurait JAMAIS ces colonnes
    et le registre lèverait `column "issuer" does not exist` à chaque boot."""
    body = _tenants_create_block()
    manquantes = [
        c for c in _TENANT_COLUMNS
        if re.search(rf"^\s*{c}\b", body, re.M) is None
        or re.search(rf"ALTER TABLE tenants ADD COLUMN IF NOT EXISTS {c}\b",
                     _INIT_SQL) is None
    ]
    assert not manquantes, (
        f"colonnes du registre d'émetteurs incomplètes : {manquantes}. Chacune doit "
        "être dans le CREATE de `_schema` (base vierge) ET dans un `ALTER TABLE "
        "tenants ADD COLUMN IF NOT EXISTS` de `_init` (base existante — `CREATE "
        "TABLE IF NOT EXISTS` ne propage pas de colonne).")


def test_lemetteur_est_unique_des_deux_cotes():
    """Un émetteur ne désigne qu'un tenant — sinon la sélection par `iss` est
    ambiguë, et l'ambiguïté porte sur l'identité de l'appelant."""
    assert re.search(r"^\s*issuer TEXT UNIQUE", _tenants_create_block(), re.M), (
        "`tenants.issuer` doit être UNIQUE dans le CREATE")
    assert "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS issuer TEXT UNIQUE" in _INIT_SQL, (
        "`tenants.issuer` doit être UNIQUE aussi côté ALTER : une base existante "
        "(la prod) ne passe QUE par là.")


def test_les_hosts_ne_sont_jamais_nuls():
    """`hosts` sert le binding host → tenant du lot L3. NULL vs `[]` y serait une
    distinction sans signification, à trancher une fois pour toutes ici."""
    assert re.search(r"^\s*hosts JSONB NOT NULL DEFAULT '\[\]'::jsonb",
                     _tenants_create_block(), re.M)
    assert ("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS hosts JSONB "
            "NOT NULL DEFAULT '[]'::jsonb") in _INIT_SQL


def test_les_alter_suivent_la_creation_de_la_table():
    """`tenants` naît dans `_SCHEMA`, appliqué par `conn.execute(_SCHEMA)` : un
    ALTER placé avant échouerait sur une base vierge (`relation does not exist`) —
    même famille de panne que l'ordre gardé par L1."""
    schema_applied = _INIT_SRC.index("conn.execute(_SCHEMA)")
    for col in _TENANT_COLUMNS:
        alter = _INIT_SRC.index(f"ALTER TABLE tenants ADD COLUMN IF NOT EXISTS {col}")
        assert schema_applied < alter, (
            f"l'ALTER de `tenants.{col}` doit suivre l'application de `_SCHEMA`")


def test_le_lot_reste_purement_additif():
    """Base PARTAGÉE prod/preprod : un DDL non additif casse la prod au boot de la
    preprod. Aucun `NOT NULL` sans DEFAULT, aucun backfill, aucun DROP sur `tenants`."""
    for col in _TENANT_COLUMNS:
        m = re.search(
            rf"ALTER TABLE tenants ADD COLUMN IF NOT EXISTS {col}[^\"]*", _INIT_SQL)
        assert m, f"ALTER de {col} introuvable"
        stmt = m.group(0)
        if "NOT NULL" in stmt:
            assert "DEFAULT" in stmt, (
                f"`tenants.{col}` : NOT NULL sans DEFAULT ⟹ l'ALTER échoue dès "
                "qu'une ligne existe (et le tenant 1 existe depuis L1).")
