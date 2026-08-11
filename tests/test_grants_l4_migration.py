"""Lot L4 (blueprint ADR 0053) — les tables du modèle d'accès existent, rien ne les touche.

Le lot ne livre que deux tables VIDES, et c'est sa réussite : le socle arrive avant
son premier usage, ce qui rendra la bascule (L5) faisable connecteur par connecteur
au lieu d'un grand soir. Trois choses portent son risque, et aucune n'est vérifiable
par de la vigilance :

1. **L'ORDRE.** `grant_counters` référence `grants(id)` : déclarée avant elle dans
   `_SCHEMA`, la FK échoue sur une base VIERGE (PostgreSQL crée les tables dans
   l'ordre du DDL). Même panne que #151 sur `orgs`, et que le seed-avant-colonne de
   L1.
2. **L'INDEX DE COMPTAGE, non partiel.** Le banc L0 l'a mesuré : 73,8 ms sans lui,
   0,035 ms avec — ×2000, sur le chemin chaud de tout appel compté, dans un serveur
   mono-loop. Le geste qui le casserait n'est pas de le supprimer (ça se verrait),
   c'est de l'« harmoniser » avec l'index de résolution en lui ajoutant
   `WHERE revoked_at IS NULL` : la lecture de D7 compte les arêtes ARCHIVÉES, donc
   un index partiel ne peut plus la servir, et les 74 ms reviennent EN SILENCE.
3. **L'INTENTION.** Personne ne lit ni n'écrit ces tables. Le test ne l'interdit pas
   pour toujours — il force à ce que le premier usage soit un acte délibéré (retirer
   le test dans le même commit), pas un effet de bord.

Ces tests sont STATIQUES : ils portent sur des ordres et des formes, là où un test
SQL exigerait un PostgreSQL et ne dirait rien de plus. Le SQL lui-même a été exercé
contre un vrai PostgreSQL 17 (conteneur jetable, `init_db()` réel) au moment du lot.
"""
from __future__ import annotations

import pathlib
import re

_DB = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp" / "db"
_SCHEMA_SRC = (_DB / "_schema.py").read_text(encoding="utf-8")


def test_grants_is_declared_before_grant_counters():
    grants = _SCHEMA_SRC.index("CREATE TABLE IF NOT EXISTS grants")
    counters = _SCHEMA_SRC.index("CREATE TABLE IF NOT EXISTS grant_counters")
    assert grants < counters, (
        "`grants` doit être déclarée AVANT `grant_counters` dans _SCHEMA : la FK "
        "`grant_counters.grant_id → grants(id)` échouerait sur une base vierge, où "
        "PostgreSQL crée les tables dans l'ordre du DDL (panne #151 sur `orgs`).")


def test_the_three_indexes_of_the_bench_are_there():
    """Trois index, et ce sont des CONDITIONS (banc L0) : trouver les feuilles,
    remonter la chaîne, compter."""
    for name, cols in (
        ("idx_grants_grantee", "grantee_kind, grantee_id"),
        ("idx_grants_parent", "parent_id"),
        ("idx_grants_resource_grantee", "resource_id, grantee_kind, grantee_id"),
    ):
        assert f"CREATE INDEX IF NOT EXISTS {name}" in _SCHEMA_SRC, f"index {name} absent"
        assert f"ON grants({cols})" in _SCHEMA_SRC, (
            f"l'index {name} ne porte plus ({cols}) — c'est sa forme MESURÉE, "
            "pas une préférence.")


def test_counting_index_is_not_partial():
    """⚠️ LE test de ce lot. L'index de comptage doit rester NON PARTIEL.

    Celui de la résolution porte `WHERE revoked_at IS NULL` — légitime, elle ignore
    les arêtes révoquées. Le comptage de D7 fait l'INVERSE : il somme les arêtes de
    la même (instance, bénéficiaire, fenêtre), **archivées comprises**, sans quoi une
    bascule de plan remet le compteur du client à zéro. Un index partiel ne peut pas
    servir une requête qui n'a pas son prédicat : PostgreSQL retombe sur un scan du
    moyeu (~52 000 arêtes entrantes pour une clé mutualisée) et la lecture repasse à
    74 ms — sans une ligne de diff qui l'explique.
    """
    stmt = _SCHEMA_SRC[_SCHEMA_SRC.index("CREATE INDEX IF NOT EXISTS idx_grants_resource_grantee"):]
    stmt = stmt[:stmt.index(";")]
    assert "WHERE" not in stmt.upper(), (
        "l'index de comptage `idx_grants_resource_grantee` est devenu PARTIEL : "
        f"{stmt.strip()!r}. Ne pas l'harmoniser avec `idx_grants_grantee` — leurs "
        "deux lectures sont opposées (la résolution ignore les révoquées, le "
        "comptage les compte). Mesuré : 0,035 ms avec, 73,8 ms sans, sur le chemin "
        "chaud de chaque appel compté d'un serveur mono-loop.")
    # …et son jumeau reste partiel, lui : la résolution n'a rien à faire des révoquées.
    leaf = _SCHEMA_SRC[_SCHEMA_SRC.index("CREATE INDEX IF NOT EXISTS idx_grants_grantee"):]
    assert "WHERE revoked_at IS NULL" in leaf[:leaf.index(";")]


def test_constraint_vocabulary_stays_closed():
    """Le vocabulaire de contraintes est FERMÉ (0053-D4) à cinq entrées, et la
    fermeture est mécanique : l'étendre est une migration — c'est le sens exact de
    « extensible seulement par amendement »."""
    m = re.search(r"constraints - ARRAY\[([^\]]*)\]", _SCHEMA_SRC)
    assert m, "le CHECK de fermeture du vocabulaire de contraintes a disparu"
    keys = {k.strip().strip("'") for k in m.group(1).split(",")}
    assert keys == {"role", "quota", "budget", "rate", "expiration"}, (
        f"vocabulaire de contraintes modifié : {sorted(keys)}. Il est FERMÉ à cinq "
        "entrées (`périmètre` en a été retiré le 08/08) — une entrée de plus est un "
        "amendement d'ADR, pas une ligne de SQL.")


def test_nothing_reads_or_writes_the_access_tables_yet():
    """L4 pose le socle, il ne le branche pas. Le jour où un call-site touche ces
    tables, ce n'est plus L4 — c'est L5, avec sa propre revue."""
    root = _DB.parent
    sql_ref = re.compile(r"\b(?:from|into|update|join|table)\s+grants\b", re.IGNORECASE)
    users = []
    for path in sorted(root.rglob("*.py")):
        if path.name in ("_init.py", "_schema.py"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith(("#", "--")):
                continue
            if sql_ref.search(line) or "grant_counters" in line:
                users.append(f"{path.relative_to(root.parent)}: {line.strip()}")
    assert not users, (
        "Quelqu'un touche `grants`/`grant_counters`, ce qui déborde du lot L4 "
        f"(« écrites par rien, lues par rien ») : {users}. Si c'est voulu, retirer "
        "ce test dans le même commit — pour que la bascule soit visible en revue.")
