"""User-facing "how-to" docs for connectors — one MARKDOWN file per connector.

The content lives in `connectors/docs/<name>.md`, next to the code, editable without
touching Python. It used to be an 850-line dict right here: writing prose inside
Python strings discourages keeping it up to date, and it showed — the Salesforce doc
still described an application model that Salesforce has since disabled.

**Format.** One file = one connector, named like it (`tools/<name>.py` ⟷
`connectors/docs/<name>.md`). Each section is a level-2 heading:

    ## <kind> — <title>

    light markdown body

`kind` ∈ {prerequisite, setup, usage, note}:
- `prerequisite` — what is needed BEFORE connecting (where to get the key, an
  authorization to grant on the provider side…). Shown before connecting;
- `setup`        — configuration steps;
- `usage`        — what the connector allows + concrete examples. Also shown in
  discovery (marketplace, showcase);
- `note`         — miscellaneous.

**Body** = light markdown: `[label](url)` (http(s) only, otherwise rendered as text),
`**bold**`, `` `code` ``, `- ` lists. Stay FACTUAL: describe what the tools actually
do, and link the vendor's doc page rather than invent an exact UI path — SaaS
consoles move around, and a stale path sends the user into a wall.

**Derived values**: `{{callback:/path}}` and `{{egress_ip}}`, resolved at read time
(see `_resoudre`).

The public catalog and `/api/me/connectors` derive from them (`Connector.doc_sections`);
it is rendered everywhere the connector is displayed — connection card, marketplace,
showcase.
"""
from __future__ import annotations

import functools
import logging
import os
import pathlib
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DIR = pathlib.Path(__file__).parent / "docs"

KINDS = ("prerequisite", "setup", "usage", "note")

# `## kind — title` (em dash or plain hyphen: both are accepted on input).
_TITRE = re.compile(r"^##\s+(" + "|".join(KINDS) + r")\s*[—-]\s*(.+?)\s*$")
# A section title that LOOKS like the pattern but whose first word is not a known
# kind. Without it, `## several workspaces — …` matched nothing: the line fell into
# the BODY of the previous section, `##` included, and the text was displayed in
# the wrong place — in the prerequisite, shown before connecting. Seen on
# 2026-08-27 on the Slack doc. A badly named title gets flagged, not swallowed.
_TITRE_SUSPECT = re.compile(r"^##\s+([a-zA-Z][\w -]*?)\s*[—-]\s*(.+?)\s*$")
_MARQUEUR = re.compile(r"\{\{callback:([^}]+)\}\}")
_MARQUEUR_EGRESS = "{{egress_ip}}"
EGRESS_IP_VAR = "OTO_EGRESS_IP"
# Undeclared: the doc says the address exists without inventing one. A hard-coded IP
# is the address of ONE instance, served to the users of all the others.
_EGRESS_INCONNUE = "our server's outgoing address (ask support for it)"


@dataclass(frozen=True)
class DocSection:
    kind: str            # prerequisite | setup | usage | note
    title: str
    body_md: str


def _resoudre(corps: str) -> str:
    """Replaces the `{{callback:/path}}` markers with their DERIVED value.

    A doc must NEVER hard-code a callback URL: it depends on the environment, so a
    prose URL lies as soon as it is read from the other one. Real bug: the docs of
    two connectors showed the PREPROD domain to production users, and the
    resulting `redirect_uri_mismatch` blamed the client. Resolved at READ time,
    never at import.

    `{{egress_ip}}` follows the same rule: the address our server calls providers
    from is the instance's own (`OTO_EGRESS_IP`). Real bug: the Salesforce doc gave
    one instance's address to the users of another, whose IP allowlist then refused
    every token refresh with a misleading `invalid_grant`."""
    if _MARQUEUR_EGRESS in corps:
        ip = os.environ.get(EGRESS_IP_VAR, "").strip()
        corps = corps.replace(_MARQUEUR_EGRESS, f"`{ip}`" if ip else _EGRESS_INCONNUE)
    if "{{callback:" not in corps:
        return corps
    from ..auth import flow as oauth_flow
    return _MARQUEUR.sub(lambda m: oauth_flow.redirect_uri(m.group(1)), corps)


def _parse(texte: str, source: str) -> tuple[DocSection, ...]:
    sections: list[DocSection] = []
    kind: str | None = None
    titre = ""
    corps: list[str] = []

    def _fermer() -> None:
        if kind is not None:
            sections.append(DocSection(kind, titre, "\n".join(corps).strip()))

    for ligne in texte.splitlines():
        m = _TITRE.match(ligne)
        if m:
            _fermer()
            kind, titre, corps = m.group(1), m.group(2), []
            continue
        suspect = _TITRE_SUSPECT.match(ligne)
        if suspect and suspect.group(1) not in KINDS:
            logger.warning(
                "connectors/docs/%s : section `%s` — unknown kind, the whole section "
                "goes into the body of the previous one. Expected: %s",
                source, suspect.group(1), ", ".join(KINDS))
        if kind is not None:
            corps.append(ligne)
        elif ligne.strip():
            # Text before any heading: it would be displayed nowhere. We flag it
            # rather than let it disappear silently.
            logger.warning("connectors/docs/%s : text outside any section, ignored: %.60s",
                           source, ligne.strip())
    _fermer()
    return tuple(sections)


@functools.lru_cache(maxsize=1)
def _fichiers() -> dict[str, tuple[DocSection, ...]]:
    """Read once per process: the content is static, shipped with the code."""
    out: dict[str, tuple[DocSection, ...]] = {}
    if not _DIR.is_dir():
        logger.warning("connectors/docs/ missing: sheets will have no doc")
        return out
    for f in sorted(_DIR.glob("*.md")):
        sections = _parse(f.read_text(encoding="utf-8"), f.name)
        if sections:
            out[f.stem] = sections
    return out


def sections_for(connector: str) -> tuple[DocSection, ...]:
    """The connector's sections, markers resolved. Empty if there is no doc."""
    return tuple(DocSection(s.kind, s.title, _resoudre(s.body_md))
                 for s in _fichiers().get(connector, ()))


class _Vue(dict):
    """`DOC_SECTIONS[name]`, `in`, `.get()` work like the old dict — but the
    values are resolved AT READ TIME: the callback URL depends on the environment and
    cannot be frozen at module load."""

    def __getitem__(self, k):
        s = sections_for(k)
        if not s:
            raise KeyError(k)
        return s

    def get(self, k, default=None):
        return sections_for(k) or default

    def __contains__(self, k):
        return k in _fichiers()

    def __iter__(self):
        return iter(_fichiers())

    def __len__(self):
        return len(_fichiers())

    def keys(self):
        return _fichiers().keys()

    def items(self):
        return ((n, sections_for(n)) for n in _fichiers())

    def values(self):
        return (sections_for(n) for n in _fichiers())


DOC_SECTIONS = _Vue()


def multi_account_section(connector: str, noun: str,
                          par_connexion: bool = False) -> DocSection:
    """The "multiple <noun>s" section of a multi-account connector — WRITTEN ONCE
    FOR ALL: the mechanics (named account, default, `_account`, refusal on
    ambiguity) are the platform's, not a provider's. A connector sheet only writes
    what is specific to it (why one key per <noun> is needed).

    `par_connexion`: the account comes from a CONNECTION (OAuth — google, sharepoint),
    not from a stored key. It is then named by its address, the first linked one is
    the default, and nothing is set at the team or org level from this action: the
    stored-keys rule "the first one unnamed, then "principal"" would be wrong here."""
    if par_connexion:
        nommage = (
            f"this connector accepts multiple {noun}s: connecting with another "
            f"{noun} **adds** it alongside the others, named by its address; "
            f"reconnecting with the same one replaces its own.",
            f"- the first linked {noun} is the default",
        )
        lister = f"`oto_identity(op='list', connector='{connector}')`"
        renommer, pose = "", "linked"
    else:
        nommage = (
            f"this connector accepts multiple {noun}s: each stored credential becomes a "
            f"**named account** (one name per {noun}), at your level, your team's or "
            f"your org's.",
            "- the first one doesn't need a name; from the second on, each one carries its own "
            "(the first then takes the name \"principal\", renamable)",
        )
        lister = (f"`oto_identity(op='list', connector='{connector}')` (`scope='org'` or "
                  f"`scope='group'` for the org's or team's)")
        renommer, pose = "; rename: `op='rename'` with `new_name`", "stored"
    return DocSection("setup", f"multiple {noun}s", "\n".join((
        *nommage,
        f"- without specification, the agent takes the only {pose} {noun}, otherwise the one marked "
        f"as default; otherwise the call is **refused**, naming the available {noun}s — "
        f"never a random choice",
        f"- target a {noun} for a call: `_account=\"<name>\"` on the tool; list them: "
        f"{lister}",
        f"- set the default: `oto_identity(op='set', connector='{connector}', "
        f"identity_id='<name>')`{renommer}",
        f"- ⚠️ no tool walks through the {noun}s on its own: a total across several "
        f"{noun}s is obtained by calling each one (`_account`) and adding up",
    )))
