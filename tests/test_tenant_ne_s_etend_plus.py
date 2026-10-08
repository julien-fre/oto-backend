"""CLIQUET « on n'étend plus le tenant » (convention du 08/10/2026, `docs/tenants.md`).

Le tenant existant est conservé tel quel ; on n'y AJOUTE plus rien — ni capacité, ni
route, ni table, ni colonne, ni rang de cascade, ni marque, ni réglage par tenant. Un
besoin qui semble appeler « par tenant » se traite par instance ou par org (§Convention
de `docs/tenants.md`, inventaire de ce qui pourrait partir : oto-backend#1167).

Ce test relève, à chaque passage, l'inventaire SERVI des surfaces « tenant » et le
compare à `tenant_inventaire_gele.txt`. Il mord dans UN sens : tout ce qui est relevé
doit déjà figurer au gel (servi ⊆ gelé). Un retrait passe toujours, sans toucher au
fichier ; retire quand même sa ligne dans le même commit, pour que le diff dise ce qui
est parti et que la chose ne puisse pas revenir sans se voir. Il n'y a pas de recette
de régénération, exprès : ajouter une ligne au gel, c'est revenir sur la convention,
ce qui se décide d'abord dans `docs/tenants.md`.

Les axes relevés — des structures, pas des chaînes trouvées au hasard dans le code :

- `capacite` : une capacité du registre (ADR 0009) dont la clé, le nom d'outil MCP ou
  un chemin REST nomme le tenant, ou dont la règle d'autorisation passe par le rôle
  d'admin de tenant (`_authz.TENANT_ADMIN_OF*`, cherché aussi dans les règles
  composées par opération) ;
- `op` : chaque opération d'une de ces capacités (une op neuve sur `oto_admin_tenant`
  est une capacité neuve) ;
- `niveau` : une valeur d'énumération qui nomme le tenant dans l'entrée d'une capacité
  quelconque — c'est ainsi qu'un rang de cascade se montre sur la surface servie ;
- `route` : une route REST montée (`api.routes.make_routes`) dont le chemin nomme le
  tenant, capacité ou non ;
- `declaration` : un champ de `tenancy.TenantIssuer`, ce que le registre porte PAR
  tenant (marque, chemins, préfixe, accès d'annuaire…) ;
- `table`, `colonne`, `domaine` : le schéma, relevé dans les ordres `CREATE TABLE` /
  `ALTER TABLE` que porte le code de `oto_mcp/` (fragments assemblés, DDL de boot,
  révisions Alembic) et dans les `_poser_domaine` du boot — une table qui nomme le
  tenant, toute colonne d'une telle table, toute colonne qui nomme le tenant ou qui
  référence `tenants`, et toute contrainte de domaine dont une valeur est `'tenant'`
  (un rang de cascade dans la base) ;
- `env` : une variable de l'inventaire d'environnement (`env_inventory.NOMS_FIXES`)
  dont le nom nomme le tenant.

Ce que ça n'attrape pas : un comportement « par tenant » qui ne passerait par aucune de
ces structures (une branche sur `org_tenant_slug` dans un handler existant, par
exemple). La convention vaut là aussi ; la revue le voit, pas ce test.
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib
import re
import sys
import typing

ROOT = pathlib.Path(__file__).resolve().parents[1]
GEL = pathlib.Path(__file__).resolve().parent / "tenant_inventaire_gele.txt"

# La racine du dépôt n'est pas dans `sys.path` en CI (pas de `__init__.py`), cf.
# `test_schema_assembly_frozen.py` : même normalisation du SQL que le gel du schéma.
sys.path.insert(0, str(ROOT))
from scripts.schema_gele import normaliser  # noqa: E402

_MOT = "tenant"


def _nomme(texte: str | None) -> bool:
    return bool(texte) and _MOT in texte.lower()


# ── Capacités, opérations, niveaux ────────────────────────────────────────────────

def _regles(regle, vues=None):
    """Les noms qualifiés de la règle d'autorisation ET des règles qu'elle compose
    (`ADMIN_BY_OP`, `BY_OP`, `*_OF_TARGET` referment leurs sous-règles en closure)."""
    vues = set() if vues is None else vues
    if id(regle) in vues:
        return
    vues.add(id(regle))
    if isinstance(regle, dict):
        for v in regle.values():
            yield from _regles(v, vues)
    elif isinstance(regle, (tuple, list)):
        for v in regle:
            yield from _regles(v, vues)
    elif callable(regle) and hasattr(regle, "__qualname__"):
        yield regle.__qualname__
        for cellule in getattr(regle, "__closure__", None) or ():
            try:
                contenu = cellule.cell_contents
            except ValueError:  # cellule vide : rien à composer
                continue
            yield from _regles(contenu, vues)


def _literaux(annotation):
    if typing.get_origin(annotation) is typing.Literal:
        yield from typing.get_args(annotation)
        return
    for argument in typing.get_args(annotation):
        yield from _literaux(argument)


def _champs(cap) -> dict:
    return getattr(cap.Input, "model_fields", None) or {}


def _liaisons_rest(cap) -> tuple:
    if not cap.rest:
        return ()
    return cap.rest if isinstance(cap.rest, tuple) else (cap.rest,)


def _releve_capacites() -> set[str]:
    from oto_mcp.capabilities import registry
    releve: set[str] = set()
    for cap in registry.CAPABILITIES:
        de_tenant = (_nomme(cap.key) or _nomme(cap.mcp)
                     or any(_nomme(b.path) for b in _liaisons_rest(cap))
                     or any(q.startswith("TENANT_ADMIN") for q in _regles(cap.authz)))
        if de_tenant:
            releve.add(f"capacite {cap.key}")
            if "op" in _champs(cap):
                releve |= {f"op {cap.key} {op}"
                           for op in _literaux(_champs(cap)["op"].annotation)}
        for nom, champ in _champs(cap).items():
            releve |= {f"niveau {cap.key}.{nom}={v}" for v in _literaux(champ.annotation)
                       if isinstance(v, str) and _nomme(v)}
    return releve


# ── Routes REST montées ───────────────────────────────────────────────────────────

class _FakeVerifier:
    """`make_routes` ne fait que capturer le verifier (cf. `test_api_routes_table_frozen`)."""


def _releve_routes() -> set[str]:
    from oto_mcp.api import routes as api_routes
    releve = set()
    for r in api_routes.make_routes(_FakeVerifier(), mcp_instance=None):
        methodes = sorted((getattr(r, "methods", None) or set()) - {"HEAD", "OPTIONS"})
        if _nomme(r.path):
            releve |= {f"route {m} {r.path}" for m in methodes}
    return releve


# ── Déclaration par tenant ────────────────────────────────────────────────────────

def _releve_declaration() -> set[str]:
    from oto_mcp import tenancy
    return {f"declaration {f.name}" for f in dataclasses.fields(tenancy.TenantIssuer)}


# ── Schéma ────────────────────────────────────────────────────────────────────────

_ORDRE_DDL = re.compile(r"^(CREATE|ALTER) TABLE\b")
_CREATE = re.compile(r"^CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*)\)", re.S)
_ALTER = re.compile(r"^ALTER TABLE (?:IF EXISTS )?(?:ONLY )?(\w+)\s+(.*)", re.S)
_ADD_COLUMN = re.compile(r"^ADD (?:COLUMN )?(?:IF NOT EXISTS )?(\w+)\b")
_DOMAINE = re.compile(r"(\w+) IN \(([^()]*)\)")
_HORS_COLONNE = {"CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "EXCLUDE", "LIKE"}


def _au_premier_niveau(corps: str) -> list[str]:
    """Découpe aux virgules de profondeur 0 (une colonne, une contrainte)."""
    morceaux, profondeur, courant = [], 0, []
    for c in corps:
        if c == "(":
            profondeur += 1
        elif c == ")":
            profondeur -= 1
        if c == "," and profondeur == 0:
            morceaux.append("".join(courant).strip())
            courant = []
        else:
            courant.append(c)
    morceaux.append("".join(courant).strip())
    return [m for m in morceaux if m]


def _ordres_ddl() -> list[str]:
    """Les `CREATE TABLE` / `ALTER TABLE` écrits en littéral dans `oto_mcp/`, hors
    docstrings et chaînes nues (de la prose, pas un ordre exécuté)."""
    ordres = []
    for fichier in sorted((ROOT / "oto_mcp").rglob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        prose = {id(n.value) for n in ast.walk(arbre) if isinstance(n, ast.Expr)}
        for n in ast.walk(arbre):
            if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in prose and re.search(r"\b(CREATE|ALTER) TABLE\b", n.value)):
                for ordre in normaliser(n.value).split(";"):
                    ordre = " ".join(ordre.split())
                    if _ORDRE_DDL.match(ordre):
                        ordres.append(ordre)
    return ordres


def _domaines_poses() -> set[str]:
    """Les `_poser_domaine(conn, table, nom, colonne, valeurs)` du boot."""
    releve = set()
    for fichier in sorted((ROOT / "oto_mcp").rglob("*.py")):
        for n in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
            if not (isinstance(n, ast.Call) and len(n.args) >= 5):
                continue
            nom = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if nom != "_poser_domaine":
                continue
            table, colonne, valeurs = n.args[1], n.args[3], n.args[4]
            if (isinstance(table, ast.Constant) and isinstance(colonne, ast.Constant)
                    and isinstance(valeurs, (ast.Tuple, ast.List))
                    and any(isinstance(v, ast.Constant) and v.value == _MOT
                            for v in valeurs.elts)):
                releve.add(f"domaine {table.value}.{colonne.value}")
    return releve


def _releve_schema(ordres: list[str] | None = None) -> set[str]:
    """`ordres` : par défaut ceux du code ; un banc passe les siens."""
    releve = _domaines_poses() if ordres is None else set()

    def colonne(table: str, definition: str) -> None:
        nom = definition.split()[0]
        if nom.upper() in _HORS_COLONNE:
            return
        if _nomme(table) or _nomme(nom) or re.search(r"\bREFERENCES tenants\b", definition):
            releve.add(f"colonne {table}.{nom}")

    for ordre in _ordres_ddl() if ordres is None else ordres:
        if (m := _CREATE.match(ordre)):
            table = m.group(1)
            if _nomme(table):
                releve.add(f"table {table}")
            for definition in _au_premier_niveau(m.group(2)):
                colonne(table, definition)
        elif (m := _ALTER.match(ordre)):
            table = m.group(1)
            for action in _au_premier_niveau(m.group(2)):
                if (a := _ADD_COLUMN.match(action)) and a.group(1).upper() not in _HORS_COLONNE:
                    colonne(table, action[len("ADD "):].removeprefix("COLUMN ")
                            .removeprefix("IF NOT EXISTS "))
        else:
            continue
        for nom, valeurs in _DOMAINE.findall(ordre):
            if f"'{_MOT}'" in valeurs:
                releve.add(f"domaine {table}.{nom}")
    return releve


# ── Inventaire d'environnement ────────────────────────────────────────────────────

def _releve_env() -> set[str]:
    from oto_mcp import env_inventory
    return {f"env {v.nom}" for v in env_inventory.NOMS_FIXES if _nomme(v.nom)}


def releve() -> set[str]:
    return (_releve_capacites() | _releve_routes() | _releve_declaration()
            | _releve_schema() | _releve_env())


def _gel() -> set[str]:
    return {ligne.strip() for ligne in GEL.read_text(encoding="utf-8").splitlines()
            if ligne.strip() and not ligne.lstrip().startswith("#")}


def test_le_tenant_ne_s_etend_plus():
    neuves = sorted(releve() - _gel())
    assert not neuves, (
        f"{len(neuves)} surface(s) « tenant » NEUVE(S) : {neuves}\n"
        "Convention du 08/10/2026 : on n'étend plus le tenant (docs/tenants.md, "
        "§Convention). Aucune capacité, route, table, colonne, rang de cascade, marque "
        "ni réglage par tenant ne s'ajoute. Un besoin « par tenant » se traite par "
        "instance (instance dédiée, réglage d'instance) ou par org. N'ajoute pas la "
        "ligne à tests/tenant_inventaire_gele.txt : c'est revenir sur la convention, "
        "ce qui se décide d'abord dans docs/tenants.md.")


def test_le_releve_voit_ce_qu_il_doit_voir():
    """L'instrument d'abord : un relevé vide passerait toujours. Chaque axe doit
    trouver au moins le noyau que l'épic #1167 garde, sinon c'est lui qui est cassé."""
    vu = releve()
    for attendu in ("capacite admin.tenant_console", "op admin.tenant_console disable",
                    "declaration issuer", "table tenants", "colonne tenants.slug",
                    "colonne orgs.tenant_id", "colonne orgs.suspended_tenant_id",
                    "env OTO_TENANT_PRIMAIRE_SLUG"):
        assert attendu in vu, f"le relevé ne voit plus {attendu!r} : l'axe est cassé"
    for axe in ("capacite", "op", "niveau", "route", "declaration", "table",
                "colonne", "domaine", "env"):
        assert any(l.startswith(axe + " ") for l in vu), f"axe {axe!r} vide"


def test_un_ajout_au_schema_serait_vu():
    """Le relevé du schéma sur des ordres écrits à la main : une colonne posée sur
    `tenants`, une colonne qui référence `tenants`, un rang ajouté à un domaine."""
    vu = _releve_schema([
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS palette JSONB",
        "CREATE TABLE IF NOT EXISTS quotas (id BIGSERIAL PRIMARY KEY, "
        "porteur BIGINT REFERENCES tenants(id), CHECK (niveau IN ('org', 'tenant')))",
    ])
    assert {"colonne tenants.palette", "colonne quotas.porteur",
            "domaine quotas.niveau"} <= vu
