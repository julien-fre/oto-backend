"""#754 — le préambule servi par `oto_use_org` prescrivait un axe que le jeu servi
ne rend pas : `org=<id>` (nom nu), alors que le jeton de contexte d'appel est
`_org=<id>` (préfixé, issue #250 — deux collisions vécues en prod avec des
arguments métier homonymes, dont `oto_use_org` lui-même : son propre champ
`org` = l'org CIBLE à résoudre, distinct de l'axe de contexte).

Mesuré en préparant ce lot (2026-09-01), PAS recopié de l'issue :
  - les capacités reçoivent `_org=` via `_mcp_adapter` (schema Pydantic du
    tool `_org`, injecté au montage) ;
  - les connecteurs ET les 14 outils `data_*` (écrits à la main dans
    `tools/datastore.py`, donc AUCUN champ `org`/`_org` dans leur propre
    signature Python) reçoivent `_org=` par un mécanisme SÉPARÉ mais au MÊME
    nom : l'axe plat `call_axes.ORG`, advertisé au schéma par
    `CallContextMiddleware.on_list_tools` et lu/épinglé/retiré des args par
    `on_call_tool` AVANT le dispatch — la fonction du tool ne le voit jamais,
    mais l'appel EST bien scopé sous cette org (`access.current_org` relit la
    même ContextVar). Vérifié de bout en bout dans
    `tests/middleware/test_call_context_org_axis.py` (garde) et
    `tests/test_call_axes_project.py` (même mécanisme, axe `_project`) — ici
    on vérifie l'axe ORG spécifiquement, et qu'il couvre bien les 14 outils
    hand-written.

Donc : contrairement à un énoncé « les data_* n'acceptent AUCUN axe d'org », le
jeu servi accepte `_org=` PARTOUT où le préambule le prescrit — le seul défaut
était le nom nu `org=` au lieu de `_org=`. Ce test garde le texte servi
ALIGNÉ sur ce fait mesuré, des deux côtés : si un jour `_org` cesse de
s'appliquer à `data_*`, ce test doit le voir AVANT que la prose ne remente.
"""
from __future__ import annotations

from oto_mcp import call_axes, org_store
from oto_mcp.capabilities import registry
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.capabilities.orgs.core import UseOrgInput, _use_org

# Les 14 outils `data_*` écrits à la main (tools/datastore.py, `@mcp.tool()` brut —
# hors les 3 siblings `data_get_schema`/`data_patch_schema`/`data_drop_column`, qui
# sont des CAPACITÉS et reçoivent `_org` par l'autre mécanisme).
DATA_TOOLS_HANDWRITTEN = [
    "data_list_namespaces", "data_create_namespace", "data_delete_namespace",
    "data_rename_namespace", "data_set_schema", "data_write", "data_claim_next",
    "data_release", "data_rows", "data_aggregate", "data_delete_row", "data_url",
    "data_share", "data_app",
]


def _served_description(key: str) -> str:
    """Le texte réellement passé à `instance.tool(description=…)` par
    `_mcp_adapter.register` (`cap.description` traverse tel quel, aucune
    réécriture — cf. `_mcp_adapter.py:155`)."""
    cap = next(c for c in registry.CAPABILITIES if c.key == key)
    assert cap.description is not None
    return cap.description


def test_preamble_prescribes_the_prefixed_axis():
    desc = _served_description("org.use_org")
    assert "_org=<id>" in desc, (
        "le préambule doit prescrire le jeton RÉELLEMENT lu (`_org=`), pas le nom "
        "nu `org=` (retiré en #250 — collision avec le champ métier `org` de ce "
        "tool lui-même)."
    )
    assert "pass `org=<id>`" not in desc, "forme nue résiduelle, non servie"


def test_data_tools_really_accept_the_org_axis():
    """Le préambule dit « data_* … accept it » — ce test garde que c'est VRAI, pour
    les 14 outils hand-written, via le même mécanisme que `_project=`/`_group=`."""
    for name in DATA_TOOLS_HANDWRITTEN:
        axes = {a.param for a in call_axes.axes_for_call(name)}
        assert "_org" in axes, f"{name} n'accepte plus `_org=` — la prose ment"


def test_a_connector_tool_accepts_the_org_axis():
    """Idem côté connecteurs — un représentant suffit, le mécanisme est générique
    (`_is_org_scopable_tool` couvre tout tool dont le namespace résout un
    connecteur du registre)."""
    axes = {a.param for a in call_axes.axes_for_call("folk_record")}
    assert "_org" in axes


def test_use_org_result_how_to_prescribes_the_prefixed_axis(monkeypatch):
    """Le `how_to` RENVOYÉ par l'appel (pas seulement la description statique) est
    le geste le plus proche du point d'usage — c'est ce que l'agent relit juste
    après avoir résolu l'org cible. Il portait le même défaut (`org=`/`oto_context(
    org=…)`), non couvert par le test ci-dessus (texte différent)."""
    monkeypatch.setattr(org_store, "resolve_org_for_user", lambda sub, org: 167)
    monkeypatch.setattr(org_store, "get_org", lambda org_id: {"id": org_id, "name": "Acme"})

    ctx = ResolvedCtx(sub="u", org_id=None)
    out = _use_org(ctx, UseOrgInput(org="167"))

    assert "`_org=167`" in out["how_to"]
    assert "`org=167`" not in out["how_to"]
    assert "oto_context(_org=167)" in out["how_to"]
    assert "oto_context(org=167)" not in out["how_to"]
