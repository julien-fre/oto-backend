#!/usr/bin/env python3
"""L'arbre POUSSÉ s'importe-t-il ? — le garde-fou de la référence sans son objet.

Le 02/09/2026, `oto_mcp/billing.py` importait `oto_mcp.billing_grants` : l'import
était dans le commit, le fichier n'y était pas. Il n'existait que sur le disque de
son auteur. Toute la suite échouait à la collecte, la préproduction était sautée,
et plus rien ne pouvait partir en production.

⚠️ Ce mode de défaillance est INVISIBLE POUR SON AUTEUR et visible pour tous les
autres : son répertoire de travail complète le commit, donc chez lui tout s'importe.
Deux sessions ont mesuré en même temps et conclu l'inverse, chacune ayant raison sur
ce qu'elle regardait. Il naît d'une bonne pratique — le staging sélectif, imposé
parce que plusieurs sessions écrivent dans le même checkout — et rien, au moment du
commit, ne dit qu'on vient de pousser un import sans son fichier.

D'où la seule parade qui tienne : NE JAMAIS MESURER UN RÉPERTOIRE DE TRAVAIL.
Ce script matérialise l'arbre depuis git (`git archive`), l'importe depuis cet
emplacement propre, et VÉRIFIE POUR CHAQUE MODULE que ce qui vient d'être importé
sort bien de là — un `oto_mcp` résolu vers le checkout de l'opérateur (installation
éditable, PYTHONPATH hérité) rendrait exactement le faux vert qu'on veut tuer.

Usage :
    python scripts/arbre-importable.py [<ref>]      # défaut : HEAD

    # avant de pousser, depuis le checkout :
    python scripts/arbre-importable.py

Sorties :
    0 — l'arbre du commit s'importe entièrement.
    1 — il ne s'importe pas : le message nomme le geste qui manque.
    2 — RIEN N'A PU ÊTRE JUGÉ (archive illisible, arbre incohérent, mauvais tree
        importé). Jamais un vert : un contrôle qui n'a pas pu s'exécuter échoue.

Les dépendances doivent être installées (`pip install -e .`) : ce script juge le
CODE DU DÉPÔT, pas la résolution des paquets tiers.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

# Le paquet servi. C'est lui qui boote en production ; c'est donc lui qu'on importe.
PAQUET = "oto_mcp"


def _git(*args: str, cwd: str | pathlib.Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _abandon(titre: str, detail: str) -> None:
    """Sortie 2 — on n'a rien jugé, et on le DIT. Jamais un succès déguisé."""
    print(f"::error title={titre}::{detail}")
    print(f"\nRIEN N'A ÉTÉ JUGÉ : {detail}", file=sys.stderr)
    sys.exit(2)


def materialiser(ref: str, racine_depot: pathlib.Path) -> pathlib.Path:
    """Extrait l'arbre de `ref` dans un répertoire neuf. Le commit, rien que le commit."""
    cible = pathlib.Path(tempfile.mkdtemp(prefix="arbre-importable-"))
    archive = cible / "arbre.tar"
    try:
        with archive.open("wb") as sortie:
            subprocess.run(
                ["git", "archive", "--format=tar", ref],
                cwd=racine_depot,
                check=True,
                stdout=sortie,
                stderr=subprocess.PIPE,
            )
        arbre = cible / "arbre"
        arbre.mkdir()
        subprocess.run(["tar", "-xf", str(archive), "-C", str(arbre)], check=True)
    except subprocess.CalledProcessError as e:
        erreur = (e.stderr or b"").decode(errors="replace") if e.stderr else str(e)
        _abandon(
            "Arbre du commit illisible",
            f"`git archive {ref}` a échoué — impossible de matérialiser le commit. {erreur.strip()[:300]}",
        )
    archive.unlink()
    return arbre


def modules_du(arbre: pathlib.Path) -> list[str]:
    """Tous les modules du paquet servi, dans l'ordre d'import le plus stable."""
    noms = []
    for chemin in sorted((arbre / PAQUET).rglob("*.py")):
        relatif = chemin.relative_to(arbre)
        nom = ".".join(relatif.with_suffix("").parts)
        noms.append(nom[: -len(".__init__")] if nom.endswith(".__init__") else nom)
    return sorted(set(noms))


def isoler(arbre: pathlib.Path) -> None:
    """Faire de l'arbre matérialisé la SEULE source possible pour le paquet servi.

    ⚠️ `sys.path.insert(0, arbre)` ne suffit pas, et c'est le piège qui a rendu la
    première version de ce contrôle inerte. Une installation éditable pose un
    chercheur dans `sys.meta_path` qui mappe `oto_mcp` → le checkout de l'opérateur ;
    `PathFinder` gagne pour le paquet racine (donc `oto_mcp.__file__` a l'air juste),
    mais un sous-module ABSENT de l'arbre lui échappe et retombe sur ce chercheur —
    qui le trouve, chez l'auteur. Le fichier jamais commité est alors importé sans
    bruit, et le garde-fou déclare vert le commit qui a cassé le tronc.
    """
    for module_charge in [k for k in sys.modules if k == PAQUET or k.startswith(PAQUET + ".")]:
        del sys.modules[module_charge]
    sys.meta_path[:] = [
        chercheur
        for chercheur in sys.meta_path
        if not getattr(chercheur, "__module__", "").startswith("__editable__")
    ]
    sys.path.insert(0, str(arbre))
    os.environ.setdefault("OTO_ARBRE_IMPORTABLE", "1")


def _fichier_fautif(arbre: pathlib.Path, tb) -> str | None:
    """Le dernier cadre de pile qui appartient à NOTRE arbre : celui qui référence."""
    dernier = None
    for cadre in traceback.extract_tb(tb):
        try:
            chemin = pathlib.Path(cadre.filename).resolve()
        except OSError:
            continue
        if chemin.is_relative_to(arbre):
            dernier = f"{chemin.relative_to(arbre)}:{cadre.lineno}"
    return dernier


def _candidats(manquant: str) -> list[str]:
    base = manquant.replace(".", "/")
    return [f"{base}.py", f"{base}/__init__.py"]


def _dans_le_commit(chemin: str, arbre: pathlib.Path) -> bool:
    """Le fichier est-il dans le commit JUGÉ ?

    ⚠️ Se poser la question à `git ls-files` répondrait pour le HEAD du checkout, pas
    pour la ref jugée : éprouvé sur le vrai commit fautif, le contrôle rougissait bien
    mais annonçait « le fichier EST suivi par git » — vrai aujourd'hui, faux dans le
    commit qui cassait. L'arbre matérialisé EST la ref : c'est lui qu'on interroge.
    """
    return (arbre / chemin).exists()


def _objet_manquant(exc: BaseException) -> str | None:
    """Le module que l'import cherchait — sous les TROIS formes que prend l'échec.

    `import p.x` et `from p.x import y` lèvent ModuleNotFoundError(name='p.x').
    Mais `from p import x` — la forme qui a cassé le tronc — lève un ImportError
    « cannot import name » dont `.name` vaut le PAQUET, pas le module absent : classée
    naïvement, elle ressort en « le module explose à l'import » et renvoie le lecteur
    chercher une panne d'environnement. `name_from` porte le nom, mais n'existe qu'à
    partir de 3.12 : sur le plancher 3.10, on relit le message.
    """
    if isinstance(exc, ModuleNotFoundError) and exc.name:
        return exc.name
    if isinstance(exc, ImportError) and exc.name:
        depuis = getattr(exc, "name_from", None)
        if depuis is None:
            trouve = re.search(r"cannot import name '([^']+)' from '([^']+)'", str(exc))
            if trouve and trouve.group(2) == exc.name:
                depuis = trouve.group(1)
        if depuis:
            return f"{exc.name}.{depuis}"
    return None


def raconter(echec, arbre: pathlib.Path) -> str:
    """Le message d'échec doit nommer LE GESTE. « ImportError » renvoie chercher chez soi."""
    module, exc, tb = echec
    ou = _fichier_fautif(arbre, tb) or f"{module} (à l'import)"

    manquant = _objet_manquant(exc)
    if manquant:
        if manquant == PAQUET or manquant.startswith(PAQUET + "."):
            attendus = _candidats(manquant)
            presents = [c for c in attendus if _dans_le_commit(c, arbre)]
            if presents:
                return (
                    f"{ou} référence « {manquant} ». Le fichier {presents[0]} EST dans le commit, "
                    f"mais le module reste introuvable à l'import — nom exporté absent du module, "
                    f"ou `packages.find` du pyproject à vérifier."
                )
            return (
                f"{ou} référence « {manquant} », QUI N'EST PAS DANS CE COMMIT.\n"
                f"    Aucun de ces fichiers n'y figure : {', '.join(attendus)}.\n"
                f"    ⇒ L'as-tu ajouté au commit ? Il existe probablement sur ton disque, et nulle part ailleurs :\n"
                f"       chez toi tout s'importe, pour tous les autres l'arbre est cassé.\n"
                f"       git add {attendus[0]} && git commit --amend --no-edit"
            )
        if manquant == "oto" or manquant.startswith("oto."):
            return (
                f"{ou} référence « {manquant} », absent de l'oto-core installé.\n"
                f"    ⇒ Le connecteur est-il livré par le tag épinglé dans pyproject.toml ? "
                f"Taguer oto-core, puis bumper le pin ici."
            )
        return (
            f"{ou} référence « {manquant} », qui n'est ni dans le dépôt ni installé.\n"
            f"    ⇒ Dépendance tierce non déclarée : l'ajouter aux `dependencies` du pyproject.toml."
        )

    return (
        f"{ou} : le module existe mais EXPLOSE à l'import — "
        f"{type(exc).__name__}: {str(exc)[:200]}\n"
        f"    ⇒ Un import ne doit rien exiger de l'environnement (base, réseau, secret)."
    )


def main() -> int:
    ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    try:
        racine_depot = pathlib.Path(_git("rev-parse", "--show-toplevel").strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        _abandon("Hors dépôt git", "impossible de trouver la racine du dépôt : ce contrôle part du COMMIT, pas d'un répertoire.")

    arbre = materialiser(ref, racine_depot)
    try:
        modules = modules_du(arbre)

        # Plancher : ce qui va être jugé doit être EXACTEMENT ce que la ref contient.
        # Un contrôle qui ne parcourt rien rendrait un verdict propre — et le pire des
        # faux verts est celui d'un instrument qui n'a rien regardé.
        # ⚠️ La référence est `ls-tree <ref>` (le COMMIT) et non `ls-files` (l'INDEX) :
        # comparer à l'index ferait échouer le contrôle en local dès qu'un fichier est
        # `git add`é sans être encore commité — un « je n'ai pas pu juger » imaginaire.
        dans_la_ref = {
            l for l in _git("ls-tree", "-r", "--name-only", ref, "--", PAQUET,
                            cwd=racine_depot).splitlines()
            if l.endswith(".py")
        }
        extraits = {
            str(p.relative_to(arbre)) for p in (arbre / PAQUET).rglob("*.py")
        } if (arbre / PAQUET).is_dir() else set()
        if not extraits:
            _abandon(
                "Aucun module à juger",
                f"l'arbre de {ref} ne contient aucun fichier sous {PAQUET}/ — rien n'a été parcouru.",
            )
        if extraits != dans_la_ref:
            _abandon(
                "Arbre matérialisé infidèle",
                f"l'archive porte {len(extraits)} modules, {ref} en contient {len(dans_la_ref)} — "
                f"ce qui allait être jugé n'est pas le commit.",
            )

        isoler(arbre)

        echecs = []
        vus_dans_sys_modules: set[str] = set()
        for nom in modules:
            try:
                importlib.import_module(nom)
            except BaseException as exc:  # noqa: BLE001 — on RACONTE tout, y compris un SystemExit
                echecs.append((nom, exc, exc.__traceback__))
            # ⚠️ LA garde qui empêche le faux vert, et elle balaie TOUT ce qui vient
            # d'entrer dans sys.modules — pas seulement le module demandé. Première
            # version de ce script : `oto_mcp.billing` sortait bien de l'arbre, mais son
            # `from . import billing_grants` retombait sur l'installation éditable et
            # résolvait le fichier manquant depuis le checkout de l'opérateur. Le
            # contrôle passait au VERT sur le commit qui avait cassé le tronc.
            for cle in set(sys.modules) - vus_dans_sys_modules:
                vus_dans_sys_modules.add(cle)
                if cle != PAQUET and not cle.startswith(PAQUET + "."):
                    continue
                fichier = getattr(sys.modules[cle], "__file__", None)
                if fichier and not pathlib.Path(fichier).resolve().is_relative_to(arbre):
                    _abandon(
                        "Mauvais arbre importé",
                        f"« {cle} » s'est résolu vers {fichier}, hors de l'arbre matérialisé — "
                        f"ce contrôle mesurerait un répertoire de travail, pas le commit.",
                    )

        print(f"{len(modules)} modules de {PAQUET}/ importés depuis l'arbre de {ref}.")
        if not echecs:
            print(f"✓ L'arbre de {ref} s'importe entièrement — aucune référence sans son objet.")
            return 0

        # Un objet manquant fait tomber tous ses dépendants : 116 modules rouges pour
        # UNE ligne, le 02/09. On compte les CAUSES, et on ne les raconte qu'une fois —
        # sinon le geste à faire se noie dans sa propre cascade.
        recits: list[str] = []
        for echec in echecs:
            recit = raconter(echec, arbre)
            if recit not in recits:
                recits.append(recit)

        print()
        print(
            f"::error title=Référence poussée sans son objet::"
            f"{len(recits)} référence(s) manquante(s) dans le commit, "
            f"{len(echecs)} module(s) de {PAQUET}/ n'en reviennent pas. "
            f"{recits[0].splitlines()[0]}"
        )
        print(
            f"✗ {len(echecs)} module(s) de {PAQUET}/ ne s'importent pas depuis le commit {ref}, "
            f"pour {len(recits)} cause(s) :\n"
        )
        for recit in recits:
            print(f"  • {recit}\n")
        print(
            "Ce que ce contrôle mesure : l'arbre TEL QU'IL EST POUSSÉ, extrait de git dans un\n"
            "emplacement propre. Si tout s'importe chez toi, c'est le symptôme même du défaut :\n"
            "ton répertoire de travail complète le commit."
        )
        return 1
    finally:
        shutil.rmtree(arbre.parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
