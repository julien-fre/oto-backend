"""Middlewares FastMCP — un module par middleware.

L'ORDRE d'enregistrement est un contrat (`server.py`, figé par
`tests/middleware/test_middleware_order.py`) : il vit là où les middlewares sont AJOUTÉS, pas
ici. Ce package n'expose donc rien — chaque middleware s'importe de son module.
"""
