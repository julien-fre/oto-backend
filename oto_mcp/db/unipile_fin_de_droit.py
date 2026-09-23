"""La fin du droit `unipile` sur les sièges de la clé PLATEFORME : ce que la base retient.

Deux dates par binding (`unipile_accounts`), posées par le travail de maintenance
`oto-mcp maintenance unipile-fin-de-droit` (`oto_mcp/unipile_fin_de_droit.py`) :

- `entitlement_lost_at` : le premier passage qui a vu l'org de ce binding SANS le droit
  `unipile`. C'est le point de départ du délai avant suppression chez unipile ;
- `entitlement_notice_at` : le préavis envoyé au propriétaire. Posé APRÈS l'envoi,
  jamais avant : marquer d'abord transformerait un envoi raté en silence définitif.

Les deux s'effacent ensemble quand le droit revient, et quand le compte est rebranché
(`set_unipile_account`) : une marque ne survit pas à ce qui l'a rendue caduque.

NON aplati dans la surface `db.*` (comme `alertes_credential`) : ses noms ne disent
quelque chose que dans leur module. Les appelants écrivent
`from ..db import unipile_fin_de_droit as db_fdd`.
"""
from __future__ import annotations

from typing import Iterable

from ._conn import _connect
from .entitlements import _VIVANT


def sieges_plateforme(delai_jours: int) -> list[dict]:
    """Les bindings sur la clé plateforme que le travail doit regarder.

    Les VIVANTS (en service), plus les morts qui portent encore une marque : un compte
    que son propriétaire a débranché après le préavis court toujours chez unipile, et
    une suppression qui a échoué après avoir délié laisse des lignes mortes — sans
    elles, le passage suivant ne la reprendrait jamais.

    `supprime_le` et `echu` sont calculés par la base (son horloge, la même pour tous
    les processus), à partir de `entitlement_lost_at` et du délai."""
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT ua.sub, ua.org_id, ua.provider, ua.account_id, ua.account_name, "
            "ua.disconnected_at, ua.entitlement_lost_at, ua.entitlement_notice_at, "
            "ua.entitlement_lost_at + make_interval(days => %s) AS supprime_le, "
            "COALESCE(ua.entitlement_lost_at + make_interval(days => %s) <= NOW(), FALSE) "
            "AS echu, "
            "u.email, u.locale, o.name AS org_name "
            "FROM unipile_accounts ua "
            "LEFT JOIN users u ON u.sub = ua.sub "
            "LEFT JOIN orgs o ON o.id = ua.org_id "
            "WHERE ua.platform_seat "
            "AND (ua.disconnected_at IS NULL OR ua.entitlement_lost_at IS NOT NULL) "
            "ORDER BY ua.account_id, ua.org_id, ua.sub",
            (int(delai_jours), int(delai_jours)),
        ).fetchall()]


def _cles(lignes: Iterable[dict]) -> tuple[list, list, list]:
    subs, orgs, canaux = [], [], []
    for r in lignes:
        subs.append(r["sub"])
        orgs.append(int(r["org_id"]))
        canaux.append(r["provider"])
    return subs, orgs, canaux


_PAR_CLE = ("(sub, org_id, provider) IN (SELECT * FROM unnest("
            "%s::text[], %s::bigint[], %s::text[]))")


def marquer_perte(lignes: Iterable[dict]) -> int:
    """Pose `entitlement_lost_at = NOW()` sur ces bindings VIVANTS qui n'en ont pas.

    Idempotent : une marque déjà posée n'est jamais repoussée — le délai part du
    premier passage qui a constaté la perte, pas du dernier."""
    subs, orgs, canaux = _cles(lignes)
    if not subs:
        return 0
    with _connect() as conn:
        return conn.execute(
            f"UPDATE unipile_accounts SET entitlement_lost_at = NOW() WHERE {_PAR_CLE} "
            "AND platform_seat AND disconnected_at IS NULL AND entitlement_lost_at IS NULL",
            (subs, orgs, canaux),
        ).rowcount


def marquer_preavis(lignes: Iterable[dict]) -> int:
    """Pose `entitlement_notice_at = NOW()` — APRÈS un envoi réussi, jamais avant."""
    subs, orgs, canaux = _cles(lignes)
    if not subs:
        return 0
    with _connect() as conn:
        return conn.execute(
            f"UPDATE unipile_accounts SET entitlement_notice_at = NOW() WHERE {_PAR_CLE} "
            "AND entitlement_lost_at IS NOT NULL AND entitlement_notice_at IS NULL",
            (subs, orgs, canaux),
        ).rowcount


def effacer_perte(org_ids: Iterable[int]) -> int:
    """Le droit est revenu : efface les deux marques de TOUS les bindings de ces orgs."""
    ids = sorted({int(o) for o in org_ids})
    if not ids:
        return 0
    with _connect() as conn:
        return conn.execute(
            "UPDATE unipile_accounts SET entitlement_lost_at = NULL, "
            "entitlement_notice_at = NULL WHERE org_id = ANY(%s) "
            "AND (entitlement_lost_at IS NOT NULL OR entitlement_notice_at IS NOT NULL)",
            (ids,),
        ).rowcount


def droit_unipile_declare_quelque_part() -> bool:
    """Au moins une org porte-t-elle un droit `unipile` VIVANT dans `org_entitlements` ?

    Le témoin de la garde du travail : une table sans aucun droit `unipile` vivant
    alors que des sièges sont en service, c'est la table qui n'est pas remplie — pas
    toutes les orgs qui ont perdu leur droit le même jour."""
    with _connect() as conn:
        return conn.execute(
            f"SELECT 1 FROM org_entitlements WHERE right_key = 'unipile' AND {_VIVANT} "
            "LIMIT 1",
        ).fetchone() is not None
