"""Réserver une échéance d'abonnement AVANT de la prélever : un seul processus la tire.

Pourquoi (10/09/2026) : pendant une bascule bleu/vert, ou si l'ancienne unité simple
revient, DEUX processus de production tournent sur la même base. `due_subscriptions`
sélectionne les échéances dues sans verrou ; les deux prenaient la même, et le second
envoyait une autre clé d'idempotence (son compteur de tentatives voyait déjà la ligne
`processing` du premier) : Mollie acceptait un second débit réel.

**La réservation est un verrou consultatif PostgreSQL de SESSION**, pris sur une
connexion hors pool, en autocommit (`_connect_autocommit`). Chaque mot compte :

- de SESSION, pas de transaction : elle doit couvrir l'appel HTTP au prestataire
  (jusqu'à ~45 s de délais cumulés, `mollie_client._TIMEOUT`). Une transaction laissée
  ouverte aussi longtemps serait coupée par `idle_in_transaction_session_timeout`
  (60 s) — et le verrou avec elle, en plein appel ;
- HORS POOL : un verrou de session oublié sur une connexion du pool survivrait à son
  retour dans le pool, et tiendrait l'échéance pour tout le monde. Ici il est rendu à la
  fermeture de la connexion, en sortie de bloc — quoi qu'il arrive, y compris si le
  processus meurt ;
- `try` : le second processus n'attend pas, il passe. L'échéance ne lui échappe pas pour
  autant : au passage suivant il la relira, et elle ne sera plus due.

**Sous le verrou, la ligne est RELUE**, avec le prédicat de `due_subscriptions`. C'est la
relecture qui ferme la course, pas le verrou seul : un processus qui a sélectionné
l'échéance AVANT que l'autre la tire, et qui obtient le verrou APRÈS, trouve le cycle
avancé et ne prélève rien. L'appelant tire donc la ligne relue, jamais celle de la
sélection.

Aucun schéma : le verrou vit dans le serveur, pas dans une table. La clé d'idempotence
reste le filet si la réservation manque (`billing_runner._cle_echeance`).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from ._conn import _connect_autocommit
from .billing import _ECHEANCE_DUE

# Famille de verrous propre aux échéances : le premier entier isole la famille (même
# convention que `_VERROU_CAMPAGNE` dans runner_fleets), le second est l'org.
_VERROU_ECHEANCE = 0x0B11_0043   # « billing, ADR 0043 »


@contextmanager
def reserver_echeance(sub_row: dict) -> Iterator[Optional[dict]]:
    """Réserve l'échéance de `sub_row` (telle que `due_subscriptions` l'a rendue) et rend
    sa ligne RELUE si elle est toujours due ; `None` si un autre processus la tient, ou si
    elle ne l'est plus. La réservation dure le temps du bloc `with`."""
    org_id = int(sub_row["org_id"])
    with _connect_autocommit() as conn:
        tenu = conn.execute("SELECT pg_try_advisory_lock(%s::int, %s::int) AS tenu",
                            (_VERROU_ECHEANCE, org_id)).fetchone()["tenu"]
        if not tenu:
            yield None
            return
        yield conn.execute(
            f"SELECT s.* FROM org_subscriptions s WHERE s.org_id = %s AND {_ECHEANCE_DUE}",
            (org_id,)).fetchone()
