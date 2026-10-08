"""Le coffre des comptes Google, en mémoire, sous la VRAIE résolution commune
(`access.resolve_credential`) — pour les bancs de `auth/google.credentials_for`
(oto-backend#1160).

Le choix du compte n'est plus propre à Google : il passe par la cascade des clés
(membre > équipe active > org), la sélection multi-compte (`account`/`_account=` >
épinglage projet > compte unique > défaut > refus) et ses gardes. Un banc qui moquait
`db.get_google_oauth` ne verrait plus rien de ce chemin : ce faux remplace le coffre
(les fonctions de `credentials_store`, `db`, `group_store`, `org_store` que la
résolution lit) et laisse tout le reste réel.
"""
from __future__ import annotations

import types


class Coffre:
    """Une ligne par (entité, compte), le secret à part du meta — mêmes signatures que
    `credentials_store` pour ce que la résolution et le renouvellement lisent."""

    def __init__(self, org: int, sub: str):
        self.membre = ("member", f"{org}:{sub}")
        self.lignes: dict[tuple, dict] = {}

    def poser(self, account, secret, *, entite=None, defaut=False, scopes=None,
              access_token="AT", expires_at="2999-01-01T00:00:00+00:00", client_id=None):
        from oto_mcp.auth import google as G
        tous = " ".join(sc for svc in G.SERVICE_SCOPES for sc in G.SERVICE_SCOPES[svc])
        self.lignes[(*(entite or self.membre), account)] = {
            "secret": secret, "set_by": "test", "set_at": "2026-10-08T00:00:00Z",
            "meta": {"is_default": defaut, "scopes": tous if scopes is None else scopes,
                     "access_token": access_token, "expires_at": expires_at,
                     "client_id": client_id}}

    def meta(self, account, entite=None):
        return self.lignes[(*(entite or self.membre), account)]["meta"]

    def get_with_meta(self, entity_type, entity_id, connector, account=""):
        assert connector == "google", connector
        ligne = self.lignes.get((entity_type, entity_id, account))
        return {**ligne, "meta": dict(ligne["meta"])} if ligne else None

    def get(self, entity_type, entity_id, connector, account=""):
        ligne = self.get_with_meta(entity_type, entity_id, connector, account)
        return ligne["secret"] if ligne else None

    def list_accounts(self, entity_type, entity_id, connector):
        assert connector == "google", connector
        return [{"account": a, "meta": dict(l["meta"]), "set_at": l["set_at"]}
                for (et, eid, a), l in sorted(self.lignes.items())
                if (et, eid) == (entity_type, entity_id)]

    def update_meta(self, entity_type, entity_id, connector, account, patch, conn=None):
        ligne = self.lignes.get((entity_type, entity_id, account))
        if ligne is None:
            return False
        ligne["meta"].update(patch)
        return True


def installer(monkeypatch, *, org: int, sub: str, group=None):
    """Pose le faux coffre et le contexte (org, équipe active, aucun épinglage).
    Rend `coffre`, `epingles` (connecteur → compte épinglé par le projet) et
    `sous_compte(axe, fn, *a, **k)` — un appel sous `_account=axe`, comme le pose le
    middleware."""
    from oto_mcp import access, credentials_store, db, group_store, org_store, session_org
    from oto_mcp.connectors import cardinality

    monkeypatch.setenv("GOOGLE_WORKSPACE_CLIENT_ID", "cid-env")
    monkeypatch.setenv("GOOGLE_WORKSPACE_CLIENT_SECRET", "secret-env")
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "state-secret-test")
    # La mesure à côté de la résolution (L7) lit la base : hors sujet ici.
    monkeypatch.setenv("OTO_L7_SHADOW", "0")
    coffre = Coffre(org, sub)
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", coffre.get_with_meta)
    monkeypatch.setattr(credentials_store, "get_credential", coffre.get)
    monkeypatch.setattr(credentials_store, "list_accounts", coffre.list_accounts)
    monkeypatch.setattr(credentials_store, "update_meta", coffre.update_meta)
    monkeypatch.setattr(credentials_store, "get_editor_app", lambda c, k: None)
    monkeypatch.setattr(db, "get_member_api_key",
                        lambda s, o, prov, account="": coffre.get(
                            "member", f"{o}:{s}", prov, account))
    monkeypatch.setattr(group_store, "get_group_secret",
                        lambda gid, prov, account="": coffre.get("group", str(gid), prov, account))
    monkeypatch.setattr(org_store, "get_org_secret",
                        lambda oid, prov, account="": coffre.get("org", str(oid), prov, account))
    monkeypatch.setattr(db, "member_instance_suspended", lambda *a, **k: False)
    monkeypatch.setattr(db, "insert_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(access, "current_org", lambda s: org)
    monkeypatch.setattr(access, "current_group", lambda s: group)
    epingles: dict[str, str] = {}
    monkeypatch.setattr(access, "project_pinned_identity",
                        lambda connector, project_id=None: epingles.get(connector))
    # La cardinalité vient du REGISTRE (aucune surcharge en base dans ces bancs).
    monkeypatch.setattr(cardinality, "_OVERRIDES", {})
    monkeypatch.setattr(cardinality, "_LOADED", True)

    def sous_compte(axe, fn, *a, **k):
        jeton = session_org.set_call_account(axe)
        try:
            return fn(*a, **k)
        finally:
            session_org.reset_call_account(jeton)

    return types.SimpleNamespace(coffre=coffre, epingles=epingles, sous_compte=sous_compte)
