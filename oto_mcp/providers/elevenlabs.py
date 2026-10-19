"""Registry declaration of the `elevenlabs` connector.

Single home of its entry: `providers/__init__.py` AGGREGATES it (it does not
describe it). See `providers/_model.py` for the `Connector` contract.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# elevenlabs: voice and audio generation — speech (with each word's timing),
# music, sound effects, voice design previews — plus the account's voices,
# models and credit balance.
#
# ONE key in the `xi-api-key` header (`keyed=True`, vault field `key`). A key can
# be restricted to some endpoints when it is created in ElevenLabs: the help says
# which permissions the tools need.
#
# Strict BYOK (`byo_user` + `byo_org`, no platform mode): every generation spends
# the customer's own ElevenLabs credits.
CONNECTOR = _c(
    "elevenlabs", ["elevenlabs"], auth_modes={"byo_user", "byo_org"},
    keyed=True, secret_kind="api_key",
    credential_fields=(
        CredentialField(
            "key", "API key", secret=True,
            help="ElevenLabs → Developers → API Keys → Create key. A restricted key "
                 "needs text to speech, music and sound effects for the generations, "
                 "and user read, voices read and models read for the reads."),
    ),
    label="ElevenLabs",
    help="voice and audio: speech with word timings, music, sound effects, voice "
         "design, and the account's voices, models and credits",
    href="https://elevenlabs.io",
)

CATEGORY = "AI models"
PUBLISHER = "ElevenLabs"
LOGO_DOMAIN = "elevenlabs.io"

DESCRIPTION = (
    "Generate audio with your ElevenLabs account: a script read by any of your "
    "voices, with each word's timing for captions and edits; music from a prompt or "
    "a section-by-section plan; sound effects; previews of a voice described in "
    "words. Also reads your voices, the models and the credits left. Audio comes "
    "back as a download link. Your own API key."
)
