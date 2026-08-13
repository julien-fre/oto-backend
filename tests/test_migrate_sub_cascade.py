"""Aucune dépendance en CASCADE vers `users(sub)` ne doit échapper à `migrate_sub`.

L'étape 4 du merge fait `DELETE FROM users WHERE sub=old_sub`. Toute colonne
déclarée `REFERENCES users(sub) ON DELETE CASCADE` et **non repointée avant** part
donc avec l'ancien compte — silencieusement, et sans retour possible.

Le docstring de `migrate_sub` annonce « les 3 FK ON DELETE CASCADE incluses » : ce
chiffre a vieilli. Ce test remplace le comptage par une dérivation du DDL, pour que
l'ajout d'une table pointant `users(sub)` casse ici plutôt qu'en fenêtre de bascule.

Les colonnes DÉLIBÉRÉMENT non repointées sont nommées, avec leur raison : une
exception muette et une omission se ressemblent trop.
"""
import re

from oto_mcp.db._schema import _SCHEMA
from oto_mcp.db.users import _MEMBERSHIP_TABLES, _PK_SUB_TABLES, _SUB_COLUMNS

# Repointées hors de la boucle `_SUB_COLUMNS`, dans le corps de `migrate_sub`.
_TRAITEES_A_PART = {
    ("user_account_profile", "sub"),        # PK sub : DELETE du frais puis UPDATE
}

# Volontairement ABANDONNÉES — chacune doit porter sa raison.
_ABANDON_DELIBERE = {
    # L'AAD du coffre dérive de l'entité : repointer sans rechiffrer fabrique une
    # ligne indéchiffrable. L'utilisateur repose sa clé (cf. test_migrate_sub_vault).
    ("connector_credentials", "entity_id"),
}


def _cascade_columns(schema_sql: str) -> set:
    """{(table, colonne)} pour tout `… REFERENCES users(sub) ON DELETE CASCADE`."""
    out = set()
    for m in re.finditer(
            r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema_sql, re.S):
        table, body = m.group(1), m.group(2)
        for ligne in body.splitlines():
            if "REFERENCES users(sub)" in ligne and "ON DELETE CASCADE" in ligne:
                col = ligne.strip().split()[0]
                out.add((table, col))
    return out


def test_toute_cascade_vers_users_est_repointee_ou_declaree():
    cascades = _cascade_columns(_SCHEMA)
    assert cascades, "le parse du DDL ne trouve plus aucune FK CASCADE — test à réparer"

    couvertes = set(_SUB_COLUMNS) | _TRAITEES_A_PART | _ABANDON_DELIBERE
    # `_MEMBERSHIP_TABLES` porte le pré-traitement ; la colonne `sub` de ces tables
    # est par ailleurs listée dans `_SUB_COLUMNS`, mais on l'admet des deux façons.
    couvertes |= {(t, "sub") for t, _ in _MEMBERSHIP_TABLES}
    couvertes |= {(t, c) for t, c, _ in _PK_SUB_TABLES}

    orphelines = sorted(cascades - couvertes)
    assert not orphelines, (
        "colonnes en CASCADE vers users(sub) que migrate_sub ne repointe pas :\n  "
        + "\n  ".join(f"{t}.{c}" for t, c in orphelines)
        + "\nÀ la suppression de l'ancien compte, ces lignes disparaissent avec lui.\n"
        "→ les ajouter à `_SUB_COLUMNS`, ou les déclarer dans `_ABANDON_DELIBERE` "
        "AVEC la raison de l'abandon.")


def test_les_abandons_sont_argumentes():
    """Un abandon sans raison écrite redevient un oubli à la première relecture."""
    import pathlib
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    bloc = src.split("_ABANDON_DELIBERE = {")[1].split("}")[0]
    for table, col in _ABANDON_DELIBERE:
        i = bloc.find(f'("{table}", "{col}")')
        assert i > 0, f"{table}.{col} introuvable dans le bloc"
        assert "#" in bloc[:i], f"{table}.{col} est abandonnée sans raison écrite"
