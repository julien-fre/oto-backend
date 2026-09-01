"""Les refus qu'une capacité peut RÉELLEMENT rendre, par son propre chemin (#792).

Le cliquet des refus déclarés vérifiait qu'un code **existe dans le module** du
handler. Deux défauts en découlaient, et ils sont les deux faces d'un même axe manqué —
l'existence au lieu de l'ATTEIGNABILITÉ :

- une capacité pouvait déclarer un refus qu'elle ne rend **jamais** (un homonyme vivant
  dans le même fichier, levé par une autre capacité) : le contrat promet, le serveur ne
  suit pas, et le cliquet reste vert ;
- une capacité ne pouvait **pas** déclarer un refus qu'elle rend vraiment, s'il est levé
  **ailleurs** — le cas de la pose d'une clé, dont les refus de saisie remontent du
  coffre.

Ce module remonte le graphe d'appel depuis le handler et rend ce qui y est levable.

⚠️ **Ce qu'il ne sait pas faire, dit une fois pour toutes** : il ne juge pas si une
branche est *accessible* (une condition impossible reste « atteignable » ici), et il
s'arrête à `PROFONDEUR_MAX`. C'est un filet, pas une preuve — le rejeu d'un refus sur
la route servie reste le seul contrôle sans angle mort, et il vit dans
`tests/api/test_rest_contract_front_tiers.py`.

⚠️ **La résolution se fait sur les OBJETS, pas sur les imports lus dans le source** :
`getattr(module, nom)` dit ce que le code voit vraiment à l'exécution — un alias, un
ré-export ou un import relatif y sont déjà résolus. Lire les `import` du fichier
reviendrait à réimplémenter le résolveur de Python, et à s'en écarter au premier cas
tordu.
"""
from __future__ import annotations

import ast
import inspect
import re
import types
from typing import Optional

# Profondeur du graphe d'appel suivie depuis le handler. 4 suffit largement au dépôt :
# handler → helper d'autorisation → store → garde de saisie. Au-delà, le coût grimpe et
# la pertinence tombe (on atteindrait des utilitaires que personne ne considère comme
# une source de refus).
PROFONDEUR_MAX = 4

# Le paquet dont on accepte de suivre les fonctions. Suivre `psycopg` ou la stdlib
# n'apporterait rien et ferait exploser le parcours.
PAQUET = "oto_mcp"

REFUS = "AuthzDenied"

# Un code d'erreur est un IDENTIFIANT, pas une phrase. Sans ce filtre, le premier
# argument de n'importe quelle exception (« secret requis », « DATABASE_URL not
# set ») devenait un code acceptable, et la règle du relais ouvrait la porte à tout.
_FORME_DE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")


class Atteignables:
    """Ce qu'un chemin peut lever.

    - `codes` : les `AuthzDenied(<status>, "<code>")` **littéraux** ;
    - `statuts_relayes` : les statuts dont le code est CALCULÉ (`AuthzDenied(400,
      getattr(e, "code", …))`) — on ne peut pas savoir lequel sort, seulement qu'un
      code inconnu peut sortir sous ce statut ;
    - `codes_exceptions` : les premiers arguments littéraux des exceptions métier
      levées sur le chemin (`raise CredentialFieldsInvalid("empty_api_key", …)`), qui
      sont précisément ce qu'un relais transporte.
    """

    def __init__(self):
        self.codes: set[tuple[int, str]] = set()
        self.statuts_relayes: set[int] = set()
        self.codes_exceptions: set[str] = set()

    def accepte(self, status: int, code: str) -> bool:
        if (status, code) in self.codes:
            return True
        # Un relais ne dit pas QUEL code il transporte : on accepte alors ceux que les
        # exceptions du chemin savent porter. Sans cette règle, un refus réellement
        # servi resterait indéclarable — l'autre face du défaut.
        return status in self.statuts_relayes and code in self.codes_exceptions


def _resout(noeud: ast.AST, module: types.ModuleType) -> Optional[object]:
    """`f` ou `mod.f` → l'objet que le code voit vraiment, ou None."""
    if isinstance(noeud, ast.Name):
        return getattr(module, noeud.id, None)
    if isinstance(noeud, ast.Attribute) and isinstance(noeud.value, ast.Name):
        porteur = getattr(module, noeud.value.id, None)
        if isinstance(porteur, types.ModuleType):
            return getattr(porteur, noeud.attr, None)
    return None


def _suivable(cible, racine: str) -> bool:
    """Suit-on cette fonction ? Le paquet du produit, **ou le module de départ**.

    La seconde clause n'est pas un confort de test : elle dit la vraie règle — on suit
    le chemin du handler, où qu'il vive. Sans elle, un banc d'épreuve hors paquet ne
    serait pas parcouru, et le garde-fou ne pourrait être éprouvé que sur les
    capacités qu'il garde — c'est-à-dire sur une cible mouvante.
    """
    if not inspect.isfunction(cible):
        return False
    mod = getattr(cible, "__module__", "")
    return mod.startswith(PAQUET) or mod == racine


def _fonctions_appelees(noeud: ast.AST, module: types.ModuleType,
                        racine: str = "") -> list:
    """Les fonctions du paquet que ce corps met en marche.

    ⚠️ **Les fonctions PASSÉES EN ARGUMENT comptent**, et c'est indispensable ici :
    tout handler asynchrone de ce dépôt sort de la boucle par
    `run_in_threadpool(_le_vrai_travail, …)`. Ne suivre que `f(...)` s'arrêterait à la
    première ligne de la moitié des capacités — la plus grosse d'entre elles a été
    trouvée ainsi, avec trois refus réels jugés inatteignables.
    """
    out = []
    for n in ast.walk(noeud):
        if not isinstance(n, ast.Call):
            continue
        candidats = [n.func] + list(n.args) + [k.value for k in n.keywords]
        for c in candidats:
            cible = _resout(c, module)
            if _suivable(cible, racine):
                out.append(cible)
    return out


def _releve(noeud: ast.AST, acc: Atteignables) -> None:
    """Les refus levés DIRECTEMENT dans ce corps."""
    for n in ast.walk(noeud):
        if not isinstance(n, ast.Raise) or not isinstance(n.exc, ast.Call):
            continue
        nom = getattr(n.exc.func, "id", None) or getattr(n.exc.func, "attr", None)
        args = n.exc.args
        if nom == REFUS and len(args) >= 2 and isinstance(args[0], ast.Constant):
            statut = args[0].value
            if isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
                acc.codes.add((statut, args[1].value))
            else:
                acc.statuts_relayes.add(statut)
        elif nom and nom != REFUS and args and isinstance(args[0], ast.Constant):
            # Une exception métier dont le premier argument est un code : c'est la
            # matière qu'un relais transporte jusqu'au refus servi.
            if isinstance(args[0].value, str) and _FORME_DE_CODE.match(args[0].value):
                acc.codes_exceptions.add(args[0].value)


def atteignables(handler) -> Atteignables:
    """Ce que ce handler peut lever, lui et ce qu'il appelle."""
    acc = Atteignables()
    vus: set = set()
    racine = getattr(handler, "__module__", "")
    file = [(handler, 0)]
    while file:
        fn, profondeur = file.pop()
        cle = (getattr(fn, "__module__", ""), getattr(fn, "__qualname__", ""))
        if cle in vus or profondeur > PROFONDEUR_MAX:
            continue
        vus.add(cle)
        module = inspect.getmodule(fn)
        if module is None:
            continue
        try:
            corps = ast.parse(inspect.getsource(fn).lstrip())
        except (OSError, SyntaxError, IndentationError):
            continue
        _releve(corps, acc)
        for appelee in _fonctions_appelees(corps, module, racine):
            file.append((appelee, profondeur + 1))
    return acc
