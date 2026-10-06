"""Désactiver un tenant, contre un VRAI PostgreSQL (oto-backend#1165).

Le scénario de l'issue, rejoué sur le SQL servi : un compte du tenant avec une session
ouverte (JWT), un jeton d'API, un jeton de délégation, et un ancien identifiant de NOTRE
annuaire redirigé vers lui. Le geste les coupe tous, d'un coup ; un compte d'un autre
tenant n'est pas touché ; `enable` rouvre les connexions sans faire revivre un jeton.

Chaque refus a son contrefactuel : le même chemin, pour le tenant voisin, passe.
"""
from __future__ import annotations

import asyncio
import json
import types

import psycopg
import pytest
from psycopg.rows import dict_row


@pytest.fixture()
def base(pg_module_dsn, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_module_dsn)
    from oto_mcp.db import _conn
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_module_dsn)
    _conn._pool = None
    from oto_mcp import db
    db.init_db()
    with psycopg.connect(pg_module_dsn, row_factory=dict_row, autocommit=True) as c:
        for t in ("user_api_tokens", "sub_aliases", "users"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM tenants WHERE slug <> 'oto'")
        c.execute("INSERT INTO tenants (slug, name, issuer) VALUES "
                  "('acme', 'Acme', 'https://auth.acme.test/oidc'), "
                  "('acme-x', 'Acme X', 'https://auth.acme-x.test/oidc'), "
                  "('beta', 'Beta', 'https://auth.beta.test/oidc')")
        for sub in ("acme:carla", "acme:dan", "acme-x:eve", "beta:fred", "nu-gil"):
            c.execute("INSERT INTO users (sub, email) VALUES (%s, %s)",
                      (sub, f"{sub.replace(':', '.')}@ex.test"))
        # L'ancien identifiant (nu, de NOTRE annuaire) que la bascule a redirigé.
        c.execute("INSERT INTO sub_aliases (old_sub, new_sub) VALUES "
                  "('u-ancien-carla', 'acme:carla'), ('u-ancien-fred', 'beta:fred')")
    yield db
    _conn._pool = None


def _jetons(db):
    return {
        "carla_api": db.create_api_token("acme:carla", "cli"),
        "carla_deleg": db.create_api_token("acme:carla", "job", ttl_seconds=600,
                                           kind="delegation"),
        "dan_api": db.create_api_token("acme:dan", "ci"),
        "dan_expire": db.create_api_token("acme:dan", "vieux", ttl_seconds=1),
        "fred_api": db.create_api_token("beta:fred", "cli"),
        "eve_api": db.create_api_token("acme-x:eve", "cli"),
    }


def _req(token: str):
    from starlette.requests import Request
    return Request({
        "type": "http", "method": "GET", "path": "/api/admin/users", "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80), "http_version": "1.1",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    })


class _Verifier:
    def __init__(self, sub):
        self.sub = sub

    async def verify_token(self, token):
        return types.SimpleNamespace(claims={"sub": self.sub})


def _rest(token, sub_du_jwt=None):
    from oto_mcp.api import routes as api_routes
    from oto_mcp.auth import token_scopes
    try:
        return asyncio.run(api_routes._authenticate(_req(token),
                                                    verifier=_Verifier(sub_du_jwt)))
    finally:
        token_scopes.set_current(None)


def _code(resultat):
    sub, err = resultat
    if err is None:
        return sub
    return err.status_code, json.loads(bytes(err.body).decode())["error"]


def test_desactiver_coupe_tout_ce_qui_est_emis_et_rien_chez_le_voisin(base, monkeypatch):
    import time
    db = base
    j = _jetons(db)
    time.sleep(1.2)  # le jeton `dan_expire` est mort avant le geste : il n'est pas compté
    from oto_mcp.api import base as api_base
    monkeypatch.setattr(api_base, "alias_drain_armed", lambda: True)

    # AVANT : tout passe — session JWT du tenant, ancien identifiant redirigé, jetons.
    assert _rest("eyJ.a.b", "acme:carla") == ("acme:carla", None)
    assert _rest("eyJ.a.b", "u-ancien-carla") == ("acme:carla", None)
    assert _code(_rest(j["carla_api"])) == "acme:carla"

    fait = db.desactiver_tenant("acme", by="op-1", reason="contrat terminé")
    assert fait["changed"] is True
    assert fait["revoked"] == {"user": 2, "delegation": 1}
    assert fait["accounts_cut"] == 2           # carla, dan — pas eve (`acme-x`)
    assert fait["aliases_cut"] == 1

    # APRÈS : refus NOMMÉ sur chaque chemin.
    assert _code(_rest("eyJ.a.b", "acme:carla")) == (403, "tenant_disabled")
    assert _code(_rest("eyJ.a.b", "u-ancien-carla")) == (403, "tenant_disabled")
    for nom in ("carla_api", "carla_deleg", "dan_api"):
        assert db.verify_api_token(j[nom]) is None, f"{nom} doit être révoqué"
        assert _code(_rest(j[nom])) == (401, "invalid_api_token")
    # La trace de révocation dit qui et pourquoi.
    with psycopg.connect(db._conn._database_url(), row_factory=dict_row) as c:
        traces = c.execute("SELECT DISTINCT revoked_by, revoked_reason FROM user_api_tokens "
                           "WHERE sub LIKE 'acme:%' AND revoked_at IS NOT NULL").fetchall()
    assert traces == [{"revoked_by": "op-1",
                       "revoked_reason": "tenant acme désactivé : contrat terminé"}]

    # Le voisin : ni son jeton, ni sa session, ni son ancien identifiant ne bougent —
    # y compris le tenant dont le slug PROLONGE celui qu'on a coupé.
    assert _code(_rest(j["fred_api"])) == "beta:fred"
    assert _code(_rest(j["eve_api"])) == "acme-x:eve"
    assert _rest("eyJ.a.b", "beta:fred") == ("beta:fred", None)
    assert _rest("eyJ.a.b", "u-ancien-fred") == ("beta:fred", None)
    assert _rest("eyJ.a.b", "nu-gil") == ("nu-gil", None)


def test_la_coupure_tient_meme_si_lannuaire_continue_demettre(base):
    """Un jeton émis APRÈS le geste (un travail réservé entre-temps, un JWT frais de
    l'annuaire du tenant) n'est pas révoqué — il est refusé à la porte."""
    db = base
    db.desactiver_tenant("acme", by="op-1", reason="contrat terminé")
    tardif = db.create_api_token("acme:carla", "job", ttl_seconds=600, kind="delegation")
    assert db.verify_api_token(tardif) is not None          # le jeton vit…
    assert _code(_rest(tardif)) == (403, "tenant_disabled")  # …et ne sert à rien
    assert _code(_rest("eyJ.frais", "acme:dan")) == (403, "tenant_disabled")
    # Le geste rejoué le ramasse, sans réécrire l'état d'origine.
    encore = db.desactiver_tenant("acme", by="op-2", reason="autre motif")
    assert encore["changed"] is False
    assert encore["revoked"] == {"delegation": 1}
    assert encore["disabled_by"] == "op-1" and encore["disabled_reason"] == "contrat terminé"


def test_aucun_compte_ne_nait_sous_un_tenant_desactive(base):
    db = base
    db.desactiver_tenant("acme", by="op-1", reason="contrat terminé")
    with pytest.raises(db.TenantDesactive) as leve:
        db.upsert_user("acme:nouveau", email="n@ex.test")
    assert leve.value.code == "tenant_disabled"
    assert db.get_user("acme:nouveau") is None, "la levée annule l'INSERT"
    db.upsert_user("beta:nouveau", email="b@ex.test")      # le voisin naît
    assert db.get_user("beta:nouveau") is not None
    db.upsert_user("acme:carla", email="c2@ex.test")      # un compte existant : pas une naissance


def test_reactiver_rouvre_les_connexions_sans_faire_revivre_un_jeton(base):
    db = base
    j = _jetons(db)
    db.desactiver_tenant("acme", by="op-1", reason="contrat terminé")
    assert db.reactiver_tenant("acme") is True
    assert db.reactiver_tenant("acme") is False             # il ne l'était plus
    assert db.tenant_desactive_du_sub("acme:carla") is None
    # Nouvelles connexions : servies.
    assert _rest("eyJ.a.b", "acme:carla") == ("acme:carla", None)
    db.upsert_user("acme:nouveau")
    assert _code(_rest(db.create_api_token("acme:carla", "neuf"))) == "acme:carla"
    # Jetons révoqués : morts pour de bon.
    assert db.verify_api_token(j["carla_api"]) is None
    assert db.verify_api_token(j["carla_deleg"]) is None


def test_le_predicat_et_la_fiche(base):
    db = base
    assert db.desactiver_tenant("inconnu", by="op", reason="x") is None
    db.desactiver_tenant("acme", by="op-1", reason="contrat terminé")
    assert db.tenant_desactive_du_sub("acme:carla")["slug"] == "acme"
    assert db.tenant_desactive_du_sub("acme-x:eve") is None
    assert db.tenant_desactive_du_sub("acme") is None
    # Le registre d'émetteurs reçoit l'état (la façade OAuth le lit là).
    par_slug = {r["slug"]: r for r in db.list_tenant_issuers()}
    assert par_slug["acme"]["disabled_at"] is not None
    assert par_slug["beta"]["disabled_at"] is None
    fiche = db.get_tenant_overview("acme")
    assert fiche["disabled_reason"] == "contrat terminé"
    ligne = next(r for r in db.list_tenants_overview() if r["slug"] == "acme")
    assert ligne["disabled_at"] is not None
