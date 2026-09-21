"""Mes ABONNEMENTS de modèles — les lire, me déconnecter, effacer mon bac à sable.

L'écran « Fournisseurs de modèles » d'une personne (OTO-130). Trois gestes, tous
au palier MEMBRE : un abonnement appartient à qui le paie, et personne d'autre —
pas même un admin de son org — n'a à le lire ni à le couper.

**Ce qui n'est PAS ici, et ne le sera pas :**

- *Poser un identifiant.* Il n'y en a aucun à poser. La personne se connecte
  DANS son bac à sable, par la procédure du fournisseur, et la session n'en sort
  jamais. Une route qui recevrait un jeton d'abonnement serait exactement ce que
  la politique du fournisseur interdit (« developers may not collect, store, or
  intermediate »), et rendrait illicite tout le chemin.
- *Connecter.* Ce geste ouvre un terminal sur un bac à sable : il attend
  l'hébergement (infrastructure) et arrivera avec lui. Ce qui existe déjà ici est
  ce dont l'écran a besoin pour DIRE l'état, et ce dont la personne a besoin pour
  partir.

**Pas de face MCP** (`mcp=None`) : ces gestes sont ceux d'un écran de réglages,
et « déconnecte-moi » n'est pas une capacité qu'un agent doive pouvoir invoquer
au milieu d'un run.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from .. import db
from . import _abonnement
from ._authz import SUB_ONLY
from ._types import (AuthzDenied, Capability, DeclaredError, ResolvedCtx,
                     RestBinding)
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)

_PATH = "/api/me/model-subscriptions"
_PATH_UN = "/api/me/model-subscriptions/{family}"


class AbonnementsInput(BaseModel):
    pass


class AbonnementInput(BaseModel):
    family: str = Field(
        description=("The model family this subscription serves, e.g. "
                     "`claude_subscription`."))
    # ⚠️ Deux gestes distincts, et la distinction est la seule chose qui compte
    # ici : se DÉCONNECTER laisse le bac à sable (se reconnecter ne recommence
    # pas de zéro) ; EFFACER détruit le bac, donc la session avec.
    #
    # ⚠️ En QUERY (`?destroy=true`), pas dans un corps : l'adaptateur ne lit un
    # corps sur DELETE que sur opt-in (`reads_body`), et aucune route du dépôt ne
    # le fait. Un corps silencieusement ignoré aurait rendu `destroy` INERTE —
    # la route aurait répondu 200 en ne détruisant rien.
    destroy: bool = Field(
        False,
        description=("`false` (default): sign out — the sandbox is kept, so "
                     "reconnecting later is one sign-in away. `true`: destroy the "
                     "sandbox itself, and everything in it. Irreversible."))


class Abonnement(BaseModel):
    """Un abonnement tel qu'un écran le montre.

    ⚠️ Ni adresse e-mail, ni organisation du fournisseur, ni rien qui approche
    une session : la sonde les lit, la plateforme n'en garde rien."""
    family: str
    statut: str = Field(
        description=("`connected` | `needs_login` | `paused_limit` | "
                     "`disconnected`."))
    plan: Optional[str] = Field(
        None, description="The plan tier the provider reported, e.g. `max`.")
    limit_reset_at: Optional[str] = Field(
        None, description=("When the plan limit lifts, if the person is waiting on "
                           "one. Their jobs stay queued until then — none fail."))
    last_ok_at: Optional[str] = None


class AbonnementsListe(BaseModel):
    subscriptions: list[Abonnement]


class AbonnementRetire(BaseModel):
    ok: bool
    family: str
    sandbox_destroyed: bool = Field(
        description=("Whether the sandbox itself was destroyed. `false` = signed "
                     "out only, the sandbox is still there."))


def _servi(ligne: dict) -> dict:
    def _txt(v):
        return v.isoformat() if hasattr(v, "isoformat") else (v or None)
    return {"family": ligne["famille"], "statut": ligne["statut"],
            "plan": ligne.get("plan"),
            "limit_reset_at": _txt(ligne.get("limit_reset_at")),
            "last_ok_at": _txt(ligne.get("last_ok_at"))}


async def _liste(ctx: ResolvedCtx, inp: AbonnementsInput) -> dict:
    return {"subscriptions": [_servi(l)
                              for l in db.user_subscriptions.list_subscriptions(ctx.sub)]}


async def _retirer(ctx: ResolvedCtx, inp: AbonnementInput) -> dict:
    if not _abonnement.est_abonnement(inp.family):
        raise AuthzDenied(
            400, "unknown_family",
            f"`{inp.family}` n'est pas une famille servie par abonnement "
            f"({', '.join(sorted(_abonnement.FAMILLES))}).")
    ligne = db.user_subscriptions.get_subscription(ctx.sub, inp.family)
    if not ligne:
        raise AuthzDenied(404, "not_connected",
                          f"aucun abonnement `{inp.family}` pour toi.")
    if not inp.destroy:
        # ⚠️ On marque `disconnected` SANS toucher au bac à sable, et c'est le
        # geste courant : la session y dort encore, mais plus aucun travail n'y
        # part (`claim`). Détruire par défaut ferait payer une reconnexion
        # complète à qui voulait juste mettre en pause.
        db.user_subscriptions.marquer_statut(
            ctx.sub, inp.family, db.user_subscriptions.DECONNECTE)
        return {"ok": True, "family": inp.family, "sandbox_destroyed": False}

    bac = db.user_subscriptions.oublier(ctx.sub, inp.family)
    # ⚠️ La ligne part AVANT la destruction. Une destruction qui échoue laisse un
    # bac orphelin — coûteux, mais muet ; l'inverse laisserait la personne
    # « connectée » sur un bac qui n'existe plus, donc des travaux réservés qui
    # ne tourneront jamais. L'infrastructure ramasse les orphelins.
    if bac:
        logger.info("bac à sable `%s` à détruire (famille %s) — la ligne est retirée",
                    bac, inp.family)
    return {"ok": True, "family": inp.family, "sandbox_destroyed": bool(bac)}


_DOC_LISTE = """List YOUR model subscriptions and their state.

A subscription lets your own agents run on the plan you already pay for, inside a
sandbox that belongs to you. The platform never holds your provider session: you
sign in inside that sandbox, through the provider's own flow.

This is member-scope only, always: a subscription belongs to whoever pays for it,
and no one else — not even an admin of your organisation — reads or cuts it."""

_DOC_RETIRER = """Sign out of a model subscription, or destroy its sandbox.

Default (`?destroy=false`): you are signed out. Your agents stop running on
that plan, and nothing is destroyed — reconnecting is one sign-in away.

With `?destroy=true`: the sandbox itself is destroyed, and everything in it.
Irreversible."""


CAPABILITIES += [
    Capability(
        key="me.model_subscriptions.list", handler=_liste, Input=AbonnementsInput,
        authz=SUB_ONLY, Output=AbonnementsListe, description=_DOC_LISTE,
        mcp=None,
        rest=RestBinding("GET", _PATH),
    ),
    Capability(
        key="me.model_subscriptions.remove", handler=_retirer, Input=AbonnementInput,
        authz=SUB_ONLY, Output=AbonnementRetire, description=_DOC_RETIRER,
        mcp=None,
        errors=(
            DeclaredError(400, "unknown_family",
                          "une famille qui n'est pas servie par abonnement"),
            DeclaredError(404, "not_connected",
                          "aucun abonnement de cette famille pour cette personne"),
        ),
        rest=RestBinding("DELETE", _PATH_UN),
    ),
]
