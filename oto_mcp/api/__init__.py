"""La face REST `/api/*` — l'assemblage et les handlers qui restent écrits à la main.

Package sans surface propre. `routes` porte la TABLE de routes (son ordre est un
contrat, figé par `tests/api/test_api_routes_table_frozen.py`) et les deux middlewares
ASGI ; `base` porte ce que tous les handlers partagent (auth, CORS, `_json`,
préflight, `bind`) ; les autres modules portent les handlers par domaine.

⚠️ Une route neuve naît **capacité** (`capabilities/`, ADR 0009), pas ici : la dette
REST écrite à la main est à ZÉRO et deux garde-fous la tiennent
(`test_rest_debt_stays_at_zero`, `test_no_new_handwritten_rest_route`).
"""
