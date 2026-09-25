"""Aide PARTAGÉE « marquer une ligne de coffre rejetée » (oto#25 lot b2).

Extrait de `capabilities/connectors/verify.py` (seul écrivain jusqu'ici, sous les
noms privés `_FLAGGABLE` / `_record_health`) pour que les modules qui RECONNAISSENT
eux-mêmes un grant mort (le motif `invalid_grant` et ses équivalents — aujourd'hui
salesforce et zoho ; google au refresh reste EXCLU, WIP concurrent sur son retour
OAuth) marquent la ligne RÉELLEMENT servie sans dupliquer le geste
ni sa garde. `verify.py` importe ce module à la place de ses définitions locales —
refactor pur, son comportement ne change pas.

Deux éléments :

- `FLAGGABLE_SCOPES` : les paliers dont la portée ne dépasse pas l'org (ou l'unique
  utilisateur) de celui qui déclenche le marquage. `tenant` et `platform` en sont
  TOUJOURS exclus — partagés par des orgs entières (ou plusieurs tenants), le hoquet
  d'un seul appelant n'a pas à les peindre en rouge pour tout le monde. `USER` (scope
  LEGACY `("user", sub)` d'avant ADR 0033 — plus aucun connecteur vivant n'y écrit
  depuis le retrait de la fédération MCP, 2026-09-09, mais des lignes y DORMENT)
  y est aussi narrow que `MEMBER` — un seul utilisateur —
  et n'atteint jamais `verify.py` (sa cascade ne produit que `MEMBER`/`group`/`org` :
  cf. `access/cascade.py`, qui yield `CascadeRung("user", credentials_store.MEMBER,
  …)` — la chaîne "user" y est un MODE, pas un `entity_type`). L'élargir ici ne
  change donc rien à ce que `verify.py` marque, et l'a laissé couvrir ce scope avec
  la MÊME garde plutôt qu'une seconde.
- `record_health(provider, scope, ok, error)` : persiste `meta.health_ko` +
  `meta.health_reason` (merge, best-effort). `scope=None` → no-op. C'est la fonction
  qu'utilise `verify.py`, qui gère elle-même le DÉMARQUAGE (`ok=True` efface
  `health_ko`) — un geste que ce lot (b2) ne touche pas (b3, à venir).
- `mark_rejected(entity_type, entity_id, provider, account, error)` : la façade que
  ce lot ajoute pour un module qui connaît son ENTITÉ directement (pas de `ResolvedCtx`
  ni de scope pré-calculé) — bâtit le scope, applique la MÊME garde, ne démarque
  jamais (toujours `ok=False`) : marquer un rejet réel n'est jamais un fallback qui
  avale l'erreur, l'appelant RE-LÈVE toujours après l'avoir appelée.

- `suivre_appel(trace, quota_epuise)` : le suivi AU MOMENT DE L'APPEL — un refus
  `quota_exhausted` marque la ligne servie `no_quota`, le premier succès l'efface
  (cf. la section « Crédits épuisés » plus bas).

Lu par `connectors/readiness.py` (via `access.credential_rejection_for`, qui lit
`credentials_store.credential_health`) — jamais l'inverse, ce module ne connaît pas
ses lecteurs.
"""
from __future__ import annotations

from typing import Optional

from starlette.concurrency import run_in_threadpool

from .. import credentials_store, providers

# Paliers dont on accepte de FLAGUER la clé — cf. docstring du module ci-dessus.
FLAGGABLE_SCOPES = (credentials_store.USER, credentials_store.MEMBER,
                    "group", credentials_store.ORG)

#: Le verdict « la clé authentifie, le compte est à sec » — même nom que celui de la
#: sonde (`connectors.verify.NO_QUOTA`), défini au coffre qui le range et le relit.
NO_QUOTA = credentials_store.NO_QUOTA_VERDICT


def record_health(provider: str, scope: "tuple | None", ok: bool,
                  error: "str | None", verdict: "str | None" = None) -> None:
    """Persiste l'état de santé du credential testé (`meta.health_ko` + raison +
    `meta.health_verdict`) — lu par `status_for` (fiche) et
    `access.credential_rejection_for`, donc par le verdict `ready` de la carte
    connecteur. Merge (n'écrase rien), best-effort. `scope` = `(entity_type,
    entity_id, account)` de la ligne RÉELLEMENT testée/servie ; `None` (clé partagée
    au-delà de la garde) → on ne flague pas. `verdict` = le classement de l'échec
    (`no_quota`, `unauthorized`…) quand il est connu : c'est lui qui fait dire à la
    carte « recharge » plutôt que « repose la clé ».

    ⚠️ Seule fonction qui DÉMARQUE (`ok=True` efface `health_ko`/`health_reason`/
    `health_verdict`) — `mark_rejected` ci-dessous ne l'appelle qu'avec `ok=False`."""
    if scope is None:
        return
    try:
        credentials_store.update_meta(
            scope[0], scope[1], provider, scope[2],
            {"health_ko": (not ok), "health_reason": (error if not ok else None),
             "health_verdict": (verdict if not ok else None)})
    # noqa: SILENT — dette déclarée : le flag de santé non écrit devrait se journaliser (#424, verdict C)
    except Exception:  # noqa: BLE001 — la santé est un bonus, jamais bloquant
        pass


def mark_rejected(entity_type: Optional[str], entity_id: Optional[str],
                  provider: str, account: str, error: "str | None",
                  verdict: "str | None" = None) -> None:
    """Marque `health_ko` sur `(entity_type, entity_id, provider, account)` — jamais
    sur un scope hors `FLAGGABLE_SCOPES` (tenant/plateforme), jamais si `entity_id`
    est absent. Pour un module qui RECONNAÎT lui-même un grant mort sur la ligne
    qu'il sait être la bonne (jamais par déduction générique) — l'appelant RE-LÈVE
    toujours l'exception d'origine juste après : marquer n'est jamais un fallback
    qui avale l'erreur réelle."""
    if entity_type not in FLAGGABLE_SCOPES or not entity_id:
        return
    record_health(provider, (entity_type, entity_id, account or ""), False, error,
                  verdict)


# --- Crédits épuisés, vus AU MOMENT DE L'APPEL (option (a), 25/09/2026) ---------
#
# La sonde `oto_instance op=verify` classait déjà un 402 en `no_quota` — mais personne
# ne la rejoue avant de travailler. Un agent tombait sur « crédits épuisés » en plein
# travail, la carte du connecteur restait verte, et personne ne rechargeait : 13
# signaux (theirstack, AI Ark…) avant ce lot. Désormais le refus d'un APPEL marque la
# clé qui l'a servi, et le premier appel réussi sur cette même clé efface la marque.
#
# Coût sur le chemin chaud : le relevé d'appel porte déjà la ligne servie
# (`access.resolve` → `credential_row`) ; l'effacement n'écrit qu'une fois par clé et
# par process (`_SANS_MARQUE`), sous condition (la marque doit être `no_quota`), et
# hors de la boucle d'événements.

#: Clés dont on sait, depuis le démarrage de ce process, qu'elles ne portent pas de
#: marque `no_quota` (un effacement conditionnel y est déjà passé). Une marque posée
#: les en retire. Perdu au redémarrage : on repaie alors UNE écriture conditionnelle.
_SANS_MARQUE: set = set()


def _ligne(row) -> Optional[tuple]:
    """`(entity_type, entity_id, porteur, account)` de la ligne servie, porteur
    normalisé (une délégation range sa clé sous le porteur : cf. `verify.py`), ou None
    si la ligne n'est pas marquable (tenant, plateforme, pas d'entité)."""
    if not row:
        return None
    entity_type, entity_id, provider, account = row
    if entity_type not in FLAGGABLE_SCOPES or not entity_id or not provider:
        return None
    return (entity_type, entity_id, providers.credential_provider(provider),
            account or "")


def marquer_quota_epuise(row, message: "str | None") -> None:
    """Sync (DB) : marque `no_quota` la ligne servie — no-op hors `FLAGGABLE_SCOPES`
    (une clé plateforme ou tenant n'est JAMAIS peinte en rouge pour tout le monde)."""
    ligne = _ligne(row)
    if ligne is None:
        return
    record_health(ligne[2], (ligne[0], ligne[1], ligne[3]), False, message, NO_QUOTA)
    _SANS_MARQUE.discard(ligne)


def effacer_quota_epuise(row) -> None:
    """Sync (DB) : sur un appel RÉUSSI, lève une marque `no_quota` de la ligne servie
    — une seule écriture conditionnelle par clé et par process, jamais une autre
    marque (un rejet `unauthorized` ne se lève qu'à la sonde ou à la repose)."""
    ligne = _ligne(row)
    if ligne is None or ligne in _SANS_MARQUE:
        return
    try:
        credentials_store.clear_health_if_verdict(*ligne, verdict=NO_QUOTA)
    # noqa: SILENT — dette déclarée : l'effacement non écrit laisse la carte rouge jusqu'à la sonde (#424, verdict C)
    except Exception:  # noqa: BLE001 — la santé est un bonus, jamais bloquant
        return
    _SANS_MARQUE.add(ligne)


def _a_effacer(row) -> bool:
    ligne = _ligne(row)
    return ligne is not None and ligne not in _SANS_MARQUE


async def suivre_appel(trace: Optional[dict], quota_epuise: "str | None") -> None:
    """Après un appel d'outil : `quota_epuise` = le message du refus `quota_exhausted`
    (la clé servie est marquée), `None` = succès (une marque `no_quota` de la clé
    servie est levée). Hors de la boucle, best-effort : le suivi de santé ne doit
    jamais changer le résultat de l'appel qu'il observe."""
    row = (trace or {}).get("credential_row")
    if row is None:
        return
    if quota_epuise is not None:
        await run_in_threadpool(marquer_quota_epuise, row, quota_epuise)
    elif _a_effacer(row):
        await run_in_threadpool(effacer_quota_epuise, row)
