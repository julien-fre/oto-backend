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
  §5.1). Sauf une révision retirée par un squash : le démarrage REFUSE (ci-dessous).
- ``SANS_VERSION`` — des tables existent, mais pas `alembic_version` : une base
  construite avant le registre, ou dont le registre a été perdu. On ne sait pas
  quelles révisions elle a reçues, donc on n'estampille **pas** — deviner la tête
  masquerait une révision jamais jouée. Le démarrage le dit à chaque passage, en
  erreur ; le geste (lire son schéma, puis `alembic stamp <révision>`) est humain.

Ce n'est pas une migration (ADR 0065 : elles se jouent hors du boot) : aucune
révision n'est exécutée, seule la version est écrite, et seulement là où le
démarrage vient de créer le schéma entier.

**Une base plus ancienne que la référence est refusée** (squash du registre,
oto-backend#1162, docs/migrations-versionnees.md §5.4). Le registre ne garde que les
révisions sur lesquelles une base vivante est en retard ; la plus ancienne d'entre
elles devient la RÉFÉRENCE, vide et sans précédente. Une base qui porte une révision
RETIRÉE par un squash ne peut plus monter avec ce registre (Alembic lève « Can't
locate revision ») : `constater` la refuse avant, en la nommant, et `env.py` fait de
même avant toute commande d'Alembic. Une révision inconnue qui n'a PAS été retirée
(base plus récente que le code, migrée par le tag suivant sur la base partagée) n'est
pas refusée ici : refuser le démarrage du code qui sert, pendant un bleu/vert, serait
une panne ; `deploy/cible/migrations_a_jour.py` la dit, lui, avant une montée.
"""
from __future__ import annotations

import enum
import functools
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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


@dataclass(frozen=True)
class Squash:
    """Un squash du registre : la référence qu'il pose, et ce qu'il a retiré."""

    #: La révision devenue la référence : vide, sans précédente, identifiant inchangé.
    reference: str
    date: str
    #: Le dernier tag qui porte encore les révisions retirées : il monte une base
    #: ancienne jusqu'à la référence (ou au-delà), avant le code d'après le squash.
    tag_anterieur: str
    retirees: frozenset[str]


#: Les squashs, du plus ancien au plus récent. Le suivant AJOUTE une entrée (§5.4) :
#: les identifiants retirés restent nommés, pour qu'une base très ancienne dise encore
#: quel tag la monte.
SQUASHS: tuple[Squash, ...] = (
    Squash(
        reference="0041_recherche_valeurs_servies",
        date="06/10/2026",
        tag_anterieur="v1.441.0",
        retirees=frozenset({
            "0001_point_de_depart", "0002_runner_jobs_index_vivant",
            "0003_runner_fleets_preneur", "0004_org_entitlements",
            "0005_journal_archives", "0006_unipile_fin_de_droit",
            "0007_jetons_revocation_tracee", "0008_billing_contracts",
            "0009_coffre_secret_obligatoire", "0010_tool_calls_result_shape",
            "0011_journal_revisions_ligne", "0012_partages_echeance",
            "0013_pages_versions_regroupees", "0014_droits_portee_personne",
            "0015_droits_valeur_obligatoire", "0016_journal_suppression",
            "0017_tableaux_contexte_org", "0018_contexte_org_rempli",
            "0019_plafond_abonnements", "0020_pool_abonnements",
            "0021_limites_du_run", "0022_journal_membres_org",
            "0023_signature_webhook", "0024_droits_personne_partout",
            "0025_repli_api", "0026_transcription_tours",
            "0027_apollo_phone_reveals", "0028_partage_en_attente",
            "0029_cle_metier_valeur_servie", "0030_file_de_travail_ordre",
            "0031_selection_org_reelle", "0032_tool_calls_org_outil_ok",
            "0033_verrou_org_delegation", "0034_recettes",
            "0035_agents_partages_a_l_org", "0036_partages_de_procedure",
            "0037_emails_cc", "0038_abandon_run",
            "0039_feed_synced_at_retiree", "0040_tenants_desactivation",
        }),
    ),
)

#: La référence en vigueur : le point de départ du registre.
REFERENCE = SQUASHS[-1].reference


class BaseAnterieureALaReference(RuntimeError):
    """La base porte une révision retirée par un squash : ce registre ne la monte plus."""

    def __init__(self, revision: str, squash: Squash) -> None:
        self.revision = revision
        self.squash = squash
        super().__init__(
            f"registre de migrations : révision {revision} antérieure à la référence "
            f"{squash.reference} (squash du {squash.date}) : monter d'abord cette base "
            f"avec un tag antérieur au squash ({squash.tag_anterieur}) — `migrer upgrade "
            "head` depuis son arbre —, puis ce code (docs/migrations-versionnees.md §5.4)")


def refuser_si_anterieure(versions: Iterable[str]) -> None:
    """Lève `BaseAnterieureALaReference` si une version de la base a été retirée par
    un squash — AVANT qu'Alembic ne lève, sans la nommer, « Can't locate revision »."""
    for version in versions:
        for squash in SQUASHS:
            if version in squash.retirees:
                raise BaseAnterieureALaReference(version, squash)


def versions_de(conn: psycopg.Connection) -> list[str]:
    """Les révisions qu'`alembic_version` porte (la table doit exister)."""
    return sorted(r["version_num"] for r in conn.execute(
        "SELECT version_num FROM alembic_version").fetchall())


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
    du démarrage, avant son premier ordre. Une base versionnée à une révision retirée
    par un squash lève `BaseAnterieureALaReference`."""
    tables = {r["relname"] for r in conn.execute(
        "SELECT relname FROM pg_class "
        "WHERE relnamespace = current_schema()::regnamespace AND relkind IN ('r', 'p')"
    ).fetchall()}
    if "alembic_version" in tables:
        refuser_si_anterieure(versions_de(conn))
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
