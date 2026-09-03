"""Une contrainte de domaine n'est reposée QUE si elle a changé.

⚠️ Un `ADD CONSTRAINT … CHECK` valide la contrainte sur **toute la table**, sous
verrou `ACCESS EXCLUSIVE`. Le faire à chaque démarrage — y compris quand la
contrainte est déjà exactement celle qu'on repose — est un coût qui **croît avec
la table**. Mesuré le 03/09 : 3 ms à 11 000 lignes, 11 ms à 100 000, quelques
secondes de verrou exclusif à dix millions.

⚠️ **Le motif conditionnel déjà employé ailleurs dans `_init` ne suffisait pas
ici** : `IF NOT EXISTS (SELECT 1 FROM pg_constraint …)` ne fait **rien** quand la
contrainte existe avec une définition périmée — or c'est précisément le cas à
traiter, ajouter une valeur au domaine d'une table déjà déployée. D'où la
comparaison des VALEURS.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture()
def base(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    nom = "oto_check_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    c = psycopg.connect(dsn, autocommit=True, row_factory=psycopg.rows.dict_row)
    c.execute("CREATE TABLE t (id BIGSERIAL PRIMARY KEY, status TEXT NOT NULL)")
    try:
        yield c
    finally:
        c.close()
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


def _poser(conn, valeurs):
    from oto_mcp.db._init import _poser_domaine
    return _poser_domaine(conn, "t", "t_status_check", "status", valeurs)


def test_la_premiere_pose_agit(base):
    assert _poser(base, ("a", "b")) is True


def test_la_SECONDE_pose_identique_ne_fait_RIEN(base):
    """⚠️ LE test. C'est ce qui évite de revalider toute la table à chaque
    démarrage — le coût qui croît sans que personne ne le voie."""
    assert _poser(base, ("a", "b")) is True
    assert _poser(base, ("a", "b")) is False, (
        "la contrainte a été reposée alors qu'elle était identique : "
        "toute la table est revalidée sous verrou exclusif, à chaque boot")


def test_un_domaine_ELARGI_est_repose(base):
    """Le cas réel qui a imposé le `DROP`+`ADD` : ajouter une valeur à une table
    déjà déployée. Un `IF NOT EXISTS` n'aurait rien fait, et la base aurait
    refusé la valeur neuve — à l'écriture, loin du boot."""
    _poser(base, ("a", "b"))
    assert _poser(base, ("a", "b", "c")) is True
    base.execute("INSERT INTO t (status) VALUES ('c')")   # la valeur neuve passe


def test_un_domaine_RESTREINT_est_repose_aussi(base):
    """⚠️ Le contrôle symétrique : une comparaison qui ne verrait que les ajouts
    laisserait une valeur retirée du code encore acceptée par la base — donc un
    domaine plus large que ce que le code croit."""
    _poser(base, ("a", "b", "c"))
    assert _poser(base, ("a", "b")) is True
    with pytest.raises(Exception):
        base.execute("INSERT INTO t (status) VALUES ('c')")


def test_l_ordre_des_valeurs_n_est_pas_un_changement(base):
    """⚠️ Sans ça, le helper reposerait à chaque boot dès que quelqu'un réordonne
    la liste dans le code — et on aurait remplacé un coût systématique par un
    coût aléatoire, plus dur à diagnostiquer."""
    _poser(base, ("a", "b", "c"))
    assert _poser(base, ("c", "a", "b")) is False
