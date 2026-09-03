"""Bascule de tenant : ce qui reste armé, et pourquoi l'autre a été retiré (ADR 0052).

La bascule faisait tourner **deux mécanismes de nature opposée**, longtemps commandés
par la même lecture d'environnement — donc impossibles à arrêter l'un sans l'autre :

- **le rapprochement d'identités** — au login, fusionner l'ancien compte de même email
  dans le nouveau. Mécanisme d'ÉCRITURE, one-shot par personne, dont la fenêtre utile
  se referme quand tout le monde a rebasculé. **Retiré du chemin de login le
  2026-09-03** (cf. `db.users.upsert_user`) : zéro rapprochement sur les 20 jours
  précédents, une commande réglée sur NOTRE émetteur — donc armée en permanence sur
  tous nos comptes — et une résurrection de compte supprimé à son actif ;
- **le drain d'alias** — à chaque requête, rediriger un ancien identifiant vers le
  compte actuel (`db.resolve_sub`). Mécanisme de LECTURE, permanent, dont la fenêtre
  utile reste ouverte tant qu'un jeton portant un ancien identifiant peut se présenter.
  **Armé, et il le reste.**

⚠️ **C'est le drain qu'il ne faut pas couper, pas le rapprochement.** La porte REST
(`api/base._authenticate`) appelle `upsert_user` **hors** de toute commande : un ancien
identifiant qui n'est PAS redirigé n'échoue pas, il **recrée** la ligne `users`
supprimée par la fusion. Couper le drain ressuscite donc les comptes fusionnés — c'est
arrivé, et le compte ressuscité a servi 884 appels sous une identité morte. Le drain
porte encore plus de mille appels par semaine, et du trafic entre par là tous les jours.

Corollaire pour qui lira la variable d'environnement : elle ne commande plus qu'UNE
chose. Son nom (`…_MIGRATION_ISS`) et sa valeur (un émetteur) sont hérités de l'époque
où elle en commandait deux ; le drain, lui, n'a jamais regardé sa valeur.

## Ré-armer le rapprochement n'est pas une affaire de variable

Il reste possible en acte d'OPÉRATEUR — `db.migrate_sub(old, new, operator_source=…)`,
où « ces deux subs sont la même personne » est tranché hors du code (ADR 0052 §6). Ce
qui a été retiré, c'est son déclenchement AUTOMATIQUE au login : une décision de fusion
prise sans que personne ne la prenne. `db.reconcile_tenant_migration` est toujours
défini, mais plus appelé par aucun chemin servi.

⚠️ **Chemin chaud** : `alias_drain_armed` est consulté en tête de CHAQUE appel (REST et
MCP). Il lit l'environnement et rien d'autre — aucune requête, aucun I/O ; un cliquet
l'exige (`tests/test_bascule_tenant_commande_centralisee.py`).
"""
from __future__ import annotations

import os

# La commande, héritée de la fenêtre de bascule. Sa valeur en production est NOTRE
# émetteur primaire (`LOGTO_ENDPOINT/oidc`) — c'est ce qui armait le rapprochement sur
# tous nos comptes en permanence. Le drain, lui, ne l'a jamais lue que par sa présence.
_COMMANDE = "OTO_MCP_TENANT_MIGRATION_ISS"


def alias_drain_armed() -> bool:
    """Un ancien identifiant doit-il être redirigé vers le compte actuel ?

    Lit la commande **par sa PRÉSENCE** : sa valeur n'est jamais regardée (le drain ne
    dépend d'aucun émetteur — il canonicalise ce que `sub_aliases` connaît, d'où que le
    jeton vienne). Une commande présente mais blanche arme donc le drain : c'est le
    comportement d'origine, relevé et figé avant le découplage, pas une intention.
    """
    return bool(os.environ.get(_COMMANDE))
