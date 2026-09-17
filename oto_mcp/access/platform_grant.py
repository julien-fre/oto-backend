"""Le palier PLATEFORME de la cascade (ADR 0044 §F, blueprint ADR 0053 lot L5).

Extrait de `cascade.py` (17/09, lot `status_for` N+1) : ce seam — qui, d'une
instance plateforme, en désigne le bénéficiaire, calcule son quota, et arbitre
entre la chaîne de grants et l'ancien chemin — ne dépend que de `scope`
(appartenance à un scope de partage) et de `grants_chain`. Le walker
(`walk_cascade`) l'appelle via le barreau `platform` d'une `CascadeProbe`,
jamais directement.
"""
from __future__ import annotations

from .. import credentials_store, grants_chain
from . import scope


def _platform_grantee_scope(sub, active_org, scopes) -> "str | None":
    """Le scope de `scopes` qui vise `sub` sur une instance PLATEFORME, ou None (ADR 0044
    §F). `user:<sub>` prime (le plus spécifique) ; `org:<id>` gaté sur l'org **ACTIVE**
    (mirroir EXACT de l'ancien `get_active_org_grant(active_org)` — un grant d'org est métré
    per-contexte-d'org, pas per-appartenance : un membre de l'org X actif dans Y n'en profite
    pas). Sert l'accès (closed) ET le quota (rate_limit_by)."""
    if not scopes:
        return None
    if f"user:{sub}" in scopes:
        return f"user:{sub}"
    if active_org is not None and f"org:{active_org}" in scopes:
        return f"org:{active_org}"
    return None


def _platform_instance_usable(sub, active_org, inst: dict) -> bool:
    """Instance plateforme utilisable par `sub` ? (ADR 0044 §F, mode-aware). Un prêt
    `share_side` autorise (membership, comme un prêt BYO). Sinon selon `share_mode` :
    'open' = `share_down` vide (free-tier, ouvert à tous) OU `sub` grantee ; 'closed' =
    `sub` grantee (défaut fermé)."""
    down, side = inst.get("share_down") or [], inst.get("share_side") or []
    if scope._sub_matches_scopes(sub, side):
        return True
    granted = _platform_grantee_scope(sub, active_org, down) is not None
    if inst.get("share_mode") == "closed":
        return bool(down) and granted
    return (not down) or granted


def _platform_quota(sub, active_org, meta: dict) -> "int | None":
    """Quota/jour du bénéficiaire sur une instance plateforme : `rate_limit_by[scope de sub]`
    (user prime > org active), sinon le défaut `rate_limit` de l'instance."""
    rlb = (meta or {}).get("rate_limit_by") or {}
    sc = _platform_grantee_scope(sub, active_org, list(rlb.keys()))
    if sc is not None and sc in rlb:
        return rlb[sc]
    return (meta or {}).get("rate_limit")


def _legacy_platform_grant_meta(sub, provider, active_org, *,
                                instances: "list[dict] | None" = None) -> "dict | None":
    """Palier plateforme (ADR 0044 §F R3) SANS secret : {label, daily_quota} de l'instance
    PLATEFORM utilisable par `sub` la plus récente, ou None. Base des miroirs `status_for`/
    `credential_mode_for` (présence + quota, jamais de déchiffrement).

    ⚠️ **L'ancien chemin, et il ne bouge pas d'un octet** (blueprint ADR 0053, lot L5) :
    il reste le seul pour les neuf connecteurs non basculés, et le repli EXACT pour un
    bénéficiaire que la chaîne ne connaît pas. Le préfixe `_legacy_` ne le déprécie pas —
    il nomme l'une des deux voies de la fenêtre de double lecture.

    `instances` : lues d'avance (cf. `cascade.preloaded_presence_probe`) — `None` relit."""
    for inst in (instances if instances is not None
                 else credentials_store.list_platform_instances(provider)):
        if _platform_instance_usable(sub, active_org, inst):
            return {"label": inst["label"],
                    "daily_quota": _platform_quota(sub, active_org, inst.get("meta"))}
    return None


def _platform_grant_meta(sub, provider, active_org, *,
                         instances: "list[dict] | None" = None) -> "dict | None":
    """Le palier plateforme, **chaîne de grants d'abord** (blueprint ADR 0053, lot L5).

    Trois issues, et la troisième est ce qui rend la fenêtre sûre :

    - la chaîne ACCORDE → son verdict (clé + quota portés par l'arête) ;
    - la chaîne REFUSE (des arêtes existent, toutes révoquées) → refus **sans repli** :
      sinon révoquer une arête ne couperait rien, l'ancien chemin free-tier
      re-accordant aussitôt ;
    - la chaîne est MUETTE (connecteur non basculé, ou aucune arête n'a jamais visé cet
      appelant) → l'ancien chemin, à l'identique.

    Les deux voies sont lues pour un connecteur basculé — c'est le prix assumé de la
    fenêtre (une lecture indexée de plus) et c'est ce qui produit le journal d'écart,
    matière du verdict de fin de fenêtre.

    `instances` : passée telle quelle aux DEUX voies — deux calculs sur une lecture."""
    verdict = grants_chain.platform_rung(sub, provider, active_org, instances=instances)
    if verdict is None:
        return _legacy_platform_grant_meta(sub, provider, active_org, instances=instances)
    legacy = _legacy_platform_grant_meta(sub, provider, active_org, instances=instances)
    grants_chain.journal_resolution(provider, sub, active_org, verdict, legacy)
    if not verdict.granted:
        return None
    return {"label": verdict.label, "daily_quota": verdict.quota}


def _resolve_platform_grant(sub, provider, active_org) -> "dict | None":
    """Palier plateforme AVEC secret : {label, secret, daily_quota} ou None. Remplace les 3
    lectures legacy (get_active_grant/get_active_org_grant/get_platform_api_key). Le secret
    n'est déchiffré QUE pour l'instance gagnante (chemin chaud)."""
    g = _platform_grant_meta(sub, provider, active_org)
    if not g:
        return None
    secret = credentials_store.get_credential(credentials_store.PLATFORM, g["label"], provider)
    if secret is None:
        return None
    return {**g, "secret": secret}
