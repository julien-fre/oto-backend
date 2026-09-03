"""Les deux commandes de la bascule se lisent à UN endroit, et n'y coûtent rien.

Séparer les deux mécanismes n'a de valeur que si la séparation tient : tant qu'un site
peut relire `OTO_MCP_TENANT_MIGRATION_ISS` dans son coin, désarmer le rapprochement ne
désarme que celui qu'on a sous les yeux — et le prochain lot rouvrira le couplage sans
que personne ne le voie, puisque rien ne l'interdit.

Deux cliquets, donc :

1. **la commande ne se lit que dans `tenant_migration`** — partout ailleurs, on demande
   au prédicat, on ne relit pas l'environnement ;
2. **le module d'armement n'importe rien d'autre que `os`** — les deux prédicats sont
   consultés en tête de CHAQUE appel (REST et MCP) ; ce chemin est celui qui a gelé la
   production le 2026-07-02. Une décision d'armement qui irait chercher une ligne en
   base, un client HTTP ou un cache poserait ce travail sur le trajet chaud de tout le
   trafic. Le cliquet le rend impossible sans le dire.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_RACINE = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"
_SEAM = _RACINE / "tenant_migration.py"
_COMMANDE = "OTO_MCP_TENANT_MIGRATION_ISS"


def _lectures_d_env(arbre: ast.AST) -> list[str]:
    """Les noms d'environnement LUS par ce module — `os.environ.get(x)`,
    `os.getenv(x)`, `os.environ[x]`. Les mentions en commentaire ou en prose de
    docstring ne comptent pas : on cherche qui CONSULTE la commande, pas qui en parle.

    ⚠️ **L'argument est résolu à travers les constantes du module.** La 1re version ne
    reconnaissait que le littéral (`os.environ.get("X")`) — elle était donc aveugle à
    `_COMMANDE = "X"` … `os.environ.get(_COMMANDE)`, c'est-à-dire à la forme qu'a
    justement prise le module d'armement, et à celle qu'un site prendrait pour se
    redonner sa propre lecture. Un cliquet aveugle à l'indirection ne garde rien.
    """
    constantes = {
        c.targets[0].id: c.value.value
        for c in ast.walk(arbre)
        if isinstance(c, ast.Assign) and len(c.targets) == 1
        and isinstance(c.targets[0], ast.Name)
        and isinstance(c.value, ast.Constant) and isinstance(c.value.value, str)
    }

    def _litteral(noeud) -> str | None:
        if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
            return noeud.value
        if isinstance(noeud, ast.Name):
            return constantes.get(noeud.id)
        return None

    noms: list[str] = []
    for n in ast.walk(arbre):
        if isinstance(n, ast.Call) and n.args:
            f, cible = n.func, None
            if isinstance(f, ast.Attribute) and f.attr in ("get", "getenv"):
                base = f.value
                if isinstance(base, ast.Attribute) and base.attr == "environ":
                    cible = _litteral(n.args[0])     # os.environ.get(X)
                elif isinstance(base, ast.Name) and base.id in ("os", "environ"):
                    cible = _litteral(n.args[0])     # os.getenv(X) / environ.get(X)
            if cible:
                noms.append(cible)
        if isinstance(n, ast.Subscript):
            v = n.value
            if isinstance(v, ast.Attribute) and v.attr == "environ":
                cible = _litteral(n.slice)           # os.environ[X]
                if cible:
                    noms.append(cible)
    return noms


def _modules():
    for f in sorted(_RACINE.rglob("*.py")):
        yield f, ast.parse(f.read_text(encoding="utf-8"), filename=str(f))


def test_la_commande_de_bascule_ne_se_lit_QUE_dans_le_module_d_armement():
    coupables = sorted(
        str(f.relative_to(_RACINE.parent))
        for f, arbre in _modules()
        if f != _SEAM and _COMMANDE in _lectures_d_env(arbre)
    )
    assert not coupables, (
        f"{coupables} relit {_COMMANDE} directement. Les deux mécanismes de la bascule "
        "(rapprochement d'identités / drain d'alias) ne se commandent pas de la même "
        "façon et ne s'arrêtent pas ensemble : demander `email_merge_armed(iss)` ou "
        "`alias_drain_armed()` à oto_mcp/tenant_migration.py, pas relire l'env."
    )


def test_le_module_d_armement_lit_bien_la_commande():
    """⚠️ Sans ce contrôle, le cliquet ci-dessus reste vert si la commande disparaît
    partout — « personne ne la lit » se confondrait avec « elle est centralisée »."""
    assert _COMMANDE in _lectures_d_env(ast.parse(_SEAM.read_text(encoding="utf-8")))


def test_armer_ne_coute_rien_sur_le_chemin_chaud():
    """Les deux prédicats sont consultés en tête de chaque appel : le module qui les
    porte ne doit dépendre que de la lecture d'environnement."""
    arbre = ast.parse(_SEAM.read_text(encoding="utf-8"))
    importes = set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.Import):
            importes.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            importes.add((n.module or "").split(".")[0])
    assert importes <= {"os", "__future__"}, (
        f"tenant_migration importe {sorted(importes - {'os', '__future__'})} : décider "
        "si un mécanisme est armé se fait sur l'environnement seul. Tout le reste "
        "(base, réseau, cache) atterrirait en tête de CHAQUE appel REST et MCP."
    )


@pytest.mark.parametrize("porte,attendu", [
    ("oto_mcp/api/base.py", "alias_drain_armed"),
    ("oto_mcp/auth/hooks.py", "alias_drain_armed"),
    ("oto_mcp/db/users.py", "email_merge_armed"),
])
def test_chaque_porte_consulte_le_predicat_de_SON_mecanisme(porte, attendu):
    """Le drain et le rapprochement ne se confondent pas : une porte qui demanderait
    le mauvais prédicat serait recouplée en silence — et le test ci-dessus, lui,
    resterait vert (elle ne relirait pas l'env pour autant)."""
    src = (_RACINE.parent / porte).read_text(encoding="utf-8")
    autre = "email_merge_armed" if attendu == "alias_drain_armed" else "alias_drain_armed"
    assert attendu in src, f"{porte} ne consulte pas {attendu}"
    assert autre not in src, f"{porte} consulte {autre}, qui commande l'AUTRE mécanisme"
