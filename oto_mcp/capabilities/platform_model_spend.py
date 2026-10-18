"""Admin capability: what ANTHROPIC charged the instance's platform keys, against what
the call journal metered (ADR 0009) — the check that does not trust the metering.

A platform `anthropic` key served to granted orgs meters itself: one
`anthropic_tokens` row per model call (`runner_jobs._metrer_un_tour`). That is our
code reporting on itself. This reads Anthropic's own usage report for one UTC day,
prices it with the catalogue tarifs, and compares it with the journal.

## What it catches

- **a gap on the platform keys** — tokens Anthropic counted that no journal row
  carries: a worker that died mid-call, a metering bug. A journal ABOVE Anthropic's
  count is a gap too (double metering, a key mix-up).
- **spend on any OTHER key** of the Anthropic organization (`other_keys`) — a worker
  running on its environment key is not metered at all. `api_key_id: null` is usage
  made in the Console.
- **models without a tarif** (`unknown_models`) — never priced, so never vouched for.

## What it does NOT do

- **Say which org owes a gap.** Anthropic's report knows API keys, not our orgs.
- **Read the TENANT rung.** A tenant key may sit in another Anthropic organization;
  only `key_mode='platform'` rows are compared.
- **Bill, alert or remember anything.** It returns a verdict; whoever charges the
  orgs decides what is worth an alert.

## Which Anthropic key is the platform key

Matched, not configured: each platform row's secret is compared with the
`partial_key_hint` of the organization's keys (`anthropic_usage.indice_correspond`),
so a rotated key is followed without a setting to update. A row no hint designates is
listed in `unmatched_platform_rows`; a row two hints designate is `ambiguous` and
priced under neither. Secrets are read and dropped in the worker thread: the output
carries key ids and names only.

Requires `OTO_ANTHROPIC_ADMIN_KEY` (an Anthropic Admin API key); absent, a named
refusal (`admin_key_not_configured`). `PLATFORM_ADMIN`, read-only.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .. import anthropic_usage, credentials_store, db
from ._authz import PLATFORM_ADMIN
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)

FOURNISSEUR = "anthropic"
OUTIL = "anthropic_tokens"


class ModelSpendInput(BaseModel):
    day: Optional[date] = Field(None, description=(
        "The UTC day to reconcile (YYYY-MM-DD). Omitted = yesterday — Anthropic's report "
        "is final a few minutes after the day ends."))


class PlatformKeySpend(BaseModel):
    label: str
    api_key_id: Optional[str] = None
    name: Optional[str] = None
    anthropic_usd: float = 0.0
    # `matched` | `unmatched` (no hint designates this row) | `ambiguous` (several do)
    match: str


class OtherKeySpend(BaseModel):
    api_key_id: Optional[str] = None   # null = usage made in the Console
    name: Optional[str] = None
    usd: float


class ModelSpend(BaseModel):
    day: str
    platform_keys: list[PlatformKeySpend]
    anthropic_usd: float
    metered_usd: float
    gap_usd: float
    gap_pct: float
    other_keys: list[OtherKeySpend]
    unknown_models: list[str]
    unmatched_platform_rows: list[str]


def _cles_plateforme() -> list[tuple[str, str]]:
    """(label, secret) de chaque clé `anthropic` posée au rang plateforme."""
    out = []
    for r in credentials_store.list_platform_credentials(FOURNISSEUR):
        ligne = credentials_store.get_credential_with_meta(
            credentials_store.PLATFORM, r["label"], FOURNISSEUR, "")
        if ligne and ligne.get("secret"):
            out.append((r["label"], ligne["secret"]))
    return out


def rapprocher(jour: date, *, usage: list[dict], cles: list[dict],
               plateforme: list[tuple[str, str]], mesure_microdollars: int) -> dict:
    """Le rapprochement d'un jour — pur, chaque entrée injectable."""
    noms = {c.get("id"): c.get("name") for c in cles}
    par_ligne, ids_plateforme = [], set()
    for label, secret in plateforme:
        trouvees = [c for c in cles if anthropic_usage.indice_correspond(
            c.get("partial_key_hint"), secret)]
        if len(trouvees) == 1:
            ids_plateforme.add(trouvees[0]["id"])
            par_ligne.append({"label": label, "api_key_id": trouvees[0]["id"],
                              "name": trouvees[0].get("name"), "match": "matched"})
        else:
            par_ligne.append({"label": label, "match":
                              "ambiguous" if trouvees else "unmatched"})
    plat_u: dict[str, int] = {}
    autres_u: dict[Optional[str], int] = {}
    inconnus: set[str] = set()
    for res in usage:
        u = anthropic_usage.microdollars(res)
        if u is None:
            inconnus.add(res.get("model") or "(none)")
            continue
        cle = res.get("api_key_id")
        if cle in ids_plateforme:
            plat_u[cle] = plat_u.get(cle, 0) + u
        else:
            autres_u[cle] = autres_u.get(cle, 0) + u
    for ligne in par_ligne:
        ligne["anthropic_usd"] = round(plat_u.get(ligne.get("api_key_id"), 0) / 1e6, 4)
    anthropic = sum(plat_u.values()) / 1e6
    mesure = mesure_microdollars / 1e6
    ecart = anthropic - mesure
    return {
        "day": jour.isoformat(), "platform_keys": par_ligne,
        "anthropic_usd": round(anthropic, 4), "metered_usd": round(mesure, 4),
        "gap_usd": round(ecart, 4),
        "gap_pct": round(ecart / anthropic * 100, 2) if anthropic > 0 else 0.0,
        "other_keys": [{"api_key_id": k, "name": noms.get(k), "usd": round(u / 1e6, 4)}
                       for k, u in sorted(autres_u.items(), key=lambda kv: -kv[1]) if u > 0],
        "unknown_models": sorted(inconnus),
        "unmatched_platform_rows": [l["label"] for l in par_ligne if l["match"] != "matched"],
    }


def _lire(jour: date, key: str) -> dict:
    """Tout ce qui bloque — réseau, coffre, base — dans UN fil hors de la boucle."""
    return rapprocher(jour, usage=anthropic_usage.usage_du_jour(jour, key),
                      cles=anthropic_usage.cles_d_api(key), plateforme=_cles_plateforme(),
                      mesure_microdollars=db.quantite_du_jour(OUTIL, credentials_store.PLATFORM,
                                                              jour))


async def _model_spend(ctx: ResolvedCtx, inp: ModelSpendInput) -> dict:
    key = anthropic_usage.admin_key()
    if not key:
        raise AuthzDenied(409, "admin_key_not_configured",
                          "OTO_ANTHROPIC_ADMIN_KEY is not set on this instance: without an "
                          "Anthropic Admin API key, its usage report cannot be read.")
    jour = inp.day or (datetime.now(timezone.utc) - timedelta(days=1)).date()
    try:
        return await asyncio.to_thread(_lire, jour, key)
    except anthropic_usage.AnthropicAdminError as e:
        logger.warning("model spend %s : %s", jour, e)
        raise AuthzDenied(502, "anthropic_admin_unavailable",
                          f"Anthropic's Admin API did not answer: {e}") from None


CAPABILITIES += [
    Capability(
        key="admin.model_spend", handler=_model_spend,
        Input=ModelSpendInput, Output=ModelSpend,
        authz=PLATFORM_ADMIN,
        errors=(
            DeclaredError(409, "admin_key_not_configured",
                          "the instance has no `OTO_ANTHROPIC_ADMIN_KEY`"),
            DeclaredError(502, "anthropic_admin_unavailable",
                          "Anthropic's Admin API refused or did not answer"),
        ),
        description=(
            "[platform admin] Reconciles one UTC day (default: yesterday) of what "
            "ANTHROPIC charged this instance's platform `anthropic` keys — its own usage "
            "report, priced at the catalogue tarifs — against the `anthropic_tokens` "
            "rows the call journal metered on the platform rung. Returns per platform "
            "key its Anthropic spend, the totals, `gap_usd`/`gap_pct` (positive = spend "
            "no journal row carries), `other_keys` (spend on any other key of the "
            "Anthropic organization: unmetered by construction; null id = Console), "
            "`unknown_models` and `unmatched_platform_rows`. Platform keys are matched "
            "to Anthropic's by `partial_key_hint`. Cannot attribute a gap to an org; "
            "tenant keys are out of scope. Read-only."),
        mcp="oto_admin_model_spend",
        rest=RestBinding("GET", "/api/admin/platform-keys/model-spend"),
    ),
]
