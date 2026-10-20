"""BRANCHER une source sur un agent webhook — `oto_trigger op=connect_source`.

Une source qui ne sait pas poser d'en-tête (Lemlist) reçoit le porteur `otoh_` de
l'agent comme `secret` de son webhook (préréglage `body_secret`). Sans ce geste,
il fallait que le porteur passe par quelqu'un : rendu à l'écran, copié, collé chez
la source — ou, pire, passé en argument d'outil par un modèle, ce que la règle du
dépôt interdit (un secret brut ne passe jamais par un outil).

Ici le SERVEUR fait tout : il fabrique le porteur, l'inscrit chez la source avec la
clé de la source que l'appelant résout dans l'org, et ne rend que les identifiants
des webhooks inscrits. Le porteur va de la mémoire du serveur à la source — jamais
dans une réponse, un journal ou un transcript.

## L'ordre, et pourquoi

1. Inscrire chez la source avec le porteur NEUF, encore en mémoire. Une inscription
   qui échoue retire celles déjà faites : la base n'a pas bougé, l'agent garde son
   ancienne porte, intacte.
2. Écrire en base, en UNE transaction : mode `body_secret`, haché du porteur,
   inscriptions (`db.brancher_la_source`).
3. Retirer chez la source les inscriptions d'AVANT (un rebranchement) : elles portent
   l'ancien porteur, qui n'ouvre plus rien. Un retrait qui échoue ne défait rien —
   il est RENDU (`remote_cleanup_failed`), jamais avalé.

Rebrancher une source déjà branchée est donc la ROTATION du porteur d'un agent
branché : `rotate_secret`, `rotate_address` et le changement de mode sont refusés
sur un agent branché (`source_connected`), parce qu'ils couperaient la source sans
qu'oto puisse lui donner la nouvelle porte.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .. import db, runner_hook
from ._types import AuthzDenied, ResolvedCtx

logger = logging.getLogger(__name__)

#: Les sources qu'oto sait brancher. Une source de plus = un client qui sait
#: inscrire et retirer un webhook, et le préréglage par lequel elle prouve.
SOURCES = ("lemlist",)

#: Combien d'événements un branchement inscrit, au plus : un webhook par
#: événement chez Lemlist, et chaque inscription est un appel au tiers.
EVENEMENTS_MAX = 10

SOURCE_CONNECTED = (
    "une source est branchée sur cet agent (`hook_sources`) : changer sa porte la "
    "couperait sans qu'oto puisse lui donner la nouvelle. Rebrancher "
    "(`op=connect_source`) renouvelle le porteur ET l'inscription ; "
    "`op=disconnect_source` d'abord pour changer autrement.")


def exiger_aucune_source(t: Optional[dict]) -> None:
    """Refuse un geste de porte sur un agent branché (voir `SOURCE_CONNECTED`)."""
    if t and t.get("hook_sources"):
        raise AuthzDenied(409, "source_connected", SOURCE_CONNECTED)


def _client(ctx: ResolvedCtx, source: str) -> Any:
    """Le client de la source, sur la clé que l'APPELANT résout dans l'org de
    l'agent — la même cascade qu'un outil `lemlist_*` dans cette org.

    `check_usage=False` : configurer une connexion ne consomme pas de quota."""
    from .. import access, session_org
    from ..auth import hooks as auth_hooks
    from ..mcp_errors import McpError
    from oto.tools.lemlist import LemlistClient

    try:
        with auth_hooks.sub_override(ctx.sub):
            jeton = session_org.set_call_org(int(ctx.org_id))
            try:
                rc = access.resolve_credential(source, sub=ctx.sub, check_usage=False)
            finally:
                session_org.reset_call_org(jeton)
    except McpError as e:
        # `from e` : la face MCP rend l'erreur d'origine, déjà actionnable.
        raise AuthzDenied(400, "credential_unavailable", e.error.message) from e
    return LemlistClient(api_key=rc.key)


def _motif(e: Exception) -> str:
    """Ce qu'on dit d'un refus du tiers. ⚠️ Jamais `str(e)` : le message d'une
    erreur amont peut recopier la requête, donc le porteur qu'elle portait."""
    statut = getattr(e, "status_code", None)
    return f"{type(e).__name__}{f' {statut}' if statut else ''}"


def _retirer(client: Any, inscrites: list) -> list:
    """Retire chez la source chaque inscription ; rend celles qui ont RÉSISTÉ."""
    restees = []
    for ins in inscrites:
        try:
            client.delete_webhook(ins["remote_id"])
        except Exception as e:  # noqa: SILENT — journalisé, et rendu à l'appelant
            logger.warning("webhook %s chez %s : retrait impossible (%s)",
                           ins.get("remote_id"), ins.get("source"), _motif(e))
            restees.append({**ins, "error": _motif(e)})
    return restees


def _evenements(events: Optional[list]) -> list:
    vus = []
    for ev in events or []:
        ev = (ev or "").strip() if isinstance(ev, str) else ""
        if ev and ev not in vus:
            vus.append(ev)
    if not vus:
        raise AuthzDenied(
            400, "events_required",
            "`events` : au moins un événement de la source (Lemlist : "
            "`emailsReplied`, `linkedinReplied`…). Aucun défaut : s'abonner à TOUT "
            "ferait tourner l'agent à chaque ouverture d'e-mail.")
    if len(vus) > EVENEMENTS_MAX:
        raise AuthzDenied(400, "too_many_events",
                          f"`events` : {EVENEMENTS_MAX} au plus par branchement.")
    return vus


def connecter(ctx: ResolvedCtx, t: dict, *, source: Optional[str],
              events: Optional[list], campaign_id: Optional[str],
              is_first: Optional[bool], url_de: Callable[[str], str]) -> dict:
    """Branche `source` sur l'agent `t` (porte DÉJÀ vérifiée par l'appelant).

    `url_de(slug)` : l'URL publique d'une adresse privée. Rend
    `{"connected": [...], "remote_cleanup_failed": [...]}` — sans porteur."""
    if source not in SOURCES:
        raise AuthzDenied(400, "unknown_source",
                          f"`source` : une de {', '.join(SOURCES)}.")
    evenements = _evenements(events)
    client = _client(ctx, source)
    # L'adresse privée AVANT : la source garde l'URL qu'on lui donne, et une
    # adresse numérique se parcourt. Poser une adresse à un agent qui n'en a pas
    # ne casse rien : sans source branchée, personne ne détient encore l'ancienne
    # sinon par le porteur, qui change de toute façon ici.
    slug = t.get("hook_slug")
    if not slug:
        slug = runner_hook.nouvelle_adresse()
        db.poser_adresse_de_hook(t["id"], ctx.org_id, slug)
    url = url_de(slug)
    porteur, hache = runner_hook.nouveau_secret()

    inscrites: list = []
    for ev in evenements:
        try:
            r = client.add_webhook(url, type=ev, secret=porteur,
                                   campaign_id=campaign_id, is_first=is_first)
        except Exception as e:  # noqa: SILENT — journalisé, défait, et refusé par son nom
            logger.warning("webhook %s : inscription `%s` chez %s refusée (%s)",
                           t["id"], ev, source, _motif(e))
            restees = _retirer(client, inscrites)
            raise AuthzDenied(
                502, "source_registration_failed",
                f"{source} a refusé l'inscription de `{ev}` ({_motif(e)}) : rien "
                "n'a changé sur l'agent"
                + (f", mais {len(restees)} inscription(s) déjà faite(s) n'ont pas pu "
                   "être retirées chez la source" if restees else "")
                + ". Vérifier le nom de l'événement et la clé de la source.") from None
        remote_id = (r or {}).get("_id") if isinstance(r, dict) else None
        inscrites.append({"source": source, "remote_id": remote_id, "event": ev,
                          "campaign_id": campaign_id})
        if not remote_id:
            restees = _retirer(client, [i for i in inscrites if i["remote_id"]])
            raise AuthzDenied(
                502, "source_registration_failed",
                f"{source} n'a pas rendu d'identifiant pour `{ev}` : oto ne saurait "
                "pas le retirer, rien n'a changé sur l'agent"
                + (f" ({len(restees)} inscription(s) non retirée(s))" if restees else "")
                + ".")

    anciennes = list(t.get("hook_sources") or [])
    db.brancher_la_source(t["id"], ctx.org_id, hook_auth=runner_hook.BODY_SECRET,
                          secret_hash=hache, sources=inscrites)
    logger.warning("webhook %s (org %s) : source %s BRANCHÉE par %s — %d événement(s), "
                   "porteur neuf posé chez la source", t["id"], ctx.org_id, source,
                   ctx.sub, len(inscrites))
    restees = _retirer(client, [a for a in anciennes if a.get("remote_id")])
    return {"connected": inscrites, "remote_cleanup_failed": restees}


def deconnecter(ctx: ResolvedCtx, t: dict) -> dict:
    """Retire chez la source tout ce qui est inscrit pour l'agent `t` (porte DÉJÀ
    vérifiée). Ce qui résiste RESTE dans `hook_sources` — oto doit pouvoir le
    retirer plus tard — et est rendu. Le mode et le porteur ne changent pas."""
    inscrites = list(t.get("hook_sources") or [])
    if not inscrites:
        return {"disconnected": [], "remote_cleanup_failed": []}
    restees = []
    par_source: dict = {}
    for ins in inscrites:
        par_source.setdefault(ins.get("source"), []).append(ins)
    for source, lot in par_source.items():
        if source not in SOURCES:
            restees += [{**i, "error": "unknown_source"} for i in lot]
            continue
        try:
            client = _client(ctx, source)
        except AuthzDenied as e:
            # Sans clé, rien ne se retire — mais `delete` ne doit pas en être
            # bloqué : ce qui reste est rendu, avec la raison.
            restees += [{**i, "error": e.code} for i in lot]
            continue
        restees += _retirer(client, lot)
    ids_restes = {r.get("remote_id") for r in restees}
    db.poser_sources_de_hook(t["id"], ctx.org_id,
                             [i for i in inscrites if i.get("remote_id") in ids_restes])
    logger.warning("webhook %s (org %s) : source(s) DÉBRANCHÉE(S) par %s — %d retirée(s), "
                   "%d restée(s)", t["id"], ctx.org_id, ctx.sub,
                   len(inscrites) - len(restees), len(restees))
    return {"disconnected": [i for i in inscrites if i.get("remote_id") not in ids_restes],
            "remote_cleanup_failed": restees}
