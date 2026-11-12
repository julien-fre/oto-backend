"""Le connecteur Custom API s'appelle `api` (ex-`http`) : ses références enregistrées suivent.

Le 12/11/2026, le connecteur `http` est renommé `api`, et ses outils `http`, `http_act`,
`http_get`, `http_post`, `http_doc` deviennent `api`, `api_act`, `api_get`, `api_post`,
`api_doc` — mêmes ops, mêmes arguments, même effet. Aucun alias : l'ancien nom refuse en
nommant le nouveau (`providers.CONNECTEURS_RENOMMES`,
`outils_retires.RENOMMAGES_CONNECTEURS`, que cette révision lit sans la recopier).

**Ce que la révision réécrit**, le nom seul, le reste de chaque ligne tel quel :

- les lignes qui NOMMENT le connecteur : `user_selected_connectors`,
  `connector_selection_removed`, `connector_availability`, `connector_settings`, et le kit
  des orgs (`orgs.default_connectors`, à sa place dans la liste) ;
- les listes d'outils enregistrées, par la mécanique de `20261021_1219_outils_act_msg`
  (`projects.mcp_tools`, `runner_fleets.tools`, `runner_triggers.tools`, la liste
  `payload.tools` des travaux du runner pas encore terminés, `user_enabled_tools`,
  `user_disabled_tools`, `org_disabled_tools`, `group_disabled_tools`) : le même outil des
  deux côtés, qu'elle autorise ou masque. `runner_triggers.outils_plateforme` ne nomme que
  des outils `oto_admin_*` (`verrou_org.PREFIXE_ADMIN`) : rien à y réécrire.

**Ce qu'elle ne touche pas.** Le coffre (`connector_credentials`) et ses instances : l'AAD
du chiffrement contient le nom du connecteur, la copie exige la clé applicative — c'est
une étape de démarrage, `api_operations.copier_depuis_http`, COPIE re-chiffrée qui laisse
les lignes `http` en place (base partagée par la prod et la préprod) et recopie la
définition de chaque instance (opérations, en-têtes, variables). Ni l'histoire (journal des
appels, travaux terminés, journaux de partage), ni le texte libre des tenants (procédures,
guides, recettes, consignes d'agent) : l'ancien nom y refuse en nommant le nouveau, et
l'annonce servie aux agents le dit pendant un mois.

**Conflit.** Une ligne `api` peut déjà exister sous la même clé (la préprod sert le code
neuf avant le tag de prod). Identique en sens (même état, même activation, même valeur) :
la ligne `http` part, la ligne `api` reste. Différente : la révision LÈVE, en nommant la
table et le nombre de clés en conflit — jamais une ligne écrasée.

**Verrous.** `UPDATE`/`DELETE` ciblés sur les lignes qui nomment l'ancien nom ;
`lock_timeout` 5 s.

**Ordre avec le code : APRÈS le déploiement** (`migrer upgrade head` après le tag). Prod et
préprod partagent la base : la jouer au tag de prod.

Retour arrière : exact pour ce que la révision a réécrit (`api` → `http`, mêmes règles de
conflit) ; les copies du coffre sous `api` restent, inertes pour l'ancien code.

Révision : 20261112_1200_connecteur_api
Précédente : 20261111_1930_dataforseo_decrit
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from alembic import context, op

revision = "20261112_1200_connecteur_api"
down_revision = "20261111_1930_dataforseo_decrit"
branch_labels = None
depends_on = None

_ATTENTE_MAX = "SET LOCAL lock_timeout = '5s'"
logger = logging.getLogger("alembic.runtime.migration")

ANCIEN, NOUVEAU = "http", "api"

#: Les tables qui nomment un connecteur : (table, colonnes de la clé hors `connector`,
#: colonnes qui portent le SENS de la ligne — comparées en cas de conflit).
TABLES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("user_selected_connectors", ("sub", "org_id"), ("state",)),
    ("connector_selection_removed", ("sub", "org_id"), ()),
    ("connector_availability", ("scope_type", "scope_id"), ("enabled",)),
    ("connector_settings", ("scope_type", "scope_id", "key"), ("value",)),
)


class ConflitDeRenommage(RuntimeError):
    """Une ligne existe sous les deux noms, avec un sens différent."""


def _revision_des_messageries():
    """La révision des messageries, pour sa mécanique (`reecrire_les_listes`)."""
    chemin = Path(__file__).with_name("20261021_1219_outils_act_msg.py")
    spec = importlib.util.spec_from_file_location("revision_outils_act_msg", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def correspondances(sens: str = "montee") -> tuple[dict, dict]:
    """`(autorise, masque)` des outils renommés — le même outil des deux côtés.
    `sens="descente"` : la table inversée, pour le retour arrière."""
    from oto_mcp import outils_retires
    paires = [(r.ancien, r.outil) for r in outils_retires.RENOMMAGES_CONNECTEURS
              if r.depuis == outils_retires.LOT_CONNECTEUR_API]
    if sens == "descente":
        paires = [(n, a) for a, n in paires]
    table = {de: (vers,) for de, vers in paires}
    return table, dict(table)


def renommer_les_lignes(conn, de: str, vers: str) -> None:
    """Chaque ligne qui nomme `de` passe à `vers`. Conflit de clé au sens identique : la
    ligne `de` part ; au sens différent : `ConflitDeRenommage`, rien n'est écrit."""
    for table, cle, sens in TABLES:
        jointure = " AND ".join(f"n.{c} = o.{c}" for c in cle)
        differe = " OR ".join(f"n.{c} IS DISTINCT FROM o.{c}" for c in sens) or "FALSE"
        conflits = conn.exec_driver_sql(
            f"SELECT count(*) FILTER (WHERE {differe}) AS differents, count(*) AS tous "
            f"FROM {table} o JOIN {table} n ON {jointure} AND n.connector = %(vers)s "
            f"WHERE o.connector = %(de)s", {"de": de, "vers": vers}).one()
        if conflits[0]:
            raise ConflitDeRenommage(
                f"{table} : {conflits[0]} clé(s) portent à la fois `{de}` et `{vers}` avec "
                f"un sens différent ({', '.join(sens)}) — à trancher à la main (garder "
                f"l'une des deux lignes), puis rejouer. La transaction de la révision est "
                f"annulée : rien n'est écrit.")
        if conflits[1]:
            conn.exec_driver_sql(
                f"DELETE FROM {table} o USING {table} n WHERE o.connector = %(de)s "
                f"AND n.connector = %(vers)s AND {jointure}", {"de": de, "vers": vers})
        n = conn.exec_driver_sql(
            f"UPDATE {table} SET connector = %(vers)s WHERE connector = %(de)s",
            {"de": de, "vers": vers}).rowcount
        logger.info("%s : %s — %d ligne(s) renommée(s), %d doublon(s) identique(s) retiré(s)",
                    revision, table, n, conflits[1])
    kits = conn.exec_driver_sql(
        "SELECT id, default_connectors FROM orgs WHERE %(de)s = ANY(default_connectors)",
        {"de": de}).all()
    for pk, kit in kits:
        # À sa place ; un kit qui portait déjà les deux noms ne le garde qu'une fois, à sa
        # première occurrence. Les autres noms ne bougent pas.
        nouveau, vu = [], False
        for k in kit:
            if k in (de, vers):
                if vu:
                    continue
                k, vu = vers, True
            nouveau.append(k)
        conn.exec_driver_sql("UPDATE orgs SET default_connectors = %(k)s::text[] "
                             "WHERE id = %(pk)s", {"k": nouveau, "pk": pk})
    n = len(kits)
    logger.info("%s : orgs.default_connectors — %d kit(s) réécrit(s)", revision, n)


def _jouer(de: str, vers: str, sens: str) -> None:
    if context.is_offline_mode():
        # L'essai à blanc (`--sql`) n'a pas de base à lire (cf. 0062).
        return
    op.execute(_ATTENTE_MAX)
    conn = op.get_bind()
    renommer_les_lignes(conn, de, vers)
    _revision_des_messageries().reecrire_les_listes(conn, *correspondances(sens), revision)


def upgrade() -> None:
    _jouer(ANCIEN, NOUVEAU, "montee")


def downgrade() -> None:
    _jouer(NOUVEAU, ANCIEN, "descente")
