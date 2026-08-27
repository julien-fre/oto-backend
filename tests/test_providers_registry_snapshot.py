"""Le registre est une PROJECTION des déclarations — verrouillé ici.

Depuis que chaque connecteur déclare son entrée dans `oto_mcp/providers/<nom>.py`,
`providers/__init__.py` n'est plus qu'un agrégateur. Trois choses doivent le rester,
et aucune n'est visible à la lecture d'un diff d'ajout de connecteur :

1. **Le registre == la concaténation des déclarations.** Rien ne s'ajoute au
   registre autrement que par un module de déclaration (pas de `_c(…)` glissé dans
   l'agrégateur, pas d'entrée fabriquée à la volée).
2. **Un domicile, un seul.** Un fichier `providers/<nom>.py` sans ligne dans
   `_DECLARATIONS` est un connecteur qui dort invisible ; une ligne sans fichier
   casse l'import. Les deux sens sont couverts.
3. **L'ordre est déterministe.** Il est écrit à la main dans `_DECLARATIONS`, donc
   reproductible d'une machine à l'autre — jamais dérivé d'un `glob` (l'ordre d'un
   répertoire n'est pas un contrat). Il ne gouverne aucun calcul, mais il gouverne
   l'affichage (`status_for`, catalogue, primer de namespaces) : une réorganisation
   silencieuse déplacerait des lignes dans des surfaces servies.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

from oto_mcp import providers

_PKG = pathlib.Path(providers.__file__).parent
# Modules d'INFRASTRUCTURE du package (tout le reste est une déclaration).
_NON_DECLARATIONS = {"__init__", "_model"}


def _fichiers_de_declaration() -> set[str]:
    return {f.stem for f in _PKG.glob("*.py")} - _NON_DECLARATIONS


# --- 1. le registre est la concaténation des déclarations --------------------

def test_le_registre_est_la_concatenation_des_declarations():
    attendu = []
    for nom in providers._DECLARATIONS:
        mod = importlib.import_module(f"oto_mcp.providers.{nom}")
        attendu.append(mod.CONNECTOR)
    assert providers._REGISTRY_LIST == attendu, (
        "le registre agrégé diverge des `CONNECTOR` déclarés — une entrée est "
        "fabriquée ailleurs que dans son module de déclaration.")
    assert list(providers.REGISTRY.values()) == attendu


def test_aucune_entree_ne_nait_dans_lagregateur():
    """`_c(…)` ne s'APPELLE que dans un module de déclaration.

    L'agrégateur en garde l'import (surface publique historique du module) ; s'il
    l'appelait, l'entrée n'aurait plus de domicile et rouvrirait le littéral
    partagé que ce découpage supprime. Sonde AST, pas textuelle : un commentaire
    qui cite `_c(…)` n'est pas un appel."""
    arbre = ast.parse((_PKG / "__init__.py").read_text())
    appels = [n for n in ast.walk(arbre)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name) and n.func.id == "_c"]
    assert not appels, (
        f"`providers/__init__.py` instancie un connecteur (ligne "
        f"{appels[0].lineno}) : sa place est `providers/<nom>.py`.")


# --- 2. un domicile, un seul -------------------------------------------------

def test_chaque_declaration_a_exactement_un_domicile():
    fichiers = _fichiers_de_declaration()
    declares = set(providers._DECLARATIONS)

    orphelins = sorted(fichiers - declares)
    assert not orphelins, (
        f"{orphelins} : module(s) de déclaration absent(s) de `_DECLARATIONS` — "
        "le connecteur existe sur le disque et n'entre jamais au registre.")

    fantomes = sorted(declares - fichiers)
    assert not fantomes, f"{fantomes} : ligne(s) de `_DECLARATIONS` sans module."

    assert declares == set(providers.REGISTRY), (
        "les noms déclarés et les noms du registre divergent.")


def test_le_module_sappelle_comme_son_connecteur():
    """Sans ça, `providers/<nom>.py` cesse d'être une adresse : on ne peut plus
    aller au connecteur `x` sans lire les 90 fichiers. L'agrégateur le refuse déjà
    à l'import ; ce test le dit à qui lit."""
    for nom in providers._DECLARATIONS:
        mod = importlib.import_module(f"oto_mcp.providers.{nom}")
        assert mod.CONNECTOR.name == nom


def test_les_donnees_curees_vivent_avec_leur_connecteur():
    """Les maps curées sont INDEXÉES par l'agrégateur, pas écrites par lui : chaque
    clé vient donc forcément d'un module, et vise donc un connecteur réel. C'est ce
    qui rend impossible l'entrée morte (une clé sans connecteur, vécu avec
    `"whatsapp"` dans la map des éditeurs — un namespace, pas un connecteur)."""
    for map_ in (providers._CATEGORY_BY_CONNECTOR,
                 providers._PUBLISHER_BY_CONNECTOR,
                 providers._DESCRIPTION_BY_CONNECTOR,
                 providers._LOGO_DOMAIN_BY_CONNECTOR,
                 providers._SANS_LOGO_DE_MARQUE):
        assert not set(map_) - set(providers.REGISTRY)


# --- 3. l'ordre est déterministe ---------------------------------------------

def test_lordre_du_registre_est_celui_ecrit_a_la_main():
    assert [c.name for c in providers._REGISTRY_LIST] == list(providers._DECLARATIONS)
    assert len(set(providers._DECLARATIONS)) == len(providers._DECLARATIONS), (
        "doublon dans `_DECLARATIONS`.")


def test_lordre_ne_vient_pas_du_systeme_de_fichiers():
    """RATCHET. Un `glob` rendrait l'ordre dépendant du répertoire — reproductible
    sur une machine, différent sur une autre, et le catalogue servi bougerait sans
    qu'aucun diff ne le montre. L'ordre alphabétique est le symptôme à guetter."""
    ordre = list(providers._DECLARATIONS)
    assert ordre != sorted(ordre), (
        "l'ordre du registre est exactement l'ordre alphabétique : soit il a été "
        "dérivé d'un `glob`, soit il a été trié — dans les deux cas ce n'est plus "
        "l'ordre de lecture voulu.")


def test_lordre_survit_a_un_rechargement():
    """L'agrégation ne dépend d'aucun état d'import : recharger le module rend le
    MÊME ordre. (Un jour où l'agrégation lirait « ce qui a déjà été importé », ce
    test tomberait — c'est exactement la magie qu'on s'interdit.)"""
    module = importlib.reload(providers)
    assert list(module._DECLARATIONS) == list(providers._DECLARATIONS)
    assert [c.name for c in module._REGISTRY_LIST] == list(providers._DECLARATIONS)


def test_key_providers_suit_lordre_de_declaration():
    """`KEY_PROVIDERS` sert l'affichage (`status_for` le sérialise). Son ordre est
    donc celui du registre, filtré — pas un ordre à lui."""
    attendu = tuple(n for n in providers._DECLARATIONS
                    if providers.REGISTRY[n].keyed)
    assert providers.KEY_PROVIDERS == attendu
