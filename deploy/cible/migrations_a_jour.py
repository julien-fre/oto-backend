#!/usr/bin/env python3
"""La base d'un rôle est-elle à la tête des migrations du tag qu'on monte ? (oto-backend#1163)

Le défaut qu'il ferme : une instance cible servait le code d'un tag dont la tête Alembic
était 0037, sur une base restée en 0031 — la chaîne de déploiement montait le code sans
jouer les migrations, et rien ne le disait.

Exécuté par `deploy/cible/deployer.sh`, sous le lanceur (qui tire `DATABASE_URL`), dans
l'arbre de la couleur INACTIVE où le tag vient d'être installé, AVANT son démarrage :

    <arbre>/.venv/bin/python <arbre>/deploy/lanceur_secrets.py --script deploy/cible/migrations_a_jour.py

- la tête ATTENDUE est celle du registre de CET arbre — le tag qu'on monte, jamais le
  code qui sert ;
- la révision COURANTE est lue dans la base du rôle, en lecture seule, avec le même
  constat que le démarrage (`db._version_alembic.constater`).

Il ne migre JAMAIS : la migration reste un geste explicite, que le refus nomme
(docs/instance-cible.md, § Les migrations, un geste explicite).

Sortie : 0 si la base est à la tête du tag, ou NEUVE (aucune table : le démarrage crée le
schéma et y pose la tête) ; 3 si elle est EN RETARD sur une révision du registre du tag
(`migrer upgrade head` la rattrape) ; 1 si rien ne se conclut — registre à plusieurs
têtes ou vide, base injoignable, base sans version, révision inconnue du tag, plusieurs
révisions en base. Aucun cas ne passe par défaut.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

ARBRE = Path(__file__).resolve().parents[2]
if str(ARBRE) not in sys.path:
    sys.path.insert(0, str(ARBRE))

import psycopg  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from oto_mcp.db._version_alembic import _REGISTRE, EtatRegistre, constater  # noqa: E402

A_JOUR = 0
ILLISIBLE = 1
EN_RETARD = 3
DELAI_S = 15


class Refus(Exception):
    """Rien ne se conclut : le message nomme pourquoi."""


def tete_du_tag(registre: Path) -> tuple[str, set[str]]:
    """La tête UNIQUE du registre de l'arbre, et toutes ses révisions."""
    script = ScriptDirectory(str(registre))
    tetes = sorted(script.get_heads())
    if not tetes:
        raise Refus(f"registre de migrations du tag vide ({registre})")
    if len(tetes) > 1:
        raise Refus(f"le registre du tag a {len(tetes)} têtes ({', '.join(tetes)}) : "
                    "aucune n'est « la » tête — fusionner les files (alembic merge) dans "
                    "le tronc, puis monter un tag qui n'en a qu'une")
    return tetes[0], {r.revision for r in script.walk_revisions()}


def lire_base(dsn: str) -> tuple[EtatRegistre, list[str]]:
    """L'état du registre de la base, et ses révisions — en lecture seule.

    Une erreur de connexion ou de lecture refuse en nommant sa classe seulement : son
    message peut porter l'hôte ou l'utilisateur de la base, et ce journal remonte jusqu'au
    run du workflow."""
    try:
        with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=DELAI_S) as conn:
            conn.read_only = True
            conn.execute(f"SET statement_timeout = '{DELAI_S}s'")
            etat = constater(conn)
            versions = []
            if etat is EtatRegistre.VERSIONNEE:
                versions = sorted(r["version_num"] for r in conn.execute(
                    "SELECT version_num FROM alembic_version").fetchall())
    except psycopg.Error as erreur:
        etat_sql = f", SQLSTATE {erreur.sqlstate}" if erreur.sqlstate else ""
        raise Refus(f"base du rôle illisible ({type(erreur).__name__}{etat_sql})") from erreur
    return etat, versions


def verdict(tete: str, connues: set[str], etat: EtatRegistre,
            versions: list[str]) -> tuple[int, str]:
    """Le code de sortie et ce qu'on en dit."""
    if etat is EtatRegistre.NEUVE:
        return A_JOUR, (f"migrations : base neuve — le démarrage y créera le schéma et "
                        f"posera la tête du tag ({tete})")
    if etat is EtatRegistre.SANS_VERSION:
        return ILLISIBLE, ("migrations : la base a des tables mais pas d'`alembic_version` — "
                           "on ignore quelles révisions elle a reçues. Établir la dernière "
                           "présente dans son schéma, puis `migrer stamp <révision>` "
                           "(docs/migrations-versionnees.md §5.2)")
    if len(versions) != 1:
        return ILLISIBLE, (f"migrations : `alembic_version` porte {len(versions)} révision(s) "
                           f"({', '.join(versions) or 'aucune'}) — une seule est attendue")
    courante = versions[0]
    if courante == tete:
        return A_JOUR, f"migrations : base à la tête du tag ({tete})"
    if courante not in connues:
        return ILLISIBLE, (f"migrations : la base est en {courante}, révision inconnue du "
                           f"registre du tag (tête {tete}) — base plus récente que le tag, ou "
                           "issue d'une autre file. `upgrade head` n'y changerait rien")
    return EN_RETARD, (f"migrations : la base est en {courante}, le tag attend {tete} — "
                       "base EN RETARD sur le code qu'on monte")


def main(lire: Callable[[str], tuple[EtatRegistre, list[str]]] = lire_base,
         registre: Path = _REGISTRE) -> int:
    try:
        tete, connues = tete_du_tag(registre)
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            raise Refus("DATABASE_URL absente de l'environnement (le lanceur la tire)")
        code, message = verdict(tete, connues, *lire(dsn))
    except Refus as refus:
        print(f"migrations : REFUS — {refus}", file=sys.stderr)
        return ILLISIBLE
    print(message, file=sys.stdout if code == A_JOUR else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
