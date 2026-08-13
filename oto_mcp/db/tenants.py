"""Palier tenant (ADR 0052) — lecture de l'étage d'identité.

Source du registre d'émetteurs (`tenancy.build`). Volontairement minuscule : L2
n'écrit pas de tenant (le provisioning est un runbook, barreau B4) et ne lit pas
le rattachement des orgs — l'existant reste NOMMÉ, pas déplacé.
"""
from __future__ import annotations

from ._conn import _connect


def list_tenant_issuers() -> list:
    """Tenants qui déclarent un émetteur, ordre stable.

    Le tenant `oto` n'y figure **pas** : son émetteur est l'env (`LOGTO_ENDPOINT`),
    donc DB-indépendant — l'authentification canonique ne doit jamais dépendre
    d'une lecture de table. Une ligne qui le redéclarerait est de toute façon
    ignorée par le registre (l'env gagne).
    """
    with _connect() as conn:
        rows = conn.execute(
            # `name` et `hosts` servent la DÉCOUVERTE (lot L3 : PRM et 401 sensibles
            # au host), jamais la vérification d'un jeton — celle-ci ne connaît que
            # l'émetteur. Les lire ici ne change donc rien au chemin d'auth.
            "SELECT slug, name, issuer, jwks_uri, hosts, oauth_client_id FROM tenants "
            "WHERE issuer IS NOT NULL AND btrim(issuer) <> '' ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]
