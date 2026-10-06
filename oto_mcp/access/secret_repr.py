"""A secret carrier does not tell its own story — the redacted `repr` of cascade objects.

⚠️ **A dataclass's default `repr` prints ALL its fields**, secret included.
This is not a logging nicety: it is the channel through which a decrypted key
leaves the server without anyone having written it. Three paths, all real:

- a `logger.debug("%r", rc)` added in good faith by a later batch;
- a **traceback** — a raising frame keeps its locals, and the error collector
  serializes them (oto-backend#564: `include_local_variables` defaulted to `True`,
  every exception left with the whole stack);
- any generic serialization of a state (diagnostic dump, assertion message).

Hence the rule, and where it is placed: **on the OBJECT, not on the variable**.
It is the object that travels — one frame holds it under one name, another under
another, and closing the two or three functions that build it closes nothing at all.
Two dataclasses currently carry a decrypted secret: `ResolvedCredential`
(the winning credential) and `CascadeRung` (the winning rung of the walk, whose
`payload` IS the secret in fetch mode).

Neighbor of `oto_mcp/journal_secrets.py`, the same rule from another angle: there
it is what we WRITE to the journal, here it is what an object SAYS about itself.
Background and history: `docs/monitoring.md` §Error tracking.
"""
from __future__ import annotations

from dataclasses import fields

_EXPURGE = "<redacted>"


def expurge(obj, *caches: str) -> str:
    """`repr` of the dataclass `obj`, with the named fields replaced by `<redacted>`.

    ⚠️ **An unknown field name RAISES.** That is the failure mode that matters here:
    a typo (`"secrret"`) would silence the protection — the `repr`
    would keep printing the key, and nothing would say so. The only moment it can
    be noticed is this one.
    """
    connus = {f.name for f in fields(obj)}
    inconnus = [c for c in caches if c not in connus]
    if inconnus:
        raise ValueError(
            f"{type(obj).__name__} has no field {inconnus} — redacting a field "
            "that does not exist protects nothing and shows up nowhere.")
    dedans = ", ".join(
        f"{f.name}=" + (_EXPURGE if f.name in caches else repr(getattr(obj, f.name)))
        for f in fields(obj))
    return f"{type(obj).__name__}({dedans})"
