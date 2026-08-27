#!/usr/bin/env python3
"""Garde-fou mécanique : un `except` large qui ne dit RIEN est refusé.

**Le refus est bruyant, la divergence est muette.** Un `except Exception` qui ne
re-lève pas, ne journalise pas et ne rend pas de refus nommé transforme une panne en
succès : l'appelant reçoit une valeur de repli et croit avoir été servi. L'inventaire
du 2026-08-27 (`docs/silences-2026-08-27.md`) en a compté 333 dont dix produisaient un
défaut cher — un jeton API non porté, un fichier « privé » resté public, un compte
servi sous son ancienne identité.

Corriger dix sites ne tient pas : l'inventaire re-dérive. Ce script est le forçage.

## Ce qui compte comme « parlant »

Un handler passe s'il fait **au moins une** de ces trois choses, n'importe où dans son
corps (y compris dans un `if` imbriqué) :

1. **`raise`** — il propage, ou il traduit en une erreur de son domaine ;
2. **un appel de logger** — `logger.warning(...)`, `_log.exception(...)` : le nom de
   base doit être un logger reconnu (`LOGGER_NAMES`) et la méthode un niveau de
   `logging` (`LOG_METHODS`). Un `print` ne compte pas ;
3. **un refus NOMMÉ rendu à l'appelant** — `return json_error(request, 400, "…")`. Les
   fabriques sont DÉCLARÉES ci-dessous, pas devinées : un `return JSONResponse(...)`
   nu ne dit pas s'il porte un refus ou un succès, et un garde-fou qui devine finit
   par se tromper dans le sens rassurant.

## L'échappatoire, et pourquoi elle est nominative

`# noqa: SILENT — <raison>` sur la ligne du `except`, **ou juste au-dessus**, à
l'indentation du `except` :

    try:
        risque()
    # noqa: SILENT — fail-open de visibilité, backstop dur au call-time
    except Exception:
        pass

Les deux placements valent. Le second a été retenu pour les 168 sites existants : une
raison utile ne tient pas en fin de ligne (elles allaient jusqu'à 191 caractères), et
à l'indentation du `except` elle s'y rattache sans ambiguïté.

La raison est **obligatoire** : c'est elle qui distingue un silence DÉLIBÉRÉ
(fail-open de visibilité, fail-closed de signature, best-effort de journal — les 207
verdicts A de l'inventaire) d'un silence oublié, et une dette DÉCLARÉE (les 115
verdicts C, qui devront rendre leur échec visible) d'une dette contractée sans le
dire. Un `# noqa: SILENT` nu est refusé au même titre qu'un silence : sinon
l'échappatoire devient le chemin par défaut.

## Portée

`oto_mcp/` uniquement — le code servi. Les tests ont le droit d'avaler ce qu'ils
provoquent eux-mêmes.

Usage : `python -m scripts.lint_silences [chemin]` (défaut `oto_mcp`). Sortie 1 si
un silence non déclaré subsiste. Le garde-fou est exercé par
`tests/test_no_silent_except.py`, qui prouve aussi qu'il MORD.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from typing import Iterator, NamedTuple

# Noms de base acceptés pour un logger. Volontairement courts et explicites : un
# `self.log.info()` d'un objet quelconque n'est pas une trace de service.
LOGGER_NAMES = frozenset({"logger", "log", "_log", "_logger", "logging", "LOG"})
LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception",
                         "critical"})

# Fabriques de refus DÉCLARÉES : rendre l'une d'elles, c'est refuser en nommant la
# cause. Toute autre valeur de retour laisse le handler muet aux yeux du garde-fou.
ERROR_FACTORIES = frozenset({"json_error", "_json_error"})

# `# noqa: SILENT — <raison>` : le tiret peut être cadratin, demi-cadratin ou simple,
# et la raison doit porter au moins quelques caractères utiles.
NOQA = re.compile(r"#\s*noqa:[^#\n]*\bSILENT\b\s*[—–:-]\s*(?P<raison>\S.*)$")


class Silence(NamedTuple):
    path: pathlib.Path
    lineno: int
    source: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}: {self.source.strip()}"


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """`except:`, `except Exception:` ou `except BaseException:` — y compris dans un
    tuple (`except (Exception, OSError):`)."""
    t = handler.type
    if t is None:
        return True
    noms = t.elts if isinstance(t, ast.Tuple) else [t]
    return any(isinstance(n, ast.Name) and n.id in ("Exception", "BaseException")
               for n in noms)


def _appelle_un_logger(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in LOG_METHODS:
        return False
    base = node.func.value
    # `logging.getLogger(__name__).exception(...)` autant que `logger.exception(...)` :
    # on remonte les attributs ET les appels intermédiaires jusqu'au nom de base.
    while isinstance(base, (ast.Attribute, ast.Call)):
        base = base.value if isinstance(base, ast.Attribute) else base.func
    return isinstance(base, ast.Name) and base.id in LOGGER_NAMES


def _rend_un_refus_nomme(node: ast.AST) -> bool:
    if not isinstance(node, ast.Return) or node.value is None:
        return False
    # `return _json_error(...)` mais aussi `return None, _json_error(...)` — la forme
    # `(valeur, erreur)` des lecteurs de requête est un refus tout autant.
    candidats = (node.value.elts if isinstance(node.value, ast.Tuple) else [node.value])
    for call in candidats:
        if not isinstance(call, ast.Call):
            continue
        f = call.func
        nom = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if nom in ERROR_FACTORIES:
            return True
    return False


def _parle(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if _appelle_un_logger(node) or _rend_un_refus_nomme(node):
            return True
    return False


def _declare(lignes: list[str], lineno: int) -> bool:
    """Un `# noqa: SILENT — <raison>` sur la ligne du `except`, ou juste au-dessus."""
    for i in (lineno - 1, lineno - 2):
        if 0 <= i < len(lignes) and NOQA.search(lignes[i]):
            return True
    return False


def scanner(racine: pathlib.Path) -> Iterator[Silence]:
    fichiers = sorted(racine.rglob("*.py")) if racine.is_dir() else [racine]
    for path in fichiers:
        texte = path.read_text(encoding="utf-8")
        lignes = texte.splitlines()
        arbre = ast.parse(texte, filename=str(path))
        for node in ast.walk(arbre):
            if not isinstance(node, ast.ExceptHandler) or not _is_broad(node):
                continue
            if _parle(node) or _declare(lignes, node.lineno):
                continue
            yield Silence(path, node.lineno, lignes[node.lineno - 1])


def main(argv: list[str]) -> int:
    racine = pathlib.Path(argv[1] if len(argv) > 1 else "oto_mcp")
    silences = list(scanner(racine))
    if not silences:
        print(f"lint_silences: aucun silence non déclaré sous {racine}/")
        return 0
    print(f"lint_silences: {len(silences)} silence(s) non déclaré(s) sous {racine}/\n",
          file=sys.stderr)
    for s in silences:
        print(f"  {s}", file=sys.stderr)
    print(
        "\nUn `except` large doit faire l'une de ces trois choses : re-lever, "
        "journaliser (logger.warning/error/exception), ou rendre un refus nommé "
        "(json_error). Si le silence est DÉLIBÉRÉ, il se déclare sur la ligne du "
        "`except` :\n\n    except Exception:  # noqa: SILENT — <pourquoi ce silence "
        "est voulu>\n\nLa raison est obligatoire : c'est elle qui distingue un choix "
        "d'un oubli.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
