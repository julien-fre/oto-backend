"""Mes ABONNEMENTS de modèles — les lire, me connecter, me déconnecter, effacer mon bac.

L'écran « Fournisseurs de modèles » d'une personne (OTO-130). Cinq gestes, tous
au palier MEMBRE : un abonnement appartient à qui le paie, et personne d'autre —
pas même un admin de son org — n'a à le lire ni à le couper.

**Ce qui n'est PAS ici, et ne le sera pas :**

- *Poser un identifiant.* Il n'y en a aucun à poser. La personne se connecte
  DANS son bac à sable, par la procédure du fournisseur, et la session n'en sort
  jamais. Une route qui recevrait un jeton d'abonnement serait exactement ce que
  la politique du fournisseur interdit (« developers may not collect, store, or
  intermediate »), et rendrait illicite tout le chemin.

**Se connecter** passe par la ferme (`oto_mcp.ferme`) en deux temps : la route rend
l'URL du fournisseur, que la personne ouvre dans SON navigateur ; elle y colle le
code affiché, que la route remet au programme du bac. Ce code est à usage unique et
inutilisable hors du bac : la session naît et reste dans le bac.

**Pas de face MCP** (`mcp=None`) : ces gestes sont ceux d'un écran de réglages,
et « déconnecte-moi » n'est pas une capacité qu'un agent doive pouvoir invoquer
au milieu d'un run.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from .. import db, ferme
from . import _abonnement
from ._authz import SUB_ONLY
from ._types import (AuthzDenied, Capability, DeclaredError, ResolvedCtx,
                     RestBinding)
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)

_PATH = "/api/me/model-subscriptions"
_PATH_UN = "/api/me/model-subscriptions/{family}"
_PATH_CONNEXION = "/api/me/model-subscriptions/{family}/login"
_PATH_CODE = "/api/me/model-subscriptions/{family}/login/code"


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


class ConnexionInput(BaseModel):
    family: str = Field(description="The model family to connect, e.g. `claude_subscription`.")
    email: Optional[str] = Field(
        None, description="Pre-fills the provider's sign-in page. Optional.")


class ConnexionOuverte(BaseModel):
    url: str = Field(description=("The provider's sign-in page. Open it in YOUR browser, "
                                  "sign in, then send the code it shows to "
                                  "`PUT …/login/code`."))


class CodeInput(BaseModel):
    family: str = Field(description="The model family being connected.")
    code: str = Field(description="The code the provider's page showed after sign-in.")


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
    waiting_jobs: int = Field(
        0, description=("How many of your jobs are queued on this subscription right "
                        "now. While you are signed out, need to reconnect, or wait on "
                        "a plan limit, they WAIT — none fail — and they resume on "
                        "their own once you are back."))


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


# ⚠️ `def`, pas `async def` (`docs/event-loop-perf.md`) : ces deux handlers ne font que
# de l'I/O BLOQUANTE (psycopg). Un `async def` sans `await` s'exécute DANS la boucle
# et gèle tout le serveur le temps de la requête ; un `def` part en threadpool.
# Écrits `async` au premier jet — les bancs passaient, c'est la prod qui aurait gelé.
def _liste(ctx: ResolvedCtx, inp: AbonnementsInput) -> dict:
    return {"subscriptions": [
        {**_servi(l),
         "waiting_jobs": db.travaux_en_attente_d_abonnement(ctx.sub, l["famille"])}
        for l in db.user_subscriptions.list_subscriptions(ctx.sub)]}


def _exiger_famille(famille: str) -> None:
    if not _abonnement.est_abonnement(famille):
        raise AuthzDenied(
            400, "unknown_family",
            f"`{famille}` n'est pas une famille servie par abonnement "
            f"({', '.join(sorted(_abonnement.FAMILLES))}).")


def _par_la_ferme(geste):
    """Un geste de la ferme, ou un refus nommé : l'échec d'un bac ne sort jamais en 500."""
    try:
        return geste()
    except ferme.FermeIndisponible as e:
        if e.statut == 409:
            raise AuthzDenied(409, "login_failed",
                              "aucune connexion en attente (expirée ?). Recommence depuis le début.")
        if e.statut == 422:
            raise AuthzDenied(422, "login_failed",
                              f"le fournisseur a refusé la connexion : {e}. Recommence depuis le début.")
        raise AuthzDenied(502, "farm_unavailable", f"la ferme ne répond pas : {e}")


def _connecter(ctx: ResolvedCtx, inp: ConnexionInput) -> dict:
    _exiger_famille(inp.family)
    _abonnement.exiger_ouvert(ctx.sub, inp.family)
    bac = ferme.bac_de(ctx.sub)
    _par_la_ferme(lambda: ferme.creer(bac))
    return {"url": _par_la_ferme(lambda: ferme.demarrer_connexion(bac, inp.email))}


def _valider_code(ctx: ResolvedCtx, inp: CodeInput) -> dict:
    _exiger_famille(inp.family)
    _abonnement.exiger_ouvert(ctx.sub, inp.family)
    bac = ferme.bac_de(ctx.sub)
    etat = _par_la_ferme(lambda: ferme.transmettre_code(bac, inp.code))
    if not etat.get("loggedIn"):
        raise AuthzDenied(422, "login_failed",
                          "le fournisseur n'a pas ouvert de session. Recommence depuis le début.")
    # La connexion est un geste de la PERSONNE (pas une observation) : elle défait
    # une déconnexion. Le palier est ce que le programme a lu — ni adresse ni org.
    db.user_subscriptions.upsert_sandbox(ctx.sub, inp.family, bac)
    ligne = db.user_subscriptions.marquer_statut(
        ctx.sub, inp.family, db.user_subscriptions.CONNECTE,
        plan=etat.get("subscriptionType"), method=etat.get("authMethod"), ok=True)
    return {**_servi(ligne),
            "waiting_jobs": db.travaux_en_attente_d_abonnement(ctx.sub, inp.family)}


def _retirer(ctx: ResolvedCtx, inp: AbonnementInput) -> dict:
    _exiger_famille(inp.family)
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

    # ⚠️ Le bac est DÉTRUIT avant que la ligne parte (revue du 23/09/2026). L'ordre
    # inverse rendait `sandbox_destroyed: true` sans rien détruire, et l'identifiant
    # du bac ne survivait que dans un log : la session de la personne restait chez
    # nous alors qu'on lui disait l'inverse. Une destruction qui échoue se DIT (502),
    # la ligne reste, et le geste se refait.
    bac = ligne.get("sandbox_id")
    if bac:
        # D'abord couper : plus aucun travail ne part vers un bac en cours de destruction.
        db.user_subscriptions.marquer_statut(
            ctx.sub, inp.family, db.user_subscriptions.DECONNECTE)
        _par_la_ferme(lambda: ferme.detruire(bac))
    db.user_subscriptions.oublier(ctx.sub, inp.family)
    return {"ok": True, "family": inp.family, "sandbox_destroyed": bool(bac)}


_DOC_LISTE = """List YOUR model subscriptions and their state.

A subscription lets your own agents run on the plan you already pay for, inside a
sandbox that belongs to you. The platform never holds your provider session: you
sign in inside that sandbox, through the provider's own flow.

This is member-scope only, always: a subscription belongs to whoever pays for it,
and no one else — not even an admin of your organisation — reads or cuts it."""

_DOC_CONNECTER = """Start connecting a model subscription: returns the provider's sign-in URL.

Open the URL in YOUR browser and sign in with your own account; the provider then
shows a code. Send it to `PUT /api/me/model-subscriptions/{family}/login/code`.
Your session is created inside your sandbox and never leaves it — the platform
never sees your credentials. Open to named people only."""

_DOC_CODE = """Finish connecting a model subscription with the code the provider showed.

The code is single-use and only works inside your sandbox. On success the
subscription is `connected` and your jobs on it can run."""

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
            DeclaredError(502, "farm_unavailable",
                          "la destruction du bac a échoué : rien n'est effacé"),
        ),
        rest=RestBinding("DELETE", _PATH_UN),
    ),
    Capability(
        key="me.model_subscriptions.connect", handler=_connecter, Input=ConnexionInput,
        authz=SUB_ONLY, Output=ConnexionOuverte, description=_DOC_CONNECTER,
        mcp=None,
        errors=(
            DeclaredError(400, "unknown_family",
                          "une famille qui n'est pas servie par abonnement"),
            DeclaredError(403, "subscription_not_enabled",
                          "ce chemin n'est pas ouvert à cette personne"),
            DeclaredError(502, "farm_unavailable", "la ferme des bacs ne répond pas"),
        ),
        rest=RestBinding("POST", _PATH_CONNEXION),
    ),
    Capability(
        key="me.model_subscriptions.login_code", handler=_valider_code, Input=CodeInput,
        authz=SUB_ONLY, Output=Abonnement, description=_DOC_CODE,
        mcp=None,
        errors=(
            DeclaredError(400, "unknown_family",
                          "une famille qui n'est pas servie par abonnement"),
            DeclaredError(403, "subscription_not_enabled",
                          "ce chemin n'est pas ouvert à cette personne"),
            DeclaredError(409, "login_failed", "aucune connexion en attente"),
            DeclaredError(422, "login_failed", "le fournisseur n'a pas ouvert de session"),
            DeclaredError(502, "farm_unavailable", "la ferme des bacs ne répond pas"),
        ),
        rest=RestBinding("PUT", _PATH_CODE),
    ),
]
