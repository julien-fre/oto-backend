"""DÉPRÉCIÉ — ré-export du registre `providers/` (ADR 0010, barreau 0).

L'axe **connexion/credential** a été extrait dans `providers` (renommage pur,
no-behavior-change ; devenu un PACKAGE le 2026-08-27, un module de déclaration
par connecteur). Ce module reste un shim le temps de basculer les imports
(`from . import connectors` → `from . import providers`). **Ne rien ajouter ici :
déclarer le connecteur dans `providers/<nom>.py`.** Suivi : otomata#24.
"""
from __future__ import annotations

from .providers import (  # noqa: F401  (ré-export rétrocompat, ADR 0010)
    Connector,
    REGISTRY,
    KEY_PROVIDERS,
    CREDENTIAL_PROVIDERS,
    ORG_SHAREABLE_PROVIDERS,
    QUOTA_DEFAULTS,
    DEFAULT_ACTIVE_CONNECTORS,
    REMOTE_CONNECTORS,
    MOUNT_CONNECTORS,
    connector_for_provider,
    connector_for_namespace,
    is_keyed,
    require_keyed,
    require_credential,
    is_byo_user,
    is_org_shareable,
    is_personal_cross_org,
    PERSONAL_CROSS_ORG_PROVIDERS,
    org_secret_meta,
    public_catalog,
)
