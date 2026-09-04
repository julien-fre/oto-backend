"""Brevo — automations (marketing workflows) via l'API PRIVÉE de l'éditeur.

⚠️ API privée non documentée : `workflow-apis.brevo.com/v1`, auth = **session
navigateur vivante** (cookie `auth` httpOnly). Reverse-engineerée depuis l'éditeur
v5 (exploration o-browser du 2026-06-24). Peut casser sans préavis côté Brevo. À NE
PAS confondre avec l'API PUBLIQUE v3 (`api.brevo.com/v3`, clé `api-key`) qui gère
transactionnel / contacts / campagnes — mais PAS l'authoring d'automations (d'où ce
connecteur séparé).

Exécution — **Browserbase** (`oto_mcp/browserbase.py`). Le token Brevo n'est accepté
que depuis une **session navigateur vivante** ; un `httpx`/curl brut est rejeté (403),
et une session **ne se transplante pas** par export de cookie. On loue donc un Chrome
distant : l'utilisateur se logue UNE fois via la **Live View** (il gère SSO/captcha/2FA),
sa session persiste dans un **Context** Browserbase (= le credential per-user, coffre
`brevoauto`), et chaque appel `workflow-apis` s'exécute en `fetch()` DANS une session
éphémère du Context (cf. `browserbase.run_fetch`). Prouvé 200 le 2026-06-24. Creds
plateforme = env `BROWSERBASE_API_KEY` / `BROWSERBASE_PROJECT_ID`.

Surface = endpoints **vérifiés empiriquement** :
- onboarding : `brevoauto_connect_start` (→ Live View) / `brevoauto_connect_status` (persiste) ;
- lecture : `listing`, workflow complet (triggers + steps + câblage), catalogue ;
- écriture : créer / configurer / supprimer trigger & step (avec `prev` +
  `condition_node` + `next_steps`), activer.

NON exposé (API distincte lourde) : la **création d'un template d'email**
(`/editor-api/*` + `/email/templates/{id}`). Un step `send_email` référence un
`template_id` existant ; le contenu se conçoit dans l'UI Brevo (ou via l'API v3).

**Surface consolidée (ADR 0047 §Amendement, appliqué au connecteur brevoauto le
2026-08-11 : 13 tools → 5)** : un tool par OBJET métier, le verbe en paramètre `op` —
`brevoauto_automation` (list/get/catalog/create/status : le scénario lui-même, tous
scopés par `workflow_id` sauf `list`/`create`), `brevoauto_trigger` (add/configure/
delete : une porte d'entrée, désignée par `trigger_point_id`) et `brevoauto_step`
(add/configure/delete : une étape, désignée par `step_id`). Trigger et step restent
DEUX tools : objets distincts, identifiants distincts (`trigger_point_id` vs
`step_id`), endpoints distincts — les fusionner ne partagerait que `workflow_id`.

Le couple `brevoauto_connect_start` / `brevoauto_connect_status` reste SEUL : c'est le
patron plateforme des flux de connexion en deux temps (`*_connect_start` /
`*_connect_status` sur unipile, pennylaneged, crunchbase — des *handles* au sens MRTR),
ses paramètres (`context_id`, `session_id`, rendus par le start) ne recouvrent AUCUN
paramètre du métier automation, et sa cible de convergence est la capacité transverse
`oto_connector op=connect`, pas une fusion par connecteur.

⚠️ **Ce module ÉCRIT sur le compte Brevo réel** (créer / configurer / supprimer un
scénario, un trigger, une étape ; activer un scénario qui enverra de vrais emails).
Deux crans : `brevoauto_trigger` et `brevoauto_step` n'ont QUE des ops d'écriture →
leur `op` est **obligatoire**, aucun défaut, donc aucune écriture n'est atteignable par
omission ; `brevoauto_automation` a pour défaut `op="list"`, une **LECTURE**. Une op
inconnue est refusée AVANT la résolution du credential — elle n'atteint jamais Brevo.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastmcp import Context, FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR

from .. import access, browser_session, browserbase
from ..auth.hooks import current_user_sub_from_token

logger = logging.getLogger(__name__)

# Couple (API privée, page d'origine) propre à Brevo — passé au substrat générique
# `browserbase.run_fetch`. Le `fetch` est same-origin avec l'app (app.brevo.com)
# pour porter le cookie de session ; l'API workflow-apis.* est un sous-domaine de
# brevo.com joignable avec ce cookie.
_API = "https://workflow-apis.brevo.com/v1"
_APP = "https://app.brevo.com/"

# Ops de chaque tool, lectures d'abord. Source unique : la validation d'entrée, le
# message de refus ET l'annotation `Literal[…]` de `op` (donc l'`enum` du schéma JSON
# servi au modèle) en dérivent — une op ajoutée ne peut pas être acceptée sans être
# annoncée (ni l'inverse). `brevoauto_trigger`/`brevoauto_step` n'ont aucune lecture :
# leur `op` n'a donc PAS de défaut (cf. docstring de module).
# ⚠️ Ces constantes sont subscriptées dans un `Literal[…]` : garder des TUPLES (une
# liste est non hashable → `Literal[[…]]` lève à la résolution des annotations).
_AUTOMATION_READ_OPS = ("list", "get", "catalog")
_AUTOMATION_WRITE_OPS = ("create", "status")
_AUTOMATION_OPS = _AUTOMATION_READ_OPS + _AUTOMATION_WRITE_OPS
_AUTOMATION_OPS_ERROR = (
    "op doit être 'list', 'get', 'catalog', 'create' ou 'status'")

_TRIGGER_OPS = ("add", "configure", "delete")
_TRIGGER_OPS_ERROR = "op doit être 'add', 'configure' ou 'delete'"

_STEP_OPS = ("add", "configure", "delete")
_STEP_OPS_ERROR = "op doit être 'add', 'configure' ou 'delete'"

# États d'un scénario, tels que l'API les accepte.
_STATUSES = ("active", "paused", "draft")


async def _verify_session(session_id: str) -> browser_session.Verdict:
    """Login Brevo confirmé ? Vérifie sur la session VIVANTE la présence du cookie
    `auth` httpOnly (posé par le login Brevo). Partagé par les deux surfaces de
    connexion (dashboard REST + MCP) via `browser_session`. Rend un `Verdict` : un
    refus DIT son motif, sans quoi l'agent appelant n'a d'autre conduite que de
    recommencer en boucle (cf. en-tête de `browser_session`)."""
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp(browserbase.connect_url(session_id))
        try:
            c = b.contexts[0] if b.contexts else await b.new_context()
            cks = await c.cookies()
            if any(x["name"] == "auth" for x in cks):
                return browser_session.Verdict(True, browser_session.LOGGED_IN)
            return browser_session.Verdict(
                False, browser_session.NO_SESSION,
                "Le cookie de session Brevo (`auth`) est absent : le login n'est pas "
                "allé au bout. Finis-le dans la Live View, puis relance "
                "`brevoauto_connect_status` avec les mêmes identifiants de session.")
        finally:
            await b.close()


# Déclare Brevo comme connecteur à session navigateur (start générique + ce verify) —
# alimente le flux de connexion REST (dashboard) ET MCP. À l'import du module.
browser_session.register("brevoauto", _verify_session, login_url=_APP)


def _err(msg: str, code: int = INVALID_PARAMS) -> McpError:
    return McpError(ErrorData(code=code, message=msg))


def _need(value, name: str, op: str):
    """Argument obligatoire pour CET op — erreur actionnable qui NOMME l'op et
    l'argument, jamais un fallback.

    Une chaîne vide compte comme absente (`name=""` créerait un scénario sans nom,
    `step_name=""` poserait le bloc de config sous une clé vide). Un `config={}` reste
    valide : configurer avec un bloc vide est un cas légitime de l'API — c'est
    l'ABSENCE de `config` qu'on refuse, car la fusion par `op=` a fait passer ce
    paramètre de « requis par le schéma » à « optionnel », et un write muet qui
    écrase la config d'une étape par les seuls défauts serait indétectable.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _err(f"op='{op}' requiert {name}")
    return value


def _sub() -> str:
    sub = None
    try:
        sub = current_user_sub_from_token()
    # noqa: SILENT — dette déclarée : sub avalé (#424, verdict C — seam commun)
    except Exception:
        pass
    if not sub:
        raise _err("Auth requise — ce tool ne marche que sur le transport HTTP authentifié.")
    return sub


def _context_id() -> str:
    """Context Browserbase de l'utilisateur (= sa session Brevo loguée), résolu du
    coffre. Lève une McpError actionnable si Brevo n'est pas connecté."""
    try:
        return access.resolve_credential("brevoauto", want="byo").key
    except McpError:
        raise _err("Brevo non connecté. Lance `brevoauto_connect_start` pour te loguer "
                   "(une fois) via la Live View.")


async def _api(method: str, path: str, body: Optional[dict] = None) -> dict:
    """Exécute un appel `workflow-apis` dans la session Browserbase de l'user.
    Renvoie le `data` décodé. Lève une McpError actionnable sinon."""
    if not browserbase.is_configured():
        raise _err("Browserbase non configuré côté plateforme "
                   "(BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID).", code=INTERNAL_ERROR)
    ctx_id = _context_id()
    try:
        res = await browserbase.run_fetch(ctx_id, method, path, body, base=_API, app=_APP)
    except browserbase.BrowserbaseError as e:
        raise _err(f"Exécution Browserbase échouée : {e}", code=INTERNAL_ERROR)
    st = res.get("status")
    if st in (401, 403):
        raise _err("Session Brevo expirée / déconnectée — relance `brevoauto_connect_start`.")
    if not (200 <= (st or 0) < 300):
        raise _err(f"Brevo a renvoyé {st} : {str(res.get('data'))[:200]}", code=INTERNAL_ERROR)
    return res["data"]


def register(mcp: FastMCP) -> None:

    # --- Onboarding (Live View) --------------------------------------------
    @mcp.tool()
    def brevoauto_connect_start(ctx: Context) -> dict:
        """Démarre la connexion à Brevo (automations). Ouvre un navigateur distant
        et renvoie une **`live_view_url`** : ouvre-la, connecte-toi à Brevo
        normalement (email/mot de passe, Google SSO, captcha — tu gères tout dans
        cette fenêtre). Puis appelle `brevoauto_connect_status(context_id, session_id)`
        avec les valeurs renvoyées pour finaliser (ta session est mémorisée ; à
        refaire seulement quand elle expire).
        """
        sub = _sub()
        try:
            out = browser_session.start(sub, "brevoauto")
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        out["instructions"] = ("Ouvre `live_view_url`, connecte-toi à Brevo, puis appelle "
                               "`brevoauto_connect_status` avec context_id + session_id.")
        return out

    @mcp.tool()
    async def brevoauto_connect_status(ctx: Context, context_id: str,
                                   session_id: str) -> dict:
        """Finalise la connexion Brevo. Vérifie que tu t'es bien logué dans la Live
        View ; si oui, **mémorise** ta session (le Context) pour les prochains
        appels. Renvoie `{connected}`. Rappelle-le si `connected=false` (pas encore
        logué)."""
        sub = _sub()
        try:
            res = await browser_session.finalize(sub, "brevoauto", context_id, session_id)
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        if not res.connected:
            # `reason` + `retry` avant tout : un refus sans motif ne laisse à l'agent
            # que la reconnexion en boucle (cf. en-tête de `browser_session`).
            return {"connected": False, "reason": res.reason, "retry": res.retry,
                    "hint": res.detail or "Pas encore logué — connecte-toi dans la Live "
                                          "View puis relance."}
        out = {"connected": True, "context_id": context_id, "reason": res.reason,
               "login_verified": not res.warning}
        if res.warning:
            out["warning"], out["retry"] = res.warning, False
        return out

    # --- Le scénario lui-même ----------------------------------------------
    @mcp.tool()
    async def brevoauto_automation(ctx: Context,
                                   op: Literal[_AUTOMATION_OPS] = "list",
                                   workflow_id: Optional[int] = None,
                                   name: Optional[str] = None,
                                   description: str = "",
                                   status: str = "active") -> dict:
        """Une automation (scénario marketing) du compte Brevo — lister, lire sa
        structure, lire la palette de l'éditeur, créer, activer / mettre en pause.

        `op`:
        - **"list"** (défaut) : liste les automations (scénarios marketing) du compte
          Brevo connecté. Renvoie `workflows[]` avec `id`, `scenario_name`, `status`,
          `created_at`/`updated_at`. Utilise l'`id` comme `workflow_id` des ops
          suivantes.
        - **"get"** : structure complète d'une automation : triggers (portes
          d'entrée), steps (étapes, MAP keyée par id) et le câblage du graphe
          (`next`/`prev`, `is_condition`, `condition_node`). Inclut le DSL compilé des
          conditions (`fe_query` / `dsl`).
        - **"catalog"** : catalogue des triggers disponibles (palette de l'éditeur),
          groupés par source (contacts / email / WhatsApp…). Chaque entrée porte son
          `internal_action_id`, `action_type`, label — à passer à
          `brevoauto_trigger(op="add")` / `brevoauto_step(op="add")`. `workflow_id`
          sert de contexte à la palette et reste optionnel.
        - **"create"** — ⚠️ ÉCRIT : crée un scénario d'automation VIDE et renvoie
          `{workflow_id}`. Étape 1 du build : ensuite `brevoauto_trigger(op="add")`
          (porte d'entrée), puis `brevoauto_step(op="add")` +
          `brevoauto_step(op="configure")`, puis
          `brevoauto_automation(op="status", status="active")`.
        - **"status"** — ⚠️ ÉCRIT : active / met en pause un scénario. `status` ∈
          `active` | `paused` | `draft`. À appeler en dernier, tous les nœuds créés ET
          configurés.

        Args:
            op: list (défaut) | get | catalog | create | status.
            workflow_id: op="get"/"status" — l'id du scénario (cf. op="list") ;
                op="catalog" — contexte optionnel de la palette.
            name: op="create" — nom du scénario (obligatoire).
            description: op="create" — description libre (facultative).
            status: op="status" — `active` | `paused` | `draft` (défaut `active`).
        """
        # Refus AVANT toute résolution de credential : une op inconnue n'atteint
        # jamais Brevo — donc jamais, par un chemin dérivé, une écriture.
        if op not in _AUTOMATION_OPS:
            raise _err(_AUTOMATION_OPS_ERROR)

        # ---- lectures --------------------------------------------------------
        if op == "list":
            return await _api("GET", "/workflow/listing")

        if op == "get":
            wid = int(_need(workflow_id, "workflow_id", op))
            return await _api("GET", f"/workflow/{wid}")

        if op == "catalog":
            # `workflow_id` absent (ou 0) → 1 : la palette est celle de l'éditeur,
            # n'importe quel scénario lui sert de contexte.
            wid = int(workflow_id or 0) or 1
            return await _api("GET", f"/workflow/getCategoryData?workflow_id={wid}")

        # ---- écritures -------------------------------------------------------
        if op == "create":
            libelle = str(_need(name, "name", op)).strip()
            return await _api("POST", "/workflow/createcustom", {
                "workflow_name": libelle, "workflow_desc": description or "",
                "multiple_trigger": False, "is_default": True,
            })

        # op == "status"
        wid = int(_need(workflow_id, "workflow_id", op))
        st = (status or "").strip().lower()
        if st not in _STATUSES:
            raise _err("`status` doit valoir active | paused | draft.")
        return await _api("PUT", f"/workflow/{wid}/status", {"status": st})

    # --- Portes d'entrée (triggers) ----------------------------------------
    @mcp.tool()
    async def brevoauto_trigger(ctx: Context, op: Literal[_TRIGGER_OPS],
                                workflow_id: int,
                                trigger_point_id: Optional[int] = None,
                                trigger_name: Optional[str] = None,
                                internal_action_id: Optional[int] = None,
                                event_name: Optional[str] = None,
                                config: Optional[dict] = None,
                                source: str = "contacts") -> dict:
        """Une porte d'entrée (trigger) d'un scénario — l'ajouter, la configurer, la
        supprimer. ⚠️ Les TROIS ops ÉCRIVENT : `op` est obligatoire, il n'a pas de
        défaut.

        `op`:
        - **"add"** : ajoute une porte d'entrée (trigger) à un scénario. Renvoie
          `{start_point_id}`. `trigger_name`/`internal_action_id`/`source` viennent de
          `brevoauto_automation(op="catalog")` (ex. segment =
          `contact_match_one_segment`, id 19, source `contacts`). La condition fine se
          règle ensuite via `op="configure"`.
        - **"configure"** : configure une porte d'entrée déjà ajoutée. `config` =
          réglages spécifiques fusionnés (ex. trigger segment :
          `config={"segment_id":1,"segment_name":"Segment A","is_bulk":True,
          "schedule":{"interval":"daily","schedule_time":"14:00",
          "timezone":"Europe/Paris"}}`). Renvoie `{status}`.
        - **"delete"** : supprime un trigger d'un scénario. Renvoie `{status}`.

        Args:
            op: add | configure | delete (obligatoire — toutes écrivent).
            workflow_id: l'id du scénario visé (obligatoire pour les trois ops).
            trigger_point_id: op="configure"/"delete" — l'id de la porte d'entrée
                (rendu par op="add" sous `start_point_id`, ou lu dans
                `brevoauto_automation(op="get")`).
            trigger_name: op="add" — nom du trigger au catalogue (ex.
                `contact_match_one_segment`).
            internal_action_id: op="add"/"configure" — id catalogue du trigger
                (ex. 19).
            event_name: op="configure" — nom de l'événement configuré.
            config: op="configure" — bloc de réglages, fusionné au corps de la
                requête.
            source: source du catalogue (`contacts` par défaut, ex. `messaging`).
        """
        if op not in _TRIGGER_OPS:
            raise _err(_TRIGGER_OPS_ERROR)
        wid = int(workflow_id)

        if op == "add":
            return await _api("POST", f"/workflow/{wid}/trigger?platform=web", {
                "trigger_name": _need(trigger_name, "trigger_name", op),
                "multiple_entry": False,
                "internal_action_id": int(
                    _need(internal_action_id, "internal_action_id", op)),
                "source": source,
            })

        if op == "configure":
            body: dict = {
                "trigger_point_id": int(
                    _need(trigger_point_id, "trigger_point_id", op)),
                "workflow_id": wid,
                "trigger_point_type": "start_workflow",
                "internal_action_id": int(
                    _need(internal_action_id, "internal_action_id", op)),
                "source": source,
                "event_name": _need(event_name, "event_name", op),
            }
            body.update(_need(config, "config", op))
            return await _api("PUT", "/workflow/update/trigger", body)

        # op == "delete"
        return await _api("DELETE", "/workflow/trigger", {
            "trigger_point_id": int(_need(trigger_point_id, "trigger_point_id", op)),
            "workflow_id": wid})

    # --- Étapes (steps) -----------------------------------------------------
    @mcp.tool()
    async def brevoauto_step(ctx: Context, op: Literal[_STEP_OPS],
                             workflow_id: int,
                             step_id: Optional[int] = None,
                             step_type: Optional[str] = None,
                             step_name: Optional[str] = None,
                             internal_action_id: Optional[int] = None,
                             config: Optional[dict] = None,
                             is_condition: bool = False,
                             prev: Optional[int] = None,
                             next: int = 0,
                             condition_node: Optional[str] = None,
                             next_steps: Optional[list] = None,
                             source: Optional[str] = None) -> dict:
        """Une étape (action ou condition) d'un scénario — l'ajouter, la configurer,
        la supprimer. ⚠️ Les TROIS ops ÉCRIVENT : `op` est obligatoire, il n'a pas de
        défaut.

        `op`:
        - **"add"** : ajoute une étape (action ou condition) et renvoie `{step_id}`.
          Câblage : `prev` = id du nœud précédent ; pour brancher SOUS une condition,
          `prev` = id du nœud condition + `condition_node` = "0" (oui) / "1" (non) ;
          `is_condition=True` pour un nœud de branche (ex.
          `if_else_bool_segmentation`, id 18). Crée le nœud SANS sa config
          (→ `op="configure"`).
        - **"configure"** : configure une étape déjà créée (le write qui porte la
          donnée réelle). `config` = le bloc de réglage, sous une clé nommée
          `step_name`. Exemples :
          - **attente** : `step_name="wait_until"`, id 21,
            `config={"wait_for":[{"unit":"Hours","delay":"2"}]}` ;
          - **email** : `step_name="send_email"`, id 1, `source="messaging"`,
            `config={"template_id":<id existant>,"subject":"…","from_name":"…",
            "from_email":"…","preview_text":"…"}` ;
          - **condition** : `step_name="if_else_bool_segmentation"`, id 18,
            `is_condition=True`, `config={"branches":[{"fe_query":"<DSL json string>"},
            {"is_last_branch":True}]}` + **`next_steps=[<step branche oui>,<step
            branche non>]`** (câblage des sorties).
          Le `send_email` référence un `template_id` **existant** (création de template
          = API distincte, non exposée).
        - **"delete"** : supprime une étape d'un scénario. Renvoie `{status}`.

        Args:
            op: add | configure | delete (obligatoire — toutes écrivent).
            workflow_id: l'id du scénario visé (obligatoire pour les trois ops).
            step_id: op="configure"/"delete" — l'id de l'étape (rendu par op="add").
            step_type: op="add" — type du nœud (`type` côté API, ex. `send_email`).
            step_name: op="configure" — nom du réglage, ET la clé sous laquelle
                `config` est posé dans le corps (ex. `wait_until`, `send_email`).
            internal_action_id: op="add"/"configure" — id catalogue de l'action
                (ex. 21 pour l'attente, 1 pour l'email, 18 pour la condition).
            config: op="configure" — le bloc de réglage (cf. exemples ci-dessus).
            is_condition: op="add" — nœud de branche ; op="configure" — n'est envoyé
                que s'il vaut True.
            prev: op="add" — id du nœud précédent (None = début du graphe).
            next: op="add" — id du nœud suivant (0 = aucun).
            condition_node: op="add" — branche du parent condition, "0" (oui) /
                "1" (non). N'est envoyé que s'il est fourni.
            next_steps: op="configure" — câblage des sorties d'un nœud condition
                (`[<branche oui>, <branche non>]`). N'est envoyé que s'il est fourni.
            source: source du catalogue. ⚠️ Asymétrie conservée de la surface
                d'origine : op="add" l'envoie TOUJOURS (défaut `contacts` si omis),
                op="configure" ne l'envoie QUE s'il est fourni (ex. `messaging` pour
                un `send_email`).
        """
        if op not in _STEP_OPS:
            raise _err(_STEP_OPS_ERROR)
        wid = int(workflow_id)

        if op == "add":
            body: dict = {
                "next": int(next), "prev": (int(prev) if prev is not None else None),
                "type": _need(step_type, "step_type", op),
                "internal_action_id": int(
                    _need(internal_action_id, "internal_action_id", op)),
                "is_condition": bool(is_condition),
                # `source` est optionnel dans la signature fusionnée (op="configure"
                # ne l'envoie que s'il est fourni) : ici le défaut historique
                # `contacts` est restitué, l'API l'attend toujours.
                "source": source or "contacts",
            }
            if condition_node is not None:
                body["condition_node"] = str(condition_node)
            return await _api("POST", f"/workflow/{wid}/step?platform=web", body)

        if op == "configure":
            nom = str(_need(step_name, "step_name", op))
            body = {
                "step_id": int(_need(step_id, "step_id", op)),
                "step_name": nom, "step_type": "",
                nom: _need(config, "config", op), "workflowId": wid,
                "internal_action_id": int(
                    _need(internal_action_id, "internal_action_id", op)),
            }
            if is_condition:
                body["is_condition"] = True
            if source is not None:
                body["source"] = source
            if next_steps is not None:
                body["next_steps"] = next_steps
            return await _api("PUT", f"/workflow/{wid}/step", body)

        # op == "delete"
        return await _api("DELETE", f"/workflow/{wid}/step",
                          {"step_id": int(_need(step_id, "step_id", op))})
