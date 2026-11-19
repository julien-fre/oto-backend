"""Sponsored copilot threads: the OPERATOR pays (the Post studio).

The Post studio is a page of the partner's front that helps a member write a LinkedIn
post about what their workspace built; once posted, the post earns the workspace
credits. People reach it when they are OUT of credits — so it must run when the
workspace's credits are spent, its platform grants withdrawn, or a daily spend limit
reached. The operator pays for it instead.

**Who decides.** The SERVER, never the page: a turn sent with `surface="post_studio"`
opens a sponsored thread only while `OTO_POST_STUDIO_SPONSOR=1`, and only within the
person's quota (`threads_par_mois()` per calendar month, UTC). The decision is written
on the thread (`copilot_threads.sponsor`) when it is created, and every later leg reads
it from there: the page cannot turn an ordinary thread into a sponsored one.

**What it changes, while a sponsored leg runs** (`activer`, a context variable set by
`copilot/routes._etape` and inherited by the threads it starts):
- the model call: the shared key is served without the org's grants
  (`resolve._resolu_parraine`), its reservation is booked to `PAYEUR` and never refused
  for balance (`credits_tours._payeur`, `credits_tours.reserver_tour`), and no daily limit
  applies (`copilot/cle.reserver`);
- a tool call on one of `FOURNISSEURS`: the platform key is served directly, grants and
  daily limit aside, traced `key_mode = "sponsor"` — a mode the partner's billing does
  not bill (it bills `platform` and `tenant` only), so the workspace's credits, ledger
  and daily spend never move;
- the toolbox: only `OUTILS` (and, for some, only the operations listed), so a
  sponsored thread is not a free general-purpose agent; `oto_call` is not in it.

**What it does not change.** Org suspension (a workspace stopped for an ended trial),
the copilot's per-person daily ceiling in USD (`copilot/depense`, the operator's own
guard), the platform's rate limits, and the person's OWN keys: a key of theirs that wins
the cascade is used and never billed, as always — only the platform rung is replaced.
And a ceiling of `appels_max()` model calls per sponsored thread (`CODE_PLAFOND`).

Doc: `docs/copilot.md` §Conversations parrainées."""
from __future__ import annotations

import os
import re
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from typing import Optional

#: The one sponsor there is today.
POST_STUDIO = "post_studio"
#: The ledger payer of a sponsored model call — not `org:<id>`, so no org's balance
#: moves, and the partner's billing (which reads org payers only) never counts it.
PAYEUR = f"sponsor:{POST_STUDIO}"
#: The `key_mode` a sponsored tool call is traced under (never billed to an org).
KEY_MODE = "sponsor"

#: The tools a sponsored thread may call → the operations allowed (`None` = all of
#: them: the tool only reads, or every operation is one the studio needs).
OUTILS: dict[str, Optional[frozenset]] = {
    # who they are: their LinkedIn profile and posts, their company
    "apify_linkedin_profiles": None,
    "apify_linkedin_posts": None,
    "apify_run_status": None,
    "apify_dataset_items": None,
    "waterfall_company": None,
    # what they built: reads of their own workspace
    "oto_context": None,
    "oto_whoami": None,
    "oto_profile": frozenset({"get", "update"}),
    "oto_trigger": frozenset({"list", "get", "links", "graph"}),
    "oto_procedure": frozenset({"list", "get"}),
    "oto_apps": frozenset({"list", "get"}),
    "oto_guide": frozenset({"list", "read"}),
    "data_list_datastores": None,
    "data_get_schema": None,
    "data_aggregate": None,
    # the AI image
    "higgsfield": None,
    "higgsfield_act": frozenset({"create_generation"}),
    # framing and reporting
    "oto_tool_schema": None,
    "run_start": None,
    "run_finish": None,
    "feedback": None,
}

#: The providers whose PLATFORM key a sponsored call is served, grants aside: the model,
#: the LinkedIn reads, the image, and the shared steps of the company waterfall
#: (`docs/waterfall.md` §Société; its free and own-key-only steps need nothing here).
FOURNISSEURS = frozenset({"anthropic", "apify", "higgsfield", "aiark", "apollo",
                          "theirstack"})

#: What ONE sponsored conversation may buy from the paid providers, counted on the thread
#: (`db/copilot.compter_parraine`, atomic): a family → how many calls. Enough for the
#: studio — read the profile and its posts, once more after a correction; the company
#: twice; one AI image —, never a free scraper or image generator. A call that fails
#: gives its count back (`boucle._lancer_outil`): the agent got nothing from it.
QUOTAS: dict[str, int] = {"linkedin": 4, "company": 2, "image": 1}
_FAMILLES = {"apify_linkedin_profiles": "linkedin", "apify_linkedin_posts": "linkedin",
             "waterfall_company": "company"}
#: An image model, not a clip, a voice or a 3D object: refused on its slug's words.
_PAS_UNE_IMAGE = re.compile(r"video|audio|speech|voice|music|sound|lipsync|3d|talking",
                            re.I)

REFUS_OUTIL = ("This tool is not available in the Post studio, which the operator pays "
               "for: it only reads the person's LinkedIn and their workspace, and makes "
               "the post's image.")
REFUS_QUOTA = ("The Post studio has already used its {quota} call(s) of this kind for this "
               "session, which the operator pays for. Carry on with what you have.")
REFUS_IMAGE = ("In the Post studio, the operator pays for ONE still image of at most "
               "${plafond} (`max_usd` ≤ {plafond}), from an image model — not a video, a "
               "voice or a 3D model. Nothing was sent.")
CODE_QUOTA = "post_studio_quota"
CODE_PLAFOND = "post_studio_ceiling"

_courant: ContextVar[Optional[str]] = ContextVar("oto_parrainage", default=None)


def ouvert() -> bool:
    """New sponsored threads may be opened (`OTO_POST_STUDIO_SPONSOR=1`). Existing ones
    keep their sponsor either way: switching it off stops new ones, not a visit."""
    return os.environ.get("OTO_POST_STUDIO_SPONSOR", "").strip() == "1"


def _entier(nom: str, defaut: int, plancher: int) -> int:
    """An integer setting, read at each use. A value that is not an integer ≥ `plancher`
    RAISES — never the default in silence."""
    brut = os.environ.get(nom, "").strip()
    if not brut:
        return defaut
    try:
        valeur = int(brut)
    except ValueError:
        raise ValueError(f"{nom}={brut!r} is not an integer") from None
    if valeur < plancher:
        raise ValueError(f"{nom}={valeur} is below {plancher}")
    return valeur


def threads_par_mois() -> int:
    """Sponsored threads one person may open per calendar month (UTC)."""
    return _entier("OTO_POST_STUDIO_THREADS_PER_MONTH", 3, 0)


def appels_max() -> int:
    """Model calls one sponsored thread may make."""
    return _entier("OTO_POST_STUDIO_MAX_MODEL_CALLS", 40, 1)


def image_max_usd() -> Decimal:
    """The most the operator pays for the studio's AI image, in dollars
    (`OTO_POST_STUDIO_IMAGE_MAX_USD`, 1 by default). Set but not a positive amount
    RAISES — never the default in silence."""
    brut = os.environ.get("OTO_POST_STUDIO_IMAGE_MAX_USD", "").strip()
    if not brut:
        return Decimal("1")
    try:
        valeur = Decimal(brut)
    except InvalidOperation:
        raise ValueError(f"OTO_POST_STUDIO_IMAGE_MAX_USD={brut!r} is not an amount") \
            from None
    if not valeur.is_finite() or valeur <= 0:
        raise ValueError(f"OTO_POST_STUDIO_IMAGE_MAX_USD={brut!r} must be positive")
    return valeur


def famille(target: str, targs: dict) -> Optional[str]:
    """The paid family `target` belongs to (`QUOTAS`), or None: not counted."""
    if target == "higgsfield_act" and (targs or {}).get("op") == "create_generation":
        return "image"
    return _FAMILLES.get(target)


def refus_d_image(targs: dict) -> Optional[str]:
    """Why this generation is not the studio's image (a clip's model, no `max_usd` or
    one above the ceiling), or None."""
    plafond = image_max_usd()
    refus = REFUS_IMAGE.format(plafond=plafond)
    if _PAS_UNE_IMAGE.search(str((targs or {}).get("model") or "")):
        return refus
    try:
        prix = Decimal(str((targs or {}).get("max_usd")))
    except InvalidOperation:
        return refus
    if not prix.is_finite() or prix <= 0 or prix > plafond:
        return refus
    return None


def refus_de_quota(fam: str) -> str:
    return REFUS_QUOTA.format(quota=QUOTAS[fam])


def activer(sponsor: Optional[str]) -> None:
    """Sets (or clears) the sponsor of the leg running in this context, from the
    thread's STORED value only (`copilot/routes._etape`)."""
    _courant.set(sponsor if sponsor == POST_STUDIO else None)


def courant() -> Optional[str]:
    return _courant.get()


def paie(provider: str) -> bool:
    """The operator pays a call to `provider` now: a sponsored leg, a covered provider."""
    return _courant.get() is not None and provider in FOURNISSEURS


def outil_permis(name: str, args: dict) -> bool:
    """`name` with `args` may run in a sponsored thread."""
    if name not in OUTILS:
        return False
    ops = OUTILS[name]
    return ops is None or str((args or {}).get("op") or "") in ops
