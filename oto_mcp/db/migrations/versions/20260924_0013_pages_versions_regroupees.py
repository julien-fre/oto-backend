"""Pages : l'auteur de la dernière modification, et la porte de chaque version — oto#274.

Le dashboard enregistre une page après 4 s d'inactivité ; chaque enregistrement créait
une version. `db.projects.update_doc` regroupe désormais une rafale du dashboard (même
compte, face REST, moins de 5 minutes depuis l'instantané qui l'a ouverte). Deux
colonnes NULLABLES, sans défaut ni index :

- `doc_revisions.face` ('mcp' | 'rest') : la porte de l'écriture qui a remplacé cet
  instantané. Sans elle, un enregistrement de l'écran juste après une écriture de
  l'agent, du même compte, effacerait de l'historique la version de l'agent. NULL =
  antérieur à cette révision : jamais regroupé.
- `docs.updated_by` : le compte de la dernière modification, servi par `oto_doc`
  (`op=get`, `op=list`) et lu par les « modifications récentes », qui l'appariaient
  jusqu'ici à la révision de même horodatage — un appariement qu'une rafale regroupée
  n'aurait plus trouvé.

**Remplissage de `docs.updated_by`**, avec exactement ce que les « modifications
récentes » servaient : la révision écrite dans la même transaction que la dernière
modification (`created_at = updated_at`), sinon le créateur d'une page jamais modifiée
(`updated_at = created_at`), sinon NULL (page déplacée). Il se fait HORS de la
transaction des `ALTER` (`autocommit_block`), par tranches de `_TRANCHE` identifiants,
chacune sa propre transaction sous `lock_timeout` : aucun verrou long sur une table que
chaque lecture de page traverse. Rejouable : il ne touche que les lignes que la règle
sait dater, et y écrit la même valeur.

Durée mesurée le 24/09/2026 sur la base de test (PostgreSQL 17 local, sans charge,
1 000 000 de pages dont la moitié modifiées avec leur révision appariée, corps courts) :
**≈ 18 s par million de lignes**, en 200 tranches de 5 000 (≈ 90 ms chacune, donc aucun
verrou de ligne tenu plus longtemps). ⚠️ **Mesurer d'abord la taille réelle** (`SELECT count(*), max(id) FROM docs`)
avant de jouer cette révision sur la base partagée : la durée suit le nombre de lignes
et la taille de `doc_revisions`.

⚠️ Prod et préprod partagent la MÊME base. Le code du même lot ÉCRIT les deux colonnes
à chaque écriture de page : **la révision se joue AVANT la fusion**, sinon chaque
`update_doc` répondrait `UndefinedColumn`. L'ancien code les ignore : jouée avant, sans
effet sur lui ; retour au tag précédent sûr. Entre la fusion et le tag, l'ancien code
de production écrit encore des pages sans poser `updated_by` : une page qu'il modifie
garde l'auteur précédent jusqu'à sa prochaine écriture par le nouveau code. Rejouer
le remplissage (`_remplir`, même règle) après le tag referme cette fenêtre pour les
écritures ; un déplacement par l'ancien code reste attribué à l'auteur précédent.

Révision : 0013_pages_versions_regroupees
Précédente : 0012_partages_echeance
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0013_pages_versions_regroupees"
down_revision = "0012_partages_echeance"
branch_labels = None
depends_on = None

# Au-delà, on abandonne plutôt que de faire attendre les lectures de pages derrière nous.
_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"
_TRANCHE = 5000

_REMPLIR_TRANCHE = text(
    "UPDATE docs d SET updated_by = a.auteur FROM ("
    "  SELECT d2.id, CASE WHEN d2.updated_at = d2.created_at THEN d2.created_by"
    "    ELSE (SELECT r.edited_by FROM doc_revisions r"
    "          WHERE r.doc_id = d2.id AND r.created_at = d2.updated_at"
    "          ORDER BY r.id DESC LIMIT 1) END AS auteur"
    "  FROM docs d2 WHERE d2.id >= :bas AND d2.id < :haut"
    ") a "
    "WHERE d.id = a.id AND d.updated_by IS DISTINCT FROM a.auteur"
    "  AND (d.updated_at = d.created_at OR EXISTS ("
    "    SELECT 1 FROM doc_revisions r"
    "    WHERE r.doc_id = d.id AND r.created_at = d.updated_at))")


def _remplir(conn) -> None:
    """Remplit `docs.updated_by` par tranches d'identifiants. Appelée en autocommit :
    chaque `UPDATE` de tranche est sa propre transaction, et le `lock_timeout` de
    SESSION vaut pour chacune (un `SET LOCAL` n'aurait aucun effet hors transaction)."""
    conn.execute(text("SET lock_timeout = '5s'"))
    try:
        bas, haut_max = conn.execute(text("SELECT min(id), max(id) FROM docs")).one()
        while bas is not None and bas <= haut_max:
            conn.execute(_REMPLIR_TRANCHE, {"bas": bas, "haut": bas + _TRANCHE})
            bas += _TRANCHE
    finally:
        conn.execute(text("RESET lock_timeout"))


def upgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE doc_revisions ADD COLUMN IF NOT EXISTS face TEXT")
    op.execute("ALTER TABLE docs ADD COLUMN IF NOT EXISTS updated_by TEXT")
    with op.get_context().autocommit_block():
        _remplir(op.get_bind())


def downgrade() -> None:
    op.execute(_ATTENTE_MAX)
    op.execute("ALTER TABLE docs DROP COLUMN IF EXISTS updated_by")
    op.execute("ALTER TABLE doc_revisions DROP COLUMN IF EXISTS face")
