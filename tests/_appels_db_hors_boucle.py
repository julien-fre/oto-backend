"""Balayage STATIQUE : quelles fonctions `async def` de `oto_mcp` atteignent la base SANS
quitter la boucle ?

Un `async def` tourne SUR la boucle d'événements (le serveur est mono-loop, cf.
`docs/event-loop-perf.md`). S'il appelle — directement, ou par une chaîne de fonctions
SYNCHRONES — `db._connect()` / `db._connect_autocommit()`, la boucle attend la base :
tout le serveur gèle le temps de la requête (gel de prod du 21/09/2026, ~140 s).

Ce que le balayage suit : les appels par nom (`f()`), par module importé (`m.f()`,
`db.f()` — le package `db` aplatit ses sous-modules), par `self.m()` dans la même classe,
par fermeture locale. Ce qu'il coupe : un `await` (le callee async est SON PROPRE site),
`run_in_threadpool(f, …)` / `asyncio.to_thread(f, …)` (`f` est passée, jamais appelée ici),
un `lambda` (c'est le corps qu'on confie à un thread).

Ce qu'il ne voit PAS : la répartition dynamique (`getattr`, callbacks, méthodes d'un objet
dont on ne connaît pas la classe). C'est le rôle de la garde d'EXÉCUTION dans
`db._conn._connect()` — les deux se recouvrent, aucune ne suffit seule.

La clé d'un site est `module::qualname` — exactement ce que la garde d'exécution lit dans
la pile (`f_globals["__name__"]` + `co_qualname`).
"""
from __future__ import annotations

import ast
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
PAQUET = RACINE / "oto_mcp"

# Les seuls points d'entrée de la base : le pool passe par eux, rien d'autre.
PUITS = {"_connect", "_connect_autocommit"}
# `db/__init__.py` aplatit ces modules (`db.<symbole>`), cf. son `_MODULES`.
DB_APLATIS = {
    "_conn", "_schema", "_init", "users", "tenants", "unipile", "connector_grants",
    "connector_instances", "grants", "keys", "usage", "platform_instructions",
    "visibility", "emails", "google", "datastore", "projects", "doc_grants", "tokens",
    "upload_tokens", "billing", "billing_invoices", "guides", "legal", "search",
    "aux_embed", "datastore_embed", "run_thread", "runner_jobs", "runner_triggers",
    "runner_hooks", "runner_fleets", "runner_fleets_preneur", "runner_workers",
    "journal_calls",
}


def _module_name(path: Path) -> str:
    rel = path.relative_to(RACINE).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _Fn:
    __slots__ = ("module", "qualname", "node", "is_async", "classe", "parent")

    def __init__(self, module, qualname, node, classe, parent):
        self.module, self.qualname, self.node = module, qualname, node
        self.is_async = isinstance(node, ast.AsyncFunctionDef)
        self.classe, self.parent = classe, parent

    @property
    def key(self) -> str:
        return f"{self.module}::{self.qualname}"


def _propres_appels(node):
    """Les `Call` du scope PROPRE de `node` — sans descendre dans une fonction imbriquée
    ni un lambda (ce sont d'autres scopes, ou du code confié à un thread)."""
    pile = list(ast.iter_child_nodes(node))
    while pile:
        n = pile.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(n, ast.Call):
            yield n
        pile.extend(ast.iter_child_nodes(n))


# `current_user_sub_from_token` ne lit la base que TANT QUE le drain d'alias est armé
# (`alias_drain_armed()`, `auth/hooks.py`) : hors de ce régime transitoire, elle est pure.
# Les sites qui ne touchent la base que par elle sont donc DORMANTS — réels le jour où le
# drain s'arme (10 allers-retours PG par `tools/call`, mesurés le 09/09), pas aujourd'hui.
IDENTITE_SOUS_DRAIN = {("oto_mcp.auth.hooks", "current_user_sub_from_token")}


class Balayage:
    def __init__(self, coupes=()):
        self._coupes = set(coupes)
        self.fns: dict[tuple[str, str], _Fn] = {}
        self.modules: set[str] = set()
        self.imports: dict[str, dict[str, tuple]] = {}   # module -> nom lié -> ("mod", M) | ("fn", M, nom)
        self._indexer()
        self._reach: dict[tuple[str, str], bool] = {}

    # ---- index ----------------------------------------------------------------
    def _indexer(self):
        arbres = {}
        for chemin in sorted(PAQUET.rglob("*.py")):
            mod = _module_name(chemin)
            self.modules.add(mod)
            arbres[mod] = (chemin, ast.parse(chemin.read_text(encoding="utf-8")))
        for mod, (chemin, arbre) in arbres.items():
            self.imports[mod] = self._imports(mod, chemin, arbre)
            self._collecter(mod, arbre, "", None, None)

    def _collecter(self, mod, noeud, prefixe, classe, parent):
        for n in ast.iter_child_nodes(noeud):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qn = f"{prefixe}{n.name}"
                fn = _Fn(mod, qn, n, classe, parent)
                self.fns[(mod, qn)] = fn
                self._collecter(mod, n, f"{qn}.<locals>.", classe, fn)
            elif isinstance(n, ast.ClassDef):
                self._collecter(mod, n, f"{prefixe}{n.name}.", n.name, parent)
            elif isinstance(n, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                self._collecter(mod, n, prefixe, classe, parent)

    def _imports(self, mod, chemin, arbre):
        est_pkg = chemin.name == "__init__.py"
        base = mod.split(".") if est_pkg else mod.split(".")[:-1]
        lies: dict[str, tuple] = {}
        for n in ast.walk(arbre):
            if isinstance(n, ast.ImportFrom):
                if n.level:
                    cible = base[: len(base) - (n.level - 1)]
                    if n.module:
                        cible = cible + n.module.split(".")
                    cible = ".".join(cible)
                else:
                    cible = n.module or ""
                if not cible.startswith("oto_mcp"):
                    continue
                for a in n.names:
                    nom = a.asname or a.name
                    sous = f"{cible}.{a.name}"
                    if sous in self.modules:
                        lies[nom] = ("mod", sous)
                    else:
                        lies[nom] = ("fn", cible, a.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith("oto_mcp") and a.asname:
                        lies[a.asname] = ("mod", a.name)
        return lies

    # ---- résolution -----------------------------------------------------------
    def _resoudre_nom(self, mod, cible, nom):
        """(module, qualname) d'une fonction `nom` vue depuis le module `cible`."""
        if cible == "oto_mcp.db":
            if nom in PUITS:
                return ("oto_mcp.db._conn", nom)
            for sous in DB_APLATIS:
                if (f"oto_mcp.db.{sous}", nom) in self.fns:
                    return (f"oto_mcp.db.{sous}", nom)
            return None
        if (cible, nom) in self.fns:
            return (cible, nom)
        # `from .pkg import f` où `f` est ré-exporté par le `__init__` : un cran
        lie = self.imports.get(cible, {}).get(nom)
        if lie and lie[0] == "fn" and lie[1] != cible:
            return self._resoudre_nom(cible, lie[1], lie[2])
        # Façade à ré-export PLAT (`org_store`, comme `db`) : `org_store.f` est défini dans
        # l'un des sous-modules que son `__init__` importe.
        for lie in self.imports.get(cible, {}).values():
            if lie[0] == "mod" and lie[1].startswith(cible + ".") and (lie[1], nom) in self.fns:
                return (lie[1], nom)
        return None

    def cibles(self, fn: _Fn, appel: ast.Call):
        f = appel.func
        mod = fn.module
        if isinstance(f, ast.Name):
            # fermeture locale (le scope courant, puis les englobants)
            scope = fn
            while scope is not None:
                cand = self.fns.get((mod, f"{scope.qualname}.<locals>.{f.id}"))
                if cand:
                    return [cand]
                scope = scope.parent
            if f.id in PUITS and mod.startswith("oto_mcp.db"):
                return [("PUITS", f.id)]
            r = self._resoudre_nom(mod, mod, f.id)
            if r:
                if r[0] == "oto_mcp.db._conn" and r[1] in PUITS:
                    return [("PUITS", r[1])]
                return [self.fns[r]] if r in self.fns else []
            lie = self.imports.get(mod, {}).get(f.id)
            if lie and lie[0] == "fn":
                if lie[2] in PUITS and lie[1].startswith("oto_mcp.db"):
                    return [("PUITS", lie[2])]
                r = self._resoudre_nom(mod, lie[1], lie[2])
                if r and r in self.fns:
                    return [self.fns[r]]
            return []
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            racine, attr = f.value.id, f.attr
            if racine in ("self", "cls") and fn.classe:
                q = fn
                while q.parent is not None:
                    q = q.parent
                cand = self.fns.get((mod, f"{fn.classe}.{attr}"))
                return [cand] if cand else []
            lie = self.imports.get(mod, {}).get(racine)
            if lie and lie[0] == "mod":
                if lie[1] == "oto_mcp.db" and attr in PUITS:
                    return [("PUITS", attr)]
                if lie[1] == "oto_mcp.db._conn" and attr in PUITS:
                    return [("PUITS", attr)]
                r = self._resoudre_nom(mod, lie[1], attr)
                if r and r[0] == "oto_mcp.db._conn" and r[1] in PUITS:
                    return [("PUITS", r[1])]
                if r and r in self.fns:
                    return [self.fns[r]]
        return []

    # ---- atteinte de la base --------------------------------------------------
    def atteint_la_base(self, fn: _Fn, _en_cours=None) -> bool:
        """`fn` (synchrone) atteint-elle un puits par des appels synchrones ?"""
        cle = (fn.module, fn.qualname)
        if cle in self._coupes:
            return False
        if cle in self._reach:
            return self._reach[cle]
        _en_cours = _en_cours or set()
        if cle in _en_cours:
            return False
        _en_cours.add(cle)
        rep = False
        for appel in _propres_appels(fn.node):
            for c in self.cibles(fn, appel):
                if isinstance(c, tuple):
                    rep = True
                elif not c.is_async and self.atteint_la_base(c, _en_cours):
                    rep = True
            if rep:
                break
        self._reach[cle] = rep
        return rep

    def sites_bloquants(self) -> dict[str, list[str]]:
        """`async def` qui atteignent la base dans la boucle → les appels fautifs."""
        sortie: dict[str, list[str]] = {}
        for fn in self.fns.values():
            if not fn.is_async or fn.module.startswith("oto_mcp.db"):
                continue
            fautifs = []
            for appel in _propres_appels(fn.node):
                for c in self.cibles(fn, appel):
                    if isinstance(c, tuple) or (not c.is_async and self.atteint_la_base(c)):
                        fautifs.append(f"l.{appel.lineno} {ast.unparse(appel.func)}")
            if fautifs:
                sortie[fn.key] = fautifs
        return dict(sorted(sortie.items()))


def sites_dormants() -> set[str]:
    """Les sites qui ne touchent la base QUE par l'identité sous drain d'alias."""
    return set(Balayage().sites_bloquants()) - set(
        Balayage(coupes=IDENTITE_SOUS_DRAIN).sites_bloquants())


if __name__ == "__main__":       # exploration à la main : python tests/_appels_db_hors_boucle.py
    for cle, appels in Balayage().sites_bloquants().items():
        print(cle, len(appels))
        for a in appels:
            print("   ", a)
