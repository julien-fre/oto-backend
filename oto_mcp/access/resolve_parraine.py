"""The key a SPONSORED copilot leg is served (`access/parrainage`): the platform key of
the provider, grants aside — unless the person's own key wins the cascade.

Split from `resolve.py` (its 500-line ratchet): the resolver calls `resolu` first when
`parrainage.paie(provider)`, and the copilot's model key reads `resolu_traces`."""
from __future__ import annotations

from typing import Optional

from .. import credentials_store
from ..mcp_errors import McpError
from . import parrainage
from .resolved_credential import ResolvedCredential

#: The modes the operator re-bills: a key of the person's own is any OTHER mode.
_REFACTURES = ("platform", "tenant")


def resolu(provider: str, want: str, sub: str, *,
           propre_d_abord: bool = True) -> Optional[ResolvedCredential]:
    """The platform key of `provider` for a sponsored leg — or None when the person's
    own key wins the cascade (used as always: never billed), or when the platform holds
    no key (the ordinary path then says why).

    Grants, the daily spend limit, the platform quota and the units budget are not
    read: none of them is the workspace's to pay here. Traced `key_mode = "sponsor"`,
    which the partner's billing does not bill; `is_platform=False`, so the person's
    platform quota is not debited after the call either."""
    if propre_d_abord:
        from .resolve import _resolve_credential_impl
        try:
            propre = _resolve_credential_impl(provider, want, sub, account=None,
                                              check_usage=False, units=1)
        except McpError:
            propre = None
        if propre is not None and propre.mode not in _REFACTURES:
            return None
    for inst in credentials_store.list_platform_instances(provider):
        secret = credentials_store.get_credential(credentials_store.PLATFORM,
                                                  inst["label"], provider)
        if secret:
            return ResolvedCredential(provider, secret, False, parrainage.KEY_MODE,
                                      credentials_store.PLATFORM, inst["label"])
    return None


def servi(provider: str, want: str, sub: str,
          account: Optional[str]) -> Optional[ResolvedCredential]:
    """The resolver's hook (`resolve.resolve_credential`): the sponsored key when the
    leg is sponsored and the provider covered (`parrainage.paie`), never for a key the
    caller named (`account`) or asked of their own (`want="byo"`)."""
    if not parrainage.paie(provider) or want == "byo" or account:
        return None
    return resolu(provider, want, sub)


def resolu_traces(provider: str, sub: str) -> Optional[ResolvedCredential]:
    """The platform key straight away, for a caller outside a tool call — the copilot's
    model key (`copilot/cle.resoudre`, which already took the org's own key and never
    serves a personal one) —, noted in the call's trace like any served key."""
    from .resolve import _note_resolved_instance
    rc = resolu(provider, "auto", sub, propre_d_abord=False)
    return _note_resolved_instance(rc) if rc is not None else None
