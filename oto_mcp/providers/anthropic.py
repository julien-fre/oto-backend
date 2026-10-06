"""Registry declaration of the `anthropic` key carrier.

Single home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). Cf. `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# ⚠️ `kind="credential"`: NO tool. This object only serves to carry the key
# that the platform uses ON BEHALF OF the org — a scheduled agent consumes
# it, no tool exposes it. An ordinary connector with empty namespaces
# would present itself as a connector without having its effects; the
# distinct type lets the screen say what it is.
#
# `byo_org` only: the key is the ORGANIZATION's, not a person's —
# it is what pays for the turns, and a scheduled agent outlives its author.
CONNECTOR = _c(
    "anthropic", [], kind="credential", auth_modes={"byo_org"}, keyed=True,
    # ⚠️ SINGLE-account, declared: the derivation would yield `multi` (api_key), and
    # the screen would offer to set a second key that nothing could choose between.
    # A run uses ONE key — two deposits for the same org would be two
    # invoices for the same work, and the worker has no criterion to decide.
    cardinality="mono",
    # `fields` (14/09/2026): the key, and the WORKSPACE of an organization key. A
    # key created for the whole Anthropic organization, and not in a workspace, makes
    # every request that does not name the workspace to bill be refused (header
    # `anthropic-workspace-id`). The workspace is not secret and lives in `meta`
    # (`in_meta`): the encrypted blob keeps the RAW key, and keys already deposited
    # read back identically. The worker receives it at claim, next to the key.
    secret_kind="fields", label="Anthropic",
    credential_fields=(
        CredentialField("key", "API key", secret=True),
        CredentialField(
            "workspace_id", "Workspace", secret=False, required=False, in_meta=True,
            help="Only for an ORGANIZATION key (created outside a workspace): "
                 "the id of the workspace to bill. Empty for a workspace key."),
    ),
    help="Anthropic model key — used by the organization's scheduled agents, never by a tool",
    href="https://console.anthropic.com/settings/keys",
)

CATEGORY = "AI models"
PUBLISHER = "Anthropic"
LOGO_DOMAIN = "anthropic.com"
