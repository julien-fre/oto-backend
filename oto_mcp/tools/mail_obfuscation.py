"""The contact addresses that a markdown rendering makes DISAPPEAR (signal #681).

Measured on 03/09/2026, by calling the hosted scraper (Serper) on three real
pages: it returns 200, a clean markdown of several thousand characters —
and **zero addresses**, whereas the HTML carries one, readable to the naked eye. Three
patterns, all taken from the source:

  - `<joomla-hidden-mail text="cHJlc2lkZW50ZUBsYXZvaXhkZXNsaXZyZXMuZnI=">`
    (lavoixdeslivres.fr/index.php/l-association) — the address is base64 in
    an ATTRIBUTE; a rendering that only keeps the text cannot show any of it;
  - `mailto:&#115;&#116;ran…&#064;&#103;&#109;ail&#046;com`
    (stranumundueditions.wordpress.com) — decimal entities in the href;
  - `<span class="__cf_email__" data-cfemail="7f13100a…">`
    (association.lourugby.fr/rugby-loisir) — Cloudflare, XOR on the 1st byte.
    That one is not silent, it LIES: the rendering shows the literal
    text `[email protected]`, which no address regex recognizes.

A tool that returns "nothing" where there is something fabricates a false
claim in a perfectly honest agent: it opened the page, saw nothing,
and writes it. On the 03/09 control batch, 8 records out of 23 carried a
false-negative contact, 4 of them attributable to the tool — five active companies
classified "undetermined", two dropped from the file.

⚠️ Decoding needs the HTML, and the hosted scraper does NOT return it: its
response only carries `text`, `markdown`, `metadata`, `jsonld`, `credits`
(verified on 03/09 on the API). The information is therefore not in what we
receive — we have to fetch the page OURSELVES. That is why this module
also carries the direct fetch (browser UA), which serves the signal's three requests
with a single mechanism: decode, return the raw HTML, and fall back when
the provider refuses.
"""
from __future__ import annotations

import base64
import binascii
import html as _html
import re
from typing import Optional

# ⚠️ These patterns read an UNTRUSTED page, in a worker thread that holds
# the GIL: a pattern that stops being LINEAR starves the event loop and
# freezes the whole process. It happened on 13/09/2026 — a free `+` on the local
# part made `search` quadratic on a long run of allowed characters, and
# a single `serper_scrape` froze prod for several hours. Hence two rules:
# every run is BOUNDED, and no free run is followed by an element that
# would force the search to backtrack over each character (bench:
# tests/test_mail_obfuscation_bornes.py).

# A "visible" address — used to decide whether the page ALREADY shows a contact
# (in which case we don't spend a request) and to validate what we decode.
# Bounds: 64 for the local part (RFC 5321), 63 per label (RFC 1035).
ADRESSE_RE = re.compile(
    r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){0,10}\.[A-Za-z]{2,24}")

# The tag is read without requiring its `>`: `[^>]*>` restarted from every opening
# to the end of the page when the `>` was missing.
_JOOMLA_RE = re.compile(r"<joomla-hidden-mail\b([^>]{0,4096})", re.I)
# An attribute name only starts at the beginning of a word: otherwise every letter of a
# long run restarted the name read.
_ATTR_RE = re.compile(r'(?<![A-Za-z\-])([A-Za-z\-]{1,64})\s*=\s*"([^"]*)"')
# A `mailto:` that carries at least one numeric entity. A plain-text address
# is not obfuscated: it is already in the rendering, nothing to recover. The run
# is taken as one block and the entity is searched afterwards: `[^…]*&\#[^…]*` backtracked
# over every character of a `mailto:` run with no entity.
_MAILTO_RE = re.compile(r"mailto:([^\"'\s<>]{1,2000})", re.I)
_CF_RE = re.compile(
    r'(?:data-cfemail="|/cdn-cgi/l/email-protection\#)([0-9a-fA-F]{6,})')


def _mailtos_en_entites(page: str) -> list:
    return [brut for brut in _MAILTO_RE.findall(page) if "&#" in brut]


def contient_adresse(texte: Optional[str]) -> bool:
    """Does the page already show a plain-text address?"""
    return bool(ADRESSE_RE.search(texte or ""))


def _b64(valeur: str) -> Optional[str]:
    """Decodes a Joomla base64 attribute, or None if it is not one."""
    if not valeur:
        return None
    try:
        brut = base64.b64decode(valeur + "=" * (-len(valeur) % 4), validate=True)
        return brut.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def _joomla(page: str) -> list:
    """`<joomla-hidden-mail first="…" last="…" text="…">`, tout en base64.

    `text` carries the whole address when the component displays one; otherwise we
    recompose `first@last` — it is the same page seen through two attributes, and a
    site that has no `text` (link without a label) stays readable."""
    trouvees = []
    for m in _JOOMLA_RE.finditer(page):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(m.group(1))}
        texte = _b64(attrs.get("text", ""))
        if texte and ADRESSE_RE.fullmatch(texte.strip()):
            trouvees.append(texte.strip())
            continue
        debut, fin = _b64(attrs.get("first", "")), _b64(attrs.get("last", ""))
        if debut and fin:
            recomposee = f"{debut.strip()}@{fin.strip()}"
            if ADRESSE_RE.fullmatch(recomposee):
                trouvees.append(recomposee)
    return trouvees


def _entites(page: str) -> list:
    """`mailto:` written in decimal (`&#64;`) or hex (`&#x40;`) HTML entities.

    `html.unescape` covers both forms; any `?subject=…` is dropped."""
    trouvees = []
    for brut in _mailtos_en_entites(page):
        clair = _html.unescape(brut).split("?")[0].strip()
        if ADRESSE_RE.fullmatch(clair):
            trouvees.append(clair)
    return trouvees


def _cloudflare(page: str) -> list:
    """`data-cfemail` / `/cdn-cgi/l/email-protection#…`: hex, XOR key = 1st byte."""
    trouvees = []
    for hexa in _CF_RE.findall(page):
        if len(hexa) % 2:
            continue
        octets = bytes.fromhex(hexa)
        cle = octets[0]
        clair = "".join(chr(o ^ cle) for o in octets[1:])
        if ADRESSE_RE.fullmatch(clair):
            trouvees.append(clair)
    return trouvees


# (name served to the agent, pattern presence, decoder). Presence is tested
# separately from decoding: a pattern SEEN that we cannot decode must still be
# reported — "the worst is not failing to decode, it is the page seeming to contain
# nothing" (#681). This is request no. 2 of the signal, the fallback one.
_MOTIFS = (
    ("joomla-hidden-mail", _JOOMLA_RE.search, _joomla),
    ("mailto in HTML entities", _mailtos_en_entites, _entites),
    ("cloudflare-email-protection", _CF_RE.search, _cloudflare),
)


def lire(page: Optional[str]) -> dict:
    """`{adresses, motifs}` — what the HTML hides, and in what form.

    `motifs` lists what was SEEN, decoded or not: it is what makes it possible to
    say "there is an address here" when decoding fails."""
    page = page or ""
    adresses, motifs = [], []
    for nom, presence, decode in _MOTIFS:
        if not presence(page):
            continue
        motifs.append(nom)
        for adresse in decode(page):
            if adresse not in adresses:
                adresses.append(adresse)
    return {"adresses": adresses, "motifs": motifs}


# ── what we go and fetch ourselves ───────────────────────────────────────────
# SHORT budget, distinct from that of an ordinary read: this request
# is added on top of an already-paid scrape, on an agent's hot path. Signal #662
# measured what a wait costs — not the lost second, the context
# cache that expires meanwhile: a generous ceiling here would make the remedy more
# expensive than the problem.
SONDE_DELAI_S = 8


def fetch(url: str, deadline_s: float = SONDE_DELAI_S) -> dict:
    """The HTML of `url`, via our own request, with a browser UA.

    Reuses notch ① of `web_read`: same SSRF guard, same read bounds,
    same redirects followed by hand. Redoing a fetch here means
    redoing its bugs — that one already paid for #491.

    Returns the dict of `web._fetch_http`: `{ok, verdict, html?, final_url?}`."""
    from . import web  # late: `web` imports `browserbase`, useless at register time
    return web._fetch_http(url, deadline_s=deadline_s)


def marqueur(lu: dict) -> str:
    """The line to PASTE into the served content — empty if there is nothing to say.

    It goes into the markdown, not only into a field alongside: an agent reads
    the page, and that is where it concludes "no published contact"."""
    if lu.get("adresses"):
        return ("\n\n[addresses obfuscated in the HTML, decoded by oto: "
                + ", ".join(lu["adresses"]) + "]")
    if lu.get("motifs"):
        return ("\n\n[address obfuscation detected in the HTML ("
                + ", ".join(lu["motifs"]) + ") but not decodable: the rendering "
                "above does NOT show all the contacts of the page — "
                "retry it with format=\"html\"]")
    return ""


# ── the three uses of the HTML we went to fetch ──────────────────────────────
# Budget of a REQUESTED read (format="html") or of a fallback: more generous
# than the probe, because it is the only path left — but still bounded
# by the caller's `timeout_s` if they set one.
LECTURE_DELAI_S = 20
# The raw HTML goes into the agent's context: a ceiling, and the total STATED
# alongside — a ceiling applied to an already-truncated read would be unreachable.
HTML_MAX_CHARS = 120_000


def _refus(message: str):
    from ..mcp_errors import McpError
    from mcp.types import ErrorData, INVALID_REQUEST
    return McpError(ErrorData(code=INVALID_REQUEST, message=message))


def _hors_perimetre(final_url, per) -> None:
    """The project's perimeter also applies to the URL where we LAND (#632)."""
    from .. import url_perimeter
    url_perimeter.refuse_if_excluded(final_url, per)


def html_brut(url: str, per, deadline_s: float = LECTURE_DELAI_S) -> dict:
    """`format="html"` — the page as it is served, without the scraper.

    Going through the hosted scraper would have been useless: its response carries
    no HTML field. So it is our own request, with a browser
    UA — and zero credits."""
    lu = fetch(url, deadline_s=deadline_s)
    if not lu.get("ok"):
        raise _refus(
            f"Direct read impossible for {url}: {lu.get('verdict')}. "
            "The raw HTML has no fallback — retry with format=\"markdown\" "
            "to try the hosted scraper.")
    _hors_perimetre(lu.get("final_url"), per)
    page = lu["html"]
    obf = lire(page)
    sortie = {"html": page[:HTML_MAX_CHARS],
              "html_caracteres": len(page),
              "html_tronque": len(page) > HTML_MAX_CHARS,
              "final_url": lu.get("final_url"),
              "source": "direct read (browser UA), 0 credits",
              "credits": 0}
    if obf["motifs"]:
        sortie["motifs_obfuscation"] = obf["motifs"]
    if obf["adresses"]:
        sortie["adresses_obfusquees"] = obf["adresses"]
    return sortie


def repli(url: str, per, deadline_s: float = LECTURE_DELAI_S) -> tuple:
    """The provider refused the page: we read it OURSELVES. `(output|None, verdict)`.

    On the 03/09 batch, three sites out of four refused by the scraper (two
    Wix, one WordPress.com) answer normally to an ordinary request carrying
    a browser UA. The fallback STATES its path — it does not pass itself off as
    the scraper — and returns text, not markdown: we have no converter
    here, and claiming otherwise would be worse than saying so."""
    lu = fetch(url, deadline_s=deadline_s)
    if not lu.get("ok"):
        return None, lu.get("verdict", "failure")
    _hors_perimetre(lu.get("final_url"), per)
    from .web import _EMPTY_TEXT_CHARS, extract_text
    texte, titre = extract_text(lu["html"])
    if len(texte.strip()) < _EMPTY_TEXT_CHARS:
        return None, f"page read directly but empty ({len(texte.strip())} useful chars)"
    obf = lire(lu["html"])
    sortie = {"text": texte + marqueur(obf),
              "metadata": {"title": titre},
              "final_url": lu.get("final_url"),
              "format_servi": "text",
              "source": ("direct read (browser UA) — the hosted scraper "
                         "refused this page")}
    if obf["motifs"]:
        sortie["motifs_obfuscation"] = obf["motifs"]
    if obf["adresses"]:
        sortie["adresses_obfusquees"] = obf["adresses"]
    return sortie, "lu"


def completer(res: dict, url: str, per) -> None:
    """The scrape succeeded but shows NO address: go look at the HTML.

    One more request, and ONLY there — it is exactly the moment when
    an agent is about to write "no published contact". A page that already
    shows an address triggers nothing: the remedy must not cost more than
    the problem (#662).

    The result is written into the served fields AND pasted into the content:
    the agent reads the page, that is where it concludes."""
    if contient_adresse(" ".join(str(res.get(k) or "") for k in ("markdown", "text"))):
        return
    from ..mcp_errors import McpError
    try:
        lu = fetch(url)
        if not lu.get("ok"):
            res["sonde_obfuscation"] = f"inconclusive ({lu.get('verdict')})"
            return
        _hors_perimetre(lu.get("final_url"), per)
    except McpError as refus:
        # The probe does NOT SERVE content: a refusal (non-public host, page
        # outside the project's URL perimeter) DISCARDS it — it does not make
        # a scrape that itself succeeded fail. The refusal is STATED in the
        # response, it is not swallowed.
        res["sonde_obfuscation"] = f"discarded — {refus.error.message}"
        return
    obf = lire(lu["html"])
    if not obf["motifs"]:
        # Nothing hidden: the response does NOT change. Silence is then a
        # correct piece of information — the served description says the tool always
        # re-reads the HTML of a page without an address, so "nothing" means "we
        # looked, there is nothing", not "we did not look". Adding a
        # field here would bloat nearly all responses to restate
        # what the contract already promises.
        return
    res["motifs_obfuscation"] = obf["motifs"]
    if obf["adresses"]:
        res["adresses_obfusquees"] = obf["adresses"]
    marque = marqueur(obf)
    for champ in ("markdown", "text"):
        if res.get(champ):
            res[champ] = res[champ] + marque
