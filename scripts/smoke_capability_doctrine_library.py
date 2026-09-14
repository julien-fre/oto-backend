"""Smoke E2E de la bibliothèque publique de guides (capacités library.*) sur PG jetable.

publish (super_admin plateforme) → list/get (membre) → fork (autre org) → unpublish
(auteur). Couvre aussi les refus : un org_admin ne publie pas (403
`publication_reservee_a_la_plateforme`, la bibliothèque est une vitrine éditée par la
plateforme), la plateforme ne reprend pas le nom d'une entrée d'org (409), public_get
ignore 'unlisted'.

Une entrée signée par une ORG ne naît plus d'une publication : elle s'écrit ici par le
store (`org_store.publish_guide(author_kind='org')`), et c'est elle qui porte les
contrats de propriété — nom gardé, fork, dépublication par son org_admin.

Lancer :  DATABASE_URL=postgresql://poc:poc@localhost:5471/poc \
          OTO_MCP_ADMIN_SUB=padmin OTO_CONFIG_DISABLE_SOPS=1 \
          .venv/bin/python -m scripts.smoke_capability_doctrine_library
"""
from __future__ import annotations

import os

from oto_mcp import db, org_store
from oto_mcp.capabilities import registry
from oto_mcp.capabilities._types import AuthzDenied, RawCtx

PLATEFORME = "padmin"   # super_admin par `OTO_MCP_ADMIN_SUB`, cf. l'en-tête


def run(key: str, actor: str, **fields):
    cap = next(c for c in registry.CAPABILITIES if c.key == key)
    inp = cap.Input(**fields)
    ctx = cap.authz(RawCtx(sub=actor), inp)
    return cap.handler(ctx, inp)


def denied(fn, status, code=None):
    try:
        fn()
        raise SystemExit(f"  ✗ aurait dû lever AuthzDenied({status})")
    except AuthzDenied as e:
        assert e.status == status, f"attendu {status}, reçu {e.status} ({e.code})"
        assert code is None or e.code == code, f"attendu {code}, reçu {e.code}"


def main() -> None:
    db.init_db()
    with db._connect() as c:
        c.execute("TRUNCATE doctrine_library RESTART IDENTITY")
        c.execute("TRUNCATE org_members, org_instructions, orgs RESTART IDENTITY CASCADE")
        for s in ("alice", "bob", PLATEFORME):
            c.execute("INSERT INTO users(sub) VALUES (%s) ON CONFLICT DO NOTHING", (s,))

    # alice = org_admin d'Org A ; bob = org_admin d'Org B ; padmin = super_admin, dont
    # l'org active (Org P) porte les procédures que la plateforme publie.
    oa = org_store.create_org("Org A", created_by="alice")
    org_store.add_org_member(oa, "alice", "org_admin")
    org_store.set_active_org("alice", oa)
    ob = org_store.create_org("Org B", created_by="bob")
    org_store.add_org_member(ob, "bob", "org_admin")
    org_store.set_active_org("bob", ob)
    op = org_store.create_org("Org P", created_by=PLATEFORME)
    org_store.add_org_member(op, PLATEFORME, "org_admin")
    org_store.set_active_org(PLATEFORME, op)
    org_store.set_instruction("org", oa, "outreach", "# Outreach\nLe playbook.",
                              title="Outreach", description="Comment prospecter", set_by="alice")
    org_store.set_instruction("org", op, "socle-prospection", "# Socle\nLa base.",
                              title="Socle", set_by=PLATEFORME)
    org_store.set_instruction("org", op, "outreach", "# Outreach (plateforme)\nAutre chose.",
                              title="Le nôtre", set_by=PLATEFORME)

    print("→ alice (org_admin, porte le skill) publie 'outreach' → 403 réservé à la plateforme")
    denied(lambda: run("library.publish", "alice", slug="outreach"),
           403, "publication_reservee_a_la_plateforme")
    assert org_store.get_library_entry(slug="outreach", include_unlisted=True) is None
    print("  ✓ rien n'est publié")

    print("→ bob (org_admin, sans le skill) publie 'outreach' → 403, avant toute lecture")
    denied(lambda: run("library.publish", "bob", slug="outreach"),
           403, "publication_reservee_a_la_plateforme")
    print("  ✓")

    print("→ padmin (super_admin) publie 'socle-prospection' depuis son org active")
    res = run("library.publish", PLATEFORME, slug="socle-prospection",
              category="Prospection", tags=["sales"], visibility="public")
    assert res["published"] and res["version"] == 1, res
    socle = org_store.get_library_entry(slug="socle-prospection", include_unlisted=True)
    assert (socle["author_kind"], socle["author_org_id"]) == ("otomata", None), socle
    print(f"  ✓ entrée #{res['id']} (slug={res['slug']}, author=otomata)")

    print("→ padmin republie LA SIENNE → v2")
    assert run("library.publish", PLATEFORME, slug="socle-prospection")["version"] == 2
    print("  ✓")

    print("→ entrée signée par Org A, écrite par le store (plus aucune org ne publie)")
    held = org_store.publish_guide(
        slug="outreach", title="Outreach", description="Comment prospecter",
        body_md="# Outreach\nLe playbook.", author_kind="org", author_org_id=oa,
        author_display="Org A", category="Prospection", tags=["sales"],
        visibility="public", source_org_id=oa, source_slug="outreach",
        published_by="alice")
    eid = held["id"]
    print(f"  ✓ entrée #{eid} (author=org)")

    print("→ list (membre) voit l'entrée d'org publique")
    items = run("library.list", "bob", query="outreach")["guides"]
    assert any(i["slug"] == "outreach" and i["author_kind"] == "org" for i in items), items
    print("  ✓ author_kind=org, author_display porté")

    print("→ get (membre) renvoie le body complet")
    got = run("library.get", "bob", slug="outreach")
    assert got["body_md"].startswith("# Outreach"), got
    print("  ✓")

    print("→ padmin publie SON skill sous le nom tenu par Org A → 409 slug_taken (#292)")
    denied(lambda: run("library.publish", PLATEFORME, slug="outreach"), 409, "slug_taken")
    held = org_store.get_library_entry(slug="outreach", include_unlisted=True)
    assert held["author_org_id"] == oa and held["version"] == 1, held
    assert held["body_md"].startswith("# Outreach\n"), held["body_md"][:40]
    print("  ✓ l'entrée d'Org A est intacte : corps, auteur, version")

    print("→ bob forke dans Org B → skill versionné v1")
    fk = run("library.fork", "bob", slug="outreach")
    assert fk["forked"] and fk["org_id"] == ob and fk["version"] == 1
    forked = org_store.get_instruction("org", ob, fk["slug"])
    assert forked and forked["body_md"].startswith("# Outreach")
    print(f"  ✓ Org B a maintenant le skill '{fk['slug']}'")

    print("→ fork une 2e fois → slug dédupliqué (-2)")
    fk2 = run("library.fork", "bob", slug="outreach")
    assert fk2["slug"] != fk["slug"], fk2
    print(f"  ✓ {fk2['slug']}")

    print("→ bob (pas l'auteur) tente unpublish → 403")
    denied(lambda: run("library.unpublish", "bob", id=eid), 403, "forbidden")
    print("  ✓")

    print("→ alice (org_admin de l'org autrice) unpublish")
    assert run("library.unpublish", "alice", id=eid)["unpublished"] is True
    assert org_store.get_library_entry(entry_id=eid, include_unlisted=True) is None
    print("  ✓")

    print("→ publication 'unlisted' : invisible de la liste publique anonyme")
    org_store.publish_guide(slug="secret-skill", body_md="# Secret", author_kind="otomata",
                               author_display="Otomata", visibility="unlisted")
    assert [i["slug"] for i in org_store.list_library(include_unlisted=False)] \
        == ["socle-prospection"]
    assert org_store.get_library_entry(slug="secret-skill", include_unlisted=False) is None
    assert org_store.get_library_entry(slug="secret-skill", include_unlisted=True) is not None
    print("  ✓ deny-by-default sur la surface anonyme")

    print("→ get authentifié (bob, non-auteur) sur l'unlisted par slug exact → OK (lien non listé)")
    got_unlisted = run("library.get", "bob", slug="secret-skill")
    assert got_unlisted["body_md"].startswith("# Secret"), got_unlisted
    print("  ✓ unlisted = partage par lien (servi par slug à tout authentifié, hors catalogue)")

    print("→ get sur un slug inexistant → 404")
    denied(lambda: run("library.get", "bob", slug="nope-nope"), 404, "unknown_entry")
    print("  ✓")

    print("\n✓ Bibliothèque publique de guides validée.")


if __name__ == "__main__":
    if os.environ.get("OTO_MCP_ADMIN_SUB") != PLATEFORME:
        raise SystemExit(f"OTO_MCP_ADMIN_SUB={PLATEFORME} requis : seul un super_admin publie.")
    try:
        main()
    finally:
        db._get_pool().close()
