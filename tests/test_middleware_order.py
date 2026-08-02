"""L'ordre des middlewares MCP est un CONTRAT, pas un détail (2026-08-02).

fastmcp exécute `instance.middleware` dans l'ordre de la liste : premier ajouté =
plus EXTERNE (`_run_middleware` wrap en reversed(), vérifié empiriquement). Les
invariants gardés ici :

- `CallContextMiddleware` OUTERMOST — sa ContextVar `_CALL_ORG` doit rester posée
  pendant que la rédaction ET le calllog (plus internes… donc ajoutés après) relisent
  `current_org`. Ajouté ailleurs, un appel `_org=` est rédigé/audité sous l'org MAISON
  (bug vécu : il était innermost jusqu'au 2026-08-02).
- `FieldRedactionMiddleware` avant le reste : retouche le résultat final en sortie.
- `ErrorEnvelopeMiddleware` plus externe que calllog + Sentry : eux voient l'erreur
  brute, l'enveloppe scrubbe en dernier.
- `UserDisabledToolsMiddleware` plus externe que `ToolCallLogger` : un refus de gate
  n'est pas journalisé.
- `SentryToolErrorMiddleware` INNERMOST : capture le vrai traceback en premier et
  pose `last_event_id` que le calllog (plus externe) stampe sur la ligne tool_calls.
"""
from oto_mcp import server


OURS = [
    "CallContextMiddleware",
    "FieldRedactionMiddleware",
    "ErrorEnvelopeMiddleware",
    "UserDisabledToolsMiddleware",
    "DynamicInstructionsMiddleware",
    "ToolCallLogger",
    "SentryToolErrorMiddleware",
]


def test_mcp_middleware_order_contract():
    # fastmcp préfixe les siens (ex. DereferenceRefsMiddleware) — on fige l'ordre
    # relatif des NÔTRES, pas la liste brute.
    names = [type(m).__name__ for m in server.mcp.middleware if type(m).__name__ in OURS]
    assert names == OURS, (
        f"Ordre des middlewares modifié : {names}. Premier ajouté = plus EXTERNE — "
        "relire les invariants du docstring avant de changer quoi que ce soit."
    )
