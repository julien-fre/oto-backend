"""Garde-fou de l'inventaire `_SUB_COLUMNS` (migrate_sub, bascule de tenant #35/#56).

La boucle de `migrate_sub` fait des UPDATE nus dans UNE transaction : une entrée
pointant une table/colonne ABSENTE fait échouer tout le merge — silencieusement,
puisque rien ne l'exerce en CI. Vécu (Phase H B1, 10/07) : `user_grants` droppée
par 0044 §F mais restée listée → migrate_sub cassé pendant deux jours.

Ce test fige le contrat : chaque `(table, col)` de l'inventaire doit exister dans
le DDL — colonne déclarée dans le bloc CREATE TABLE de `_schema.py`, OU ajoutée
par un `ALTER TABLE <t> ADD COLUMN IF NOT EXISTS <col>` de `_init.py`. Dropper
une table/colonne sans retirer son entrée casse ce test au lieu de casser le
merge en prod.
"""
import pathlib
import re

from oto_mcp.db._schema import _SCHEMA
from oto_mcp.db.users import _SUB_COLUMNS

_INIT_SRC = (pathlib.Path(__file__).resolve().parent.parent
             / "oto_mcp" / "db" / "_init.py").read_text(encoding="utf-8")


def _create_blocks(schema_sql: str) -> dict[str, str]:
    """{table: corps du CREATE} — parse suffisant pour vérifier la présence d'un
    nom de colonne (les DDL du repo sont réguliers : un bloc par table)."""
    blocks = {}
    for m in re.finditer(
            r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema_sql, re.S):
        blocks[m.group(1)] = m.group(2)
    return blocks


def test_sub_columns_inventory_matches_ddl():
    blocks = _create_blocks(_SCHEMA)
    problems = []
    for table, col in _SUB_COLUMNS:
        body = blocks.get(table)
        if body is None:
            problems.append(f"{table}.{col} : table absente de _schema.py")
            continue
        in_create = re.search(rf"^\s*{col}\b", body, re.M) is not None
        in_alter = re.search(
            rf"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col}\b",
            _INIT_SRC) is not None
        if not (in_create or in_alter):
            problems.append(f"{table}.{col} : colonne introuvable (CREATE et ALTER)")
    assert not problems, (
        "entrées _SUB_COLUMNS mortes (migrate_sub échouerait en prod) :\n  "
        + "\n  ".join(problems)
        + "\nRetirer l'entrée de l'inventaire (ou restaurer la colonne)."
    )


def test_active_membership_tables_are_pre_treated():
    """Une table `(scope, sub)` avec un `is_active` UNIQUE par sub ne peut pas passer
    par l'UPDATE nu de `_SUB_COLUMNS`.

    Vécu prod 2026-07-28 (julien@folk.app) : `UPDATE org_members SET sub=new` a fait
    porter DEUX appartenances actives au même sub → `UniqueViolation
    org_members_one_active`. Le merge échouait à CHAQUE requête de l'utilisateur (donc
    jamais fusionné, plus un round-trip Logto et un traceback par appel). Ces tables
    doivent être listées dans `_MEMBERSHIP_TABLES` et traitées AVANT la boucle.
    """
    from oto_mcp.db.users import _MEMBERSHIP_TABLES
    declared = {t for t, _ in _MEMBERSHIP_TABLES}
    # Source de vérité = le DDL : tout index unique partiel `ON <table>(sub) WHERE is_active`.
    in_ddl = set(re.findall(
        r"CREATE UNIQUE INDEX IF NOT EXISTS \w+\s*\n?\s*ON (\w+)\(sub\) WHERE is_active",
        _SCHEMA))
    assert in_ddl, "le parse du DDL ne trouve plus les index `one_active` — test à réparer"
    manquantes = in_ddl - declared
    assert not manquantes, (
        f"tables à `is_active` unique non pré-traitées par migrate_sub : {sorted(manquantes)}. "
        "Les ajouter à `_MEMBERSHIP_TABLES` (sinon le merge de comptes lève "
        "UniqueViolation en prod, en silence côté CI).")
