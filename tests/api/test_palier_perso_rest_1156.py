"""Le palier PERSONNEL atteignable depuis la face REST (#1156, exigence 7).

Du 09 au 10/09/2026, une copie personnelle a masqué la procédure de l'org pour tous les
travaux qui tournaient sous un compte : l'écran ne pouvait ni la voir ni la retirer, les
routes `/api/me/instructions*` ne servant que le palier de l'org. Elles prennent
désormais `scope=user` : lister, lire, supprimer, archiver et désarchiver SA copie
perso, sans agent. Sans `scope`, rien ne change.

Rejoué sur la table de routes réelle (`make_routes`), l'adaptateur de capacités et un
vrai PostgreSQL : ce sont des clés de propriété et des paliers, qu'aucun double ne
prouve. Le porteur est identifié par un vérifieur factice dont le bearer EST le sub.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

_ORG = "# Relance\n\nÉtapes de l'org.\n"
_PERSO = "# Relance\n\nÉtapes perso.\n"
_SLUG = "relance"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@palier-perso.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h(sub: str) -> dict:
    return {"Authorization": f"Bearer {sub}"}


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE et le vrai `init_db()` (même recette que le front tiers)."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_palier_perso_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield init_db
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture(scope="module")
def client(live):
    from oto_mcp.api import routes as api_routes
    return TestClient(Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None)))


@pytest.fixture(scope="module")
def org(live):
    """Une org, son admin, deux simples membres, et un compte sans org active."""
    from oto_mcp import db, org_store
    admin, membre, autre, sans_org = ("usr_pp_admin", "usr_pp_membre", "usr_pp_autre",
                                      "usr_pp_sans_org")
    for sub in (admin, membre, autre, sans_org):
        db.upsert_user(sub, email=f"{sub}@palier-perso.invalid", name=sub)
    oid = org_store.create_org("Org du palier perso", created_by=admin)
    org_store.add_org_member(oid, admin, "org_admin")
    for sub in (membre, autre):
        org_store.add_org_member(oid, sub, "org_member")
    for sub in (admin, membre, autre):
        org_store.set_active_org(sub, oid)
    return {"id": oid, "admin": admin, "membre": membre, "autre": autre,
            "sans_org": sans_org}


@pytest.fixture
def masque(org):
    """Le cas de l'incident : la procédure de l'org, et la copie perso du même slug d'un
    simple membre, qui la masque pour ses lectures par nom. Nettoie derrière lui."""
    from oto_mcp import org_store
    org_store.set_instruction("org", org["id"], _SLUG, _ORG, title="Relance (org)",
                              set_by=org["admin"])
    org_store.set_instruction("user", org["membre"], _SLUG, _PERSO,
                              title="Relance (perso)", set_by=org["membre"])
    yield {"org": org_store.get_instruction("org", org["id"], _SLUG),
           "perso": org_store.get_instruction("user", org["membre"], _SLUG)}
    for owner in (("org", org["id"]), ("user", org["membre"]), ("user", org["autre"]),
                  ("user", org["sans_org"])):
        org_store.delete_instruction(*owner, _SLUG)


# ── Sans `scope`, rien ne change ─────────────────────────────────────────────

def test_sans_scope_les_routes_servent_lorg_comme_avant(client, org, masque):
    membre = org["membre"]
    liste = client.get("/api/me/instructions", headers=_h(membre))
    assert liste.status_code == 200, liste.text
    corps = liste.json()
    assert "scope" not in corps
    assert [i["slug"] for i in corps["instructions"]] == [_SLUG]
    assert corps["instructions"][0]["id"] == masque["org"]["id"]
    assert "other_scopes" not in corps["instructions"][0]       # la liste d'org ne calcule rien
    assert corps["can_delete_instructions"] is False             # simple membre

    lu = client.get(f"/api/me/instructions/{_SLUG}", headers=_h(membre))
    assert lu.status_code == 200, lu.text
    lu = lu.json()
    assert "scope" not in lu and "Étapes de l'org" in lu["body_md"]
    assert lu["id"] == masque["org"]["id"]
    assert "SHADOWS" in lu["shadow_warning"]                       # #1156 ex. 4, inchangé

    # Supprimer, archiver : toujours la garde org_admin sans `scope`.
    for verbe, chemin in (("DELETE", f"/api/me/instructions/{_SLUG}"),
                          ("POST", f"/api/me/instructions/{_SLUG}/archive")):
        r = client.request(verbe, chemin, headers=_h(membre))
        assert r.status_code == 403, (verbe, r.text)
    from oto_mcp import org_store
    assert org_store.get_instruction("user", membre, _SLUG)["archived_at"] is None
    assert org_store.get_instruction("org", org["id"], _SLUG)["archived_at"] is None


# ── Lister et lire son palier perso ──────────────────────────────────────────

def test_lister_son_palier_perso_dit_quelle_copie_masque_lorg(client, org, masque):
    r = client.get("/api/me/instructions", params={"scope": "user"},
                   headers=_h(org["membre"]))
    assert r.status_code == 200, r.text
    corps = r.json()
    assert corps["scope"] == "user" and corps["org_id"] == org["id"]
    [perso] = corps["instructions"]
    assert perso["slug"] == _SLUG and perso["id"] == masque["perso"]["id"]
    [lorg] = perso["other_scopes"]
    assert lorg["scope"] == "org" and lorg["guide_id"] == masque["org"]["id"]
    assert "You are reading your PERSONAL copy" in perso["shadow_warning"]
    assert corps["archived"] == []
    # Ce que CETTE face permet sur son palier : supprimer oui, écrire non (pas de
    # `scope` sur `PUT …/{slug}`) — même pour un simple membre.
    assert corps["can_delete_instructions"] is True
    assert corps["can_write_instructions"] is False


def test_lire_sa_copie_perso_nomme_lorg_ecartee(client, org, masque):
    r = client.get(f"/api/me/instructions/{_SLUG}", params={"scope": "user"},
                   headers=_h(org["membre"]))
    assert r.status_code == 200, r.text
    lu = r.json()
    assert lu["scope"] == "user" and "Étapes perso" in lu["body_md"]
    assert lu["id"] == masque["perso"]["id"]
    assert lu["other_scopes"][0]["guide_id"] == masque["org"]["id"]
    assert "You are reading your PERSONAL copy" in lu["shadow_warning"]
    assert "SKIPPED" in lu["shadow_warning"]


def test_le_palier_perso_ne_depend_pas_de_lorg_active(client, org, masque):
    """Un compte dont l'org active n'est pas celle du masque (ici son org perso) liste
    et lit quand même sa copie : elle suit la personne, pas l'org."""
    from oto_mcp import org_store
    org_store.set_instruction("user", org["sans_org"], _SLUG, _PERSO, set_by=org["sans_org"])
    r = client.get("/api/me/instructions", params={"scope": "user"},
                   headers=_h(org["sans_org"]))
    assert r.status_code == 200, r.text
    corps = r.json()
    assert corps["org_id"] != org["id"]
    assert [i["slug"] for i in corps["instructions"]] == [_SLUG]
    assert "other_scopes" not in corps["instructions"][0]          # rien à masquer là
    lu = client.get(f"/api/me/instructions/{_SLUG}", params={"scope": "user"},
                    headers=_h(org["sans_org"]))
    assert lu.status_code == 200, lu.text
    assert "Étapes perso" in lu.json()["body_md"]


def test_sans_org_active_le_palier_perso_reste_servi(org, masque):
    """Sans AUCUNE org active (compte sans ligne d'org), la liste d'org est un bundle
    vide ; la liste perso, elle, sert les procédures de la personne."""
    import asyncio

    from oto_mcp import org_store
    from oto_mcp.capabilities._types import RawCtx
    from oto_mcp.capabilities.registry import CAPABILITIES
    sub = "usr_pp_fantome"
    org_store.set_instruction("user", sub, _SLUG, _PERSO, set_by=sub)
    try:
        cap = next(c for c in CAPABILITIES if c.key == "org.instruction.list")
        inp = cap.Input(scope="user")
        out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)
        out = asyncio.run(out) if asyncio.iscoroutine(out) else out
        assert out["org_id"] is None and out["can_edit"] is False
        assert out["scope"] == "user"
        assert [i["slug"] for i in out["instructions"]] == [_SLUG]
        assert out["can_delete_instructions"] is True
    finally:
        org_store.delete_instruction("user", sub, _SLUG)


# ── Archiver, désarchiver, supprimer sa copie perso ──────────────────────────

def test_archiver_sa_copie_perso_leve_le_masque_et_le_dit(client, org, masque):
    membre = org["membre"]
    r = client.post(f"/api/me/instructions/{_SLUG}/archive", params={"scope": "user"},
                    headers=_h(membre))
    assert r.status_code == 200, r.text
    ack = r.json()
    assert ack["scope"] == "user" and ack["user_id"] == membre
    assert ack["archived"] is True and ack["already_archived"] is False
    assert f"`guide_id` {masque['org']['id']}" in ack["shadow_lifted"]
    # La lecture d'org ne se dit plus masquée ; la liste perso range la copie à part.
    lu = client.get(f"/api/me/instructions/{_SLUG}", headers=_h(membre)).json()
    assert "shadow_warning" not in lu
    liste = client.get("/api/me/instructions", params={"scope": "user"},
                       headers=_h(membre)).json()
    assert liste["instructions"] == []
    [retiree] = liste["archived"]
    assert retiree["slug"] == _SLUG and retiree["archived_at"]
    assert "shadow_warning" not in retiree                          # archivée : ne masque rien

    # Ré-archiver ne lève plus rien : la copie ne masquait déjà plus.
    again = client.post(f"/api/me/instructions/{_SLUG}/archive", params={"scope": "user"},
                        headers=_h(membre)).json()
    assert again["already_archived"] is True and "shadow_lifted" not in again

    # La remettre en service la remet en tête de la cascade : la réponse le dit.
    r = client.post(f"/api/me/instructions/{_SLUG}/unarchive", params={"scope": "user"},
                    headers=_h(membre))
    assert r.status_code == 200, r.text
    ack = r.json()
    assert ack["unarchived"] is True and ack["user_id"] == membre
    assert "SHADOWS" in ack["shadow_warning"]
    from oto_mcp import org_store
    assert org_store.get_instruction("org", org["id"], _SLUG)["archived_at"] is None


def test_supprimer_sa_copie_perso_leve_le_masque_et_laisse_lorg(client, org, masque):
    membre = org["membre"]
    r = client.delete(f"/api/me/instructions/{_SLUG}", params={"scope": "user"},
                      headers=_h(membre))
    assert r.status_code == 200, r.text
    ack = r.json()
    assert ack["deleted"] is True and ack["scope"] == "user" and ack["user_id"] == membre
    assert [a["scope"] for a in ack["other_scopes"]] == ["org"]
    assert "was shadowing the ORG procedure" in ack["shadow_lifted"]
    assert f"`guide_id` {masque['org']['id']}" in ack["shadow_lifted"]
    # La copie perso n'est plus là, celle de l'org est intacte et ne se dit plus masquée.
    assert client.get(f"/api/me/instructions/{_SLUG}", params={"scope": "user"},
                      headers=_h(membre)).status_code == 404
    lu = client.get(f"/api/me/instructions/{_SLUG}", headers=_h(membre)).json()
    assert lu["id"] == masque["org"]["id"]
    assert "other_scopes" not in lu and "shadow_warning" not in lu


# ── La copie perso d'un AUTRE compte reste hors d'atteinte ───────────────────

def test_la_copie_perso_dun_autre_compte_est_hors_datteinte(client, org, masque):
    autre, membre = org["autre"], org["membre"]
    # Il ne la voit pas dans sa liste…
    liste = client.get("/api/me/instructions", params={"scope": "user"},
                       headers=_h(autre)).json()
    assert liste["instructions"] == [] and liste["archived"] == []
    # … ni ne la lit, ne l'archive, ne la désarchive ou ne la supprime : 404, comme une
    # procédure qui n'existe pas (son palier est le sien, jamais celui d'un autre).
    for verbe, suffixe in (("GET", ""), ("POST", "/archive"), ("POST", "/unarchive"),
                           ("DELETE", "")):
        r = client.request(verbe, f"/api/me/instructions/{_SLUG}{suffixe}",
                           params={"scope": "user"}, headers=_h(autre))
        assert r.status_code == 404, (verbe, suffixe, r.text)
        assert r.json()["error"] == "not_found"
    # Le désigner par un champ client est refusé, jamais suivi.
    r = client.delete(f"/api/me/instructions/{_SLUG}",
                      params={"scope": "user", "user_id": membre}, headers=_h(autre))
    assert r.status_code == 400 and r.json()["error"] == "unknown_fields", r.text
    # Le palier équipe n'est pas une valeur de cette face.
    r = client.get(f"/api/me/instructions/{_SLUG}", params={"scope": "group"},
                   headers=_h(autre))
    assert r.status_code == 400, r.text
    from oto_mcp import org_store
    restee = org_store.get_instruction("user", membre, _SLUG)
    assert restee and restee["archived_at"] is None and restee["version"] == 1
