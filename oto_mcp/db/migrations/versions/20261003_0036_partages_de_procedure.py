"""partages_de_procedure : une procédure d'org se partage par un lien, et ses lecteurs.

Trois tables NEUVES et leurs index — le fragment
`db/schema/procedures.py::PROCESS_SHARES`, exécuté tel quel :

- `process_shares` : un jeton par procédure publiée (au plus UN actif par procédure,
  index unique partiel sur `revoked_at IS NULL`), le réglage « voir qui lit », la
  FORME du graphe servie en vitrine et le compteur quotidien des vues de vitrine ;
- `process_share_readers` : une ligne par (partage, lecteur connecté) — première et
  dernière lecture, nombre, copie, inclusion dans un résumé ;
- `process_readers_digest_optouts` : qui ne veut plus du résumé quotidien des lecteurs.

**Aucune clé étrangère vers `org_instructions`** : sa colonne `id` naît dans `_init`
(ALTER), après l'assemblage — une FK échouerait sur une base vierge. Le lien est
logique ; une procédure supprimée laisse un partage que toutes les lectures traitent
comme introuvable. FK vers `orgs` (CASCADE) et de la table des lecteurs vers celle des
partages (CASCADE), tables vides à la création : aucun verrou qui dure.

**Ordre** : le démarrage crée les mêmes tables s'il ne les trouve pas (`CREATE TABLE IF
NOT EXISTS`, ADR 0065) — révision et boot sont idempotents l'un envers l'autre. Le code
du lot les LIT dès qu'une procédure s'ouvre côté propriétaire (`GET …/share`) : la
jouer **avant la fusion**. L'ancien code ne les lit ni ne les écrit.

⚠️ **Numérotation** : la file de la semaine du 02/10 est 0032 (oto-backend#1145),
0033 (#1118, verrou d'org de la délégation), 0034 (#1115, recettes), 0035 (#1117,
agents partagés), 0036 (celle-ci). Elle n'a une seule tête Alembic qu'une fois la file
entière fusionnée.

⚠️ Prod et préprod partagent la MÊME base. Le retour arrière retire les trois tables :
les liens publiés cessent de répondre (404), la liste des lecteurs est perdue. Tant que
le fragment reste dans l'assemblage, le démarrage suivant les recrée vides.

Révision : 0036_partages_de_procedure
Précédente : 0035_agents_partages_a_l_org
"""
from __future__ import annotations

from alembic import op

from oto_mcp.db.schema.procedures import PROCESS_SHARES

revision = "0036_partages_de_procedure"
down_revision = "0035_agents_partages_a_l_org"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute(PROCESS_SHARES)


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("DROP TABLE IF EXISTS process_readers_digest_optouts")
    op.execute("DROP TABLE IF EXISTS process_share_readers")
    op.execute("DROP TABLE IF EXISTS process_shares")
