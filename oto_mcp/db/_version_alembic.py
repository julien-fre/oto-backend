"""La version Alembic d'une base NEUVE, posée par le démarrage (oto-backend#969).

**Le défaut.** Une base neuve reçoit tout son schéma du démarrage (`_SCHEMA` puis les
ordres idempotents) : elle est donc, dès sa naissance, dans l'état d'APRÈS toutes les
révisions du registre. Mais rien ne l'écrivait dans `alembic_version` : un
`alembic upgrade head` joué plus tard sur elle rejouerait chaque révision depuis la
première — un renommage sur une colonne déjà renommée, une recopie sur des données
déjà là. Le geste juste sur une base neuve est `stamp head`, jamais `upgrade` depuis
zéro ; un geste manuel qu'on ne fait qu'une fois par base est un geste qu'on oublie,
et l'oubli ne se voit qu'à la migration suivante, sur la base d'un tiers.

**Ce que fait le démarrage.** Il constate l'état du registre AVANT son premier ordre,
sous le verrou consultatif du boot (un second démarrage concurrent constate donc
l'état que le premier a commité), puis conclut EN FIN de la même transaction :

- ``NEUVE`` — le schéma courant ne contient **aucune table** : c'est le démarrage
  qui vient de tout créer, par ses `CREATE TABLE`. Il y pose la tête du registre.
- ``VERSIONNEE`` — `alembic_version` existe : on n'y touche **jamais**. Sa tenue
  appartient à Alembic (`upgrade`, joué à la main, docs/migrations-versionnees.md
  §5.1).
- ``SANS_VERSION`` — des tables existent, mais pas `alembic_version` : une base
  construite avant le registre, ou dont le registre a été perdu. On ne sait pas
  quelles révisions elle a reçues, donc on n'estampille **pas** — deviner la tête
  masquerait une révision jamais jouée. Le démarrage le dit à chaque passage, en
  erreur ; le geste (lire son schéma, puis `alembic stamp <révision>`) est humain.

Ce n'est pas une migration (ADR 0065 : elles se jouent hors du boot) : aucune
révision n'est exécutée, seule la version est écrite, et seulement là où le
démarrage vient de créer le schéma entier.
"""
from __future__ import annotations

import enum
import functools
import logging
from pathlib import Path

import psycopg
from alembic.script import ScriptDirectory

logger = logging.getLogger(__name__)

# Le registre, lu là où Alembic le lit (`alembic.ini` : `script_location`).
_REGISTRE = Path(__file__).resolve().parent / "migrations"

# La table de version, SOUS LA FORME qu'Alembic lui donne (`alembic.ddl.impl`,
# `version_table_pk=True`) : c'est Alembic qui la relira et la tiendra ensuite.
_TABLE_VERSION = (
    "CREATE TABLE alembic_version ("
    "version_num VARCHAR(32) NOT NULL, "
    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
)


class EtatRegistre(enum.Enum):
    NEUVE = "neuve"
    VERSIONNEE = "versionnée"
    SANS_VERSION = "sans version"


@functools.lru_cache(maxsize=1)
def tete_du_registre() -> str:
    """La tête unique du registre des révisions. Plusieurs têtes lèvent (Alembic,
    `MultipleHeads`) : estampiller l'une d'elles serait choisir une file au hasard."""
    tete = ScriptDirectory(str(_REGISTRE)).get_current_head()
    if tete is None:
        raise RuntimeError(f"registre de migrations vide : {_REGISTRE}")
    return tete


def constater(conn: psycopg.Connection) -> EtatRegistre:
    """L'état du registre de CETTE base, lu au catalogue — à appeler sous le verrou
    du démarrage, avant son premier ordre."""
    tables = {r["relname"] for r in conn.execute(
        "SELECT relname FROM pg_class "
        "WHERE relnamespace = current_schema()::regnamespace AND relkind IN ('r', 'p')"
    ).fetchall()}
    if "alembic_version" in tables:
        return EtatRegistre.VERSIONNEE
    return EtatRegistre.SANS_VERSION if tables else EtatRegistre.NEUVE


def conclure(conn: psycopg.Connection, etat: EtatRegistre) -> None:
    """Pose la tête sur une base NEUVE, dans la transaction qui vient de créer son
    schéma ; dit une base SANS_VERSION ; ne touche jamais une base VERSIONNEE."""
    if etat is EtatRegistre.NEUVE:
        tete = tete_du_registre()
        conn.execute(_TABLE_VERSION)
        conn.execute("INSERT INTO alembic_version (version_num) VALUES (%s)", (tete,))
        logger.info("registre de migrations : base neuve, version posée à %s", tete)
    elif etat is EtatRegistre.SANS_VERSION:
        logger.error(
            "registre de migrations : base EXISTANTE sans `alembic_version` — non "
            "estampillée, car on ignore quelles révisions elle a reçues. Tant qu'elle "
            "ne l'est pas, `alembic upgrade head` y rejouerait tout le registre. Geste : "
            "établir la dernière révision déjà présente dans son schéma, puis "
            "`alembic stamp <révision>` (docs/migrations-versionnees.md §5.2)."
        )
