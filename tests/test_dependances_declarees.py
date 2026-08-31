"""Ce que le code IMPORTE, le projet le DÉCLARE.

Le 31/08/2026, deux pannes de CI d'affilée, même cause racine : le dépôt s'appuyait
sur des paquets qu'il n'a jamais demandés, présents seulement parce qu'un tiers les
tirait. Le SDK MCP a renommé sa classe d'erreur ; puis un autre tiers a cessé de
fournir `httpx`. Dans les deux cas la CI est tombée **sur toutes les branches à la
fois**, pendant que les environnements de développement — plus anciens — restaient
verts. Six PR prêtes bloquées, aucune en cause.

Une dépendance non déclarée n'est pas une économie : c'est un pari sur les choix d'un
tiers. `requests` portait les flux OAuth, `httpx` le client de paiement.

⚠️ Ce test résout le nom du PAQUET depuis le nom du MODULE (`packages_distributions`)
plutôt que de les supposer identiques : `docx` vient de `python-docx`,
`googleapiclient` de `google-api-python-client`. Un garde-fou qui crie à tort finit
ignoré — et il aurait cinq faux positifs ici.
"""
from __future__ import annotations

import ast
import importlib.metadata as meta
import pathlib
import sys
import tomllib

RACINE = pathlib.Path(__file__).resolve().parent.parent

# Modules fournis par une dépendance qu'on ne peut pas nommer autrement : ils
# arrivent par un EXTRA d'un paquet déclaré, et le résolveur ne remonte pas jusque-là.
_PAR_UN_EXTRA = {
    "prefab_ui",      # fastmcp[apps] — MCP Apps (SEP-1865)
    "mcp",            # le SDK, derrière fastmcp ; sa façade est `oto_mcp/mcp_errors`
    "oto",            # oto-core, pinné sur un tag git
}


def _declares() -> set[str]:
    proj = tomllib.loads((RACINE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    brut = list(proj.get("dependencies", []))
    for v in proj.get("optional-dependencies", {}).values():
        brut += list(v)
    out = set()
    for d in brut:
        nom = d.split("[")[0].split(">")[0].split("<")[0].split("=")[0].split("@")[0]
        out.add(nom.strip().lower().replace("_", "-"))
    return out


def _paquet_de(module: str) -> set[str]:
    """Le ou les paquets qui fournissent ce module, tels que l'environnement les
    connaît. Vide si le module n'est pas installé — on ne conclut alors rien."""
    return {p.lower().replace("_", "-")
            for p in meta.packages_distributions().get(module, [])}


def _modules_tiers_importes() -> dict[str, str]:
    stdlib, locaux = set(sys.stdlib_module_names), {"oto_mcp", "tests"}
    vus: dict[str, str] = {}
    for f in (RACINE / "oto_mcp").rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mod = None
            if isinstance(n, ast.Import):
                mod = n.names[0].name.split(".")[0]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                mod = n.module.split(".")[0]
            if mod and mod not in stdlib and mod not in locaux:
                vus.setdefault(mod, str(f.relative_to(RACINE)))
    return vus


def test_tout_module_tiers_importe_est_declare():
    declares, manquants = _declares(), []
    for module, exemple in sorted(_modules_tiers_importes().items()):
        if module in _PAR_UN_EXTRA:
            continue
        paquets = _paquet_de(module)
        if not paquets:
            continue                      # non installé ici : on ne conclut pas
        # Le nom du MODULE compte aussi : `fastmcp` est déclaré tel quel mais fourni
        # par `fastmcp-slim` (l'extra `[apps]` en tire la variante). Exiger le nom du
        # paquet réel ferait crier le garde-fou sur une dépendance parfaitement
        # déclarée — et un garde-fou qui crie à tort finit ignoré.
        if not (paquets & declares) and module.lower().replace("_", "-") not in declares:
            manquants.append(f"{module} (paquet {'/'.join(sorted(paquets))}) — {exemple}")
    assert not manquants, (
        "modules importés par le code SERVI mais absents de `pyproject.toml` :\n  "
        + "\n  ".join(manquants)
        + "\n→ les déclarer. Ils ne sont présents que parce qu'un tiers les tire "
        "aujourd'hui ; le jour où il cesse, la panne est totale et silencieuse "
        "jusqu'à la CI.")


def test_les_deux_clients_http_sont_declares():
    """Nommément, parce que ce sont eux qui ont cassé : `requests` porte les flux
    OAuth et la Management API, `httpx` le client de paiement, l'extraction de
    fichiers et l'email."""
    declares = _declares()
    for paquet in ("requests", "httpx"):
        assert paquet in declares, f"`{paquet}` n'est plus déclaré"


def test_les_dependances_STRUCTURANTES_ont_un_plafond():
    """Un plancher sans plafond n'est pas une déclaration, c'est un pari quotidien.

    Le 31/08/2026, trois pannes de CI en une soirée, toutes sur la même mécanique :
    `>=X` sans borne haute fait installer la DERNIÈRE version au moment où la
    vérification tourne. Le SDK a renommé sa classe d'erreur, un tiers a cessé de
    fournir `httpx`, `fastmcp` a réorganisé ses modules. À chaque fois : rouge sur
    toutes les branches à la fois, vert sur les postes — installés plus tôt, donc
    plus anciens. **La vérification dépendait du jour où elle tournait.**

    Le plafond ne gèle rien : il rend la montée de version DÉLIBÉRÉE — on l'essaie,
    on lance la suite, on borne plus haut. C'est la différence entre choisir et subir.

    ⚠️ La liste est courte À DESSEIN : seulement ce dont la STRUCTURE nous traverse,
    pas tout l'arbre. Une bibliothèque dont on n'utilise que quelques fonctions
    stables n'a pas besoin d'être bornée, et tout borner ferait un projet qu'on ne
    met plus jamais à jour.
    """
    STRUCTURANTES = {"fastmcp"}          # son organisation interne est notre API
    proj = tomllib.loads((RACINE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    sans_plafond = []
    for d in proj.get("dependencies", []):
        nom = d.split("[")[0].split(">")[0].split("<")[0].split("=")[0].split("@")[0]
        if nom.strip().lower() in STRUCTURANTES and "<" not in d:
            sans_plafond.append(d)
    assert not sans_plafond, (
        "dépendance structurante sans borne haute :\n  " + "\n  ".join(sans_plafond)
        + "\n→ poser un plafond. Sans lui, la CI installe la dernière version publiée "
        "au moment où elle tourne, et une réorganisation amont casse toutes les "
        "branches en même temps.")
