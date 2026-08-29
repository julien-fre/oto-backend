"""La CLÉ DE CONNECTEUR d'un tenant — pose, lecture, retrait (L-clés PR 1, ADR 0052).

Façade au grain TENANT du coffre chiffré `credentials_store` (`entity_type='tenant'`,
`entity_id` = le slug) — même forme que `org_store.vault` : aucune table locale, aucun
secret en clair. Une clé posée ici sert à TOUTES les orgs du tenant qui n'en ont pas
de plus proche (membre, équipe, org) : c'est l'étage entre l'org et la plateforme du
walker unique (`access.cascade.walk_cascade`), sondé par les deux voies de la fenêtre
L7 (la cascade qui décide, la chaîne 0053 qui calcule à côté).

**Le tenant d'un appelant se lit sur son sub qualifié, jamais sur le rattachement de
son org** (lot L1 : aucun chemin de résolution ne dépend de ce rattachement, et c'est
gardé). D'où `rung_tenant`, l'unique fonction qui dit à la cascade et à la chaîne QUEL
slug sonder — et quand ne rien sonder :

- un sub NU relève du tenant primaire (`oto`), et **le tenant primaire n'a pas de clé
  de tenant** : ses clés partagées SONT les instances plateforme, avec leurs grants
  (ADR 0044 §F, 0053). Une clé « tenant oto » serait un second mécanisme pour la même
  fonction — refusée à la pose (`PrimaryTenantKeyRefused`) ET jamais sondée à la
  lecture, pour que les deux ne divergent pas (#409 : une ligne acceptée que personne
  ne lit). Conséquence mesurable : à 99 % du trafic, le barreau ne coûte RIEN ;
- sans sub (endpoint anonyme, ADR 0032), aucun tenant : la cascade `org > plateforme`
  de l'anonyme reste celle d'avant. Lui donner l'étage demanderait l'arête tenant→org
  de la chaîne 0053 — PR 2, avec le rôle « admin de tenant ».

Feuille : n'importe que le coffre, le registre des tenants et le registre des connecteurs.
"""
from __future__ import annotations

from typing import Optional

from . import credentials_store, providers, tenancy


class PrimaryTenantKeyRefused(ValueError):
    """Le tenant primaire ne porte pas de clé de tenant (cf. en-tête du module)."""


def rung_tenant(sub: Optional[str]) -> Optional[str]:
    """Le slug dont la clé partagée peut servir CET appelant, ou None quand aucun
    barreau tenant n'existe pour lui (sub nu = tenant primaire ; pas de sub = anonyme).

    Classification par PRÉFIXE dans le registre du process (`tenancy.tenant_of`) :
    aucune lecture de base, aucun découpage du sub."""
    if not sub:
        return None
    slug = tenancy.current().tenant_of(sub)
    return None if slug == tenancy.PRIMARY_SLUG else slug


def get_tenant_secret(slug: str, provider: str, account: str = "") -> Optional[str]:
    """Clé du secret partagé `provider` possédé par le tenant, ou None (déchiffre).
    `account` discrimine le multi-compte à ce palier ('' = mono)."""
    return credentials_store.get_credential(credentials_store.TENANT, slug, provider, account)


def has_tenant_secret(slug: str, provider: str) -> bool:
    """Présence SANS déchiffrer (sonde de présence, statut)."""
    return credentials_store.has_credential(credentials_store.TENANT, slug, provider)


def set_tenant_secret(slug: str, provider: str, secret: str, set_by: Optional[str] = None,
                      meta: Optional[dict] = None, account: str = "") -> None:
    """Pose/rote la clé partagée `provider` du tenant. Même garde d'éligibilité que
    l'org (`byo_org` : la clé est lue aux barreaux partagés du walker, gatés
    `ORG_SHAREABLE_PROVIDERS`) ; un connecteur remote (ADR 0003) est défini par la
    donnée (`meta.base_url`), sans entrée registre.

    Le tenant primaire est refusé ici, et le walker ne sonde jamais son barreau : les
    deux verdicts sont les mêmes par construction."""
    if not slug or slug == tenancy.PRIMARY_SLUG:
        raise PrimaryTenantKeyRefused(
            f"Le tenant `{tenancy.PRIMARY_SLUG}` ne porte pas de clé de tenant : ses "
            "clés partagées sont les instances plateforme (posées sur "
            "/api/admin/platform-keys, accordées par oto_admin_key_grant).")
    if not (meta and meta.get("base_url")):
        providers.require_credential(credentials_store.TENANT, provider)
    if not secret:
        raise ValueError("secret requis")
    credentials_store.set_credential(credentials_store.TENANT, slug, provider, secret,
                                     set_by=set_by, meta=meta, account=account)


def delete_tenant_secret(slug: str, provider: str, account: str = "") -> bool:
    """Retire la clé (et archive son instance, L6 pièce 2). False = rien à retirer."""
    return credentials_store.clear_credential(credentials_store.TENANT, slug, provider,
                                              account=account)


def list_tenant_secrets(slug: str) -> list[dict]:
    """Connecteurs posés sur le tenant — SANS la clé (jamais exposée). `base_url`
    rendu pour un connecteur remote (satellite non-secret de `meta`)."""
    out: list[dict] = []
    for c in credentials_store.list_credentials(credentials_store.TENANT, slug):
        entry = {"provider": c["connector"], "account": c["account"],
                 "set_by": c["set_by"], "set_at": c["set_at"]}
        base_url = (c.get("meta") or {}).get("base_url")
        if base_url:
            entry["base_url"] = base_url
        out.append(entry)
    return out
