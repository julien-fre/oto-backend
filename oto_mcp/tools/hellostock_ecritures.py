"""HelloStock administration — the three actions that ACT on the production
marketplace.

Three tools, one per action, because none shares its parameters with a
read (ADR 0047: disjoint parameters → the NAME carries the warning) — and because
`status`, a list filter, would become the written value here: the same word
with two meanings on a single surface.

- `hellostock_demande_send` **sends an email** to real members. It is the
  only action whose effect reaches a person: it is **dry-run BY DEFAULT**
  (the repo's convention for whatever goes out to a third party), and the preview names the
  recipients, those who already received it, and what the email contains.
- `hellostock_demande_set_status` and `hellostock_offre_update` change what the
  marketplace DISPLAYS: only `published` is publicly visible, and an offer
  keyword feeds the public search. No email goes out. `dry_run`
  available, default `False`, like the writes of the other connectors.

**What this module adds to the contract**, because the API does not:

- **no unintended duplicate resend**: HelloStock traces each send but
  refuses none, and has no idempotency key — an agent that replays its turn
  would write twice to the same suppliers. A recipient who already received this
  request is refused, unless `allow_resend=True` (a deliberate reminder);
- **the message bounded here**: beyond 2,000 characters, the server answers « no
  recipient selected », which would make one look for the error elsewhere;
- **a write is read BEFORE** (an unknown request id returns a 500
  server-side, not a 404) **and re-read AFTER** for an offer (the server
  normalizes keywords: we return what is stored, not what was requested).
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INTERNAL_ERROR
from oto.tools.hellostock import STATUSES
from pydantic import Field

from ..mcp_errors import McpError
from .hellostock_socle import _bad, _client, _run, traduire

_MESSAGE_MAX = 2000
_DESTINATAIRES_MAX = 50

# What the sending email carries, and what it withholds (contract + server template).
_SPECS = ("matiere", "nuance", "format", "dimensions", "epaisseur", "quantite",
          "delai", "certificatRequis")
_VISIBILITE = ("`published` is visible on the public marketplace immediately; "
               "any other status takes it off the public pages. No email goes out.")


def _destinataires(user_ids: list[int]) -> list[int]:
    if not user_ids:
        raise _bad("`user_ids`: at least one recipient member (ids from hellostock_membre).")
    if len(user_ids) > _DESTINATAIRES_MAX:
        raise _bad(f"`user_ids`: at most {_DESTINATAIRES_MAX} recipients per send "
                   f"({len(user_ids)} received).")
    doublons = sorted({u for u in user_ids if user_ids.count(u) > 1})
    if doublons:
        raise _bad(f"`user_ids` contains duplicates: {doublons}.")
    return list(user_ids)


def _deja_recus(demande: dict) -> dict[int, str]:
    """`{user_id: date of the last send}` from the request's traced sends."""
    out: dict[int, str] = {}
    for e in demande.get("envois") or []:
        uid = e.get("userId")
        if isinstance(uid, int) and uid not in out:  # from newest to oldest
            out[uid] = e.get("sentAt")
    return out


def _avertissements(demande: dict, ids: list[int]) -> list[str]:
    """FACTS about the send, not refusals: the administrator decides."""
    out = []
    if demande.get("status") != "published":
        out.append(f"status `{demande.get('status')}`: the email's links open the "
                   "request's public page, which only displays published "
                   "requests — recipients will land on « Demande indisponible » "
                   "until it is.")
    acheteur = (demande.get("contact") or {}).get("userId")
    if acheteur in ids:
        out.append(f"member {acheteur} is the buyer who posted this request.")
    return out


def _apercu_destinataires(client, ids: list[int]) -> tuple[list[dict], list[int]]:
    """Who would receive the email — and the ids that are not members (the real
    send would be refused entirely: HelloStock checks all the ids before sending)."""
    from oto.tools.common.errors import UpstreamHTTPError

    trouves, inconnus = [], []
    for uid in ids:
        try:
            m = client.get_user(uid)
        except UpstreamHTTPError as e:
            if e.status_code == 404:
                inconnus.append(uid)
                continue
            raise traduire(e) from None
        trouves.append({k: m.get(k) for k in
                        ("id", "name", "company", "email", "location", "services")})
    return trouves, inconnus


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def hellostock_demande_send(
        demande_id: int,
        user_ids: list[int],
        message: Optional[str] = None,
        allow_resend: bool = False,
        dry_run: bool = True,
    ) -> dict:
        """SEND a HelloStock demande by EMAIL to chosen members — real people, from
        HelloStock's address, recorded in the admin's name. IRREVERSIBLE once sent.

        **dry_run is True by default**: nothing is sent; the tool returns what would
        go out — the specs, the recipients (name, company, email), who already
        received this demande, and warnings. Show it to the human; send only on
        their go, with `dry_run=False`.

        The email carries the specs (material, grade, format, dimensions, thickness,
        quantity, deadline, certificate) and links to the demande's page — never
        the buyer's identity, reference or comment. `message` goes in verbatim.

        Result of a real send: `envoyes` (count), `echecs` (emails that failed,
        not recorded), `noop` (true = HelloStock's mailer is not configured: NO
        email left, yet the sends were recorded as done).

        Args:
            demande_id: the demande to send.
            user_ids: recipients, 1-50 member ids (`hellostock_membre`).
            message: optional note put verbatim in the email, 2000 characters max.
            allow_resend: send again to members who already received this demande (a deliberate reminder).
            dry_run: default True = preview only. Pass False to send.
        """
        ids = _destinataires(user_ids)
        if message is not None and len(message) > _MESSAGE_MAX:
            raise _bad(f"`message`: at most {_MESSAGE_MAX} characters "
                       f"({len(message)} received) — nothing was sent.")
        client = _client()
        demande = _run(lambda: client.get_demande(demande_id))
        deja = _deja_recus(demande)
        deja_servis = [u for u in ids if u in deja]
        avert = _avertissements(demande, ids)

        if dry_run:
            recipients, inconnus = _apercu_destinataires(client, ids)
            if inconnus:
                avert.append(f"ids that are not members: {inconnus} — the real "
                             "send would be refused entirely.")
            bloque = bool(deja_servis) and not allow_resend
            return {
                "dry_run": True,
                "demande": {"id": demande.get("id"), "status": demande.get("status"),
                            **{k: demande.get(k) for k in _SPECS},
                            "fichiers": len(demande.get("fichiers") or [])},
                "recipients": recipients,
                "already_sent": [{"user_id": u, "sent_at": deja[u]} for u in deja_servis],
                "message": message,
                "warnings": avert,
                "note": ("Nothing was sent. dry_run=False writes to "
                         f"{len(recipients)} real member(s), with no way back"
                         + (" — refused while `already_sent` is not empty, unless "
                            "allow_resend=True." if bloque else ".")),
            }

        if deja_servis and not allow_resend:
            raise _bad(
                "Already received by " + ", ".join(f"{u} (on {deja[u]})" for u in deja_servis)
                + ": nothing was sent. Remove these members from `user_ids`, or pass "
                "`allow_resend=True` if it is a deliberate reminder.")
        from oto.tools.common.errors import UpstreamHTTPError
        try:
            out = client.send_demande(demande_id, ids, message=message)
        except ValueError as e:
            raise _bad(str(e)) from None
        except UpstreamHTTPError as e:
            if e.status_code == 502:
                echecs = e.body.get("echecs") if isinstance(e.body, dict) else None
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=(
                    "HelloStock could not send any email (502): no send was "
                    f"recorded. Failures: {echecs or 'not detailed'}. Do not retry "
                    "in a loop — alert a HelloStock administrator."))) from None
            raise traduire(e) from None
        notes = []
        if out.get("noop"):
            notes.append("noop=true: HelloStock's mailer is not configured — "
                         "NO email went out, and yet the sends are "
                         "recorded on the request.")
        if out.get("echecs"):
            notes.append(f"{len(out['echecs'])} email(s) failed, not recorded: "
                         f"{out['echecs']}.")
        return {**out, "demande_id": demande_id, "user_ids": ids, "warnings": avert,
                **({"note": " ".join(notes)} if notes else {})}

    @mcp.tool()
    def hellostock_demande_set_status(
        demande_id: int,
        status: Literal[STATUSES],
        dry_run: bool = False,
    ) -> dict:
        """⚠️ WRITES on the live HelloStock marketplace: set a demande's status.

        Any transition is accepted (closed → published included). `published` is
        visible on the public marketplace at once; any other status takes it off
        the public pages. No email is sent. The current status is read first and
        returned as `from`; `dry_run=True` writes nothing.

        Args:
            demande_id: the demande.
            status: the new status.
            dry_run: True = show the transition, write nothing.
        """
        client = _client()
        avant = _run(lambda: client.get_demande(demande_id)).get("status")
        base = {"demande_id": demande_id, "from": avant, "to": status}
        if avant == status:
            return {**base, "unchanged": True, "note": "already in this status, nothing written."}
        if dry_run:
            return {**base, "dry_run": True, "effect": _VISIBILITE}
        return {**_run(lambda: client.update_demande_status(demande_id, status)),
                **base, "effect": _VISIBILITE}

    @mcp.tool()
    def hellostock_offre_update(
        offre_id: int,
        status: Optional[Literal[STATUSES]] = None,
        keywords: Optional[Annotated[list[str], Field(min_length=1, max_length=30)]] = None,
        dry_run: bool = False,
    ) -> dict:
        """⚠️ WRITES on the live HelloStock marketplace: an offre's status and/or its
        keywords.

        `keywords` REPLACES the whole list and is PUBLIC (it feeds the marketplace
        search). Only material designations and specs belong there (`304L`,
        `1.4307`, `X2CrNi18-9`, `EN 10204 3.1`, `3 mm`) — never a steelmaker's
        name, a heat or order number, even when the certificate check shows them:
        HelloStock refuses the whole list (400, naming the term). It lowercases,
        trims and de-duplicates; the result returns what is actually stored.

        `status`: `published` is visible publicly at once, any other status takes
        the offre off the public pages. No email is sent. `dry_run=True` shows the
        change and writes nothing (it cannot pre-check HelloStock's keyword rules).

        Args:
            offre_id: the offre.
            status: the new status.
            keywords: 1-30 terms, 60 characters max each; replaces the current list.
            dry_run: True = show the change, write nothing.
        """
        if status is None and keywords is None:
            raise _bad("`status` or `keywords`: at least one of the two.")
        client = _client()
        avant = _run(lambda: client.get_offre(offre_id))
        voulu = {k: v for k, v in (("status", status), ("keywords", keywords))
                 if v is not None}
        base = {"offre_id": offre_id,
                "from": {k: avant.get(k) for k in voulu}, "to": voulu}
        if dry_run:
            return {**base, "dry_run": True}
        _run(lambda: client.update_offre(offre_id, status=status, keywords=keywords))
        apres = _run(lambda: client.get_offre(offre_id))
        return {**base, "success": True,
                "written": {k: apres.get(k) for k in voulu}}
