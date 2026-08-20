"""Smoke LIVE du connecteur Webflow — le tool layer réel + le client réel + le
vrai Site API token.

Même astuce que les tests unitaires (register sur un FastMCP nu, appel du
`fn` du tool), mais SANS mock : `resolve_api_key` est remplacé par le token lu
dans l'env, l'appel part vraiment chez Webflow. Pas de site_id à fournir — le
client oto-core le résout lui-même via `GET /sites` (nécessite le scope
`sites:read` sur le token, en plus de `cms:read`/`cms:write`).

Par défaut, LECTURE SEULE : site -> collections -> schéma d'une collection ->
items (page de 5). Rien n'écrit sur le site tant que WEBFLOW_SMOKE_WRITE=1
n'est pas posé — dans ce cas un item est créé en DRAFT (jamais publié par ce
script) sur WEBFLOW_TEST_COLLECTION_ID, prévisualisé en dry_run d'abord, puis
réellement créé, puis SUPPRIMÉ (nettoyage) — jamais laissé traîner.

Lancer :  set -a; . /chemin/vers/.env; set +a   # WEBFLOW_API_TOKEN
          [WEBFLOW_TEST_COLLECTION_ID=... [WEBFLOW_SMOKE_WRITE=1]]
          OTO_CONFIG_DISABLE_SOPS=1 .venv/bin/python -m scripts.webflow_smoke_test

Le token n'est JAMAIS imprimé.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import patch

from fastmcp import FastMCP


def _tool(m, name):
    return asyncio.run(m.get_tool(name)).fn


def main() -> int:
    token = os.environ.get("WEBFLOW_API_TOKEN")
    if not token:
        print("✗ WEBFLOW_API_TOKEN absent de l'env (source le .env d'abord)")
        return 2

    from oto_mcp.tools import webflow

    m = FastMCP("smoke-webflow")
    webflow.register(m)
    site_tool = _tool(m, "webflow_site")
    collections_tool = _tool(m, "webflow_collections")
    items_tool = _tool(m, "webflow_items")
    publish_tool = _tool(m, "webflow_publish")

    with patch("oto_mcp.access.resolve_api_key", return_value=(token, False)):
        print("→ webflow_site()")
        site = site_tool()
        print(f"  ✓ site={site.get('displayName') or site.get('id')!r} "
              f"id={site.get('id')}")

        print("→ webflow_collections(op='list')")
        collections = collections_tool(op="list")["collections"]
        print(f"  ✓ {len(collections)} collection(s)")
        for c in collections[:10]:
            print(f"   - {c.get('displayName')!r} id={c.get('id')} "
                  f"slug={c.get('slug')}")

        collection_id = os.environ.get("WEBFLOW_TEST_COLLECTION_ID")
        if not collection_id:
            if not collections:
                print("  (aucune collection sur ce site — arrêt ici)")
                return 0
            collection_id = collections[0]["id"]
            print(f"  (WEBFLOW_TEST_COLLECTION_ID non posé — utilise la "
                  f"première collection : {collection_id})")

        print(f"→ webflow_collections(op='get', collection_id={collection_id!r})")
        schema = collections_tool(op="get", collection_id=collection_id)
        fields = schema.get("fields", [])
        print(f"  ✓ {len(fields)} champ(s) custom : "
              + ", ".join(f"{f.get('slug')}({f.get('type')}"
                         + (",required" if f.get("isRequired") else "") + ")"
                         for f in fields[:15]))

        print(f"→ webflow_items(op='list', collection_id={collection_id!r}, "
              "max_results=5)")
        page = items_tool(op="list", collection_id=collection_id, max_results=5)
        items = page.get("items", [])
        total = page.get("pagination", {}).get("total")
        print(f"  ✓ {len(items)} item(s) rendu(s) sur {total} total")
        for it in items:
            fd = it.get("fieldData", {})
            print(f"   - id={it.get('id')} name={fd.get('name')!r} "
                  f"isDraft={it.get('isDraft')}")

        if items:
            sample_id = items[0]["id"]
            print(f"→ webflow_items(op='get', id={sample_id!r})")
            one = items_tool(op="get", collection_id=collection_id, id=sample_id)
            assert one["id"] == sample_id
            print("  ✓ get-by-id cohérent avec list")

            print(f"→ webflow_publish(id={sample_id!r}, dry_run=True)")
            preview = publish_tool(collection_id=collection_id, id=sample_id,
                                   dry_run=True)
            assert preview["dry_run"] is True
            print(f"  ✓ dry_run montre l'état courant : "
                  f"{preview['would_publish']}")

        if os.environ.get("WEBFLOW_SMOKE_WRITE") == "1":
            _write_probe(items_tool, collection_id, fields)

    print("✓ smoke test OK")
    return 0


def _write_probe(items_tool, collection_id: str, fields: list) -> None:
    """Crée un item DRAFT jetable, vérifie dry_run puis le vrai create, et
    SUPPRIME l'item — ne laisse jamais rien traîner sur le site."""
    import time

    slug = f"oto-smoke-{int(time.time())}"
    required = {f["slug"] for f in fields if f.get("isRequired")}
    field_data = {"name": f"Oto smoke {slug}", "slug": slug}
    for r in required:
        if r not in field_data:
            field_data[r] = "oto-smoke-test"

    print(f"→ webflow_items(op='create', dry_run=True) fieldData={field_data}")
    preview = items_tool(op="create", collection_id=collection_id, dry_run=True,
                         item={"fieldData": field_data})
    assert preview["dry_run"] is True
    print("  ✓ dry_run preview OK, aucun appel d'écriture")

    print("→ webflow_items(op='create') — VRAI create (draft, jamais publié)")
    created = items_tool(op="create", collection_id=collection_id,
                         item={"fieldData": field_data, "isDraft": True})
    item_id = created["id"]
    print(f"  ✓ créé id={item_id} isDraft={created.get('isDraft')}")
    assert created.get("isDraft") is not False, \
        "l'item créé par le smoke test ne doit JAMAIS être live"

    print(f"→ webflow_items(op='delete', id={item_id!r}) — nettoyage")
    items_tool(op="delete", collection_id=collection_id, id=item_id)
    print("  ✓ nettoyé")


if __name__ == "__main__":
    sys.exit(main())
