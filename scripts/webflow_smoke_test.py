"""Smoke LIVE du connecteur Webflow — le tool layer réel + le client réel + le
vrai Site API token.

Même astuce que les tests unitaires (register sur un FastMCP nu, appel du
`fn` du tool), mais SANS mock : `resolve_api_key` est remplacé par le token lu
dans l'env, l'appel part vraiment chez Webflow. Pas de site_id à fournir — le
client oto-core le résout lui-même via `GET /sites` (nécessite le scope
`sites:read` sur le token, en plus de `cms:read`/`cms:write`).

Par défaut, LECTURE SEULE : site -> collections -> schéma d'une collection ->
items (page de 5) -> liste des webhooks existants -> liste des formulaires ->
soumissions du premier formulaire (page de 5) -> dry_run update/delete sur une
soumission RÉELLE (aucun appel d'écriture, juste la lecture qui nourrit le
diff). Rien n'écrit sur le site tant que WEBFLOW_SMOKE_WRITE=1 n'est pas posé
— dans ce cas :
- un item CMS est créé en DRAFT (jamais publié par ce script) sur
  WEBFLOW_TEST_COLLECTION_ID, prévisualisé en dry_run d'abord, puis réellement
  créé, puis SUPPRIMÉ ;
- un webhook JETABLE (URL non-routable https://example.invalid/...) est créé,
  vérifié via get, puis SUPPRIMÉ — les webhooks EXISTANTS du site (intégrations
  réelles n8n/Zapier/etc.) ne sont jamais touchés, seul l'id que create()
  vient de renvoyer est supprimé.
⚠️ **update/delete de soumission NE sont testés qu'en dry_run**, même avec
WRITE=1 : il n'existe AUCUNE API pour fabriquer une soumission jetable (seul
un vrai visiteur qui remplit le formulaire en crée une) — la mutation réelle
sur une soumission réelle (donnée de contact d'un vrai lead) n'est délibérément
jamais exercée par ce script, seulement par les tests unitaires mockés.
Rien n'est jamais laissé traîner.

Lancer :  set -a; . /chemin/vers/.env; set +a   # WEBFLOW_API_TOKEN
          [WEBFLOW_TEST_COLLECTION_ID=... [WEBFLOW_TEST_FORM_ID=...
          [WEBFLOW_SMOKE_WRITE=1]]]
          OTO_CONFIG_DISABLE_SOPS=1 .venv/bin/python -m scripts.webflow_smoke_test

Le token n'est JAMAIS imprimé.
"""
from __future__ import annotations

import asyncio
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
    cms_tool = _tool(m, "webflow_cms")
    publish_tool = _tool(m, "webflow_publish")
    webhooks_tool = _tool(m, "webflow_webhooks")
    forms_tool = _tool(m, "webflow_forms")
    submissions_tool = _tool(m, "webflow_submissions")

    with patch("oto_mcp.access.resolve_api_key", return_value=(token, False)):
        print("→ webflow_cms(op='site')")
        site = cms_tool(op="site")
        print(f"  ✓ site={site.get('displayName') or site.get('id')!r} "
              f"id={site.get('id')}")

        print("→ webflow_cms(op='collections')")
        collections = cms_tool(op="collections")["collections"]
        print(f"  ✓ {len(collections)} collection(s)")
        for c in collections[:10]:
            print(f"   - {c.get('displayName')!r} id={c.get('id')} "
                  f"slug={c.get('slug')}")

        collection_id = os.environ.get("WEBFLOW_TEST_COLLECTION_ID")
        fields: list = []
        if not collections:
            print("  (aucune collection sur ce site — passe direct aux webhooks)")
        else:
            if not collection_id:
                collection_id = collections[0]["id"]
                print(f"  (WEBFLOW_TEST_COLLECTION_ID non posé — utilise la "
                      f"première collection : {collection_id})")

            print(f"→ webflow_cms(op='collection', collection_id={collection_id!r})")
            schema = cms_tool(op="collection", collection_id=collection_id)
            fields = schema.get("fields", [])
            print(f"  ✓ {len(fields)} champ(s) custom : "
                  + ", ".join(f"{f.get('slug')}({f.get('type')}"
                             + (",required" if f.get("isRequired") else "") + ")"
                             for f in fields[:15]))

            print(f"→ webflow_cms(op='items', collection_id={collection_id!r}, "
                  "max_results=5)")
            page = cms_tool(op="items", collection_id=collection_id, max_results=5)
            items = page.get("items", [])
            total = page.get("pagination", {}).get("total")
            print(f"  ✓ {len(items)} item(s) rendu(s) sur {total} total")
            for it in items:
                fd = it.get("fieldData", {})
                print(f"   - id={it.get('id')} name={fd.get('name')!r} "
                      f"isDraft={it.get('isDraft')}")

            if items:
                sample_id = items[0]["id"]
                print(f"→ webflow_cms(op='item', id={sample_id!r})")
                one = cms_tool(op="item", collection_id=collection_id, id=sample_id)
                assert one["id"] == sample_id
                print("  ✓ get-by-id cohérent avec list")

                print(f"→ webflow_publish(id={sample_id!r}, dry_run=True)")
                preview = publish_tool(collection_id=collection_id, id=sample_id,
                                       dry_run=True)
                assert preview["dry_run"] is True
                print(f"  ✓ dry_run montre l'état courant : "
                      f"{preview['would_publish']}")

            if os.environ.get("WEBFLOW_SMOKE_WRITE") == "1":
                _cms_write_probe(cms_tool, collection_id, fields)

        print("→ webflow_webhooks(op='list')")
        before = webhooks_tool(op="list")["webhooks"]
        before_ids = {w["id"] for w in before}
        print(f"  ✓ {len(before)} webhook(s) existant(s) — laissés intacts")
        for w in before[:10]:
            print(f"   - {w.get('triggerType')} → {w.get('url')} id={w.get('id')}")

        if os.environ.get("WEBFLOW_SMOKE_WRITE") == "1":
            _webhooks_write_probe(webhooks_tool, before_ids)

        print("→ webflow_forms(op='list')")
        forms = forms_tool(op="list")["forms"]
        print(f"  ✓ {len(forms)} formulaire(s)")
        for f in forms[:10]:
            print(f"   - {f.get('displayName')!r} id={f.get('id')} "
                  f"page={f.get('pageName')}")

        if forms:
            sample_form_id = os.environ.get("WEBFLOW_TEST_FORM_ID") or forms[0]["id"]
            if os.environ.get("WEBFLOW_TEST_FORM_ID"):
                print(f"  (WEBFLOW_TEST_FORM_ID posé — utilise {sample_form_id})")
            print(f"→ webflow_forms(op='get', form_id={sample_form_id!r})")
            form_detail = forms_tool(op="get", form_id=sample_form_id)
            assert form_detail["id"] == sample_form_id
            print(f"  ✓ get cohérent avec list, {len(form_detail.get('fields', {}))} "
                  f"champ(s)")

            print(f"→ webflow_submissions(op='list', form_id={sample_form_id!r}, "
                  "max_results=5)")
            sub_page = submissions_tool(op="list", form_id=sample_form_id,
                                        max_results=5)
            submissions = sub_page.get("formSubmissions", [])
            sub_total = sub_page.get("pagination", {}).get("total")
            print(f"  ✓ {len(submissions)} soumission(s) rendue(s) sur {sub_total} total")

            if submissions:
                sample_sub_id = submissions[0]["id"]
                print(f"→ webflow_submissions(op='get', submission_id={sample_sub_id!r})")
                one_sub = submissions_tool(op="get", submission_id=sample_sub_id)
                assert one_sub["id"] == sample_sub_id
                print("  ✓ get-by-id cohérent avec list")

                print(f"→ webflow_submissions(op='update', dry_run=True) — "
                      "lecture seule, AUCUNE mutation sur une vraie soumission")
                update_preview = submissions_tool(
                    op="update", submission_id=sample_sub_id, dry_run=True,
                    form_submission_data={"oto_smoke_probe": "dry-run-only"})
                assert update_preview["dry_run"] is True
                print(f"  ✓ dry_run diff : {update_preview['changes']}")

                print(f"→ webflow_submissions(op='delete', dry_run=True) — "
                      "lecture seule, AUCUNE mutation sur une vraie soumission")
                delete_preview = submissions_tool(
                    op="delete", submission_id=sample_sub_id, dry_run=True)
                assert delete_preview["dry_run"] is True
                print(f"  ✓ dry_run would_delete montre le record réel "
                      f"(id={delete_preview['would_delete'].get('id')})")
            else:
                print("  (aucune soumission sur ce formulaire — pas de "
                      "get/dry_run à tester)")
        else:
            print("  (aucun formulaire sur ce site)")

    print("✓ smoke test OK")
    return 0


def _cms_write_probe(cms_tool, collection_id: str, fields: list) -> None:
    """Crée un item CMS DRAFT jetable, vérifie dry_run puis le vrai create, et
    SUPPRIME l'item — ne laisse jamais rien traîner sur le site."""
    import time

    slug = f"oto-smoke-{int(time.time())}"
    required = {f["slug"] for f in fields if f.get("isRequired")}
    field_data = {"name": f"Oto smoke {slug}", "slug": slug}
    for r in required:
        if r not in field_data:
            field_data[r] = "oto-smoke-test"

    print(f"→ webflow_cms(op='create', dry_run=True) fieldData={field_data}")
    preview = cms_tool(op="create", collection_id=collection_id, dry_run=True,
                       item={"fieldData": field_data})
    assert preview["dry_run"] is True
    print("  ✓ dry_run preview OK, aucun appel d'écriture")

    print("→ webflow_cms(op='create') — VRAI create (draft, jamais publié)")
    created = cms_tool(op="create", collection_id=collection_id,
                       item={"fieldData": field_data, "isDraft": True})
    item_id = created["id"]
    print(f"  ✓ créé id={item_id} isDraft={created.get('isDraft')}")
    assert created.get("isDraft") is not False, \
        "l'item créé par le smoke test ne doit JAMAIS être live"

    print(f"→ webflow_cms(op='delete', id={item_id!r}) — nettoyage")
    cms_tool(op="delete", collection_id=collection_id, id=item_id)
    print("  ✓ nettoyé")


def _webhooks_write_probe(webhooks_tool, before_ids: set) -> None:
    """Crée un webhook JETABLE (URL non-routable), vérifie dry_run puis le
    vrai create + get, et SUPPRIME uniquement CET id — jamais les webhooks
    déjà présents sur le site (intégrations réelles)."""
    url = "https://example.invalid/oto-smoke-webhook"

    print(f"→ webflow_webhooks(op='create', dry_run=True) url={url!r}")
    preview = webhooks_tool(op="create", trigger_type="collection_item_created",
                            url=url, dry_run=True)
    assert preview["dry_run"] is True
    print("  ✓ dry_run preview OK, aucun appel d'écriture")

    print("→ webflow_webhooks(op='create') — VRAI create (URL jetable)")
    created = webhooks_tool(op="create", trigger_type="collection_item_created",
                            url=url)
    wh_id = created["id"]
    assert wh_id not in before_ids, \
        "id de webhook créé en collision avec un webhook existant — arrêt"
    print(f"  ✓ créé id={wh_id} secretKey présent={('secretKey' in created)}")

    print(f"→ webflow_webhooks(op='get', webhook_id={wh_id!r})")
    fetched = webhooks_tool(op="get", webhook_id=wh_id)
    assert fetched["id"] == wh_id
    assert "secretKey" not in fetched, \
        "secretKey ne doit JAMAIS être rendu par get (create-only, confirmé live)"
    print("  ✓ get cohérent, secretKey absent (comme attendu, create-only)")

    print(f"→ webflow_webhooks(op='delete', webhook_id={wh_id!r}) — nettoyage")
    webhooks_tool(op="delete", webhook_id=wh_id)

    after = webhooks_tool(op="list")["webhooks"]
    after_ids = {w["id"] for w in after}
    assert after_ids == before_ids, \
        f"le set de webhooks a changé au-delà du nettoyage : {after_ids} != {before_ids}"
    print("  ✓ nettoyé — set de webhooks identique à avant le test")


if __name__ == "__main__":
    sys.exit(main())
