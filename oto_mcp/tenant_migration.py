"""Bascule de tenant : les deux commandes, séparées (ADR 0052 / otomata#35).

La bascule fait tourner **deux mécanismes de nature opposée**, longtemps commandés
par la même lecture d'environnement — donc impossibles à arrêter l'un sans l'autre :

- **le rapprochement d'identités** — au login, fusionner l'ancien compte de même email
  dans le nouveau (`db.reconcile_tenant_migration`). Mécanisme d'ÉCRITURE, one-shot par
  personne, dont la fenêtre utile se referme quand tout le monde a rebasculé ;
- **le drain d'alias** — à chaque requête, rediriger un ancien identifiant vers le
  compte actuel (`db.resolve_sub`). Mécanisme de LECTURE, permanent, dont la fenêtre
  utile reste ouverte tant qu'un jeton portant un ancien identifiant peut se présenter.

⚠️ **Ils ne s'arrêtent pas ensemble, et surtout pas dans cet ordre-là.** La porte REST
(`api/base._authenticate`) appelle `upsert_user` **hors** de toute commande : un ancien
identifiant qui n'est PAS redirigé n'échoue pas, il **recrée** la ligne `users`
supprimée par la fusion. Couper le drain ressuscite donc les comptes fusionnés — c'est
arrivé, et le compte ressuscité a servi 884 appels sous une identité morte.

## Ce que ce module sépare, et ce qu'il ne sépare pas

Il sépare les **décisions** : deux prédicats nommés, un par mécanisme, chacun avec sa
propre lecture. Il ne sépare pas (encore) la **variable** : les deux lisent aujourd'hui
`OTO_MCP_TENANT_MIGRATION_ISS`, et c'est volontaire — le découplage devait être neutre
à l'exécution, donc sans acte d'exploitation sur la box.

⚠️ Les deux lectures ne sont **pas** équivalentes, et cette asymétrie est reprise ici
telle qu'elle était :

| commande | rapprochement | drain |
|---|---|---|
| absente / vide | inerte | inerte |
| blanche (`"   "`) | **inerte** (`.strip()`) | **armé** (présence seule) |
| une autre valeur que notre `iss` | inerte | armé |
| notre `iss` | armé | armé |

Elle est figée par `tests/test_bascule_tenant_deux_commandes.py`, relevé écrit AVANT le
découplage : « normaliser » l'une des deux au passage serait un changement de
comportement déguisé en propreté.

⚠️ **Chemin chaud** : ces deux prédicats sont consultés en tête de CHAQUE appel (REST et
MCP). Ils lisent l'environnement et rien d'autre — aucune requête, aucun I/O.
"""
from __future__ import annotations

import os

# La commande unique, héritée de la fenêtre de bascule. Sa valeur en production est
# NOTRE émetteur primaire (`LOGTO_ENDPOINT/oidc`), pas celui d'un tiers : le
# rapprochement est donc armé sur tous nos comptes, en permanence — ce qui était une
# fenêtre est devenu un état. C'est la raison d'être de ce module.
_COMMANDE = "OTO_MCP_TENANT_MIGRATION_ISS"


def email_merge_armed(iss: str | None) -> bool:
    """Le rapprochement d'identités doit-il être tenté pour un login venu de `iss` ?

    Lit la commande **par sa VALEUR** : elle doit désigner l'émetteur dont on rapatrie
    les comptes, et l'`iss` du jeton doit être celui-là. Les deux côtés sont comparés
    sans leur `/` final ; la comparaison reste sensible à la casse.
    """
    if not iss:
        return False
    commande = os.environ.get(_COMMANDE, "").strip().rstrip("/")
    return bool(commande) and iss.rstrip("/") == commande


def alias_drain_armed() -> bool:
    """Un ancien identifiant doit-il être redirigé vers le compte actuel ?

    Lit la commande **par sa PRÉSENCE** : sa valeur n'est jamais regardée (le drain ne
    dépend d'aucun émetteur — il canonicalise ce que `sub_aliases` connaît, d'où que le
    jeton vienne). Une commande présente mais blanche arme donc le drain.
    """
    return bool(os.environ.get(_COMMANDE))
