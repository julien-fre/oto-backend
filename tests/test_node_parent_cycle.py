"""L'arbre ne se referme pas sur lui-même — et s'il l'était déjà, rien ne tourne à vide.

**Le défaut, mesuré de bout en bout le 2026-09-01.** `move A sous B`, alors que B est
déjà enfant de A, rendait **200** : aucune garde. Le `delete A` qui suivait jouait un
`WITH RECURSIVE … UNION ALL` sans borne sur la boucle ainsi créée et ne terminait pas —
`psycopg.errors.DiskFull: could not write to file "base/pgsql_tmp/…"`. Avec une borne
artificielle posée à 20 000 niveaux, la requête produisait 20 001 lignes.

⚠️ **Cette base est partagée entre la préproduction et la production.** Un appel
authentifié quelconque saturait donc le disque de la base de PRODUCTION, et la box a
déjà connu un disque plein (SSL et préproduction cassés à la clé).

**Deux gestes, et il faut les deux.** La garde amont (`move_page` refuse) protège les
déplacements à venir ; la borne (clause `CYCLE`) protège de ce qui serait DÉJÀ en base
— aucune garde ne défait rétroactivement une boucle écrite hier. Les tests d'ici sont
appariés sur cette distinction : ceux qui refusent, et ceux qui terminent malgré tout.

## Comment ce fichier prouve « ça ne termine pas » sans remplir un disque

La base jetable est ouverte avec `temp_file_limit` et `statement_timeout` posés SUR
ELLE (`ALTER DATABASE`). Une récursion non bornée y meurt en quelques secondes contre
le plafond de fichiers temporaires, au lieu d'écrire jusqu'au `DiskFull`. Le test dit
donc exactement la bonne chose : « cette requête consomme du temporaire sans fin »,
mesuré, jamais reproduit à l'échelle.
"""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture()
def live(pg_dsn):
    """Une base à SOI sur le conteneur partagé, plafonnée en temporaire.

    ⚠️ Jamais `init_db()` dans la base du conteneur : `pg_dsn` est session-scopé et un
    boot complet y laisse ~67 tables, ce qui fait rougir des tests étrangers qui
    recréent deux tables autonomes. C'est la recette du repo (cf.
    `test_write_by_id_effect.py`), plus les deux plafonds.
    """
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_cycle_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    # Les deux garde-fous du banc. `temp_file_limit` est celui qui mord en premier sur
    # une récursion sans borne : la worktable spille, et le plafond la tue.
    root.execute(f"ALTER DATABASE \"{name}\" SET temp_file_limit = '64MB'")
    root.execute(f"ALTER DATABASE \"{name}\" SET statement_timeout = '20s'")
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


OWNER = {"owner_type": "org", "owner_id": "1"}


def _page(titre, parent_id=None):
    from oto_mcp.db import nodes as db_nodes
    return db_nodes.create_page(title=titre, parent_id=parent_id, **OWNER)


def _parent_de(node_id):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("SELECT parent_id FROM nodes WHERE id = %s",
                            (node_id,)).fetchone()["parent_id"]


def _boucle(enfant_id, nouveau_parent_id):
    """Referme l'arbre PAR LA BASE, sans passer par le code — c'est la seule façon
    d'obtenir l'état « un cycle est déjà là » une fois la garde amont en place."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("UPDATE nodes SET parent_id = %s WHERE id = %s",
                     (nouveau_parent_id, enfant_id))


# ── 1. La garde amont : le cycle est REFUSÉ ────────────────────────────────────

def test_ranger_un_noeud_sous_son_propre_enfant_est_refuse(live):
    """Le geste exact de l'incident : `move A sous B`, B déjà enfant de A."""
    from oto_mcp.db import nodes as db_nodes

    a = _page("A")
    b = _page("B", parent_id=a["id"])

    with pytest.raises(db_nodes.ParentCycle):
        db_nodes.move_page(a["id"], parent_id=b["id"])


def test_le_refus_n_ecrit_RIEN(live):
    """Un refus qui aurait déjà déplacé le nœud laisserait la boucle en base : le
    `DELETE` suivant serait quand même sur un cycle. Le refus vaut par ce qu'il
    n'écrit pas, pas par ce qu'il lève."""
    from oto_mcp.db import nodes as db_nodes

    a = _page("A")
    b = _page("B", parent_id=a["id"])

    with pytest.raises(db_nodes.ParentCycle):
        db_nodes.move_page(a["id"], parent_id=b["id"])

    assert _parent_de(a["id"]) is None
    assert _parent_de(b["id"]) == a["id"]


def test_ranger_un_noeud_sous_LUI_MEME_est_refuse(live):
    """La boucle la plus courte — un nœud son propre parent. Même geste, pas un cas
    particulier : la remontée s'amorce sur le parent demandé, donc elle le contient."""
    from oto_mcp.db import nodes as db_nodes

    a = _page("A")
    with pytest.raises(db_nodes.ParentCycle):
        db_nodes.move_page(a["id"], parent_id=a["id"])
    assert _parent_de(a["id"]) is None


def test_ranger_un_noeud_sous_un_PETIT_enfant_est_refuse(live):
    """La descendance, pas les enfants directs : c'est la classe, pas le cas."""
    from oto_mcp.db import nodes as db_nodes

    a = _page("A")
    b = _page("B", parent_id=a["id"])
    c = _page("C", parent_id=b["id"])

    with pytest.raises(db_nodes.ParentCycle):
        db_nodes.move_page(a["id"], parent_id=c["id"])


def test_les_deplacements_LEGITIMES_passent_toujours(live):
    """La garde ne doit pas rétrécir ce qu'on sert. Trois déplacements honnêtes : vers
    une autre branche, vers la racine, et vers un descendant d'un FRÈRE."""
    from oto_mcp.db import nodes as db_nodes

    a = _page("A")
    b = _page("B", parent_id=a["id"])
    autre = _page("Autre")
    petit = _page("Petit", parent_id=autre["id"])

    assert db_nodes.move_page(b["id"], parent_id=autre["id"]) is True
    assert _parent_de(b["id"]) == autre["id"]

    assert db_nodes.move_page(b["id"], parent_id=None) is True
    assert _parent_de(b["id"]) is None

    assert db_nodes.move_page(b["id"], parent_id=petit["id"]) is True
    assert _parent_de(b["id"]) == petit["id"]


def test_un_noeud_inexistant_rend_toujours_False_et_ne_LEVE_pas(live):
    """`False` garde son sens d'origine — « ce nœud n'existe pas ». Le cycle lève ;
    confondre les deux rendrait le refus muet pour l'appelant."""
    from oto_mcp.db import nodes as db_nodes
    assert db_nodes.move_page(10**9, parent_id=None) is False


def _est_son_propre_ancetre(node_id):
    """Remonte les parents EN PYTHON, avec un ensemble de vus.

    Délibérément pas en SQL : le chemin de lecture est justement l'un des chemins
    qu'on corrige, et le mesurer avec lui ferait dépendre le constat de ce qu'on teste.
    """
    from oto_mcp.db._conn import _connect
    vus, courant = set(), node_id
    with _connect() as conn:
        while courant is not None:
            if courant in vus:
                return True
            vus.add(courant)
            ligne = conn.execute("SELECT parent_id FROM nodes WHERE id = %s",
                                 (courant,)).fetchone()
            courant = ligne["parent_id"] if ligne else None
    return False


def test_apres_un_move_l_arbre_ne_BOUCLE_pas(live):
    """L'INVARIANT, formulé sans nommer le refus.

    C'est la forme qui rougit sur le tronc pour la bonne raison : là-bas, `move_page`
    rendait `True` et l'arbre bouclait. Un test écrit uniquement en `pytest.raises`
    sur une exception qui n'existe pas encore rougirait, lui, sur un `AttributeError`
    — il dirait « le symbole manque », pas « l'arbre s'est refermé ».
    """
    from oto_mcp.db import nodes as db_nodes

    a = _page("A")
    b = _page("B", parent_id=a["id"])
    try:
        db_nodes.move_page(a["id"], parent_id=b["id"])
    except Exception:                   # noqa: BLE001 — le COMMENT est jugé ailleurs
        pass

    assert not _est_son_propre_ancetre(a["id"]), "l'arbre boucle après un move"
    assert not _est_son_propre_ancetre(b["id"])


# ── 2. La borne : ce qui est DÉJÀ en base ne fait plus tourner à vide ───────────

def test_supprimer_un_noeud_pris_dans_un_cycle_TERMINE(live):
    """Le cœur de l'incident. Sans la clause `CYCLE`, ce `DELETE` empile dans
    `pgsql_tmp` jusqu'au plafond du banc (en production : jusqu'au disque)."""
    from oto_mcp.db import nodes as db_nodes
    from oto_mcp.db._conn import _connect

    a = _page("A")
    b = _page("B", parent_id=a["id"])
    _boucle(a["id"], b["id"])          # A sous B, B sous A

    assert db_nodes.delete_page(a["id"]) is True

    with _connect() as conn:
        restants = conn.execute(
            "SELECT count(*) AS n FROM nodes WHERE id = ANY(%s)",
            ([a["id"], b["id"]],)).fetchone()["n"]
    assert restants == 0, "la suppression doit emporter les deux nœuds de la boucle"


def test_supprimer_emporte_les_BLOCS_des_noeuds_de_la_boucle(live):
    """La descente sert deux fois (blocs puis nœuds) : la borne doit tenir aux deux."""
    from oto_mcp.db import nodes as db_nodes
    from oto_mcp.db._conn import _connect

    a = _page("A", None)
    b = _page("B", parent_id=a["id"])
    from oto_mcp.db import blocks as db_blocks
    with _connect() as conn:
        db_blocks.write_node_blocks(conn, b["id"], "Un corps.")
    _boucle(a["id"], b["id"])

    db_nodes.delete_page(a["id"])

    with _connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM blocks WHERE node_id = ANY(%s)",
                            ([a["id"], b["id"]],)).fetchone()["n"] == 0


def test_supprimer_un_arbre_SAIN_emporte_toujours_toute_la_descendance(live):
    """La borne ne doit pas tronquer un arbre honnête — un `DELETE` tronqué laisserait
    des enfants accrochés à un identifiant disparu. C'est pourquoi la borne est la
    clause `CYCLE` et non un plafond de profondeur."""
    from oto_mcp.db import nodes as db_nodes
    from oto_mcp.db._conn import _connect

    ids, parent = [], None
    for i in range(40):                # bien au-delà des 12 du fil
        n = _page(f"N{i}", parent_id=parent)
        ids.append(n["id"])
        parent = n["id"]

    db_nodes.delete_page(ids[0])
    with _connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM nodes WHERE id = ANY(%s)",
                            (ids,)).fetchone()["n"] == 0


# ── 3. La lecture DIT le cycle, elle ne sert pas un fil qui oscille ─────────────

def test_la_remontee_du_fil_LEVE_sur_un_cycle(live):
    """Avant : douze maillons `A, B, A, B…` et un succès. Un cycle déjà en base était
    donc invisible à la lecture — c'est ça qu'on ferme."""
    from oto_mcp.db import node_view as db_view
    from oto_mcp.db import nodes as db_nodes

    a = _page("A")
    b = _page("B", parent_id=a["id"])
    _boucle(a["id"], b["id"])

    with pytest.raises(db_nodes.ParentCycle):
        db_view.ancestors_of(b["id"])


def test_la_remontee_ne_sert_JAMAIS_deux_fois_le_meme_maillon(live):
    """La formulation qui survit à un changement d'implémentation : quelle que soit la
    façon dont la lecture s'arrête, elle ne doit pas rendre un fil qui se répète."""
    from oto_mcp.db import node_view as db_view
    from oto_mcp.db import nodes as db_nodes

    a = _page("A")
    b = _page("B", parent_id=a["id"])
    _boucle(a["id"], b["id"])

    try:
        chaine = db_view.ancestors_of(b["id"])
    except db_nodes.ParentCycle:
        return                          # le dire, c'est la bonne réponse
    vus = [c["id"] for c in chaine]
    assert len(vus) == len(set(vus)), f"fil oscillant servi en silence : {vus}"


def test_un_fil_SAIN_est_toujours_servi_dans_le_bon_ordre(live):
    """Non-régression : la racine d'abord, le nœud en dernier, et rien en plus dans
    les maillons (`boucle` ne fuit pas dans la forme rendue)."""
    from oto_mcp.db import node_view as db_view

    a = _page("A")
    b = _page("B", parent_id=a["id"])
    c = _page("C", parent_id=b["id"])

    chaine = db_view.ancestors_of(c["id"])
    assert [x["id"] for x in chaine] == [a["id"], b["id"], c["id"]]
    assert "boucle" not in chaine[0] and "chemin" not in chaine[0]


# ── 4. La même classe, une table plus loin : l'arbre des `docs` ────────────────

def _projet():
    from oto_mcp.db import projects as db_p
    return db_p.create_project("org", "1", "P")


def test_docs_ranger_une_page_sous_sa_descendance_est_refuse(live):
    """`docs.parent_id` a une FK auto-référente — qui n'empêche AUCUN cycle : elle
    exige que la ligne visée existe, pas que l'arbre soit acyclique."""
    from oto_mcp.db import projects as db_p

    p = _projet()
    a = db_p.create_doc(p, "A")
    b = db_p.create_doc(p, "B", parent_id=a)

    with pytest.raises(db_p.DocParentCycle):
        db_p.move_doc(a, b)


def test_docs_le_refus_n_ecrit_rien(live):
    from oto_mcp.db import projects as db_p
    from oto_mcp.db._conn import _connect

    p = _projet()
    a = db_p.create_doc(p, "A")
    b = db_p.create_doc(p, "B", parent_id=a)
    with pytest.raises(db_p.DocParentCycle):
        db_p.move_doc(a, b)
    with _connect() as conn:
        assert conn.execute("SELECT parent_id FROM docs WHERE id = %s",
                            (a,)).fetchone()["parent_id"] is None


def test_docs_deplacer_vers_un_projet_refuse_aussi_le_cycle(live):
    from oto_mcp.db import projects as db_p

    p = _projet()
    a = db_p.create_doc(p, "A")
    b = db_p.create_doc(p, "B", parent_id=a)
    with pytest.raises(db_p.DocParentCycle):
        db_p.move_doc_to_project(a, p, b)


def test_docs_compter_la_descendance_TERMINE_sur_un_cycle(live):
    """`count_doc_descendants` est appelé sur le chemin de PRÉVISUALISATION d'une
    suppression : une lecture, donc le geste le plus banal qui soit."""
    from oto_mcp.db import projects as db_p
    from oto_mcp.db._conn import _connect

    p = _projet()
    a = db_p.create_doc(p, "A")
    b = db_p.create_doc(p, "B", parent_id=a)
    with _connect() as conn:
        conn.execute("UPDATE docs SET parent_id = %s WHERE id = %s", (b, a))

    assert db_p.count_doc_descendants(a) == 1


def test_docs_compter_la_descendance_d_un_arbre_sain_est_inchange(live):
    """Le compte EXCLUT toujours la page elle-même — la formule a changé d'amorce, pas
    de sens."""
    from oto_mcp.db import projects as db_p

    p = _projet()
    a = db_p.create_doc(p, "A")
    b = db_p.create_doc(p, "B", parent_id=a)
    db_p.create_doc(p, "C", parent_id=b)
    db_p.create_doc(p, "D", parent_id=a)

    assert db_p.count_doc_descendants(a) == 3
    assert db_p.count_doc_descendants(b) == 1
    assert db_p.count_doc_descendants(db_p.create_doc(p, "Seule")) == 0


def test_docs_supprimer_une_page_et_sa_descendance_TERMINE_sur_un_cycle(live):
    from oto_mcp.db import projects as db_p
    from oto_mcp.db._conn import _connect

    p = _projet()
    a = db_p.create_doc(p, "A")
    b = db_p.create_doc(p, "B", parent_id=a)
    with _connect() as conn:
        conn.execute("UPDATE docs SET parent_id = %s WHERE id = %s", (b, a))

    db_p.delete_doc(a)
    with _connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM docs WHERE id = ANY(%s)",
                            ([a, b],)).fetchone()["n"] == 0


# ── 5. Le cliquet : aucune récursion nue ne rentre dans `db/` ──────────────────

def test_aucune_recursion_sur_l_arbre_n_est_SANS_BORNE():
    """Le garde-fou de CLASSE, au grain de la fonction — pas du fichier.

    Une allowlist par fichier laisserait passer une seconde récursion nue ajoutée à
    côté d'une bornée. On relève donc chaque segment de source (constante de module ou
    fonction) qui contient `WITH RECURSIVE`, et on exige autant de clauses `CYCLE`.

    ⚠️ Ce test compte, il ne cherche pas un motif : une récursion qui n'est PAS sur un
    arbre auto-référent n'a rien à faire ici, et devra être nommée explicitement le
    jour où elle apparaîtra — mieux vaut un refus à expliquer qu'une boucle à découvrir
    en production.
    """
    import ast
    import pathlib

    import oto_mcp.db as pkg

    racine = pathlib.Path(pkg.__file__).parent
    releve, nus = [], []
    for fichier in sorted(racine.rglob("*.py")):
        source = fichier.read_text()
        if "WITH RECURSIVE" not in source:
            continue
        arbre = ast.parse(source)
        for noeud in arbre.body:        # segments de PREMIER niveau : constantes + defs
            seg = ast.get_source_segment(source, noeud) or ""
            n = seg.count("WITH RECURSIVE")
            if not n:
                continue
            nom = getattr(noeud, "name", None) or f"{fichier.name}:{noeud.lineno}"
            releve.append(f"{fichier.name}::{nom}")
            if seg.count("CYCLE ") < n:
                nus.append(f"{fichier.name}::{nom} ({n} récursion(s), "
                           f"{seg.count('CYCLE ')} clause(s) CYCLE)")

    assert releve, "aucune récursion relevée — le cliquet ne garde plus rien"
    assert not nus, "récursion sur l'arbre sans clause CYCLE : " + " ; ".join(nus)
