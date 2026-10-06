"""Mailpool DNS — pure logic behind `mailpool_domains(op="audit")` and
`mailpool_domain_fix`: no network, no client, testable on plain record lists.

A record is Mailpool's shape: `{type, key, value, priority?}`, `key` being the
host label (`None` for the apex).

What was observed live (2026-10-05, six domains) and is encoded here:

- `PUT /domains/{id}/dns` REPLACES the record set: a plan always carries the
  WHOLE set, current records included;
- the `_dmarc` TXT record is stored by the PUT but only published by
  `POST /dmarc-email` (which writes the address as `rua` AND `ruf`): a plan
  that touches DMARC says so (`publish_dmarc_email`);
- the records are Mailpool's stored configuration, not a live DNS lookup.

Two guards live here, so a preview refuses exactly what a write would:

- a plan never starts from an unreadable set: an empty or malformed read would
  make the PUT wipe the domain (`DnsUnreadable`);
- a plan never introduces an audit ERROR the current set does not have (second
  SPF, MX or DKIM gone, CNAME at the apex…): `WouldBreak`. Fixing an existing
  error stays possible.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

Record = Dict[str, Any]


class PlanRefused(ValueError):
    """A refused plan, named (`reason`) so the tool can say which refusal it is."""
    reason = "invalid_argument"


class DnsUnreadable(PlanRefused):
    reason = "dns_unreadable"


class WouldBreak(PlanRefused):
    reason = "dns_would_break"

    def __init__(self, message: str, findings: List[Dict[str, str]]):
        super().__init__(message)
        self.findings = findings


#: A DMARC report address: no `;`, `,` or space can reach the `_dmarc` record.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

#: CNAME targets of sending tools' custom tracking domains (verified ones only).
TRACKING_TARGETS = {
    "custom.lemlist.com": "lemlist",
}

#: DMARC tags dropped when the record is normalised: `fo` / `rf` only tune
#: forensic reports, `ri=86400` is the default. `ruf` is NOT dropped: Mailpool
#: republishes the record with `ruf` = the report address whatever is stored, so
#: the normalised record carries it too — otherwise every run would see a diff.
_DMARC_DROPPED = ("fo", "rf", "ri")
_DMARC_ORDER = ("v", "p", "sp", "pct", "adkim", "aspf", "rua", "ruf")


def _host(r: Record) -> str:
    return (r.get("key") or "@").strip().lower()


def _apex_spf(records: List[Record]) -> List[Record]:
    return [r for r in records if r.get("type") == "TXT" and _host(r) == "@"
            and str(r.get("value", "")).lower().startswith("v=spf1")]


def _dmarc(records: List[Record]) -> List[Record]:
    return [r for r in records if r.get("type") == "TXT" and _host(r) == "_dmarc"]


def parse_dmarc(value: str) -> Dict[str, str]:
    """`v=DMARC1; p=quarantine; rua=mailto:x` → ordered tag dict."""
    tags: Dict[str, str] = {}
    for part in value.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            tags[k.strip().lower()] = v.strip()
    return tags


def render_dmarc(tags: Dict[str, str]) -> str:
    keys = [k for k in _DMARC_ORDER if k in tags] + [
        k for k in tags if k not in _DMARC_ORDER]
    return "; ".join(f"{k}={tags[k]}" for k in keys)


def _mailto(tag: Optional[str]) -> List[str]:
    return [a.strip()[len("mailto:"):] for a in (tag or "").split(",")
            if a.strip().lower().startswith("mailto:")]


def audit(domain: str, records: List[Record]) -> Dict[str, Any]:
    """Findings on a domain's stored records, worst first:
    `{domain, ok, findings: [{level, check, message}], tracking}`."""
    findings: List[Dict[str, str]] = []

    def add(level: str, check: str, message: str) -> None:
        findings.append({"level": level, "check": check, "message": message})

    if not any(r.get("type") == "MX" and _host(r) == "@" for r in records):
        add("error", "mx", "No MX record at the apex: the domain cannot receive mail (replies, bounces).")

    spf = _apex_spf(records)
    if not spf:
        add("error", "spf", "No SPF record at the apex.")
    elif len(spf) > 1:
        add("error", "spf", f"{len(spf)} SPF records at the apex: receivers treat that as a permanent error.")
    else:
        value = spf[0]["value"].strip().lower()
        if value.endswith("-all"):
            pass
        elif value.endswith("~all"):
            add("warning", "spf", "SPF ends in ~all (soft fail): -all is stricter, once every sender is listed.")
        else:
            add("error", "spf", "SPF does not end in -all or ~all: any server may send as this domain.")

    if not any(_host(r).endswith("._domainkey") for r in records):
        add("error", "dkim", "No DKIM record (`*._domainkey`).")

    dmarc = _dmarc(records)
    if not dmarc:
        add("error", "dmarc", "No DMARC record (`_dmarc`).")
    elif len(dmarc) > 1:
        add("error", "dmarc", f"{len(dmarc)} `_dmarc` records: receivers ignore DMARC altogether.")
    else:
        tags = parse_dmarc(dmarc[0]["value"])
        if tags.get("p", "none").lower() == "none":
            add("warning", "dmarc", "DMARC policy is p=none: failing mail is still delivered.")
        rua = _mailto(tags.get("rua"))
        if not rua:
            add("warning", "dmarc", "DMARC has no rua address: no aggregate report is ever received.")
        for addr in rua:
            other = addr.rsplit("@", 1)[-1].lower()
            if other != domain.lower() and not domain.lower().endswith("." + other):
                add("info", "dmarc",
                    f"Reports go to another domain ({other}): receivers only send them if {other} "
                    f"publishes `{domain}._report._dmarc.{other}` (or `*._report._dmarc.{other}`) "
                    "as TXT `v=DMARC1` — outside Mailpool.")

    for host in sorted({_host(r) for r in records if r.get("type") == "CNAME"}):
        others = [r for r in records if _host(r) == host]
        if len(others) > 1:
            add("error", "cname", f"`{host}` has a CNAME next to {len(others) - 1} other "
                "record(s): resolvers then answer unpredictably.")

    tracking = [{"host": _host(r), "target": r["value"].rstrip(".").lower(),
                 "tool": TRACKING_TARGETS.get(r["value"].rstrip(".").lower())}
                for r in records if r.get("type") == "CNAME"
                and r["value"].rstrip(".").lower() in TRACKING_TARGETS]
    if not tracking:
        add("info", "tracking",
            "No custom tracking domain (CNAME to a sending tool): tracked links use the tool's shared domain.")

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order[f["level"]])
    return {"domain": domain, "ok": not any(f["level"] == "error" for f in findings),
            "findings": findings, "tracking": tracking,
            "source": "Mailpool's stored DNS configuration, not a live lookup"}


def _identity(r: Record) -> tuple:
    return (r.get("type"), _host(r), r.get("value"), r.get("priority"),
            r.get("port"), r.get("weight"))


def _same(a: Record, b: Record) -> bool:
    return _identity(a) == _identity(b)


def check_current(records: Any) -> List[Record]:
    """The set read from Mailpool, refused unless it is a non-empty list of
    records: a write replaces the whole set, so planning from `{}` or `[]`
    would delete every record of the domain."""
    if (not isinstance(records, list) or not records
            or not all(isinstance(r, dict) and r.get("type") for r in records)):
        raise DnsUnreadable(
            "Mailpool returned no readable DNS record set for this domain, so nothing "
            "can be planned: a write replaces the whole set. Check it with "
            "mailpool_domains(op=\"dns\") or in Mailpool.")
    return records


def _errors(records: List[Record]) -> List[Dict[str, str]]:
    return [f for f in audit("", records)["findings"] if f["level"] == "error"]


def label(r: Record) -> str:
    prio = f" ({r['priority']})" if r.get("priority") is not None else ""
    return f"{r.get('type')} {_host(r)} {r.get('value')}{prio}"


def plan(records: List[Record], *, spf_hard_fail: bool = False,
         dmarc_report_email: Optional[str] = None,
         tracking_host: Optional[str] = None, tracking_target: Optional[str] = None,
         add_records: Optional[List[Record]] = None,
         remove_records: Optional[List[Record]] = None) -> Dict[str, Any]:
    """The target record set and its diff. Raises `PlanRefused` on an impossible
    or harmful request. Never drops a record that was not explicitly asked for."""
    records = check_current(records)
    target = [dict(r) for r in records]
    notes: List[str] = []
    publish_email: Optional[str] = None

    if spf_hard_fail:
        spf = _apex_spf(target)
        if len(spf) != 1:
            raise PlanRefused(f"spf_hard_fail: expected one apex SPF record, found {len(spf)}.")
        value = spf[0]["value"]
        new = re.sub(r"[~?+]all\s*$", "-all", value.strip(), flags=re.IGNORECASE)
        if not new.lower().endswith("-all"):
            raise PlanRefused("spf_hard_fail: the SPF record has no `all` mechanism to tighten.")
        spf[0]["value"] = new

    if dmarc_report_email is not None:
        if not _EMAIL.fullmatch(dmarc_report_email):
            raise PlanRefused("`dmarc_report_email` must be one plain email address, "
                              "such as dmarc@example.com.")
        dmarc = _dmarc(target)
        if len(dmarc) > 1:
            raise PlanRefused(f"{len(dmarc)} `_dmarc` records: fix them by hand first.")
        if dmarc:
            tags = parse_dmarc(dmarc[0]["value"])
            for k in _DMARC_DROPPED:
                tags.pop(k, None)
            tags["rua"] = tags["ruf"] = f"mailto:{dmarc_report_email}"
            dmarc[0]["value"] = render_dmarc(tags)
        else:
            target.append({"type": "TXT", "key": "_dmarc", "value": render_dmarc({
                "v": "DMARC1", "p": "quarantine", "sp": "quarantine", "pct": "100",
                "rua": f"mailto:{dmarc_report_email}", "ruf": f"mailto:{dmarc_report_email}"})})
            notes.append("The domain had no `_dmarc` record: it is created with policy "
                         "p=quarantine; sp=quarantine; pct=100.")
        publish_email = dmarc_report_email
        notes.append("Mailpool publishes `_dmarc` with the address as rua AND ruf: ruf cannot be removed there, so it is kept.")

    if (tracking_host is None) != (tracking_target is None):
        raise PlanRefused("`tracking_host` and `tracking_target` go together.")
    if tracking_host is not None:
        host = tracking_host.strip().lower().rstrip(".")
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", host):
            raise PlanRefused("`tracking_host` must be a single label, such as `track`.")
        existing = [r for r in target if _host(r) == host]
        if any(r.get("type") != "CNAME" for r in existing):
            raise PlanRefused(f"`{host}` already has non-CNAME records: a CNAME cannot sit next to them.")
        target = [r for r in target if _host(r) != host]
        target.append({"type": "CNAME", "key": host, "value": tracking_target.strip().rstrip(".")})

    for r in remove_records or []:
        matches = [x for x in target if _same(x, r)]
        if not matches:
            raise PlanRefused(f"remove_records: no record {label(r)}.")
        target = [x for x in target if not _same(x, r)]
    for r in add_records or []:
        if not any(_same(x, r) for x in target):
            target.append({k: v for k, v in r.items() if v is not None or k == "key"})

    before = {(f["check"], f["message"]) for f in _errors(records)}
    broken = [f for f in _errors(target) if (f["check"], f["message"]) not in before]
    if broken:
        raise WouldBreak(
            "Refused, nothing was sent: this change would break the domain — "
            + " ".join(f["message"] for f in broken), broken)

    added = [label(r) for r in target if not any(_same(r, c) for c in records)]
    removed = [label(c) for c in records if not any(_same(c, r) for r in target)]
    dmarc_changed = any(" _dmarc " in x for x in added + removed)
    if dmarc_changed and publish_email is None:
        rua = _mailto(parse_dmarc(_dmarc(target)[0]["value"]).get("rua")) if _dmarc(target) else []
        if not rua:
            raise PlanRefused("The new `_dmarc` record has no rua address, which Mailpool needs to publish it.")
        publish_email = rua[0]
    return {"records": target, "added": added, "removed": removed,
            "changed": bool(added or removed),
            "publish_dmarc_email": publish_email if dmarc_changed else None,
            "notes": notes}


def missing_after(expected: List[Record], actual: List[Record]) -> Dict[str, List[str]]:
    """Two record sets compared: what `actual` lacks, what it has in excess."""
    return {"missing": [label(r) for r in expected if not any(_same(r, a) for a in actual)],
            "unexpected": [label(a) for a in actual if not any(_same(a, r) for r in expected)]}
