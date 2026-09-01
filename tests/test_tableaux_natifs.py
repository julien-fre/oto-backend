"""Tableaux et lignes NATIFS — la nouvelle surface sait remplir ses propres tableaux.

La recopie est arrêtée : la surface nœud part de vide et n'a plus qu'une façon d'être
remplie, ses propres verbes. Tant qu'elle ne savait écrire que des pages, « tout porter
du nouvel univers » restait une intention.

Ce que ce fichier tient, et qui ne se déduit pas du code :

1. **une ligne EST un nœud** — mêmes quatre verbes pour les trois genres, pas trois
   vocabulaires pour une seule notion ;
2. **la donnée métier ne touche jamais les propriétés du nœud** — c'est la raison
   d'être de la colonne `data`, et une cellule nommée `title` en est la preuve ;
3. **le schéma se stocke sous la clé que la LECTURE attend** — la surface dit
   « colonnes », le stockage dit `fields`, et se tromper produit un tableau qui
   s'affiche sans aucune colonne, sans la moindre erreur. Le mode d'échec est muet,
   donc il se teste de bout en bout : on écrit par la surface, on relit par la surface ;
4. **ce qui n'est pas servi est REFUSÉ** — filtre, recherche et tri sur un tableau
   natif rendent une erreur nommée, jamais une page complète qui passerait pour un
   résultat filtré.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_tbl_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


COLONNES = [{"key": "societe", "label": "Société", "type": "text"},
            {"key": "score", "label": "Score", "type": "number"}]


def _tableau(**kw):
    from oto_mcp.db import node_tables as t
    return t.create_table(owner_type="org", owner_id="1", title="Prospects Q3",
                          columns=COLONNES, **kw)


def test_un_tableau_natif_stocke_son_schema_sous_la_cle_que_la_lecture_lit(live):
    """Le mode d'échec est MUET : sous la mauvaise clé, le tableau s'affiche sans
    colonne et personne ne voit d'erreur. On écrit, puis on relit par la face de
    lecture, plutôt que d'affirmer la forme du stockage."""
    from oto_mcp.capabilities import node_rows
    from oto_mcp.db import node_view

    table = _tableau()
    fiche = node_view.node_by_public_id(table["public_id"])
    colonnes = node_rows._colonnes((fiche.get("props") or {}).get("child_schema"))

    assert [c.key for c in colonnes] == ["societe", "score"]
    assert [c.title for c in colonnes] == ["Société", "Score"]
    assert colonnes[1].numeric is True, "une colonne de nombres s'aligne à droite"


def test_la_donnee_metier_n_ecrase_jamais_le_noeud(live):
    from oto_mcp.db import node_tables as t
    from oto_mcp.db._conn import _connect

    table = _tableau()
    ligne = t.add_row(table["id"], {"title": "Société Dupont", "position": "gérant",
                                    "score": 42})
    assert ligne is not None
    with _connect() as conn:
        r = conn.execute(
            "SELECT props, data, position, parent_id, owner_type, owner_id "
            "  FROM nodes WHERE id = %s", (ligne["id"],)).fetchone()
    assert r["props"] == {}, "une valeur métier s'est glissée dans les propriétés"
    assert r["data"]["title"] == "Société Dupont"
    assert isinstance(r["position"], int), "la position reste l'ordre de la fratrie"
    assert r["parent_id"] == table["id"]
    # 0054-D4 : une ligne n'a pas de propriétaire propre, elle prend celui du tableau.
    assert (r["owner_type"], r["owner_id"]) == ("org", "1")


def test_l_ecriture_d_une_ligne_fusionne_et_n_efface_pas_le_reste(live):
    from oto_mcp.db import node_tables as t
    from oto_mcp.db._conn import _connect

    table = _tableau()
    ligne = t.add_row(table["id"], {"societe": "Dupont", "score": 42})
    assert t.update_row(ligne["id"], {"score": 51}) is True
    with _connect() as conn:
        data = conn.execute("SELECT data FROM nodes WHERE id = %s",
                            (ligne["id"],)).fetchone()["data"]
    assert data == {"societe": "Dupont", "score": 51}, (
        "poster une cellule a effacé les autres — le piège lecture partielle + "
        "remplacement total, déjà vécu sur un pont client")


def test_les_lignes_sortent_dans_l_ordre_et_le_curseur_ne_saute_rien(live):
    from oto_mcp.db import node_tables as t

    table = _tableau()
    for i in range(5):
        t.add_row(table["id"], {"societe": f"S{i}"})

    page1, suivant = t.list_rows(table["id"], limit=2)
    assert [l["data"]["societe"] for l in page1] == ["S0", "S1"]
    assert suivant is not None

    # Une ligne intercalée entre deux pages ne doit ni décaler ni répéter : le
    # curseur est une position, pas un décalage.
    t.add_row(table["id"], {"societe": "S5"})
    page2, _ = t.list_rows(table["id"], limit=2, after_position=suivant)
    assert [l["data"]["societe"] for l in page2] == ["S2", "S3"]
    assert t.count_rows(table["id"]) == 6


def test_la_derniere_page_ne_promet_pas_de_suite(live):
    from oto_mcp.db import node_tables as t

    table = _tableau()
    for i in range(3):
        t.add_row(table["id"], {"societe": f"S{i}"})
    lignes, suivant = t.list_rows(table["id"], limit=10)
    assert len(lignes) == 3
    assert suivant is None, "un curseur non nul ferait redemander une page vide"


def test_une_ligne_ne_se_pose_pas_sous_autre_chose_qu_un_tableau(live):
    from oto_mcp.db import node_tables as t, nodes as db_nodes

    page = db_nodes.create_page(owner_type="org", owner_id="1", title="Une page")
    assert t.add_row(page["id"], {"x": 1}) is None


def test_une_ecriture_ne_touche_jamais_un_noeud_recopie(live):
    """La garde de dernier recours, sous la surface : même en appelant la couche
    directement, un nœud marqué par la recopie n'est pas modifiable ici."""
    import json
    from oto_mcp.db import node_tables as t
    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        recopie = conn.execute(
            "INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
            "VALUES (%s, 'tableau', 'org', '1', %s::jsonb) RETURNING id",
            ("nod_" + uuid.uuid4().hex[:10],
             json.dumps({"legacy": "table", "legacy_id": "7"}))).fetchone()["id"]
        ligne = conn.execute(
            "INSERT INTO nodes (public_id, parent_id, kind, owner_type, owner_id, "
            "                   props, data) "
            "VALUES (%s, %s, 'ligne', 'org', '1', %s::jsonb, '{}'::jsonb) RETURNING id",
            ("row_" + uuid.uuid4().hex[:10], recopie,
             json.dumps({"legacy": "row"}))).fetchone()["id"]

    assert t.add_row(recopie, {"x": 1}) is None
    assert t.set_columns(recopie, COLONNES) is False
    assert t.update_row(ligne, {"x": 1}) is False
    assert t.delete_row(ligne) is False


def test_filtre_recherche_et_tri_sont_refuses_pas_ignores(live):
    """Accepter en silence serait pire : l'appelant croirait avoir filtré."""
    from oto_mcp.capabilities import node_rows
    from oto_mcp.capabilities._types import AuthzDenied
    from oto_mcp.db import node_view

    table = _tableau()
    fiche = node_view.node_by_public_id(table["public_id"])
    props = fiche.get("props") or {}

    for champ, valeur in (("q", "dupont"), ("sort", "score"), ("filter", ["a:b"])):
        inp = node_rows.NodeRowsInput(node_id=table["public_id"], **{champ: valeur})
        with pytest.raises(AuthzDenied) as e:
            node_rows._natif(inp, fiche, props)
        assert e.value.code == "non_supporte_sur_tableau_natif", champ

    # Sans eux, la lecture passe.
    page = node_rows._natif(
        node_rows.NodeRowsInput(node_id=table["public_id"]), fiche, props)
    assert page["columns"] and page["total"] == 0
