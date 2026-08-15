"""Les compteurs de suivi des tenants, contre un VRAI PostgreSQL.

Ce fichier existe parce que le reste ne peut pas remplacer une base : les compteurs
sont **du SQL** — une classification par préfixe, quatre agrégats et leurs jointures.
Un stub validerait la forme du dict et laisserait passer exactement ce qui casse :
une colonne mal nommée, une CTE qui ombre la table `orgs`, un `LEFT JOIN` qui perd
le tenant à zéro compte. On monte donc le **vrai schéma** (`init_db()`), on sème une
population de forme réelle, et on lit les chiffres.

Ce qui se joue dans les assertions, au-delà des totaux :

- **le tenant à zéro reste une ligne** — un tenant provisionné dont personne ne s'est
  connecté est celui qu'on veut le plus voir ; un `JOIN` l'aurait fait disparaître ;
- **les deux sources restent séparées** — un compte qualifié `tulina:…` compte pour
  Tulina même quand son organisation est restée sur le tenant `oto`, et cet écart
  est ce que `orgs_desalignees` nomme ;
- **la classification est celle du registre** — même résultat que
  `tenancy.IssuerRegistry.tenant_of` sur les mêmes subs (deux implémentations, une
  seule règle : le préfixe déclaré, jamais une découpe).

Sauté proprement sans PostgreSQL joignable (fixture `pg_dsn`).
"""
from __future__ import annotations

import psycopg
import pytest

from oto_mcp import tenancy


@pytest.fixture()
def base(pg_dsn, monkeypatch):
    """Le VRAI schéma (`init_db`) + une population semée à la main.

    ⚠️ `init_db` sème le tenant `oto` (id 1) et pose `orgs.tenant_id` : on part donc
    de l'état réel d'une base neuve, pas d'un DDL reconstitué pour le test.
    """
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    monkeypatch.setenv("OTO_CONFIG_DISABLE_SOPS", "1")
    from oto_mcp.db import _conn
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_dsn)
    _conn._pool = None  # le pool est mémoïsé au module : le forcer sur CETTE base
    from oto_mcp import db
    db.init_db()

    with psycopg.connect(pg_dsn, autocommit=True) as c:
        for t in ("tool_calls", "org_members", "orgs", "users"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM tenants WHERE slug <> 'oto'")
        c.execute("INSERT INTO tenants (slug, name, issuer) VALUES "
                  "('tulina', 'Tulina', 'https://auth.tulina.ai/oidc'), "
                  "('acme', 'Acme', NULL) RETURNING id")
        tulina = c.execute("SELECT id FROM tenants WHERE slug='tulina'").fetchone()["id"]
        oto = c.execute("SELECT id FROM tenants WHERE slug='oto'").fetchone()["id"]

        # 2 comptes nus (tenant oto) + 2 comptes qualifiés (tenant tulina), dont un
        # qui n'a jamais rien appelé — l'inactif est celui qu'un suivi doit montrer.
        for sub in ("alice", "bob", "tulina:carla", "tulina:dan"):
            c.execute("INSERT INTO users (sub, email) VALUES (%s, %s)",
                      (sub, f"{sub}@ex.test"))

        # Une org par tenant, + l'org DÉSALIGNÉE : rattachée au tenant `oto`, créée
        # par un compte qualifié `tulina:` (l'état que laisse une bascule partielle).
        c.execute("INSERT INTO orgs (name, created_by, tenant_id) VALUES "
                  "('Oto Team', 'alice', %s), ('Tulina', 'tulina:carla', %s), "
                  "('Reprise', 'tulina:dan', %s)", (oto, tulina, oto))
        # Une org archivée ne compte pas comme active (elle a sa propre colonne).
        c.execute("INSERT INTO orgs (name, created_by, tenant_id, archived_at) "
                  "VALUES ('Ancienne', 'bob', %s, NOW())", (oto,))

        c.execute("INSERT INTO tool_calls (sub, tool, kind) VALUES "
                  "('alice', 'fr_search', 'mcp'), ('alice', 'fr_search', 'mcp'), "
                  "('tulina:carla', 'oto_search', 'mcp'), "
                  # kind='rest' : un appel d'API, pas une invocation d'outil — il ne
                  # doit PAS gonfler le trafic MCP du tenant.
                  "('tulina:carla', '/api/me', 'rest')")
        # Hors fenêtre : le compteur est fenêtré, `comptes` ne l'est pas.
        c.execute("INSERT INTO tool_calls (sub, tool, kind, created_at) VALUES "
                  "('bob', 'fr_search', 'mcp', NOW() - INTERVAL '90 days')")
        yield c


def _par_slug(rows):
    return {r["slug"]: r for r in rows}


def test_les_compteurs_dun_tenant(base):
    from oto_mcp import db
    par = _par_slug(db.list_tenants_overview(days=30))

    assert set(par) == {"oto", "tulina", "acme"}

    oto = par["oto"]
    assert (oto["orgs"], oto["orgs_archivees"]) == (3, 1)
    assert oto["comptes"] == 2                    # alice, bob (subs nus)
    assert (oto["appels"], oto["comptes_actifs"]) == (2, 1)  # bob est hors fenêtre
    assert oto["orgs_desalignees"] == 1           # « Reprise », créée par tulina:dan

    tulina = par["tulina"]
    assert tulina["orgs"] == 1
    assert tulina["comptes"] == 2                 # carla + dan, qualifiés
    assert (tulina["appels"], tulina["comptes_actifs"]) == (1, 1)  # le 'rest' exclu
    assert tulina["orgs_desalignees"] == 0


def test_un_tenant_sans_aucun_compte_reste_une_ligne(base):
    """Un tenant provisionné où personne ne s'est encore connecté : la ligne DOIT
    exister, à zéro. C'est l'état qu'on surveille — un JOIN l'effacerait."""
    from oto_mcp import db
    acme = _par_slug(db.list_tenants_overview(days=30))["acme"]
    assert (acme["comptes"], acme["orgs"], acme["appels"]) == (0, 0, 0)
    assert acme["authenticates"] is False   # aucune ligne d'émetteur


def test_la_classification_sql_dit_la_meme_chose_que_le_registre(base):
    """Deux implémentations du même classement (SQL ici, Python dans `tenancy`) :
    elles doivent trancher pareil, sinon l'écran compte des comptes que
    l'authentification range ailleurs."""
    registre = tenancy.IssuerRegistry(tenancy.build(
        "https://auth.oto.ninja/oidc",
        tenants=[{"slug": "tulina", "issuer": "https://auth.tulina.ai/oidc"}]))
    from oto_mcp import db
    fiche = db.get_tenant_overview("tulina", days=30)
    subs = [c["sub"] for c in fiche["comptes_recents"]]
    assert sorted(subs) == ["tulina:carla", "tulina:dan"]
    assert all(registre.tenant_of(s) == "tulina" for s in subs)

    oto = db.get_tenant_overview("oto", days=30)
    assert all(registre.tenant_of(c["sub"]) == "oto" for c in oto["comptes_recents"])


def test_la_fiche_donne_ladresse_de_lecart(base):
    """Le chiffre `orgs_desalignees` sans sa liste serait une alarme sans adresse."""
    from oto_mcp import db
    fiche = db.get_tenant_overview("oto", days=30)
    ecart = fiche["orgs_desalignees_detail"]
    assert [(o["name"], o["tenant_du_createur"]) for o in ecart] == [("Reprise", "tulina")]
    assert [o["name"] for o in fiche["orgs_recentes"]][-1] == "Ancienne"  # archivée en fin


def test_un_slug_inconnu_ne_rend_pas_de_fiche(base):
    from oto_mcp import db
    assert db.get_tenant_overview("fantome", days=30) is None


def test_les_comptes_les_plus_actifs_dabord(base):
    from oto_mcp import db
    comptes = db.get_tenant_overview("tulina", days=30)["comptes_recents"]
    assert comptes[0]["sub"] == "tulina:carla" and comptes[0]["appels"] == 1
    assert comptes[-1]["appels"] == 0   # l'inactif reste servi, en fin de liste
