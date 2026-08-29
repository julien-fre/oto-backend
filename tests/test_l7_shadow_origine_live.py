"""L'ORIGINE d'une observation de la fenêtre L7, contre un VRAI PostgreSQL.

**Le problème que ça règle.** Prod et preprod partagent la même base. Le premier
compteur de la fenêtre L7 mélangeait donc les deux — 134 observations dont on ne
pouvait pas dire lesquelles venaient de la production, alors que c'est exactement la
question qui autorise la bascule d'autorité.

**Ce que seul un vrai serveur peut dire**, et qui est tout l'objet de ce fichier :

- l'écriture **ne suppose pas la forme de la clé primaire**. La colonne naît au
  démarrage (additif), la clé s'étend par une commande explicite (ADR 0065), et les
  deux ne tournent pas au même instant. Entre les deux, l'ancienne clé tient et deux
  origines se partagent une ligne : l'écriture doit alors **fusionner**, pas lever ni
  perdre le compte. Un `ON CONFLICT` figé sur l'une des deux formes casserait d'un
  côté ou de l'autre — ça ne se voit qu'en jouant les deux formes sur une vraie base ;
- `NULLS NOT DISTINCT` sur la clé étendue : deux lignes d'origine INCONNUE ne doivent
  pas se dupliquer. C'est un comportement du moteur, pas du code ;
- la lentille compte ce qu'elle DIT compter, jusqu'à la validation du modèle servi.

`pg_dsn` (conftest) : `OTO_TEST_PG_DSN`, sinon un conteneur jetable, sinon skip.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture()
def live(pg_dsn):
    """Une base JETABLE par test — la forme de la CLÉ change au cours de ces tests,
    donc les partager les rendrait dépendants de l'ordre."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_l7o_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
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


def _lignes():
    from oto_mcp.db import access_shadow as db_shadow
    return db_shadow.read_shadow(days=1)


# ── L'origine est DÉRIVÉE, jamais posée à la main ─────────────────────────────

@pytest.mark.parametrize("url,attendu", [
    ("https://mcp.oto.cx", "prod"),
    ("https://mcp.oto.ninja", "preprod"),
])
def test_l_origine_se_derive_de_l_url_publique_du_process(live, monkeypatch, url, attendu):
    from oto_mcp.db import access_shadow as db_shadow

    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", url)
    db_shadow.bump_shadow("serper", 7, "accord", 1)
    assert [l["origine"] for l in _lignes()] == [attendu]


def test_sans_url_publique_l_origine_reste_INCONNUE(live, monkeypatch):
    """Un environnement qui ne peut pas se nommer ne se devine pas. C'est ce qui
    sépare cette dérivation de `project_domain()`, dont le défaut est le domaine de
    PRODUCTION : une variable oubliée y serait classée « prod » en silence."""
    from oto_mcp.db import access_shadow as db_shadow

    monkeypatch.delenv("OTO_MCP_PUBLIC_URL", raising=False)
    db_shadow.bump_shadow("serper", 7, "accord", 1)
    assert [l["origine"] for l in _lignes()] == [None]


# ── L'écriture tolère les DEUX formes de clé ──────────────────────────────────

def test_sous_l_ANCIENNE_cle_deux_origines_fusionnent_sans_lever(live):
    """L'état entre le déploiement et la commande. Perdre le compte serait pire que
    le mélanger : on crédite la ligne partagée, exactement comme avant la colonne."""
    from oto_mcp.db import access_shadow as db_shadow

    db_shadow.bump_shadow("serper", 7, "accord", 2, origine="prod")
    db_shadow.bump_shadow("serper", 7, "accord", 3, origine="preprod")
    lignes = _lignes()
    assert len(lignes) == 1, "l'ancienne clé n'autorise qu'une ligne par (jour, …)"
    assert lignes[0]["n"] == 5, "les deux origines sont créditées, aucune perdue"


def test_apres_la_commande_les_deux_origines_se_SEPARENT(live):
    from scripts import migrate_shadow_origine as cmd
    from oto_mcp.db import access_shadow as db_shadow

    assert cmd.main(apply=True) == 0
    db_shadow.bump_shadow("serper", 7, "accord", 2, origine="prod")
    db_shadow.bump_shadow("serper", 7, "accord", 3, origine="preprod")
    par_origine = {l["origine"]: l["n"] for l in _lignes()}
    assert par_origine == {"prod": 2, "preprod": 3}


def test_les_lignes_AMBIGUES_survivent_a_la_commande_sans_etre_reecrites(live):
    """L'existant n'est pas réécrit : une observation écrite quand personne ne notait
    l'origine reste NULL, et la commande ne lui invente pas de valeur."""
    from scripts import migrate_shadow_origine as cmd
    from oto_mcp.db import access_shadow as db_shadow

    db_shadow.bump_shadow("serper", 7, "accord", 1, origine=None)   # avant la commande
    assert cmd.main(apply=True) == 0
    db_shadow.bump_shadow("serper", 7, "accord", 4, origine=None)   # après
    assert [(l["origine"], l["n"]) for l in _lignes()] == [(None, 5)]


def test_la_cle_etendue_refuse_DEUX_lignes_d_origine_inconnue(live):
    """`NULLS NOT DISTINCT`, exercé là où il agit VRAIMENT — sur l'INSERT.

    L'écriture normale ne l'atteint jamais : son UPDATE compare avec `IS NOT DISTINCT
    FROM`, donc elle retrouve la ligne NULL et n'insère pas. L'index protège l'autre
    cas, celui d'une COURSE — deux process qui ne trouvent rien et insèrent tous les
    deux. Sans la clause, les deux réussissent et le compteur se dédouble en silence.
    (Écrit après qu'une mutation a montré que le test d'origine ne prouvait rien : il
    passait aussi bien avec la clause que sans.)"""
    import psycopg
    from scripts import migrate_shadow_origine as cmd
    from oto_mcp.db._conn import _connect

    assert cmd.main(apply=True) == 0
    insert = ("INSERT INTO access_shadow_l7 (day, connector, org_id, classe, n, origine) "
              "VALUES (CURRENT_DATE, 'serper', 7, 'accord', 1, NULL)")
    with _connect() as conn:
        conn.execute(insert)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with _connect() as conn:
            conn.execute(insert)


# ── La commande elle-même ─────────────────────────────────────────────────────

def test_le_dry_run_ne_change_pas_la_cle(live):
    from scripts import migrate_shadow_origine as cmd

    assert cmd.main(apply=False) == 0
    assert cmd._etat()["pk_ancienne"] is True and cmd._etat()["index_neuf"] is False


def test_la_commande_est_idempotente(live):
    from scripts import migrate_shadow_origine as cmd

    assert cmd.main(apply=True) == 0
    e = cmd._etat()
    assert e["index_neuf"] is True and e["pk_ancienne"] is False
    assert cmd.main(apply=True) == 0          # second passage : rien à faire
    assert cmd._etat() == e


# ── La lentille compte ce qu'elle dit compter ─────────────────────────────────

def test_la_lentille_ne_compte_que_l_origine_demandee(live):
    from scripts import migrate_shadow_origine as cmd
    from oto_mcp.capabilities import access_shadow_admin as lentille
    from oto_mcp.db import access_shadow as db_shadow

    cmd.main(apply=True)
    db_shadow.bump_shadow("serper", 7, "accord", 9, origine="prod")
    db_shadow.bump_shadow("serper", 7, "accord", 4, origine="preprod")
    db_shadow.bump_shadow("serper", 7, "accord", 2, origine=None)

    prod = lentille.ShadowOut(**lentille._read(None, lentille.AccessShadowInput()))
    assert prod.origine == "prod" and prod.verdict.origine == "prod"
    assert prod.verdict.observations == 9, "la preprod et l'inconnu ne comptent pas"
    assert prod.verdict.origine_inconnue == 2, "…mais l'inconnu est DIT"
    assert [l.origine for l in prod.lignes] == ["prod"]

    toutes = lentille.ShadowOut(**lentille._read(
        None, lentille.AccessShadowInput(origine="toutes")))
    assert toutes.verdict.observations == 15
    assert {l.origine for l in toutes.lignes} == {"prod", "preprod", None}


def test_une_fenetre_prod_VIDE_ne_s_ouvre_pas_meme_avec_de_l_inconnu(live):
    """Le cas du 2026-08-29 : 134 observations en base, aucune attribuable à la prod.
    La porte doit rester fermée, et la réponse doit dire POURQUOI — sinon on lit
    « rien ne s'est passé » là où il faut lire « on ne sait pas qui a écrit »."""
    from oto_mcp.capabilities import access_shadow_admin as lentille
    from oto_mcp.db import access_shadow as db_shadow

    db_shadow.bump_shadow("serper", 7, "accord", 134, origine=None)
    out = lentille.ShadowOut(**lentille._read(None, lentille.AccessShadowInput()))
    assert out.verdict.observations == 0
    assert out.verdict.porte_ouverte is False
    assert out.verdict.origine_inconnue == 134
