"""Le classement du périmètre couvre EXACTEMENT le schéma réel (oto-backend#1088).

Le schéma est celui que monte `init_db` sur une base neuve (fixture `live`), jamais une
reconstitution : les colonnes posées par `ALTER` au démarrage n'existent que là.

Ce test est le garde-fou de l'export : une table ajoutée au schéma sans être classée
dans `oto_mcp/export_perimetre/classement.py` le fait rougir — sans lui, l'export
raterait cette table en silence, et la cible naîtrait amputée sans que personne le voie.
Chaque refus est prouvé en lui PRÉSENTANT l'anomalie qu'il prétend attraper.
"""
from __future__ import annotations

import dataclasses

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row  # noqa: E402

from oto_mcp.export_perimetre import classement as cl  # noqa: E402
from oto_mcp.export_perimetre.decouverte import (  # noqa: E402
    ClassementIncomplet, lire_schema, verifier_classement)
from oto_mcp.export_perimetre.extraction import ordre_d_export  # noqa: E402
from oto_mcp.export_perimetre.importation import lignes_deja_la  # noqa: E402
from oto_mcp.export_perimetre.regles import ParOrg, ParSub, Via  # noqa: E402


@pytest.fixture(scope="module")
def conn(live, pg_module_dsn):
    with psycopg.connect(pg_module_dsn, autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def schema(conn):
    return lire_schema(conn)


def _refus(schema, classement) -> str:
    with pytest.raises(ClassementIncomplet) as e:
        verifier_classement(schema, classement)
    return str(e.value)


def test_le_classement_couvre_le_schema_reel(schema):
    assert len(schema.colonnes) > 90, "le schéma lu n'est pas celui du démarrage"
    resolu = verifier_classement(schema, cl.CLASSEMENT)
    assert set(resolu) == set(schema.colonnes)


def test_une_entree_qui_nomme_une_vue_classe_sa_table(schema):
    """La bibliothèque publique se nomme par sa vue (#526) : la découverte la résout."""
    assert "guide_library" in cl.CLASSEMENT
    table = schema.vues["guide_library"]
    assert table in schema.colonnes and table not in cl.CLASSEMENT
    assert verifier_classement(schema, cl.CLASSEMENT)[table] is cl.CLASSEMENT["guide_library"]
    classement = {t: e for t, e in cl.CLASSEMENT.items() if t != "guide_library"}
    assert f"table `{table}` non classée" in _refus(schema, classement)


def test_une_table_classee_deux_fois_par_sa_vue_est_refusee(schema):
    table = schema.vues["guide_library"]
    classement = {**cl.CLASSEMENT, table: cl.CLASSEMENT["guide_library"]}
    assert "classée deux fois" in _refus(schema, classement)


def test_une_table_ajoutee_au_schema_sans_classement_est_refusee(conn):
    conn.execute("CREATE TABLE table_neuve_1088 (id BIGSERIAL PRIMARY KEY, org_id BIGINT)")
    try:
        message = _refus(lire_schema(conn), cl.CLASSEMENT)
    finally:
        conn.execute("DROP TABLE table_neuve_1088")
    assert "table `table_neuve_1088` non classée" in message


def test_une_table_retiree_du_classement_est_refusee(schema):
    classement = {t: e for t, e in cl.CLASSEMENT.items() if t != "docs"}
    assert "table `docs` non classée" in _refus(schema, classement)


def test_une_entree_sans_table_est_refusee(schema):
    classement = {**cl.CLASSEMENT, "table_disparue": cl.possedee(ParOrg())}
    assert "entrée `table_disparue`" in _refus(schema, classement)


def test_une_regle_qui_vise_une_colonne_absente_est_refusee(schema):
    classement = {**cl.CLASSEMENT, "users": cl.possedee(ParSub("sub_renomme"))}
    assert "`users` : la colonne `sub_renomme`" in _refus(schema, classement)


def test_un_lien_declare_fk_sans_cle_etrangere_est_refuse(schema):
    classement = {**cl.CLASSEMENT,
                  "docs": cl.indirecte(Via("projects", ("parent_id",)))}
    assert "aucune clé étrangère" in _refus(schema, classement)


def test_un_heritage_d_une_table_qui_ne_part_pas_est_refuse(schema):
    classement = {**cl.CLASSEMENT,
                  "tenant_admins": cl.indirecte(Via("alembic_version", ("slug",),
                                                    ("version_num",), fk=False))}
    assert "`tenant_admins` hérite de `alembic_version`" in _refus(schema, classement)


def test_une_table_indirecte_ne_porte_pas_de_regle_directe(schema):
    classement = {**cl.CLASSEMENT, "org_groups": cl.indirecte(ParOrg())}
    assert "`org_groups` (indirecte)" in _refus(schema, classement)


def test_une_exclusion_ou_une_table_d_instance_dit_pourquoi(schema):
    muette = dataclasses.replace(cl.CLASSEMENT["billing_payments"], raison="")
    classement = {**cl.CLASSEMENT, "billing_payments": muette,
                  "platform_instructions": cl.Table(cl.INSTANCE)}
    message = _refus(schema, classement)
    assert "`billing_payments` (exclue) doit dire pourquoi" in message
    assert "`platform_instructions` (instance) doit dire pourquoi" in message


def test_les_colonnes_secretes_et_hors_base_existent(schema):
    for t, e in verifier_classement(schema, cl.CLASSEMENT).items():
        for c in (*e.secrets, *e.hors_base):
            assert c in schema.colonnes[t], f"{t}.{c}"


def test_l_ordre_d_export_place_chaque_parent_avant_ses_enfants(schema):
    classement = verifier_classement(schema, cl.CLASSEMENT)
    ordre = ordre_d_export(schema, classement)
    rang = {t: i for i, t in enumerate(ordre)}
    assert set(ordre) == {t for t, e in classement.items() if e.classe in cl.EXPORTEES}
    for t in ordre:
        for k in schema.cles_de(t):
            if k.cible in rang and k.cible != t:
                assert rang[k.cible] < rang[t], f"{k.cible} doit précéder {t}"


def test_la_naissance_declaree_est_exactement_ce_qu_init_db_seme(conn, schema):
    """#1161 : l'import tolère, dans les tables qu'il écrit, les lignes que la naissance
    de l'instance y sème (`Table.naissance`), et rien d'autre. La déclaration se lit
    contre la base que vient de monter `init_db` : une table qu'il sème sans déclaration
    ferait refuser TOUTE cible neuve, une déclaration sans semis ou trop large laisserait
    passer des lignes que l'import heurterait. Une cible neuve passe donc le contrôle."""
    classement = verifier_classement(schema, cl.CLASSEMENT)
    semees = {t for t, e in classement.items() if e.classe in cl.EXPORTEES
              and conn.execute(f"SELECT EXISTS (SELECT 1 FROM {t}) AS e").fetchone()["e"]}
    declarees = {t for t, e in classement.items() if e.naissance}
    assert semees == declarees
    assert lignes_deja_la(conn, classement) == {}


def test_une_colonne_de_naissance_absente_est_refusee(schema):
    classement = {**cl.CLASSEMENT, "connector_selection_seeded": cl.possedee(
        ParOrg(), comptes=("sub",), naissance=("org_renomme", "0"))}
    assert "`connector_selection_seeded` : la colonne `org_renomme`" in \
        _refus(schema, classement)


def test_une_colonne_compte_absente_est_refusee(schema):
    classement = {**cl.CLASSEMENT, "connector_selection_seeded": cl.possedee(
        ParOrg(), comptes=("sub_renomme",))}
    assert "`connector_selection_seeded` : la colonne `sub_renomme`" in \
        _refus(schema, classement)


# Les tables exportées dont la colonne `sub` n'est PAS le compte de la ligne, et pourquoi.
SUB_SANS_COMPTE = {
    "org_members": "les membres : le périmètre en dérive, ils en sont tous",
    "org_group_members": "les membres d'équipe : le périmètre en dérive, ils en sont tous",
    "project_activity": "l'auteur d'un geste sur le projet, qui est à l'org",
    "transcription_jobs": "qui a demandé la transcription d'un fichier du projet",
}


def test_toute_colonne_sub_d_une_table_exportee_dit_a_qui_est_la_ligne(schema):
    """Une ligne possédée par org peut porter le compte d'un ANCIEN membre : sa colonne
    `sub` est une colonne-compte (`comptes`), que la règle rattache ou omet — sinon
    l'import retombe sur un doublon brut. Une table ajoutée avec un `sub` se range ici
    ou là, pas nulle part."""
    classement = verifier_classement(schema, cl.CLASSEMENT)
    sans_statut = sorted(
        t for t, e in classement.items()
        if e.classe in cl.EXPORTEES and "sub" in schema.colonnes[t]
        and "sub" not in e.regle.colonnes()
        and "sub" not in {k.colonne for k in cl.comptes_de(e)}
        and t not in SUB_SANS_COMPTE)
    assert sans_statut == []
    assert all("sub" in schema.colonnes[t] for t in SUB_SANS_COMPTE)
