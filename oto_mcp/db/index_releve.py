"""`idx_tool_calls_org_tool_ok` : l'index des relevés d'org par outil (oto-backend#1145).

`tool_calls (org_id, tool, created_at DESC) WHERE ok`. Il naît par la révision Alembic
0032 (CONCURRENTLY) et par le démarrage d'une base neuve (`_init.py`, non concurrent),
sous le verdict commun des index de ce régime (`db/index_concurrent.py`) : mesuré en
production le 04/10/2026, 172 s de construction pour environ 12 M lignes — au-delà des
120 s de la fenêtre de démarrage, d'où le seuil de taille et le geste manuel (§5.1).
"""
from __future__ import annotations

from .index_concurrent import IndexConcurrent

RELEVE = IndexConcurrent(
    nom="idx_tool_calls_org_tool_ok",
    table="tool_calls",
    forme="(org_id, tool, created_at DESC) WHERE ok",
    revision="0032",
)
