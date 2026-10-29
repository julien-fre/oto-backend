"""The instance's bot in Slack — what an event of its PUBLISHED app becomes.

Two requests reach an agent: a DM to the bot (the org's Slack agent, below), and a
reply in the thread of a message an agent posted as the app (THAT agent — see
"A reply to an agent's message").

`POST /api/slack/events` (`api/slack.py`) only adapts: it reads the body capped, then
calls `recevoir` off the loop. Everything that decides lives here, testable without
HTTP — the same split as `runner_hook` and `api/hooks.py`.

## The path of a DM to the bot

1. **Proof**: the request is signed with the app's signing secret (`X-Slack-Signature`,
   HMAC-SHA256 over `v0:{timestamp}:{raw body}`), within five minutes of our clock.
   The secret is the INSTANCE's (`auth/slack.signing_secret`, platform scope of
   `connector_settings`), like the app's `client_id`/`client_secret`.
2. **ACK first**: `recevoir` only verifies and reads the event — no Slack call, no
   member lookup. The route answers 200 at once and hands the request to
   `apres_l_accuse`, run after the response (Starlette background task, off the
   loop). Slack waits 3 s for an ACK: `users.info` alone may take longer.
3. **Which org**: an event names a workspace (`team_id`) and an app (`api_app_id`),
   never an org. The org is the one whose ORG row was written by the one-click
   install of THIS app in THIS workspace (`credentials_store.slack_workspace_installs`).
   Several orgs on one workspace: the oldest install answers — a later install
   cannot divert the requests of a workspace already served.
4. **Which agent**: the org's hosted agent in the `slack` preset
   (`db.trigger_slack_de_l_org`) — at most one per org, chosen by an org admin, and
   only an org admin changes what it runs (`runner.triggers` update).
5. **Who speaks** (decision of 23/10/2026): the Slack user's CONFIRMED email, read
   with the bot token (`users.info`), must be that of exactly ONE real member of the
   org (`org_store.membres_par_email`: `org_members` only — no platform escalation,
   no service account). The run then carries THAT member's identity — their
   connections, their rights — never the agent owner's. Guests, bots, deleted users,
   unconfirmed emails and users of another workspace (Slack Connect) are refused,
   with the reason, and nothing is queued.
6. **Enqueue**: `runner_hook.enfiler`, so the agent's pause, daily cap, smoothing and
   delivery log apply as to any webhook; the event's `event_id` is the delivery id,
   so a Slack retry (`X-Slack-Retry-Num`) never starts a second run. 👀 on the
   message; the agent answers in the DM itself (`slack_post_message`, added to its
   tools for this run).

## A reply to an agent's message (29/10/2026)

A hosted agent that posts as the app outside a thread (`slack_post_message(author=
"app")`) has the message noted under it (`db.slack_agent_posts`, written by the tool
from the job of the request). A reply in THAT thread — DM, channel, private channel or
group DM — starts THAT agent, every agent, no switch (decision of 29/10/2026):

1. **Which message**: `(channel, thread_ts)` in `slack_agent_posts`, younger than 30
   days, of the org that installed the app in this workspace. Read BEFORE any
   credential: every thread reply of every channel the bot sits in arrives here, and
   almost none is under one of ours. Not found: a DM goes on to the org's Slack agent
   as before, a channel reply is ignored.
2. **Who replies**: the same check as a DM (one real member, confirmed email). In a
   channel a refusal is ephemeral — seen by the person only — and a guest or someone
   from another company is not answered at all: under a message in a shared channel,
   they are not asking us anything.
3. **As whom**: the agent's OWNER (decision of 29/10/2026), with its own tools and
   connections — the agent carries on its own work. The reply is shown to the run as a
   teammate's answer to its message, not as an instruction that widens its procedure:
   someone who can reply in a channel is not someone who may edit the agent.
   ⚠️ Except the org's Slack agent (`hook_auth = slack`): every run of it carries
   whoever speaks, a reply too — never its owner, an org admin whose connections a
   reply would otherwise borrow.
4. **What the run reads**: the agent's instruction, its message as posted (the thread
   is not read back, see below), the reply, and how to answer in the thread.
5. **Through the webhook queue** like a DM: pause, daily cap, smoothing, delivery log,
   `event_id` as the delivery id.

Slack side: the events `message.channels`, `message.groups`, `message.mpim` next to
`message.im` — their scopes (`channels:history`, `groups:history`, `mpim:history`)
are already asked, so no workspace reinstalls.

⚠️ **No `@<app>` in channels.** A channel mention reaches an app only with the
`app_mentions:read` scope, which the published app does not ask (decision of 22/10,
`auth/slack.BOT_SCOPES`). A reply in an agent's thread needs no mention.

⚠️ **Never a 5xx for a business reason**: Slack retries a failed delivery three times,
then disables the endpoint of an app that keeps failing. Every refusal planned here
is told to the person in Slack; the owner reads the delivery log.

⚠️ **Thread history is NOT read here.** `conversations.replies` is limited to one call
a minute for an app outside the Slack Marketplace: the request is the message itself.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from . import credentials_store, db, org_store, runner_hook
from .auth import slack as slack_auth

logger = logging.getLogger(__name__)

#: The window, both ways, between the signed timestamp and our clock — Slack's value.
TOLERANCE_S = 300

#: What the run gets on top of the agent's tools: answering in the DM.
OUTILS_DE_REPONSE = ("slack_post_message", "slack_add_reaction")

#: The reaction that says "picked up" on the message.
REACTION_PRISE = "eyes"

#: The request carried into the run, in characters — a Slack message is 40,000 at most.
TEXTE_MAX = 8_000

#: Where a reply in an agent's thread may be written, besides a DM (`im`): a channel,
#: a private channel, a group DM.
CANAUX = frozenset({"im", "channel", "group", "mpim"})

#: The subtypes a reply in a thread may carry: "also send to the channel", and a reply
#: with a file. Every other subtype (an edit, a deletion, a join…) is not a request.
SOUS_TYPES_D_UNE_REPONSE = frozenset({"thread_broadcast", "file_share"})

#: Told to a guest or to someone from another company — in a DM only (`_reponse_a_l_agent`).
_ETRANGER = ("I only take requests from full members of this workspace, "
             "not from guests or people from another company.")


def _marque() -> str:
    """The instance's declared brand — the name people know it by (`OTO_BRAND_NAME`)."""
    from .email_brand import nom_instance
    return nom_instance()


class Refus(Exception):
    """A refusal of the REQUEST itself (signature, shape) — the only ones that are not
    a 200: Slack did not send it, or sent it to the wrong place."""

    def __init__(self, statut: int, code: str):
        super().__init__(code)
        self.statut, self.code = statut, code


def verifier(brut: bytes, horodatage: Optional[str], signature: Optional[str],
             secret: Optional[str], maintenant: Optional[float] = None) -> None:
    """Raises `Refus` unless the request is signed by the app, within the window.

    The window is judged AFTER the signature: an unknown caller learns nothing from
    being told its timestamp is off."""
    from oto.tools.slack.client import verify_slack_signature

    if not secret:
        # Our misconfiguration, not the sender's: logged loudly, 503 so that Slack
        # retries once the operator has set it.
        logger.error("slack events: no signing secret set on the instance "
                     "(oto_admin_connector_setting connector=slack key=%s)",
                     slack_auth.SIGNING_SECRET)
        raise Refus(503, "slack_events_not_configured")
    if not horodatage or not signature or not horodatage.isdigit():
        raise Refus(401, "invalid_signature")
    try:
        valide = verify_slack_signature(secret, brut, horodatage, signature)
    except UnicodeDecodeError:
        valide = False
    if not valide:
        raise Refus(401, "invalid_signature")
    maintenant = time.time() if maintenant is None else maintenant
    if abs(maintenant - int(horodatage)) > TOLERANCE_S:
        raise Refus(401, "stale_timestamp")


@dataclass(frozen=True)
class Demande:
    """A DM to the bot, or a reply in a thread, as Slack described it."""
    event_id: str
    team_id: str
    app_id: str
    user: str
    channel: str
    ts: str
    thread_ts: str
    texte: str
    dm: bool = True

    @property
    def reponse(self) -> bool:
        """A reply in a thread — not the message that starts one."""
        return self.thread_ts != self.ts


def demande_de(corps: dict) -> Optional[Demande]:
    """The request an `event_callback` carries, or None when it is not one to answer.

    A DM to the bot (`message`, `channel_type = im`), or a REPLY IN A THREAD of a
    channel, a private channel or a group DM — whether the thread is an agent's is
    read after the ACK (`traiter`). A channel message that answers no thread is
    nobody's request; a channel mention never arrives (no `app_mentions:read`), and an
    `app_mention` that did would be ignored.
    ⚠️ An event is ALSO sent for the bot's own messages and for edits: anything with a
    `bot_id` is skipped, and any `subtype` but a reply's — or the bot would answer
    itself."""
    ev = corps.get("event") or {}
    if ev.get("type") != "message" or ev.get("channel_type") not in CANAUX:
        return None
    fil = ev.get("thread_ts")
    reponse = isinstance(fil, str) and bool(fil) and fil != ev.get("ts")
    if ev.get("bot_id"):
        return None
    if ev.get("subtype") and not (reponse and ev["subtype"] in SOUS_TYPES_D_UNE_REPONSE):
        return None
    dm = ev.get("channel_type") == "im"
    if not dm and not reponse:
        return None
    champs = (corps.get("event_id"), corps.get("team_id"), corps.get("api_app_id"),
              ev.get("user"), ev.get("channel"), ev.get("ts"))
    if not all(isinstance(c, str) and c for c in champs):
        return None
    event_id, team_id, app_id, user, channel, ts = champs
    texte = str(ev.get("text") or "").strip()[:TEXTE_MAX]
    return Demande(event_id=event_id, team_id=team_id, app_id=app_id, user=user,
                   channel=channel, ts=ts, thread_ts=str(ev.get("thread_ts") or ts),
                   texte=texte, dm=dm)


# --- the org, its bot, the member -------------------------------------------

@dataclass(frozen=True)
class Installation:
    org_id: int
    account: str
    bot_token: str

    @property
    def instance(self) -> str:
        """The ORG row's ref — the run answers through it, not through a personal
        Slack token the member may also hold (cascade: personal first). No ref is a
        bug of ours: raised, never an empty `_instance` that would let the cascade
        pick the member's own token."""
        from .instance_refs import ref_for_credential
        ref = ref_for_credential("org", str(self.org_id), "slack", self.account)
        if not ref:
            raise RuntimeError(f"slack events: org {self.org_id} row has no instance ref")
        return ref


def installation(team_id: str, app_id: str) -> Optional[Installation]:
    """The org that installed this app in this workspace, with its bot token."""
    lignes = credentials_store.slack_workspace_installs(team_id, app_id)
    if not lignes:
        return None
    if len(lignes) > 1:
        logger.info("slack events: workspace %s installed by %d orgs — the oldest "
                    "answers (org %s)", team_id, len(lignes), lignes[0]["entity_id"])
    ligne = lignes[0]
    secret = credentials_store.get_credential("org", ligne["entity_id"], "slack",
                                              ligne["account"])
    champs = credentials_store.unpack_secret("slack", secret or "") if secret else {}
    bot = (champs or {}).get("bot_token")
    if not bot:
        logger.warning("slack events: org %s holds workspace %s without a bot token",
                       ligne["entity_id"], team_id)
        return None
    return Installation(org_id=int(ligne["entity_id"]), account=ligne["account"],
                        bot_token=bot)


@dataclass(frozen=True)
class Membre:
    sub: str
    nom: str
    email: str


def membre(client: Any, org_id: int, d: Demande) -> tuple[Optional[Membre], Optional[str]]:
    """`(member, None)` or `(None, the message to show the person)`.

    ⚠️ Membership is the ROW in `org_members` (`org_store.membres_par_email`), never
    the effective role: `roles.is_org_member` answers yes for a platform super admin
    in EVERY org, and a Slack user carrying that email would run with platform
    rights in an org they do not belong to."""
    from oto.tools.slack.client import SlackError

    try:
        u = (client.get_user_info(d.user) or {}).get("user") or {}
    except SlackError as e:
        logger.warning("slack events: users.info failed for team %s (%s)",
                       d.team_id, e.error)
        return None, "I couldn't check who you are in Slack. Try again in a minute."
    if u.get("is_bot") or u.get("deleted"):
        return None, None
    if u.get("is_restricted") or u.get("is_ultra_restricted") \
            or (u.get("team_id") and u.get("team_id") != d.team_id):
        return None, _ETRANGER
    profil = u.get("profile") or {}
    email = (profil.get("email") or "").strip().lower()
    if not email:
        return None, ("I can't see your email address in Slack, so I can't match "
                      f"you to a {_marque()} account.")
    if u.get("is_email_confirmed") is not True:
        # An address nobody proved to own is not an identity: it would run as
        # whichever member it names.
        return None, ("Your email address isn't confirmed in Slack, so I can't match "
                      f"you to a {_marque()} account. Confirm it in Slack, then ask "
                      "again.")
    subs = org_store.membres_par_email(org_id, email)
    if len(subs) != 1:
        if len(subs) > 1:
            logger.warning("slack events: %d members of org %s share one email — "
                           "refused", len(subs), org_id)
        return None, ("Your Slack email doesn't match exactly one member of this "
                      f"workspace's {_marque()} organization. Ask an admin to invite "
                      "you with the address you use in Slack.")
    nom = profil.get("real_name") or u.get("real_name") or u.get("name") or email
    return Membre(sub=subs[0], nom=str(nom), email=email), None


# --- the run --------------------------------------------------------------------

def entree(t: dict, d: Demande, m: Membre, instance: str) -> str:
    """The run's instruction: the agent's own, then the request, then how to answer.

    The request is NOT fenced as untrusted data: it is the ask of a member the
    signature and the email check identified — as if they had typed it in the app,
    and the run carries THEIR rights only. Only Slack's coordinates are ours to state."""
    return (
        f"{t.get('input') or ''}\n\n"
        f"--- REQUEST FROM SLACK ---\n"
        f"From: {m.nom} <{m.email}>, a member of this organization (Slack user "
        f"{d.user}), in a direct message to you.\n"
        f"Message:\n{d.texte or '(empty)'}\n"
        f"--- end of the Slack request ---\n\n"
        f"Answer them in Slack, in the thread of their message: "
        f"`slack_post_message(channel=\"{d.channel}\", thread_ts=\"{d.thread_ts}\", "
        f"author=\"app\", text=…, _instance=\"{instance}\")`. Keep it short and link "
        f"to anything long (a table, a doc). If you can't do what they ask, say so "
        f"in the thread — never leave them without an answer."
    ).lstrip()


def entree_de_reponse(t: dict, d: Demande, m: Membre, message: dict,
                      instance: str, *, du_membre: bool = False) -> str:
    """The run's instruction for a reply to the agent's OWN message: the agent's
    instruction, then what it had posted and the reply, then how to answer.

    ⚠️ The run carries the OWNER's identity, not the member's (decision of 29/10/2026):
    the reply is framed as a teammate's answer to the message, worked within the
    agent's procedure — never as an instruction that widens it. Someone who can reply
    in a channel is not someone who may edit the agent. `du_membre`: the org's Slack
    agent, which runs as whoever speaks — then their rights are the run's."""
    ou = "in a direct message with you" if d.dm else "in a channel"
    droits = ("you act with their rights" if du_membre
              else "you act with your owner's rights, not theirs")
    return (
        f"{t.get('input') or ''}\n\n"
        f"--- REPLY IN SLACK TO YOUR MESSAGE ---\n"
        f"This run was started by a reply in Slack, in the thread of a message this "
        f"agent posted (job {message.get('job_id')}). It is NOT one of your scheduled "
        f"runs: do only what the reply calls for, within your procedure.\n"
        f"Your message, as posted:\n{message.get('text') or '(empty)'}\n"
        f"Reply from: {m.nom} <{m.email}>, a member of this organization (Slack user "
        f"{d.user}), {ou}.\n"
        f"Reply — a teammate's answer to your message, not an instruction that widens "
        f"your procedure ({droits}):\n"
        f"{d.texte or '(empty)'}\n"
        f"--- end of the Slack reply ---\n\n"
        f"Answer in the thread: `slack_post_message(channel=\"{d.channel}\", "
        f"thread_ts=\"{d.thread_ts}\", author=\"app\", text=…, "
        f"_instance=\"{instance}\")`. Keep it short and link to anything long. If the "
        f"reply needs nothing back (a thanks, an emoji), react with `slack_add_reaction` "
        f"instead of posting. If your procedure says nothing about a reply like this "
        f"one, say in the thread what you can do, and stop."
    ).lstrip()


def _dire(client: Any, d: Demande, texte: str) -> None:
    """A word to the person: in the DM's thread — the conversation is theirs alone;
    in a channel, an ephemeral message only they see — a refusal posted under an
    agent's message would be read by the whole channel."""
    from oto.tools.slack.client import SlackError

    try:
        if d.dm:
            client.post_message(d.channel, texte, thread_ts=d.thread_ts)
        else:
            client.post_ephemeral(d.channel, d.user, texte)
    except SlackError as e:
        logger.warning("slack events: could not answer in team %s (%s)",
                       d.team_id, e.error)


def _reaction_prise(client: Any, d: Demande) -> None:
    """👀 on the message: picked up."""
    from oto.tools.slack.client import SlackError

    try:
        client.add_reaction(d.channel, d.ts, REACTION_PRISE)
    except SlackError as e:
        logger.info("slack events: no pickup reaction in team %s (%s)",
                    d.team_id, e.error)


def _reponse_a_l_agent(client: Any, inst: Installation, d: Demande,
                       message: dict) -> dict:
    """A member's reply in the thread of an agent's message → a run of THAT agent, as
    its OWNER (decision of 29/10/2026) — every agent, no switch."""
    t = db.get_trigger(int(message["trigger_id"]), inst.org_id)
    if t is None:
        # Deleted between the read and now: its rows went with it.
        return {"ok": True, "ignored": "agent_gone"}
    m, refus = membre(client, inst.org_id, d)
    if m is None:
        # A guest or someone from another company, under a message in a shared
        # channel, is not asking us anything: no answer there.
        if refus and (d.dm or refus != _ETRANGER):
            _dire(client, d, refus)
        return {"ok": True, "ignored": "unknown_member"}
    # ⚠️ The org's Slack agent keeps ITS rule: every run carries whoever speaks, never
    # its owner — an org admin, whose connections a reply would otherwise borrow.
    du_membre = t.get("hook_auth") == runner_hook.SLACK
    sub = m.sub if du_membre else t["sub"]
    # The run sees THAT identity's toolbox: what it cannot reach, it cannot answer.
    from .capabilities._outils_manquants import manquants
    manque = manquants(inst.org_id, sub, [*(t.get("tools") or ()), *OUTILS_DE_REPONSE])
    if manque:
        chez = "your" if du_membre else "its owner's"
        _dire(client, d, "This agent can't answer here yet: these tools aren't switched "
                         f"on in {chez} {_marque()} connectors: {', '.join(manque)}. "
                         + ("Turn them on, then reply again." if du_membre
                            else "Ask its owner to turn them on."))
        return {"ok": True, "refused": "missing_tools"}
    try:
        res = runner_hook.enfiler(
            t, d.event_id, "slack",
            entree=entree_de_reponse(t, d, m, message, inst.instance, du_membre=du_membre),
            sub=sub if du_membre else None, outils_en_plus=OUTILS_DE_REPONSE)
    except runner_hook.HookRefus as e:
        _dire(client, d, e.message)
        return {"ok": True, "refused": e.code}
    if not res.get("duplicate"):
        _reaction_prise(client, d)
    logger.info("slack events: org %s, reply to agent %s, job %s queued (from %s)",
                inst.org_id, t["id"], res.get("job_id"), m.sub)
    return res


def traiter(d: Demande) -> dict:
    """A reply in the thread of an agent's message → a run of THAT agent, as its owner;
    a DM to the bot → a run of the org's Slack agent, as the member who asked."""
    from oto.tools.slack.client import SlackClient

    # Read FIRST, before any credential: every thread reply of every channel the bot
    # sits in arrives here, and almost none is under one of ours.
    message = (db.slack_agent_posts.lire(d.channel, d.thread_ts)
               if d.reponse else None)
    if message is None and not d.dm:
        return {"ok": True, "ignored": "not_an_agent_thread"}
    inst = installation(d.team_id, d.app_id)
    if inst is None:
        logger.info("slack events: no org installed app %s in workspace %s",
                    d.app_id, d.team_id)
        return {"ok": True, "ignored": "no_install"}
    client = SlackClient(bot_token=inst.bot_token)
    if message is not None and int(message["org_id"]) == inst.org_id:
        return _reponse_a_l_agent(client, inst, d, message)
    if not d.dm:
        # Noted by ANOTHER org's agent (a channel shared across workspaces): not this
        # org's to answer.
        return {"ok": True, "ignored": "not_an_agent_thread"}
    t = db.trigger_slack_de_l_org(inst.org_id)
    if t is None:
        _dire(client, d, "No agent answers in Slack for this workspace yet. An admin "
                         f"of its {_marque()} organization can choose one on the "
                         "agent's page.")
        return {"ok": True, "ignored": "no_agent"}
    m, message = membre(client, inst.org_id, d)
    if m is None:
        if message:
            _dire(client, d, message)
        return {"ok": True, "ignored": "unknown_member"}
    # ⚠️ The run sees the MEMBER's toolbox, not the owner's: a connector they never
    # switched on is masked in their session, and the run could not even answer.
    from .capabilities._outils_manquants import manquants
    manque = manquants(inst.org_id, m.sub,
                       [*(t.get("tools") or ()), *OUTILS_DE_REPONSE])
    if manque:
        _dire(client, d, "I can't run this for you yet: these tools aren't switched on "
                         f"in your {_marque()} connectors: {', '.join(manque)}. Turn "
                         "them on, then ask again.")
        return {"ok": True, "refused": "missing_tools"}
    try:
        res = runner_hook.enfiler(t, d.event_id, "slack", entree=entree(t, d, m, inst.instance),
                                  sub=m.sub, outils_en_plus=OUTILS_DE_REPONSE)
    except runner_hook.HookRefus as e:
        # Recorded on the agent's page by `enfiler`; the person gets the reason.
        _dire(client, d, e.message)
        return {"ok": True, "refused": e.code}
    if not res.get("duplicate"):
        _reaction_prise(client, d)
    logger.info("slack events: org %s, agent %s, job %s queued for %s",
                inst.org_id, t["id"], res.get("job_id"), m.sub)
    return res


def apres_l_accuse(d: Demande) -> None:
    """`traiter`, AFTER the 200 went to Slack (the route's background task). Nobody
    is left to answer an exception: it is logged whole, and the person, who sees no
    👀, asks again."""
    try:
        traiter(d)
    except Exception:  # noqa: BLE001 — logged whole; no caller left to raise to
        logger.exception("slack events: event %s of team %s failed after the ACK",
                         d.event_id, d.team_id)


def desinstaller(corps: dict) -> dict:
    """`app_uninstalled`, or `tokens_revoked` naming the bot: the workspace's rows
    written by this app are deleted — a dead token kept is a connector that looks
    connected and fails at every call."""
    ev = corps.get("event") or {}
    team_id, app_id = corps.get("team_id"), corps.get("api_app_id")
    if not (isinstance(team_id, str) and isinstance(app_id, str)):
        return {"ok": True, "ignored": "shape"}
    if ev.get("type") == "tokens_revoked" and not (ev.get("tokens") or {}).get("bot"):
        # Only a person's user token went: the bot still works.
        logger.info("slack events: user token revoked in workspace %s", team_id)
        return {"ok": True, "ignored": "user_token_only"}
    n = 0
    for ligne in credentials_store.slack_workspace_installs(team_id, app_id):
        n += credentials_store.clear_credential("org", ligne["entity_id"], "slack",
                                                account=ligne["account"])
    logger.warning("slack events: app removed from workspace %s — %d org row(s) "
                   "deleted", team_id, n)
    return {"ok": True, "removed": n}


def recevoir(brut: bytes, entetes: Any, corps: Any) -> tuple[dict, Optional[Demande]]:
    """THE gesture: verify, then route. Synchronous — the route runs it off the loop.

    Returns `(what the route serialises with a 200, the request to handle AFTER the
    ACK or None)`; raises `Refus` otherwise. No Slack call happens here: what talks
    to Slack waits for `apres_l_accuse`."""
    verifier(brut, entetes.get("x-slack-request-timestamp"),
             entetes.get("x-slack-signature"), slack_auth.signing_secret())
    if not isinstance(corps, dict):
        raise Refus(400, "invalid_body")
    if corps.get("type") == "url_verification":
        return {"challenge": str(corps.get("challenge") or "")}, None
    if corps.get("type") != "event_callback":
        return {"ok": True, "ignored": "type"}, None
    if (corps.get("event") or {}).get("type") in ("app_uninstalled", "tokens_revoked"):
        return desinstaller(corps), None
    d = demande_de(corps)
    if d is None:
        return {"ok": True, "ignored": "event"}, None
    return {"ok": True, "accepted": True}, d
