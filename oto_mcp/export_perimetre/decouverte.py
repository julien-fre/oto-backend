"""Lire le schéma RÉEL et refuser un classement qui ne le couvre pas exactement.

Le schéma se lit dans le catalogue de la base (tables, colonnes, clés étrangères,
séquences), jamais dans `db/schema/` : les colonnes posées par `ALTER` au démarrage
n'y sont pas, et un banc qui reconstitue le schéma mesure la représentation qu'on
s'en fait (`docs/conventions.md`, « le banc s'exerce sur le VRAI DDL »).

`verifier_classement` rassemble TOUTES les anomalies avant de lever : une table ajoutée
et une colonne renommée dans le même lot se lisent en un seul refus, pas en deux runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .classement import (EXCLUE, EXPORTEES, FAITS_DE_RUN, INDIRECTE, INSTANCE, JOURNAL,
                         Table)
from .regles import Via, vias


@dataclass(frozen=True)
class Cle:
    """Une clé étrangère déclarée : `table(colonnes) → cible(colonnes_cible)`."""
    table: str
    colonnes: tuple[str, ...]
    cible: str
    colonnes_cible: tuple[str, ...]


@dataclass(frozen=True)
class Unique:
    """Un index UNIQUE (contrainte comprise) : ses clés et son prédicat, en SQL tels que
    le catalogue les rend (`pg_get_indexdef`), sur les noms nus des colonnes."""
    cles: tuple[str, ...]
    predicat: str | None = None
    nulls_egaux: bool = False     # NULLS NOT DISTINCT


@dataclass(frozen=True)
class Schema:
    colonnes: dict[str, tuple[str, ...]]      # table → colonnes, ordre du catalogue
    generees: dict[str, frozenset[str]]       # colonnes GENERATED (ne s'exportent pas)
    sequences: dict[str, tuple[str, ...]]     # colonnes dont le défaut tire une séquence
    cles: tuple[Cle, ...]
    primaires: dict[str, tuple[str, ...]]     # clé primaire (absente sur deux tables)
    vues: dict[str, str]                      # vue SIMPLE → sa seule table sous-jacente
    uniques: dict[str, tuple[Unique, ...]] = field(default_factory=dict)

    def cles_de(self, table: str) -> tuple[Cle, ...]:
        return tuple(c for c in self.cles if c.table == table)


class ClassementIncomplet(RuntimeError):
    """Le classement ne couvre pas exactement le schéma : l'export ne part pas."""

    def __init__(self, anomalies: list[str]):
        self.anomalies = anomalies
        super().__init__("classement du périmètre incomplet — "
                         f"{len(anomalies)} anomalie(s) :\n  - " + "\n  - ".join(anomalies))


class JournalNonDetachable(RuntimeError):
    """Le journal ne peut pas voyager à part du reste (`classement.JOURNAL`)."""

    def __init__(self, anomalies: list[str]):
        self.anomalies = anomalies
        super().__init__("le journal ne se détache pas du reste du périmètre — "
                         f"{len(anomalies)} anomalie(s) :\n  - " + "\n  - ".join(anomalies))


def verifier_journal(schema: Schema, classement: dict[str, Table]) -> None:
    """Refuse, toutes les anomalies d'un coup, ce qui empêcherait le journal de voyager
    par tranches, hors de la fenêtre de coupure :

    - une autre table exportée qui y renvoie (clé étrangère ou `Via`) : sur la cible, sa
      ligne viserait un appel pas encore versé ;
    - une règle qui passe par un parent : une tranche se borne sur la table elle-même ;
    - une clé du journal vers une table exportée : il se POUSSE dans la cible avant
      l'import principal, quand ni orgs ni comptes n'y sont encore ;
    - une colonne d'horodatage ou de faits de run absente, une clé primaire absente
      (l'import d'une tranche est idempotent PAR elle).
    """
    anomalies: list[str] = []
    for t, horodatage in sorted(JOURNAL.items()):
        entree = classement.get(t)
        if entree is None or entree.classe not in EXPORTEES:
            anomalies.append(f"`{t}` (journal) n'est pas une table exportée")
        elif vias(entree.regle):
            anomalies.append(f"`{t}` (journal) : sa règle passe par un parent, une tranche "
                             "ne se borne que sur une règle directe")
        for role, c in (("d'horodatage", horodatage), ("des faits de run",
                                                      FAITS_DE_RUN.get(t, (horodatage,))[0])):
            if c not in schema.colonnes.get(t, ()):
                anomalies.append(f"`{t}` (journal) : colonne {role} `{c}` absente")
        if not schema.primaires.get(t):
            anomalies.append(f"`{t}` (journal) : pas de clé primaire, l'import d'une tranche "
                             "ne peut pas être idempotent")
    for k in schema.cles:
        nom = f"`{k.table}({', '.join(k.colonnes)})` → `{k.cible}`"
        if k.cible in JOURNAL:
            anomalies.append(f"{nom} : " + ("le journal renvoie à lui-même"
                                            if k.table in JOURNAL else
                                            "une table renvoie au journal, qui voyage à part"))
        elif k.table in JOURNAL and classement.get(k.cible) is not None \
                and classement[k.cible].classe in EXPORTEES:
            anomalies.append(f"{nom} : le journal renvoie à une table qui n'arrive qu'avec "
                             "l'import principal — il ne pourrait plus être poussé avant")
    for t, entree in sorted(classement.items()):
        if t not in JOURNAL and entree.classe in EXPORTEES:
            anomalies.extend(f"`{t}` hérite du journal `{v.parent}`, qui voyage à part"
                             for v in vias(entree.regle) if v.parent in JOURNAL)
    if anomalies:
        raise JournalNonDetachable(anomalies)


def lire_schema(conn, schema: str = "public") -> Schema:
    colonnes: dict[str, list[str]] = {}
    generees: dict[str, set[str]] = {}
    sequences: dict[str, list[str]] = {}
    for r in conn.execute(
            "SELECT c.table_name, c.column_name, c.is_generated, c.column_default "
            "FROM information_schema.columns c JOIN information_schema.tables t "
            "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
            "WHERE c.table_schema = %s AND t.table_type = 'BASE TABLE' "
            "ORDER BY c.table_name, c.ordinal_position", (schema,)):
        colonnes.setdefault(r["table_name"], []).append(r["column_name"])
        if r["is_generated"] == "ALWAYS":
            generees.setdefault(r["table_name"], set()).add(r["column_name"])
        if (r["column_default"] or "").startswith("nextval("):
            sequences.setdefault(r["table_name"], []).append(r["column_name"])
    cles = tuple(
        Cle(r["tbl"], tuple(r["cols"]), r["cible"], tuple(r["cols_cible"]))
        for r in conn.execute(
            "SELECT k.conrelid::regclass::text AS tbl, k.confrelid::regclass::text AS cible, "
            "  ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY u(n, i) "
            "        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.n "
            "        ORDER BY u.i)::text[] AS cols, "
            "  ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY u(n, i) "
            "        JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = u.n "
            "        ORDER BY u.i)::text[] AS cols_cible "
            "FROM pg_constraint k JOIN pg_namespace ns ON ns.oid = k.connamespace "
            "WHERE k.contype = 'f' AND ns.nspname = %s "
            "ORDER BY 1, 2, 3", (schema,)))
    primaires = {
        r["tbl"]: tuple(r["cols"]) for r in conn.execute(
            "SELECT k.conrelid::regclass::text AS tbl, "
            "  ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY u(n, i) "
            "        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.n "
            "        ORDER BY u.i)::text[] AS cols "
            "FROM pg_constraint k JOIN pg_namespace ns ON ns.oid = k.connamespace "
            "WHERE k.contype = 'p' AND ns.nspname = %s", (schema,))}
    uniques: dict[str, list[Unique]] = {}
    for r in conn.execute(
            "SELECT i.indrelid::regclass::text AS tbl, "
            "  ARRAY(SELECT pg_get_indexdef(i.indexrelid, k, true) "
            "        FROM generate_series(1, i.indnkeyatts) k ORDER BY k)::text[] AS cles, "
            "  pg_get_expr(i.indpred, i.indrelid, true) AS predicat, "
            "  i.indnullsnotdistinct AS nulls_egaux "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
            "JOIN pg_namespace ns ON ns.oid = c.relnamespace "
            "WHERE i.indisunique AND ns.nspname = %s ORDER BY 1, i.indexrelid", (schema,)):
        uniques.setdefault(r["tbl"], []).append(
            Unique(tuple(r["cles"]), r["predicat"], r["nulls_egaux"]))
    sous_jacentes: dict[str, set[str]] = {}
    for r in conn.execute(
            "SELECT DISTINCT v.relname AS vue, t.relname AS tbl "
            "FROM pg_class v JOIN pg_namespace ns ON ns.oid = v.relnamespace "
            "JOIN pg_rewrite rw ON rw.ev_class = v.oid "
            "JOIN pg_depend d ON d.objid = rw.oid AND d.classid = 'pg_rewrite'::regclass "
            "  AND d.refclassid = 'pg_class'::regclass "
            "JOIN pg_class t ON t.oid = d.refobjid AND t.relkind = 'r' "
            "WHERE v.relkind = 'v' AND ns.nspname = %s", (schema,)):
        sous_jacentes.setdefault(r["vue"], set()).add(r["tbl"])
    return Schema(
        colonnes={t: tuple(c) for t, c in colonnes.items()},
        generees={t: frozenset(c) for t, c in generees.items()},
        sequences={t: tuple(c) for t, c in sequences.items()},
        cles=cles,
        primaires=primaires,
        vues={v: next(iter(t)) for v, t in sous_jacentes.items() if len(t) == 1},
        uniques={t: tuple(u) for t, u in uniques.items()},
    )


def verifier_classement(schema: Schema, classement: dict[str, Table]) -> dict[str, Table]:
    """Rend le classement indexé par TABLE PHYSIQUE (une entrée qui nomme une vue simple
    est résolue en sa table), ou lève `ClassementIncomplet` si le classement et le
    schéma divergent en quoi que ce soit."""
    anomalies: list[str] = []
    resolu: dict[str, Table] = {}
    for nom, entree in classement.items():
        table = schema.vues.get(nom, nom)
        if table in resolu:
            anomalies.append(f"`{table}` classée deux fois (dont par la vue `{nom}`)")
        resolu[table] = entree
    classement = resolu
    for t in sorted(set(schema.colonnes) - set(classement)):
        anomalies.append(f"table `{t}` non classée : la déclarer dans "
                         "export_perimetre/classement.py (possédée, indirecte, instance "
                         "ou exclue)")
    for t in sorted(set(classement) - set(schema.colonnes)):
        anomalies.append(f"entrée `{t}` du classement : aucune table de ce nom dans le schéma")
    for t in sorted(set(classement) & set(schema.colonnes)):
        anomalies.extend(_anomalies_table(t, classement[t], schema, classement))
    if anomalies:
        raise ClassementIncomplet(anomalies)
    return classement


def _anomalies_table(t: str, entree: Table, schema: Schema,
                     classement: dict[str, Table]) -> list[str]:
    if entree.classe == INSTANCE:
        return ([f"`{t}` (instance) ne porte pas de règle"] if entree.regle else []) + \
               ([f"`{t}` (instance) doit dire pourquoi"] if not entree.raison else [])
    if entree.regle is None:
        return [f"`{t}` ({entree.classe}) sans règle d'appartenance"]
    out: list[str] = []
    if entree.classe == EXCLUE and not entree.raison:
        out.append(f"`{t}` (exclue) doit dire pourquoi elle ne part pas")
    presentes = set(schema.colonnes[t])
    destinataire = entree.destinataire.colonnes() if entree.destinataire else ()
    naissance = entree.naissance[:1] if entree.naissance else ()
    for c in (*entree.regle.colonnes(), *entree.secrets, *entree.hors_base, *destinataire,
              *entree.comptes, *naissance):
        if c not in presentes:
            out.append(f"`{t}` : la colonne `{c}` nommée par le classement n'existe pas")
    liens = vias(entree.regle)
    if entree.classe == INDIRECTE and (not liens or _a_une_regle_directe(entree.regle)):
        out.append(f"`{t}` (indirecte) : sa règle doit passer par son parent (`Via`) seulement")
    for v in liens:
        out.extend(_anomalies_via(t, v, schema, classement))
    return out


def _a_une_regle_directe(regle) -> bool:
    enfants = getattr(regle, "regles", None)
    if enfants is None:
        return not isinstance(regle, Via)
    return any(_a_une_regle_directe(r) for r in enfants)


def _anomalies_via(t: str, v: Via, schema: Schema, classement: dict[str, Table]) -> list[str]:
    parent = classement.get(v.parent)
    if parent is None or parent.classe not in EXPORTEES:
        return [f"`{t}` hérite de `{v.parent}`, qui n'est pas une table exportée "
                f"({parent.classe if parent else 'non classée'})"]
    if v.parent not in schema.colonnes:
        return []  # déjà dit : entrée sans table
    manquantes = [c for c in v.colonnes_parent if c not in schema.colonnes[v.parent]]
    if manquantes:
        return [f"`{t}` → `{v.parent}` : colonnes parentes absentes {manquantes}"]
    if v.fk and not any(k.cible == v.parent and k.colonnes == v.colonnes_enfant
                        and k.colonnes_cible == v.colonnes_parent for k in schema.cles_de(t)):
        return [f"`{t}` → `{v.parent}` : aucune clé étrangère "
                f"{v.colonnes_enfant} → {v.colonnes_parent} ; si le lien est logique, "
                "le déclarer `fk=False`"]
    return []
