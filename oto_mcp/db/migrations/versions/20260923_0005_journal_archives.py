"""journal_archives : le registre des mois du journal archivés au froid (#665).

Arbitrage d'Alexis du 23/09/2026, option B : un run archivé garde ses bornes, et sa
page dit « contenu archivé le … » à la place du contenu. L'archive
(`deploy/archive_tool_calls.py`) inscrit ici chaque mois APRÈS la relecture qui
autorise la suppression et AVANT la suppression ; la page d'un run lit ce registre.

Le `CREATE TABLE` vit dans le fragment `db/schema/usage.py::JOURNAL_ARCHIVES` et cette
révision l'exécute tel quel — une seule écriture du DDL. Comme toute table neuve, il
est aussi joué au démarrage (`CREATE TABLE IF NOT EXISTS`, ADR 0065) : révision et boot
sont idempotents l'un envers l'autre, et l'ordre entre eux est indifférent. Aucune clé
étrangère, aucune table existante touchée : aucun verrou pris ailleurs que sur la table
neuve.

⚠️ Prod et préprod partagent la MÊME base. L'ancien code ignore la table : jouer cette
révision avant ou après le déploiement est sûr, et un retour au tag précédent aussi.
Elle doit en revanche exister AVANT le premier tir de l'archive qui supprime un mois :
sans elle, l'archive s'arrête avant de supprimer (c'est voulu). Le retour arrière
(`downgrade`) supprime la table ET ses lignes — la date d'archivage des mois déjà
partis serait perdue ; tant que le fragment reste dans l'assemblage, le démarrage
suivant la recrée vide.

Révision : 0005_journal_archives
Précédente : 0004_org_entitlements
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.usage import JOURNAL_ARCHIVES

revision = "0005_journal_archives"
down_revision = "0004_org_entitlements"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(JOURNAL_ARCHIVES)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS journal_archives")
