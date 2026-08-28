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


def test_migrate_sub_sub_bearing_columns_are_triaged():
    """Le tripwire INVERSE — celui dont l'absence a coûté `orgs.personal_of` (14
    espaces personnels en double, 14/08) puis les neuf colonnes du dossier du 23/08
    (déroulés, activité, CGU, déclencheurs, réservations, options comp invisibles au
    compte fusionné) : `test_sub_columns_inventory_matches_ddl` vérifie que les
    entrées LISTÉES existent, jamais qu'une colonne porteuse d'un identifiant de
    compte SOIT listée.

    Ici : toute colonne du DDL dont le nom appartient à la famille « porte un sub »
    doit être TRIAGÉE — repointée (`_SUB_COLUMNS`), pré-traitée (`_PK_SUB_TABLES`,
    `_MEMBERSHIP_TABLES`), ou dans l'allowlist ci-dessous AVEC sa raison. Une
    colonne neuve de cette famille arrive donc ROUGE : le triage (repointer ou
    abandonner, et pourquoi) devient un acte explicite, plus un oubli.
    """
    from oto_mcp.db.users import _MEMBERSHIP_TABLES, _PK_SUB_TABLES

    NAMES = ("sub|old_sub|new_sub|effective_sub|owner_sub|grantee_sub|accepted_sub|"
             "personal_of|requested_by|resolved_by|granted_by|created_by|set_by|"
             "invited_by|published_by|principal_id|entity_id|grantee_id|owner_id")
    porteurs: set[tuple[str, str]] = set()
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);",
                         _SCHEMA, re.S):
        table, body = m.group(1), m.group(2)
        for lm in re.finditer(rf"^\s*({NAMES})\s+TEXT", body, re.M):
            porteurs.add((table, lm.group(1)))
    for am in re.finditer(
            rf"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS ({NAMES})\s+TEXT",
            _INIT_SRC):
        porteurs.add((am.group(1), am.group(2)))
    assert porteurs, "le parse du DDL ne trouve plus de colonne porteuse — test à réparer"

    # Hors inventaire, chacune pour une raison STRUCTURELLE (pas un oubli) :
    allow = {
        # L'ENTITÉ du coffre entre dans l'AAD : une ligne repointée sans rechiffrement
        # est indéchiffrable — pire qu'absente (0052 §Migrer : l'utilisateur repose
        # ses clés, jamais d'UPDATE ici).
        ("connector_credentials", "entity_id"),
        # L'instance (lot L6) SUIT la ligne de coffre : son `owner_id` EST
        # l'`entity_id` juste au-dessus, et le lien entre les deux est ce quadruplet.
        # Repointer l'instance seule la DÉTACHERAIT de sa ligne de coffre — un objet
        # qui désigne une clé qui n'existe pas, strictement pire que rien. Elle ne
        # peut donc pas être repointée tant que la ligne du coffre ne l'est pas, et
        # la ligne du coffre ne l'est jamais (l'AAD). Le compte migré repose ses clés,
        # le boot suivant nomme les lignes neuves.
        # ⚠️ CORRIGÉ le 2026-08-28 (L6 pièce 2), qui EST « le lot qui fait suivre
        # l'instance aux déplacements du coffre » : il n'y a rien à archiver ici. La
        # ligne du coffre n'est pas SUPPRIMÉE par une bascule de compte, seulement
        # abandonnée en place — instance et ligne restent donc appariées, et
        # l'invariant (chaque ligne vivante ↔ une instance vivante, dans les deux
        # sens) tient. Archiver l'instance seule le CASSERAIT, à l'endroit précis où
        # la version précédente de ce commentaire proposait de le faire. Le compte
        # migré repose ses clés : ce sont des instances neuves, nommées à la pose.
        ("connector_instances", "owner_id"),
        # Repointée par l'étape 3 bis de migrate_sub, FILTRÉE sur grantee_kind='user'
        # (la colonne porte aussi des ids d'org) — pas un UPDATE nu d'inventaire.
        ("grants", "grantee_id"),
        # owner_type ∈ {org, group} : ces owner_id sont des ids numériques, jamais un
        # sub (les procédures user n'existent pas dans cette table).
        ("org_instructions", "owner_id"), ("org_instruction_revisions", "owner_id"),
        # La marque d'espace personnel : étape 2 quater (index unique ⟹ démarquage
        # conditionnel, pas un UPDATE nu). Vécu 14/08.
        ("orgs", "personal_of"),
        # La table d'alias EST le produit du merge — la repointer se mordrait la queue.
        ("sub_aliases", "old_sub"), ("sub_aliases", "new_sub"),
        # Étape 2 : PK sub ⟹ DELETE du frais puis repointage, pas un UPDATE nu.
        ("user_account_profile", "sub"),
        # Le sujet même du merge (étapes 1 et 4).
        ("users", "sub"),
    }
    couvertes = (set(_SUB_COLUMNS)
                 | {(t, c) for t, c, _ in _PK_SUB_TABLES}
                 | {(t, "sub") for t, _ in _MEMBERSHIP_TABLES}
                 | allow)
    manquantes = porteurs - couvertes
    assert not manquantes, (
        "colonnes porteuses d'un sub NON triagées par migrate_sub :\n  "
        + "\n  ".join(f"{t}.{c}" for t, c in sorted(manquantes))
        + "\nLes repointer (_SUB_COLUMNS / _PK_SUB_TABLES), les traiter à part, ou "
          "les ajouter à l'allowlist de ce test AVEC leur raison.")
    mortes = allow - porteurs
    assert not mortes, (
        f"entrées d'allowlist sans colonne DDL correspondante : {sorted(mortes)} — "
        "retirer l'entrée (la colonne a disparu) ou réparer le parse.")
