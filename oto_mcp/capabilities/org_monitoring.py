"""Observabilité au niveau ORG — l'étage manquant entre « moi » et « la plateforme ».

Il y avait deux sièges et pas de troisième : un membre voyait SON activité
(`/api/me/activity-summary`, `/api/me/calls`), un opérateur plateforme voyait TOUT
(`oto_admin_monitoring`, ADR 0047) — et le responsable d'une org, rien, hormis
l'export brut du journal d'audit (#67). Or c'est lui qui doit répondre à « qui dans
mon équipe s'en sert », « qu'est-ce qui casse chez nous », « qu'est-ce qui manque à
mes gens ». Ce module ouvre les mêmes lentilles, bornées à SON org.

Scope = **exact et unique** : `tool_calls.org_id` / `usage_signals.org_id`, l'org sous
laquelle l'appel a été émis (seam `current_org`), JAMAIS l'appartenance du membre —
un membre de N orgs n'apporte ici que ce qu'il a fait sous celle-ci. Même règle que
l'export d'audit, donc mêmes chiffres d'un écran à l'autre.

Deux lentilles plateforme ne descendent PAS : `rest` (télémétrie de surface `/api/*` —
santé d'infra, pas usage d'org) et `funnel` (comptes de toute la base). Le funnel a un
pendant org qui répond à la même question à l'échelle d'une équipe : `adoption`.

Surface : console MCP **`oto_org_monitoring(op=…, org_id=…)`** + une route REST par
lentille sous `/api/orgs/{id}/monitoring/*` (per-verbe, idiomatique dashboard — le
verbe en `op` reste la face MCP, cf. monitoring.py). Autz **`ORG_ADMIN_OF`** partout :
le grain nominatif (qui a appelé quoi) est une donnée de responsable, alignée sur
`org.audit_log.export` déjà gaté ainsi.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .. import db, deprecations
from . import audit_log, monitoring
from ._authz import ORG_ADMIN_OF
from ._types import cap_limit, AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_ID = {"id": "org_id"}


# ── sorties ─────────────────────────────────────────────────────────────────
# Deux invariants valent pour TOUTES les lentilles de ce module, et ils décident
# de ce qu'un chiffre veut dire :
#
# ① **Rétention `OTO_JOURNAL_RETENTION_DAYS` jours, 90 par défaut** — portée par le
#    timer `oto-journal-archive`, qui EXPORTE le mois au froid S3 puis le supprime.
#    ⚠️ Jusqu'au 2026-08-28 la fenêtre effective était de ~30 jours et personne ne
#    l'avait décidé : le boot purgeait à 30 j sans archiver, donc plus court que la
#    politique écrite, qu'il vidait d'avance (ADR 0065 lot 0, oto-backend#426). Un
#    chiffre lu ici avant cette date porte donc au plus un mois d'histoire, quelle
#    que soit la fenêtre demandée. `days` accepte jusqu'à 365, mais au-delà de la
#    rétention il n'y a plus de lignes : une fenêtre large ne rend pas plus
#    d'histoire, elle rend le même mois avec un dénominateur trompeur.
# ② **Scope = ce qui a été ÉMIS SOUS cette org** (`tool_calls.org_id` / `usage_signals
#    .org_id`), jamais l'appartenance. Un membre de N orgs n'apporte ici que son
#    activité sous celle-ci — donc « inactif » veut dire « inactif ICI ».
#
# Les horodatages sortent en `"YYYY-MM-DD HH:MM:SS"` (sans `T`, sans offset).

class ToolStat(BaseModel):
    """Un outil sur la fenêtre. `avg_ms`/`p95_ms` sont `null` quand aucune durée n'a
    été enregistrée sur ces appels — pas `0`."""
    tool_name: str
    calls: int
    errors: int
    avg_ms: Optional[int] = None
    p95_ms: Optional[int] = None


class UserStat(BaseModel):
    sub: Optional[str] = None
    email: Optional[str] = None      # None si le sub n'a pas de ligne `users`
    name: Optional[str] = None
    calls: int
    errors: int


class DayStat(BaseModel):
    day: str                          # "YYYY-MM-DD"
    calls: int
    errors: int


class OrgMonitoringSummary(BaseModel):
    """Agrégats d'activité de l'org sur `since_days`.

    ⚠️ **Les totaux et les ventilations n'ont pas le même dénominateur** :
    `total_calls`/`error_count`/`active_users` couvrent TOUT, alors que `by_tool` et
    `by_user` sont tronqués aux **100 premiers**. Sommer `by_tool[].calls` pour
    retrouver `total_calls` échoue dès qu'une org dépasse 100 outils distincts, et
    l'écart n'est signalé nulle part.

    ⚠️ **`by_day` n'a pas de ligne pour les jours à zéro** — ce sont des trous, pas des
    zéros : un graphe qui relie les points sans re-densifier la série ment sur la forme.

    `active_users` = subs distincts ayant appelé SOUS cette org dans la fenêtre — ce
    n'est ni le nombre de membres, ni le nombre de comptes."""
    since_days: int
    total_calls: int
    error_count: int
    active_users: int
    by_tool: list[ToolStat]
    by_user: list[UserStat]
    by_day: list[DayStat]


class CallRow(BaseModel):
    """Une ligne du journal. Les noms `tool_name`/`called_at` sont des ALIAS de compat
    (`tool`/`created_at` en base) — la fiche `op=call`, elle, rend les noms bruts :
    les deux surfaces du même objet ne nomment pas ses champs pareil.

    La ligne ne porte pas les arguments : `arg_keys` en donne les CLÉS, triées (`[]` =
    l'appel n'en portait aucun), jamais une valeur — « cet appel portait-il un numéro
    d'entreprise ? » se répond ici ; le contenu, tronqué et masqué, est `call.args`
    sur la fiche (#634)."""
    id: int
    sub: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    tool_name: Optional[str] = None
    called_at: Optional[str] = None
    duration_ms: Optional[int] = None
    ok: Optional[bool] = None
    error: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    org_id: Optional[int] = None
    # Id de l'event Sentry correspondant, quand l'erreur en a produit un — le pont
    # vers le traceback. None sur un appel réussi (et sur une erreur gérée).
    sentry_event_id: Optional[str] = None
    arg_keys: list[str] = []


class OrgCalls(BaseModel):
    """Journal d'appels de l'org, plus récent d'abord — et ce que son scope laisse dehors.

    Scope = les appels RÉSOLUS sous cette org (`org_id` stampé à l'appel). Un appel d'un
    run de l'org résolu sous une autre org — l'axe `_org` absent le fait retomber sur
    l'org maison de l'appelant — n'y figure pas : `hors_scope` les compte sous les mêmes
    filtres (fenêtre = `days`, sinon la page, sinon 30 j), `hors_scope_hint` dit où les
    voir (`runs/{run_id}`). Un compte lu ici est « lignes + hors_scope », jamais les
    lignes seules (#630).

    ⚠️ `limit` est **silencieusement plafonné à 1000** côté store : demander 5000 rend
    1000 lignes sans le dire. Comme il n'y a ni total ni curseur, une liste de la
    taille du `limit` doit toujours être lue comme « probablement tronquée »."""
    calls: list[CallRow]
    scope: Optional[str] = None
    hors_scope: Optional[int] = None
    hors_scope_hint: Optional[str] = None


class CallDetail(BaseModel):
    """La ligne complète du journal, noms BRUTS (`tool`, `created_at` — pas les alias
    de la liste). `args` = les arguments **tels que journalisés** : tronqués à
    l'écriture (`truncated_args`, 300 caractères par valeur, les valeurs composées
    stringifiées) et masqués (#582 : un jeton part en empreinte `#…`, jamais en clair).
    `null` = l'appel n'en portait aucun. C'est la seule clé qui porte les arguments —
    il n'y a pas d'`arguments` : un lecteur qui la cherche avec un défaut `{}` fabrique
    lui-même l'objet vide (vécu le 29/08/2026, #634)."""
    id: int
    kind: Optional[str] = None
    server: Optional[str] = None
    sub: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    tool: Optional[str] = None
    args: Optional[dict] = None
    ok: Optional[bool] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    org_id: Optional[int] = None
    org_name: Optional[str] = None
    client_id: Optional[str] = None
    sentry_event_id: Optional[str] = None


class OrgCall(BaseModel):
    """Fiche d'UN appel. ⚠️ `call.args` est **tronqué à l'écriture** (`truncated_args`) :
    ce n'est pas le payload intégral, et ça ne doit pas servir à rejouer un appel.

    L'id est un entier séquentiel donc devinable : un appel d'une AUTRE org rend le
    **même 404** qu'un id inexistant — l'absence de résultat ne prouve pas l'absence
    d'appel."""
    call: CallDetail


class ConnectorFailure(BaseModel):
    provider: str
    failures: int
    users_affected: int
    last_at: Optional[str] = None


class OrgConnectorHealth(BaseModel):
    """Échecs de **résolution de credential** par connecteur — « quel connecteur bloque
    mes membres ». Ce ne sont PAS des erreurs d'API tierce : le connecteur n'a même
    pas trouvé de clé à utiliser.

    ⚠️ `total_failures` est la somme de `by_provider`, lui-même **tronqué aux 100
    premiers providers** : sur une org qui dépasserait ce seuil, le total est
    sous-estimé, pas exact."""
    since_days: int
    total_failures: int
    by_provider: list[ConnectorFailure]


class AdoptionMember(BaseModel):
    """Un membre et son activité SOUS cette org.

    ⚠️ **`calls: 0` avec un `last_call_at` non nul ≠ « n'a jamais utilisé »** :
    `last_call_at` n'est pas borné par la fenêtre, il date le DERNIER appel connu. La
    combinaison décrit un **décrochage** — l'action de l'org_admin n'est pas la même
    que pour quelqu'un qui n'a jamais commencé (`last_call_at: null`).

    `connector_failures > 0` = le membre a essayé et rien ne résolvait ; c'est
    l'opposé de « n'a pas essayé », et ça se répare autrement."""
    sub: str
    email: Optional[str] = None
    name: Optional[str] = None
    org_role: Optional[str] = None
    calls: int
    errors: int
    last_call_at: Optional[str] = None
    connector_failures: int


class OrgAdoption(BaseModel):
    """Adoption membre par membre — la lentille qui part de `org_members`, pas des
    appels (sinon le membre à 0 appel, justement celui qu'on cherche, serait invisible).

    ⚠️ **`truncated: true` ne dégrade que la LISTE** : `members` est plafonné à 500,
    les compteurs (`total_members`, `active`, `never_active`, `blocked_by_connector`)
    portent sur toute la population. Ne jamais recompter depuis `members`.

    ⚠️ `never_active` veut dire « aucun appel **sous cette org** » : un membre très
    actif ailleurs y figure — c'est voulu (l'org ne voit que ce qui la concerne), mais
    ça ne se dit pas « il n'utilise pas oto »."""
    org_id: int
    window_days: int
    total_members: int
    active: int
    never_active: int
    blocked_by_connector: int
    truncated: bool
    members: list[AdoptionMember]


class RunRow(BaseModel):
    """Un déroulé. `slug` est un alias de compat = `guide` s'il y en a un, sinon
    `label` — pas un troisième identifiant.

    ⚠️ `guide` est servi aussi sous son nom d'hier, `doctrine`, jusqu'au 29/10/2026
    (#519) — cf. `docs/alias-deprecies.md`.

    ⚠️ `finished_at`/`outcome` à `null` = le déroulé n'a **pas été fermé** (`run_finish`
    jamais appelé, ou sa ligne purgée par la rétention). Ce n'est pas « en cours » au
    sens d'un processus vivant : personne ne le clôturera.

    ⚠️ `n_calls` compte les appels du `run_id` **toutes orgs confondues** — la seule
    valeur de cette lentille qui n'est pas scopée à l'org."""
    run_id: str
    slug: Optional[str] = None
    label: Optional[str] = None
    guide: Optional[str] = None
    doctrine: Optional[str] = None        # ALIAS déprécié (retrait 29/10/2026)
    sub: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    outcome: Optional[str] = None
    n_calls: int


class OrgRuns(BaseModel):
    """Déroulés récents. ⚠️ Le scope d'org est appliqué à la ligne `run_start` : un
    déroulé OUVERT sous une autre org mais poursuivi ici n'apparaît pas, même si ses
    appels, eux, sont dans le journal de l'org. `limit` est plafonné à 500."""
    runs: list[RunRow]


class RunCall(BaseModel):
    """Un appel de la timeline d'un déroulé.

    ⚠️ **Même ligne de journal que `CallDetail`, donc mêmes arguments** : la timeline
    et la fiche d'un appel lisent la même colonne, écrite par la même fonction. Ce qui
    vaut là-bas vaut ici, et le contrat le disait d'un seul côté jusqu'au 01/09/2026 —
    un client prudent affichait « arguments journalisés » sans savoir de quoi il
    parlait.
    """
    id: Optional[int] = Field(default=None, description=(
        "L'identifiant de CET appel — celui que prend `GET …/monitoring/calls/"
        "{call_id}` et `op=call`. ⚠️ Sans lui, une ligne de la timeline était une "
        "impasse : on voyait qu'un appel avait eu lieu sans pouvoir l'ouvrir. C'est "
        "le MÊME identifiant que `CallDetail.id` — la timeline et la fiche parlent de "
        "la même ligne de journal, elles la nomment donc pareil."))
    created_at: Optional[str] = None
    tool: Optional[str] = None
    args: Optional[dict] = Field(default=None, description=(
        "Les arguments **tels que journalisés**, jamais l'appel d'origine : tronqués "
        "à l'écriture (300 caractères par valeur, les valeurs composées stringifiées) "
        "et masqués (un argument déclaré secret pour cet outil part en empreinte "
        "`#…`, jamais en clair — y compris à travers le dispatch universel). `null` = "
        "l'appel n'en portait aucun. Identique à `CallDetail.args` : même colonne, "
        "même voie d'écriture."))
    ok: Optional[bool] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None


class OrgRun(BaseModel):
    """Timeline d'un déroulé, bornée aux appels émis SOUS cette org — un run à cheval
    sur deux orgs n'en montre donc que la tranche locale, sans le signaler. Un `run_id`
    deviné depuis une autre org rend une timeline vide, traduite en 404."""
    run_id: str
    calls: list[RunCall]


class GapRow(BaseModel):
    """Un manque signalé par un membre (`feedback(signal='gap')`). `intent` est le texte
    LIBRE saisi par l'agent — l'axe de regroupement, donc deux formulations du même
    besoin font deux lignes."""
    intent: Optional[str] = None
    kind: Optional[str] = None
    n: int
    last_at: Optional[str] = None
    # Emails distincts des rapporteurs, repli sur le sub si le compte est inconnu :
    # la liste MÉLANGE donc deux formats d'identifiant.
    users: list[str] = []


class OrgGaps(BaseModel):
    """Ce qui manque à TES membres. ⚠️ Une liste vide ne veut pas dire « rien ne
    manque » : elle veut dire que personne n'a émis de signal — c'est une mesure de
    la remontée, pas du besoin."""
    gaps: list[GapRow]


class ToolFeedbackRow(BaseModel):
    tool: Optional[str] = None
    kind: Optional[str] = None
    n: int
    last_at: Optional[str] = None
    users: list[str] = []


class OrgToolQuality(BaseModel):
    """Retours d'outil de TES membres, groupés par (outil, `kind`). ⚠️ `kind` porte
    aussi bien un compliment qu'un défaut : `n` est un volume de SIGNAL, pas un compte
    de problèmes — trier par `n` sans lire `kind` classe en tête un outil très aimé."""
    tools: list[ToolFeedbackRow]


# ── entrées (une par lentille : `org_id` porté par le path REST) ─────────────

class OrgSummaryInput(BaseModel):
    org_id: int
    days: int = 7
    sub: Optional[str] = None      # restreindre à UN membre (email ou sub)


class OrgWindowInput(BaseModel):
    org_id: int
    days: int = 7


class OrgDaysInput(BaseModel):
    org_id: int
    days: int = 30


class OrgCallsInput(BaseModel):
    org_id: int
    limit: int = 200
    sub: Optional[str] = None            # email ou sub
    tool: Optional[str] = None
    errors: bool = False
    days: Optional[int] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    min_duration_ms: Optional[int] = None
    error_contains: Optional[str] = None

    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v):
        return cap_limit(v, 200)


class OrgCallInput(BaseModel):
    org_id: int
    call_id: int


class OrgRunsInput(BaseModel):
    org_id: int
    limit: int = 100

    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v):
        return cap_limit(v, 100)


class OrgRunInput(BaseModel):
    org_id: int
    run_id: str


# ── handlers ────────────────────────────────────────────────────────────────

def _summary(ctx: ResolvedCtx, inp: OrgSummaryInput) -> dict:
    return db.tool_call_stats(since_days=inp.days, org_id=inp.org_id,
                              sub=monitoring._resolve_sub(inp.sub))


def _calls(ctx: ResolvedCtx, inp: OrgCallsInput) -> dict:
    # La page ET son plancher (#630) — le même geste que la console plateforme.
    return monitoring.calls_with_scope(monitoring.CallsInput(
        limit=inp.limit, sub=inp.sub, tool=inp.tool,
        errors=inp.errors, days=inp.days, org_id=inp.org_id,
        run_id=inp.run_id, session_id=inp.session_id,
        min_duration_ms=inp.min_duration_ms, error_contains=inp.error_contains))


def _call(ctx: ResolvedCtx, inp: OrgCallInput) -> dict:
    """Fiche d'un appel — l'id est un entier séquentiel, donc devinable : la garde
    d'org N'EST PAS une formalité. Un appel d'une autre org rend le MÊME 404 qu'un id
    inexistant (ne pas confirmer son existence)."""
    row = db.get_tool_call(inp.call_id)
    if row is None or row.get("org_id") != inp.org_id:
        raise AuthzDenied(404, "unknown_call",
                          f"Aucun appel id={inp.call_id} dans cette org.")
    return {"call": row}


def _connectors(ctx: ResolvedCtx, inp: OrgWindowInput) -> dict:
    return db.connector_failure_stats(since_days=inp.days, org_id=inp.org_id)


def _adoption(ctx: ResolvedCtx, inp: OrgDaysInput) -> dict:
    return db.org_adoption(inp.org_id, active_window_days=inp.days)


def _runs(ctx: ResolvedCtx, inp: OrgRunsInput) -> dict:
    return {"runs": deprecations.lignes_avec_les_deux_noms(
        db.list_runs(inp.limit, org_id=inp.org_id))}


def _run(ctx: ResolvedCtx, inp: OrgRunInput) -> dict:
    """Timeline d'un déroulé. Un run_id d'une autre org rend une timeline vide côté
    db → 404 ici (même raisonnement que `_call`, sur une clé opaque cette fois)."""
    calls = db.get_run(inp.run_id, org_id=inp.org_id)
    if not calls:
        raise AuthzDenied(404, "unknown_run",
                          f"Aucun déroulé `{inp.run_id}` dans cette org.")
    return {"run_id": inp.run_id, "calls": calls}


def _gaps(ctx: ResolvedCtx, inp: OrgDaysInput) -> dict:
    return {"gaps": db.aggregate_gaps(inp.days, org_id=inp.org_id)}


class OrgSignalRow(BaseModel):
    """Un signal tel qu'un responsable d'ORG le voit. `resolved_by` est absent : qui a
    tranché chez nous est notre conduite interne, pas la sienne. La NOTE, elle, est là
    — c'est la réponse qu'on lui doit."""
    id: int
    created_at: Optional[str] = None
    sub: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    org_id: Optional[int] = None
    signal: Optional[str] = None       # tool_feedback | gap
    kind: Optional[str] = None
    target: Optional[str] = None
    body: Optional[str] = None         # LA prose — ce que les compteurs ne disent pas
    session_id: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution: Optional[str] = None
    notified_at: Optional[str] = None


class OrgSignals(BaseModel):
    org_id: int
    signals: list[OrgSignalRow]
    count: int


def _signals(ctx: ResolvedCtx, inp: "OrgMonitoringInput") -> dict:
    """Les signaux BRUTS de cette org — le CORPS, pas seulement le compte.

    **C'est ce qui manquait, et le manque a coûté cinq jours à cinq clients.** Les
    lentilles `gaps` et `tool_quality` rendent l'intitulé et le nombre ; la cause est
    dans la prose. « le projet de destination a été archivé le 21/08 » ne se déduit
    d'aucun compteur — un responsable voyait donc « 8 manques » sans jamais pouvoir
    savoir lesquels, et l'écran d'org le disait explicitement : « pas de drill-down,
    le corps est servi par une capacité PLATEFORME ».

    Le corps est de la prose libre écrite par un agent SOUS un compte de cette org.
    Le rendre à l'`org_admin` suit exactement la règle du journal d'audit : ce qui a
    été émis sous l'org appartient à l'org. Le scope est `usage_signals.org_id`,
    JAMAIS l'appartenance du rapporteur — un prestataire qui travaille pour trois
    clients ne verse pas ses retours dans les trois.

    Ce que ça ne rend pas : l'arbitrage plateforme (`resolved_by`), qui est notre
    conduite interne, pas la leur."""
    lignes = db.list_usage_signals(
        signal=inp.signal, target=inp.tool, limit=inp.limit or 200,
        status=inp.status, org_id=inp.org_id)
    for s in lignes:
        s.pop("resolved_by", None)
    return {"org_id": inp.org_id, "signals": lignes, "count": len(lignes)}


def _tool_quality(ctx: ResolvedCtx, inp: OrgDaysInput) -> dict:
    return {"tools": db.aggregate_tool_feedback(inp.days, org_id=inp.org_id)}


# ── console MCP consolidée `oto_org_monitoring(op=…)` (pattern ADR 0047) ─────

class OrgMonitoringInput(BaseModel):
    op: Literal["summary", "calls", "call", "connectors", "adoption",
                "runs", "run", "gaps", "tool_quality", "signals", "export"]
    org_id: int
    days: Optional[int] = None            # fenêtre (défaut 7 ; adoption/gaps/tool_quality : 30)
    limit: Optional[int] = None           # calls (200) / runs (100) / export (1000)
    sub: Optional[str] = None             # summary/calls : filtre membre (email ou sub)
    tool: Optional[str] = None            # calls : filtre outil exact
    errors: bool = False                  # calls : erreurs seulement
    run_id: Optional[str] = None          # run (requis) / calls (filtre)
    session_id: Optional[str] = None      # calls : tous les appels d'une conversation
    min_duration_ms: Optional[int] = None  # calls : appels lents
    error_contains: Optional[str] = None  # calls : recherche dans le message d'erreur
    call_id: Optional[int] = None         # call (requis)
    signal: Optional[str] = None          # signals : tool_feedback | gap
    status: Optional[str] = None          # signals : open|acknowledged|declined|resolved|pending
    since: Optional[str] = None           # export : borne basse ISO
    until: Optional[str] = None           # export : borne haute ISO

    # Console op-aware : plafond du plus large de ses ops (`export`, 1000) — borner
    # plus bas écrêterait un export légitime. Écrête au lieu de refuser (#300).
    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v):
        return cap_limit(v, 1000) if v is not None else None


def _need(val, code: str, msg: str):
    if val is None or (isinstance(val, str) and not val.strip()):
        raise AuthzDenied(400, code, msg)
    return val


def _console(ctx: ResolvedCtx, inp: OrgMonitoringInput) -> dict:
    oid = inp.org_id
    if inp.op == "summary":
        return _summary(ctx, OrgSummaryInput(org_id=oid, days=inp.days or 7, sub=inp.sub))
    if inp.op == "calls":
        return _calls(ctx, OrgCallsInput(
            org_id=oid, limit=inp.limit or 200, sub=inp.sub, tool=inp.tool,
            errors=inp.errors, days=inp.days, run_id=inp.run_id,
            session_id=inp.session_id, min_duration_ms=inp.min_duration_ms,
            error_contains=inp.error_contains))
    if inp.op == "call":
        return _call(ctx, OrgCallInput(org_id=oid, call_id=_need(
            inp.call_id, "missing_call_id", "`call_id` requis pour call.")))
    if inp.op == "connectors":
        return _connectors(ctx, OrgWindowInput(org_id=oid, days=inp.days or 7))
    if inp.op == "adoption":
        return _adoption(ctx, OrgDaysInput(org_id=oid, days=inp.days or 30))
    if inp.op == "runs":
        return _runs(ctx, OrgRunsInput(org_id=oid, limit=inp.limit or 100))
    if inp.op == "run":
        return _run(ctx, OrgRunInput(org_id=oid, run_id=_need(
            inp.run_id, "missing_run_id", "`run_id` requis pour run.")))
    if inp.op == "gaps":
        return _gaps(ctx, OrgDaysInput(org_id=oid, days=inp.days or 30))
    if inp.op == "tool_quality":
        return _tool_quality(ctx, OrgDaysInput(org_id=oid, days=inp.days or 30))
    if inp.op == "signals":
        # Passe l'Input consolidé tel quel : cette lentille lit `signal`/`status`,
        # deux champs qui n'existent sur aucun des Input par-op. En fabriquer un
        # quatrième pour deux champs ajouterait une forme à garder d'accord.
        return _signals(ctx, inp)
    # export : le journal d'audit org existe déjà (#67) — même autz, même scope,
    # on le REBRANCHE plutôt que d'en écrire un second.
    return audit_log._export(ctx, audit_log.AuditExportInput(
        org_id=oid, since=inp.since, until=inp.until, limit=inp.limit or 1000))


_ADMIN_OF = ORG_ADMIN_OF("org_id")

CAPABILITIES += [
    Capability(key="org.monitoring.summary", handler=_summary, Input=OrgSummaryInput,
               authz=_ADMIN_OF, mcp=None, Output=OrgMonitoringSummary,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/summary", _ID)),
    Capability(key="org.monitoring.calls", handler=_calls, Input=OrgCallsInput,
               authz=_ADMIN_OF, mcp=None, Output=OrgCalls,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/calls", _ID)),
    Capability(key="org.monitoring.call", handler=_call, Input=OrgCallInput,
               authz=_ADMIN_OF, mcp=None, Output=OrgCall,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/calls/{call_id}", _ID)),
    Capability(key="org.monitoring.signals", handler=_signals, Input=OrgMonitoringInput,
               authz=_ADMIN_OF, mcp=None, Output=OrgSignals,
               description="Raw usage signals REPORTED UNDER this org — the BODY, not "
                           "just the count. Filters: `signal` (tool_feedback|gap), "
                           "`tool` (target), `status`. Org admin only.",
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/signals", _ID)),
    Capability(key="org.monitoring.connectors", handler=_connectors, Input=OrgWindowInput,
               authz=_ADMIN_OF, mcp=None, Output=OrgConnectorHealth,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/connectors", _ID)),
    Capability(key="org.monitoring.adoption", handler=_adoption, Input=OrgDaysInput,
               authz=_ADMIN_OF, mcp=None, Output=OrgAdoption,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/adoption", _ID)),
    Capability(key="org.monitoring.runs", handler=_runs, Input=OrgRunsInput,
               authz=_ADMIN_OF, mcp=None, Output=OrgRuns,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/runs", _ID)),
    Capability(key="org.monitoring.run", handler=_run, Input=OrgRunInput,
               authz=_ADMIN_OF, mcp=None, Output=OrgRun,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/runs/{run_id}", _ID)),
    Capability(key="org.monitoring.gaps", handler=_gaps, Input=OrgDaysInput,
               authz=_ADMIN_OF, mcp=None, Output=OrgGaps,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/gaps", _ID)),
    Capability(key="org.monitoring.tool_quality", handler=_tool_quality, Input=OrgDaysInput,
               authz=_ADMIN_OF, mcp=None, Output=OrgToolQuality,
               rest=RestBinding("GET", "/api/orgs/{id}/monitoring/tool-quality", _ID)),
    Capability(
        key="org.monitoring.console", handler=_console, Input=OrgMonitoringInput,
        authz=_ADMIN_OF,
        description=(
            "Observability of YOUR org (org admin) — `org_id` required. op=summary "
            "(aggregates over the org: totals, by tool w/ avg+p95 latency, by member, by "
            "day; `days`, optional `sub` email|sub) / adoption (member by member: who "
            "actually uses oto, who never did, who is blocked by a connector — the org's "
            "answer to 'is my team on board') / calls (call log of the org, newest first; "
            "filters `sub`, `tool`, `errors`, `days`, `run_id`, `session_id`, "
            "`min_duration_ms`, `error_contains`) / call (`call_id`) / runs · run "
            "(`run_id` → timeline) / connectors (which connector fails to resolve for "
            "your members) / gaps · tool_quality (what YOUR members reported missing or "
            "broken, AGGREGATED) / signals (the same reports RAW, with their body — the "
            "counts say how many, only the body says why; filters `signal` "
            "tool_feedback|gap, `tool`, `status`) / export (audit log, `since`/`until` "
            "ISO — compliance evidence). "
            "Everything is scoped to calls EMITTED UNDER this org, never to membership. "
            "Platform-wide investigation is oto_admin_monitoring (platform admin)."),
        mcp="oto_org_monitoring",
    ),
]

