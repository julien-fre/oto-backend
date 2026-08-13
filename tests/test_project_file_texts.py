"""Le texte extrait d'un fichier : sa table et sa file (#298, barreau 2).

Contre un vrai PostgreSQL, comme les lots de conversion : ce barreau EST du SQL (une
jointure qui définit une file, un `ON CONFLICT` qui compte les tentatives, un CASCADE
qui nettoie), et une relecture ne prouve ni qu'une file se vide, ni qu'elle ne se
repasse pas le même fichier pour toujours.

Ce qui est gardé ici :

1. **l'absence de ligne EST la file** — un fichier sans texte extrait est à traiter,
   sans drapeau à poser à l'upload ni état à réconcilier ;
2. **un refus s'écrit** — sinon le fichier revient à chaque passage : c'est ce qui
   distingue « on a regardé, ce format n'est pas supporté » de « pas encore regardé » ;
3. **les statuts terminaux ne reviennent jamais**, et l'échec imprévu revient un
   nombre BORNÉ de fois ;
4. **rien ne survit à son fichier** — pas de tâche fantôme après suppression.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE, le VRAI `init_db()`, le vrai pool."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_pft_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
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


@pytest.fixture
def projet(live):
    from oto_mcp import db
    p = db.create_project("org", "1", "Projet " + uuid.uuid4().hex[:6])
    return int(p["id"] if isinstance(p, dict) else p)


def _fichier(projet: int, nom: str = "doc.pdf") -> int:
    from oto_mcp import db
    f = db.add_project_file(projet, f"s3/{uuid.uuid4().hex}", nom, mime="application/pdf")
    return int(f["id"])


def _ids_en_attente() -> set:
    from oto_mcp import db
    return {r["id"] for r in db.files_pending_extraction(limit=100)}


# ── la file ──────────────────────────────────────────────────────────────────

def test_a_fresh_file_is_pending_without_any_flag(projet):
    """L'absence de ligne EST la file : rien n'est posé à l'upload, donc rien ne peut
    diverger. Un backfill est gratuit — les fichiers déjà déposés y sont déjà."""
    fid = _fichier(projet)
    assert fid in _ids_en_attente()


def test_an_extracted_file_leaves_the_queue(projet):
    from oto_mcp import db
    fid = _fichier(projet)
    db.save_extracted_text(fid, status="ok", text="le contenu du document", pages=3)

    assert fid not in _ids_en_attente()
    got = db.get_extracted_text(fid)
    assert got["status"] == "ok" and got["pages"] == 3
    assert got["extracted_text"] == "le contenu du document"


@pytest.mark.parametrize("statut", ["unsupported", "encrypted", "empty",
                                    "too_large", "rejected_dtd"])
def test_a_refusal_is_recorded_and_never_comes_back(projet, statut):
    """⚠️ Le point de conception du barreau. Un refus DOIT s'écrire : sans ligne, le
    fichier reviendrait à chaque passage du worker, pour un travail qui ne réussira
    jamais. Et le statut nommé est ce qui permet à l'interface de dire « format non
    supporté » plutôt que « en cours »."""
    from oto_mcp import db
    fid = _fichier(projet, "image.png")
    db.save_extracted_text(fid, status=statut, detail="raison dite")

    assert fid not in _ids_en_attente(), f"{statut} est terminal, il ne se retente pas"
    assert db.get_extracted_text(fid)["status"] == statut


def test_only_an_unexpected_failure_is_retried_and_not_forever(projet):
    """L'échec IMPRÉVU peut venir d'un fichier tronqué à l'upload : il mérite une
    seconde chance. Pas une file qui se repasse le même fichier pour toujours."""
    from oto_mcp import db
    fid = _fichier(projet)

    db.save_extracted_text(fid, status="failed", detail="PdfReadError")
    assert fid in _ids_en_attente(), "un échec imprévu se retente"

    for _ in range(db.MAX_EXTRACT_ATTEMPTS):
        db.save_extracted_text(fid, status="failed", detail="PdfReadError")

    assert fid not in _ids_en_attente(), "mais un nombre BORNÉ de fois"
    assert db.get_extracted_text(fid)["attempts"] >= db.MAX_EXTRACT_ATTEMPTS


def test_a_retry_that_succeeds_leaves_the_queue(projet):
    """Le cas heureux de la reprise : le second passage réussit, le fichier sort."""
    from oto_mcp import db
    fid = _fichier(projet)
    db.save_extracted_text(fid, status="failed", detail="tronqué ?")
    db.save_extracted_text(fid, status="ok", text="finalement lisible", pages=1)

    assert fid not in _ids_en_attente()
    assert db.get_extracted_text(fid)["status"] == "ok"


def test_saving_twice_updates_instead_of_duplicating(projet):
    """`file_id` est la clé : une seconde extraction remplace, elle n'empile pas."""
    from oto_mcp import db
    fid = _fichier(projet)
    db.save_extracted_text(fid, status="ok", text="première lecture")
    db.save_extracted_text(fid, status="ok", text="seconde lecture")

    got = db.get_extracted_text(fid)
    assert got["extracted_text"] == "seconde lecture"
    assert got["attempts"] == 2


# ── rien ne survit à son fichier ─────────────────────────────────────────────

def test_deleting_the_file_takes_its_text_with_it(projet):
    """CASCADE : aucune ligne d'extraction ne survit à son fichier, donc aucune tâche
    fantôme ne revient dans la file — et aucun contenu ne reste en base après une
    suppression demandée par l'utilisateur."""
    from oto_mcp import db
    fid = _fichier(projet)
    db.save_extracted_text(fid, status="ok", text="contenu à ne pas conserver")

    db.delete_project_file(fid)

    assert db.get_extracted_text(fid) is None
    assert fid not in _ids_en_attente()


def test_the_cascade_is_carried_by_the_constraint_not_by_code(projet):
    """Le même invariant un cran plus haut, et vérifié là où il vit VRAIMENT.

    Un projet ne se supprime pas par le code (il s'archive — `db.delete_project`
    n'existe pas, contrairement à ce que ce test supposait d'abord). Ce qui protège
    ici est donc la CONTRAINTE : `project_file_texts.file_id` référence
    `project_files` en CASCADE, qui référence lui-même `projects` en CASCADE. On
    l'exerce par un DELETE direct — la seule façon de prouver la contrainte plutôt
    qu'un chemin applicatif qui l'imite."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    fid = _fichier(projet)
    db.save_extracted_text(fid, status="ok", text="contenu")

    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = %s", (projet,))

    assert db.get_extracted_text(fid) is None
    assert db.get_project_file(fid) is None


# ── l'état non encore regardé se distingue du refus ──────────────────────────

def test_never_looked_at_is_not_the_same_as_refused(projet):
    """`None` (jamais regardé) ≠ un statut de refus. L'interface doit pouvoir dire
    « en cours » dans un cas et « non supporté » dans l'autre — c'est toute la raison
    d'un statut nommé plutôt que d'un booléen."""
    from oto_mcp import db
    fid = _fichier(projet)
    assert db.get_extracted_text(fid) is None

    db.save_extracted_text(fid, status="unsupported", detail="format « png » non supporté")
    assert db.get_extracted_text(fid)["status"] == "unsupported"
