"""Une bascule de compte doit emporter les prêts de compte SCOPÉS À UN GROUPE —
exercé sur le merge réel, en SQL réel.

`test_migrate_sub_cascade` et `test_migrate_sub_inventory` dérivent leur verdict du
DDL : ils prouvent que la colonne est TRIAGÉE, jamais que le repointage a lieu. Or
c'est le repointage qui compte pour l'utilisateur — sans lui, l'étape 4 du merge
(`DELETE FROM users WHERE sub=old_sub`) emporte la ligne en CASCADE, et le
propriétaire fusionné découvre que son équipe a perdu l'accès à son compte, sans
trace de ce qui a disparu. Un partage d'équipe ne se re-consent pas de mémoire.

Deux cas, parce que le second est la RAISON du pré-traitement :
  1. le prêt suit la personne (bout en bout : le membre du groupe joint toujours le
     compte après la bascule) ;
  2. les DEUX comptes ont prêté le même canal au même groupe — l'`UPDATE` nu de
     `_SUB_COLUMNS` y lèverait `UniqueViolation` sur la PK
     `(owner_sub, provider, grantee_group_id)`, et cette exception ferait échouer
     TOUT le merge, pas seulement cette table (mode d'échec vécu en prod le
     2026-07-28 sur `org_members` : merge en échec à CHAQUE requête de l'user).
     D'où l'entrée dans `_PK_SUB_TABLES` et non dans `_SUB_COLUMNS` ; déplacer la
     ligne de l'une à l'autre rend ce test rouge.

Patron de base éphémère : `test_account_group_grants_db.py::live`.
"""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_migrgrant_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "4" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url),
                            ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _lignes_de_grant_groupe(group_id: int) -> list[dict]:
    """Le contenu BRUT de la table — le test ne peut pas le lire par une fonction
    de lecture scopée au owner, puisque c'est justement le owner qui change."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        rows = conn.execute(
            "SELECT owner_sub, provider, account_id, granted_by "
            "FROM connector_account_group_grants WHERE grantee_group_id=%s",
            (group_id,)).fetchall()
    return [dict(r) for r in rows]


def test_le_pret_de_compte_a_un_groupe_suit_la_bascule(live):
    """Le cas nominal, de bout en bout : après le merge, le membre du groupe joint
    toujours le compte, et la ligne désigne le compte canonique — propriétaire ET
    signature."""
    from oto_mcp import db, group_store, org_store
    uniq = uuid.uuid4().hex[:8]
    ancien, canonique, membre = f"old_{uniq}", f"new_{uniq}", f"member_{uniq}"
    for sub in (ancien, canonique, membre):
        db.upsert_user(sub)
    org = org_store.create_org(f"org_{uniq}", created_by=ancien)
    groupe = group_store.create_group(org, f"group_{uniq}")
    group_store.add_group_member(groupe, membre)
    db.set_unipile_account(ancien, "ACC_OLD", "Avatar", org_id=org,
                           provider="LINKEDIN")
    db.set_account_group_grant(ancien, "LINKEDIN", "ACC_OLD", groupe,
                               granted_by=ancien)
    assert db.granted_accounts_for(membre, "LINKEDIN") != {}, "décor invalide"

    assert db.migrate_sub(ancien, canonique, operator_source="test") is True

    lignes = _lignes_de_grant_groupe(groupe)
    assert len(lignes) == 1, "le prêt a disparu avec l'ancien compte"
    assert lignes[0]["owner_sub"] == canonique
    # La signature suit la même personne : la laisser sur l'ancien identifiant ne
    # conserverait pas la trace (pas de FK ⟹ la ligne survit au DELETE de l'étape 4),
    # elle la rendrait illisible.
    assert lignes[0]["granted_by"] == canonique
    # Bout en bout : le membre du groupe joint toujours le compte, désormais sous le
    # compte canonique (la ligne `unipile_accounts` a suivi par `_SUB_COLUMNS`).
    joignables = db.granted_accounts_for(membre, "LINKEDIN")
    assert joignables == {"ACC_OLD": {"owner_sub": canonique, "owner_email": None}}
    # L'identifiant d'origine reste retrouvable — l'alias est le produit du merge.
    assert db.resolve_sub(ancien) == canonique


def test_deux_prets_au_meme_groupe_ne_font_pas_echouer_le_merge(live):
    """Les deux comptes de la personne ont prêté le même canal au même groupe.

    C'est le cas qui justifie `_PK_SUB_TABLES` : un `UPDATE` nu lèverait
    `UniqueViolation` sur la PK et ferait échouer le merge ENTIER. On garde la ligne
    du compte canonique et on jette celle de l'ancien (même règle qu'en 2 bis)."""
    from oto_mcp import db, group_store, org_store
    uniq = uuid.uuid4().hex[:8]
    ancien, canonique = f"old_{uniq}", f"new_{uniq}"
    for sub in (ancien, canonique):
        db.upsert_user(sub)
    org = org_store.create_org(f"org_{uniq}", created_by=canonique)
    groupe = group_store.create_group(org, f"group_{uniq}")
    db.set_account_group_grant(ancien, "LINKEDIN", "ACC_OLD", groupe,
                               granted_by=ancien)
    db.set_account_group_grant(canonique, "LINKEDIN", "ACC_NEW", groupe,
                               granted_by=canonique)

    # Ne doit PAS lever : c'est tout le merge qui tomberait, pas seulement ce prêt.
    assert db.migrate_sub(ancien, canonique, operator_source="test") is True

    lignes = _lignes_de_grant_groupe(groupe)
    assert len(lignes) == 1, "la PK aurait dû rester unique après le merge"
    assert lignes[0]["owner_sub"] == canonique
    assert lignes[0]["account_id"] == "ACC_NEW", "le canonique est la ligne gardée"
