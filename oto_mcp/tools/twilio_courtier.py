"""Twilio through the BROKER: the org's Tulina numbers, reached through the commerce.

An org either connects its own Twilio key (`tools/twilio.py`, the client called here
directly) or uses the numbers it bought on Tulina's own Twilio account. Those numbers,
their billing in credits and the platform's Twilio keys live in the COMMERCE (decision
of 26/11/2026: agents go through it, this service never holds a copy of the platform's
Twilio keys). This module is the agent's way there: the commerce's phone relay, called
with the relay's token for the person this server vouches for.

**Config, no default** (`OTO_COMMERCE_RELAY_URL`, `OTO_COMMERCE_RELAY_TOKEN`, the same
pair as the credits relay): unset = no broker, and a tool call without the org's own key
is refused with what to do.

**The contract** (`/service/oto/orgs/{org_id}/phone…` on the commerce):

| call | path |
|---|---|
| numbers, prices | `GET  /orgs/{org}/phone` |
| threads / thread | `GET  /orgs/{org}/phone/messages[?number_id=&peer=]` |
| send an SMS | `POST /orgs/{org}/phone/messages` `{number_id, to, body}` |
| call log | `GET  /orgs/{org}/phone/calls` |
| bridged call | `POST /orgs/{org}/phone/calls` `{number_id, rep, to}` |

Each call carries `X-Oto-Sub` (the person) and `X-Oto-Org-Role` (`org_admin` |
`org_member`, their role IN THE ORG). The commerce applies the same refusals as its app
(destination without a rate, org that cannot spend, another org's number) and bills the
org in credits.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import requests

_TIMEOUT_S = (5, 30)


@dataclass(frozen=True)
class Courtier:
    url: str
    token: str
    org_id: int
    sub: str
    role: str

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "X-Oto-Sub": self.sub,
                "X-Oto-Org-Role": self.role, "Accept": "application/json"}

    def _path(self, tail: str) -> str:
        return f"{self.url}/orgs/{self.org_id}/phone{tail}"

    def get(self, tail: str = "", params: Optional[dict] = None) -> Any:
        resp = requests.get(self._path(tail), params=params, headers=self._headers(),
                            timeout=_TIMEOUT_S)
        return _answer(resp)

    def post(self, tail: str, body: dict) -> Any:
        resp = requests.post(self._path(tail), json=body, headers=self._headers(),
                             timeout=_TIMEOUT_S)
        return _answer(resp)


class CourtierRefus(Exception):
    """A refusal from the commerce: `code` is its slug (`destination_not_allowed`,
    `credits_blocked`, `not_found`, `twilio_refused`…), `detail` Twilio's words if any."""

    def __init__(self, status: int, code: str, detail: Optional[str] = None):
        super().__init__(code)
        self.status, self.code, self.detail = status, code, detail


def _answer(resp) -> Any:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400:
        raise CourtierRefus(resp.status_code, str(body.get("error") or resp.status_code),
                            body.get("message"))
    return body


def config() -> "tuple[str, str] | None":
    url = os.environ.get("OTO_COMMERCE_RELAY_URL", "").strip().rstrip("/")
    token = os.environ.get("OTO_COMMERCE_RELAY_TOKEN", "").strip()
    if not url or not token:
        return None
    return url, token


def pour_l_appelant() -> Optional[Courtier]:
    """The broker for the caller in their current org, or None (not configured, no
    caller, no org)."""
    cfg = config()
    if cfg is None:
        return None
    from .. import access, roles
    from ..auth.hooks import current_user_sub_from_token
    sub = current_user_sub_from_token()
    org_id = access.current_org(sub) if sub else None
    if not sub or not org_id:
        return None
    role = "org_admin" if roles.is_org_admin(sub, int(org_id)) else "org_member"
    return Courtier(url=cfg[0], token=cfg[1], org_id=int(org_id), sub=sub, role=role)


MESSAGES = {
    "destination_not_allowed": "this destination is not open on Tulina numbers (no rate for it)",
    "credits_blocked": "the organization is out of credits",
    "not_found": "that number is not one of the organization's Tulina numbers",
    "not_configured": "Tulina's phone is not connected to Twilio yet",
    "empty_body": "the message is empty",
    "body_too_long": "the message is longer than 1600 characters",
}


def message_de(e: CourtierRefus) -> str:
    base = MESSAGES.get(e.code, f"refused ({e.code})")
    return f"Tulina phone: {base}." + (f" Twilio: {e.detail}" if e.detail else "")
