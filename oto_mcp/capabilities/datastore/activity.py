"""Capacités « activité d'un tableau » (journal de travail du datastore, ADR 0046 b4).

Deux lectures, REST-only (le dashboard les rend ; l'agent, lui, a déjà son parcours
dans ses propres réponses `data_*`) :

- `me.datastore.row_activity`  — GET …/rows/{row_id}/activity : ce qui est arrivé à UNE ligne ;
- `me.datastore.activity`      — GET …/activity              : ce qui est arrivé au TABLEAU.

Le journal montre les **deux surfaces** : `kind='mcp'` = appel d'agent, `kind='rest'`
= geste fait au cockpit (posé par `datastore_journal` via `calllog.log_rest_call`).
Avant, seul le MCP était visible — un clic de transition dans le dashboard ne laissait
aucune trace exploitable, d'où l'angle mort « quelle ligne vient de changer d'état ? ».

**Les écritures viennent du journal des révisions** (oto#273, M3) : chaque appel qui a
écrit la ligne porte ses `revisions` — valeurs avant et après, acteur, run, source —
rattachées par le geste (`geste_id` = `tool_calls.call_uid`). Une écriture que
`tool_calls` ne connaît pas (upload signé, formules, maintenance, SQL à la main) devient
une entrée `kind='revision'`. `tool_calls` reste la source de ce que le journal n'a pas :
lectures, réservations, refus. ⚠️ Les révisions écrites avant l'estampille (M1, sans
`geste_id`) ne se rattachent à aucun appel : elles apparaissent en `kind='revision'` À
CÔTÉ de l'appel qui les a faites.

Autz : `SUB_ONLY` au seuil, le vrai gate est la LECTURE du datastore — résolu par le
store (scopé org active + ownership), jamais par l'id nu passé en path. Un datastore
hors périmètre est un 404 (on ne divulgue pas son existence), comme partout ailleurs
dans le datastore.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ... import db
from ...datastore.identite import Adresse
from ...datastore import journal as datastore_journal
from ...datastore import schema as dsv2
from ...datastore.core import DatastoreNotFound, RowNotFound, make_store
from ...db import historique
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES
from .common import EntreeDatastore, HORODATAGE

# La fenêtre annoncée est **DÉRIVÉE de la purge**, jamais recopiée à côté d'elle.
#
# ⚠️ Incident du 08/09/2026, et il a failli coûter une livraison en urgence. Cette
# constante valait `30` en dur, écrite ici pendant que la purge, elle, tournait à 90
# jours dans `maintenance`. Les deux ont divergé sans que rien ne le signale — et la
# requête ci-dessous ne filtre sur AUCUNE date : elle rendait donc des entrées de 41
# jours tout en annonçant une fenêtre de 30. Un consommateur en a conclu que la preuve
# de ce que ses agents avaient fait sur les fiches d'une cliente disparaissait dans
# trois jours, et s'apprêtait à livrer un écran de repli pour une échéance imaginaire.
#
# **Un chiffre qui décrit une purge dont il ne dépend pas finit toujours par mentir.**
# D'où l'import : si la rétention change, cette annonce change avec elle.
from ...maintenance import _JOURNAL_RETENTION_DAYS as RETENTION_DAYS


# La page du parcours d'une ligne : celle que `db.datastore_row_activity` rendait déjà
# par défaut, appliquée aussi aux révisions et au parcours fusionné.
LIMITE_PARCOURS = 50


class RowActivityInput(EntreeDatastore):
    datastore: Adresse
    row_id: str


class DatastoreActivityInput(EntreeDatastore):
    datastore: Adresse
    limit: int = 50


def _store(sub: Optional[str]):
    return make_store(sub)


def _attach_emails(entries: list[dict]) -> list[dict]:
    """Renseigne `email` sur les entrées — l'auteur du geste, en UN lot.

    `tool_calls.email` est NULL en base : ni le sink MCP (`_calllog_identity`, qui ne
    rend que le `sub`) ni `log_rest_call` ne le peuplent. On résout donc à la LECTURE
    (une seule requête pour toute la page, y compris les lignes déjà en base) plutôt
    que d'ajouter une résolution au chemin chaud de chaque geste. Sans ça la colonne
    « qui » du parcours reste vide ou n'affiche qu'un uuid — sur une feature dont
    l'énoncé est précisément « voir l'historique des actions »."""
    subs = [e.get("sub") for e in entries if e.get("sub") and not e.get("email")]
    if not subs:
        return entries
    by_sub = db.emails_by_subs(subs)
    for e in entries:
        if not e.get("email"):
            e["email"] = by_sub.get(e.get("sub"))
    return entries


def _entree_de_revision(row_id: str, revisions: list[dict],
                        status_key: Optional[str]) -> dict:
    """Une écriture que `tool_calls` ne connaît pas → entrée `kind='revision'`, par le
    MÊME producteur que les appels (`_ds_activity_entry`) : mêmes clés, toujours.
    `revisions` est le geste entier, plus récente d'abord."""
    derniere = revisions[0]
    champs = sorted({k for r in revisions for k in (r["diff"] or {})})
    statut = [r["diff"][status_key] for r in reversed(revisions)
              if status_key and status_key in (r["diff"] or {})]
    acteur = derniere["acteur"]
    return db._ds_activity_entry({
        "created_at": derniere["at"],
        "kind": "revision",
        "ok": True,
        # Un sub résout son email à la lecture ; `service:<nom>` n'en a pas.
        "sub": acteur if acteur and not acteur.startswith("service:") else None,
        "run_id": derniere["run_id"],
        "call_uid": derniere["geste_id"],
        # Déballé, comme le relevé d'une mutation (`journal.status_of`, #586) : une
        # colonne d'état à couches afficherait son enveloppe.
        "args": {"id": row_id, "fields": champs,
                 "from_status": dsv2.unwrap(statut[0].get("avant")) if statut else None,
                 "to_status": dsv2.unwrap(statut[-1].get("apres")) if statut else None},
    })


def _joindre_les_revisions(activity: list[dict], ns_id: Optional[int], row_id: str,
                           status_key: Optional[str], limit: int) -> list[dict]:
    """Pose sur chaque appel les révisions de CETTE ligne qu'il a écrites (par le
    geste), et ajoute une entrée pour chaque écriture qu'aucun appel ne porte. Rend le
    parcours trié du plus récent au plus ancien, borné à `limit`."""
    if ns_id is None:
        return activity
    par_geste: dict = {}
    for r in historique.revisions_de_ligne(ns_id, row_id, limit=limit)[:limit]:
        # Sans geste (écrite avant l'estampille), une révision est son propre geste.
        par_geste.setdefault(r["geste_id"] or ("revision", r["id"]), []).append(r)
    for entree in activity:
        revisions = par_geste.pop(entree.get("geste_id"), None) if entree.get("geste_id") else None
        if revisions:
            entree.update(revisions=revisions, source=revisions[0]["source"],
                          acteur=revisions[0]["acteur"])
    for revisions in par_geste.values():
        entree = _entree_de_revision(row_id, revisions, status_key)
        entree.update(revisions=revisions, source=revisions[0]["source"],
                      acteur=revisions[0]["acteur"])
        activity.append(entree)
    activity.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return activity[:limit]


def _row_activity(ctx: ResolvedCtx, inp: RowActivityInput) -> dict:
    store = _store(ctx.sub)
    try:
        row = store.get_row(inp.datastore, inp.row_id)
    except DatastoreNotFound:
        raise AuthzDenied(404, "datastore_not_found")
    except RowNotFound:
        raise AuthzDenied(404, "row_not_found")
    key = store.declared_key(inp.datastore)
    key_value = row.get(key) if key else None
    nsctx = datastore_journal.context(store, inp.datastore)
    # Le PROPRIÉTAIRE part avec la requête : l'axe « clé métier » est une recherche de
    # sous-chaîne dans les args, il doit être borné au tenant (sinon une clé banale
    # remonterait les gestes d'une autre org).
    activity = db.datastore_row_activity(
        inp.row_id, str(key_value) if key_value is not None else None,
        owner_type=nsctx.owner_type, owner_id=nsctx.owner_id, limit=LIMITE_PARCOURS)
    activity = _joindre_les_revisions(activity, nsctx.ns_id, inp.row_id,
                                      nsctx.status_key, LIMITE_PARCOURS)
    # Toutes ces entrées parlent de CETTE ligne (c'est le critère de la requête) → son
    # libellé les qualifie toutes, y compris celles matchées par clé métier dont les
    # args ne portent pas d'`id`. Pas de relecture : la ligne est déjà là.
    title = row.get(nsctx.title_key) if nsctx.title_key else None
    for entry in activity:
        entry["row_title"] = None if title is None else str(title)
    _attach_emails(activity)
    return {"activity": activity, "key": key, "retention_days": RETENTION_DAYS}


def _activity(ctx: ResolvedCtx, inp: DatastoreActivityInput) -> dict:
    store = _store(ctx.sub)
    try:
        ns_id = store.resolve_ns_id(inp.datastore)
    except DatastoreNotFound:
        raise AuthzDenied(404, "datastore_not_found")
    nsctx = datastore_journal.context(store, inp.datastore, ns_id=ns_id)
    # Le datastore est résolu ICI (une fois) et passé sous ses DEUX formes au journal :
    # les gestes REST y sont enregistrés par `ns_id`, les appels MCP par le nom OU l'id
    # tels que l'agent les a tapés. Le PROPRIÉTAIRE part avec — un nom de tableau n'est
    # unique que par propriétaire, l'axe nom doit être borné au tenant (fuite cross-org
    # sinon, cf. `db.datastore_activity`).
    activity = db.datastore_activity(
        ns_id, nsctx.name, owner_type=nsctx.owner_type, owner_id=nsctx.owner_id,
        limit=inp.limit)
    datastore_journal.attach_titles(ns_id, nsctx.title_key, activity)
    _attach_emails(activity)
    return {"activity": activity, "retention_days": RETENTION_DAYS}


class ActivityEntry(BaseModel):
    """Un geste d'agent lu depuis la boucle d'usage (0017) — le journal EST la source,
    il n'y a pas de table d'activité. D'où la fenêtre bornée par `retention_days` :
    au-delà, l'appel n'existe plus, ce n'est pas un trou de données.

    ⚠️ **Ce modèle DÉCRIT le payload, il ne le fabrique pas** (`Capability.Output` ne
    valide rien : le handler rend un `dict`, servi tel quel). Déclarer un champ ne le
    fait donc pas apparaître, et en retirer un ne l'enlève pas du fil — la seule chose
    qui change est ce qu'un client peut LIRE dans le contrat. C'est ce qui a permis à
    cette liste de dériver dans les deux sens jusqu'au 2026-09-01 : neuf clés servies
    et tues (dont `row_id`, sans lequel le cockpit ne sait pas quelle ligne annuler),
    quatre clés promises et jamais rendues (`call_id`, `at`, `run_doctrine`,
    `run_outcome` — le producteur nomme l'instant `created_at`, et les deux dernières
    sans leur préfixe `run_`). Un champ
    promis et absent est le pire des deux : le client lit `undefined`, sans erreur,
    sans log. Le cliquet `tests/datastore/test_journal_declare.py` compare désormais
    les deux listes à chaque exécution.
    """
    # ── L'appel : quand, par quelle surface, par qui ──────────────────────────
    created_at: Optional[str] = Field(default=None, description=HORODATAGE)
    # `mcp` = geste d'agent, `rest` = geste fait au cockpit. Les DEUX sont journalisés
    # (avant, filtrer `mcp` laissait le parcours vide pour qui travaille au dashboard).
    # `revision` (oto#273) = une écriture lue dans le journal des révisions, qu'aucun
    # appel journalisé ne porte : `tool` y est `null`.
    kind: Optional[str] = None
    tool: Optional[str] = None
    sub: Optional[str] = None
    # Résolu à la LECTURE (`tool_calls.email` est NULL en base) — la colonne « qui »
    # afficherait un uuid sinon.
    email: Optional[str] = None
    ok: Optional[bool] = None
    error: Optional[str] = None

    # ── Le run qui portait le geste (ADR 0017), quand il y en avait un ────────
    run_id: Optional[int] = None
    run_label: Optional[str] = None
    # Le guide sous lequel le run s'est déclaré. ⚠️ **Servi sous ses DEUX noms** :
    # `guide` (aujourd'hui) et `doctrine` (l'ancien, doublé par `avec_les_deux_noms`
    # et voué au retrait). Un client neuf lit `guide`.
    guide: Optional[str] = None
    doctrine: Optional[str] = None
    outcome: Optional[str] = None

    # ── La ligne visée — de quoi VISER, pas de quoi refaire l'état ────────────
    # L'`_id` de la ligne, lu des args journalisés. C'est la POIGNÉE : le cockpit
    # annule en ré-appliquant un patch (aucune op d'annulation n'existe côté serveur
    # pour une ligne), et il lui faut de quoi désigner sa cible.
    # ⚠️ `null` sur les gestes qui ne citent pas d'id : un `append` n'en a pas encore
    # (la ligne se corrèle alors par sa clé métier), une lecture de tableau non plus.
    row_id: Optional[str] = None
    row_title: Optional[str] = None              # posé sur le parcours d'UNE ligne
    fields: list[str] = Field(default=[], description=(
        "Les NOMS des champs touchés par l'écriture, relevés dans les arguments de "
        "l'appel. Bornés à 50 noms, chacun tronqué à 64 caractères. `[]` sur un geste "
        "qui ne touche aucun champ (une lecture) et sur les lignes antérieures à ce "
        "relevé. Les VALEURS avant et après ne sont pas ici : elles sont dans "
        "`revisions` (journal des révisions, oto#273), sur le parcours d'une ligne."))
    # La transition de statut, relevée PENDANT la mutation et non relue après : une
    # relecture courrait avec une écriture concurrente, et le cockpit proposerait
    # d'annuler vers un état que la ligne n'a jamais eu (cf. `rows.py::_update_row`).
    # `null` des deux côtés sur un geste qui ne touche pas au statut.
    from_status: Optional[str] = None
    to_status: Optional[str] = None

    # ── Le journal des révisions (oto#273, M3) — les VALEURS ─────────────────
    geste_id: Optional[str] = Field(default=None, description=(
        "Le geste : `call_uid` de l'appel, le même que le `geste_id` des révisions "
        "qu'il a écrites."))
    source: Optional[str] = Field(default=None, description=(
        "Sur une écriture : la face selon le journal (`import`, `agent`, `console`, "
        "`api`, `upload`, `system`). `null` sur ce que le journal ne voit pas (lecture, "
        "réservation, refus) et sur le parcours d'un tableau entier."))
    acteur: Optional[str] = Field(default=None, description=(
        "Sur une écriture : l'acteur selon le journal — un sub, ou `service:<nom>`."))
    revisions: list[dict] = Field(default=[], description=(
        "Parcours d'une ligne seulement : les révisions de CETTE ligne que ce geste a "
        "écrites, plus récente d'abord — `{id, rev, at, acteur, run_id, source, "
        "geste_id, diff: {colonne: {avant, apres}}}`, valeurs comprises (la forme de "
        "`GET …/rows/{row_id}/history`). `[]` sur une lecture, une réservation, un "
        "refus, et sur une écriture antérieure au journal (" + historique.MISE_EN_SERVICE
        + "). `kind='revision'` = une écriture qu'aucun appel journalisé ne porte "
        "(upload signé, travail de fond, écriture hors serveur, ou antérieure à "
        "l'estampille)."))


class DatastoreActivity(BaseModel):
    activity: list[ActivityEntry]
    retention_days: int


class RowActivity(BaseModel):
    activity: list[ActivityEntry]
    # La clé métier de la ligne, quand le tableau en déclare une : c'est le second axe
    # de corrélation (des appels citent la valeur, pas l'`_id`).
    key: Optional[object] = None
    retention_days: int


CAPABILITIES += [
    Capability(
        key="me.datastore.row_activity",
        handler=_row_activity,
        Input=RowActivityInput,
        Output=RowActivity,
        authz=SUB_ONLY,
        mcp=None,  # opt-out explicite : lecture de cockpit, l'agent a son propre fil
        rest=RestBinding(
            verb="GET",
            path="/api/datastores/{datastore}/rows/{row_id}/activity",
        ),
        description="Parcours d'une ligne du datastore (gestes d'agent et de dashboard).",
    ),
    Capability(
        key="me.datastore.activity",
        handler=_activity,
        Input=DatastoreActivityInput,
        Output=DatastoreActivity,
        authz=SUB_ONLY,
        mcp=None,
        rest=RestBinding(
            verb="GET",
            path="/api/datastores/{datastore}/activity",
        ),
        description="Activité d'un tableau du datastore (qui a touché quoi, depuis quel état).",
    ),
]
