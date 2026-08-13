"""Créer un tableau en DOUBLON refuse, et le refus dit pourquoi.

Bug servi en production par le découpage (#325) : `create_datastore_namespace` a été
déplacée dans son module, mais le nom `psycopg` — qu'elle n'employait que dans sa
clause `except` — vivait dans les globals du monolithe. Une clause `except` ne
s'évalue qu'au moment de l'exception : le boot restait vert, la suite complète aussi,
et le défaut attendait le seul chemin qui l'emprunte.

Conséquence : un doublon rendait une erreur interne au lieu du refus « existe déjà »,
et surtout la dérivation automatique d'un nom libre (`base-1`, `base-2`…) — le cas
NOMINAL quand on duplique un projet — cassait à la première collision.

La leçon dépasse le découpage : **la suite ne couvre pas une clause `except` que rien
n'exerce.** Ce test manquait déjà au monolithe ; il y aurait attrapé le même défaut si
quelqu'un avait retiré l'import. Et le garde-fou qui suit ferme la classe entière
plutôt que ce cas-là : un nom utilisé mais jamais défini ne doit plus attendre son
chemin d'exécution pour se signaler.
"""
from __future__ import annotations

import ast
import builtins
import contextlib

import psycopg
import pytest


class _Conn:
    def execute(self, *a, **k):
        raise psycopg.errors.UniqueViolation("duplicate key value violates unique …")


@contextlib.contextmanager
def _fake_connect():
    yield _Conn()


def test_a_duplicate_is_refused_with_its_reason(monkeypatch):
    """Le chemin qui rendait une erreur interne."""
    from oto_mcp.db import datastore_ns as ns
    monkeypatch.setattr(ns, "_connect", _fake_connect)
    monkeypatch.setattr(ns, "upsert_user", lambda *a, **k: None)

    with pytest.raises(ValueError) as e:
        ns.create_datastore_namespace("user", "u1", "vivier")
    assert "vivier" in str(e.value) and "existe déjà" in str(e.value)


def test_the_store_turns_it_into_its_own_refusal(monkeypatch):
    """L'appelant compte dessus pour dériver un nom libre : c'est ce contrat-là qui
    cassait, pas seulement le libellé de l'erreur."""
    from oto_mcp.db import datastore_ns as ns
    from oto_mcp.datastore import DatastorePg, NamespaceExists
    monkeypatch.setattr(ns, "_connect", _fake_connect)
    monkeypatch.setattr(ns, "upsert_user", lambda *a, **k: None)

    s = DatastorePg("u-1")
    monkeypatch.setattr(s, "_default_owner", lambda: ("user", "u-1"))
    with pytest.raises(NamespaceExists):
        s.create_namespace("vivier")


# --- le garde-fou qui ferme la classe ----------------------------------------------

_MODULES = [
    "oto_mcp/db/paths.py", "oto_mcp/db/query.py", "oto_mcp/db/rowlock.py",
    "oto_mcp/db/datastore_ns.py", "oto_mcp/db/datastore.py",
    "oto_mcp/datastore_columns.py", "oto_mcp/datastore_errors.py",
    "oto_mcp/datastore_schema_ops.py", "oto_mcp/datastore.py",
    "oto_mcp/datastore_schema.py",
]


def _noms_absents(chemin: str) -> list:
    """Noms LUS par le module sans y être définis ni importés.

    Volontairement grossier — il ne suit pas les portées imbriquées et ne juge que le
    niveau module. C'est suffisant pour ce qu'il vise : un nom hérité des globals d'un
    fichier qu'on vient de scinder. Un faux positif se lit en une seconde ; le défaut
    qu'il attrape a coûté une erreur interne en production."""
    arbre = ast.parse(open(chemin).read(), chemin)
    definis = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}
    for n in ast.walk(arbre):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                definis.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definis.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            definis.add(n.id)
        elif isinstance(n, ast.arg):
            definis.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            definis.add(n.name)
        elif isinstance(n, ast.comprehension):
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    definis.add(t.id)
        elif isinstance(n, ast.Global):
            definis.update(n.names)
    return sorted({n.id for n in ast.walk(arbre)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                   and n.id not in definis})


@pytest.mark.parametrize("module", _MODULES)
def test_no_module_reads_a_name_it_never_got(module):
    absents = _noms_absents(module)
    assert not absents, (
        f"{module} lit {', '.join(absents)} sans l'avoir importé ni défini — "
        f"un nom hérité d'un fichier scindé, qui n'échoue qu'au chemin qui l'emprunte")


def test_the_guard_bites():
    """Un garde-fou se prouve en lui présentant l'anomalie qu'il prétend attraper."""
    import tempfile, os
    fd, p = tempfile.mkstemp(suffix=".py")
    try:
        os.write(fd, b"def f():\n    try:\n        pass\n"
                     b"    except psycopg.errors.UniqueViolation:\n        pass\n")
        os.close(fd)
        assert _noms_absents(p) == ["psycopg"]
    finally:
        os.unlink(p)
