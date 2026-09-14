"""Un refus attendu d'outil tient sur UNE ligne de journal, pas dans un cadre de trace.

Mesuré le 14/09/2026 sur une heure du journal de production : 258 lignes « Error calling
tool », dont 230 pour `serper_scrape`, chacune suivie d'un cadre de trace complet — 398
cadres au total. Ce n'étaient pas des plantages : des refus levés exprès et nommés
(« Scrape impossible… cette page n'existe pas »), que fastmcp journalise par
`logger.exception` parce qu'une `McpError` n'est pas une de ses erreurs à lui. Le bruit
gonflait le compte d'erreurs du backend et noyait les vraies traces.

Le critère n'est pas réécrit ici : c'est celui qui écarte déjà ces refus de Sentry
(`error_taxonomy._is_user_input_error` — une `McpError` de code INVALID_PARAMS ou
INVALID_REQUEST dans la chaîne). Le refus reste JOURNALISÉ, avec son message, au niveau
INFO ; seule sa trace disparaît. Toute autre exception garde sa trace et son niveau.

Rien ne change pour le client : le filtre agit sur l'enregistrement de journal, jamais
sur l'exception, qui poursuit son chemin telle quelle.

⚠️ **Une vraie panne habillée en refus garde son nom au journal.** Environ 180 sites lèvent
une `McpError` INVALID_* dans un `except` : sans `from e`, Python y attache quand même
l'exception d'origine en `__context__`, et le critère la classe en refus. Elle échappe
déjà à Sentry ; sans trace, le journal brut était son dernier filet. La ligne nomme donc
la cause chaînée (`— cause : KeyError('siren')`) : elle reste une ligne, et une panne mal
classée reste lisible.
"""
from __future__ import annotations

import logging
from typing import Optional

from .error_taxonomy import _USER_INPUT_CODES, _chain, _is_user_input_error
from .mcp_errors import McpError

#: Le journal où fastmcp écrit « Error calling tool » (`server/server.py`).
JOURNAL_FASTMCP = "fastmcp.server.server"

_MESSAGE_MAX = 300
_CAUSE_MAX = 200


def _cause_du_refus(exc: BaseException) -> Optional[BaseException]:
    """L'exception que le refus enveloppe (`__cause__`, sinon `__context__`), s'il y en a une."""
    chaine = list(_chain(exc))
    for i, e in enumerate(chaine):
        if isinstance(e, McpError) and getattr(e.error, "code", None) in _USER_INPUT_CODES:
            return chaine[i + 1] if i + 1 < len(chaine) else None
    return None


class RefusSurUneLigne(logging.Filter):
    """Retire la trace d'un refus explicite et le rabaisse en INFO, message compris."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if exc is None or not _is_user_input_error(exc):
            return True
        record.msg = f"{record.getMessage()} — refus : {str(exc)[:_MESSAGE_MAX]}"
        cause = _cause_du_refus(exc)
        if cause is not None:
            record.msg += f" — cause : {repr(cause)[:_CAUSE_MAX]}"
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.levelno, record.levelname = logging.INFO, "INFO"
        return True


def installer() -> None:
    """Pose le filtre sur le journal de fastmcp — une fois, même rappelé."""
    journal = logging.getLogger(JOURNAL_FASTMCP)
    if not any(isinstance(f, RefusSurUneLigne) for f in journal.filters):
        journal.addFilter(RefusSurUneLigne())
