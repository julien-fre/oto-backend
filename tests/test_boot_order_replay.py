"""L'ORDRE DU BOOT, rejoué — le garde-fou qui manquait le 2026-08-27 (#450, #426).

Ce soir-là un push a cassé le boot preprod (`column "status" does not exist`, rollback
auto) : un index posé dans le DDL de base sur une colonne qui naît d'un `ALTER` plus
bas. **Ni le DDL seul ni la migration seule ne pouvaient l'attraper — chacun passait de
son côté.** Ce qui échouait, c'était leur ORDRE, et rien ne jouait cet ordre ailleurs
qu'au démarrage d'un vrai serveur contre une vraie base.

Trois choses vérifiées ici, contre un PostgreSQL réel (le seul instrument qui prouve
quoi que ce soit sur du DDL) :

1. **l'ordre passe** — `_SCHEMA` assemblé puis les ALTER, dans la séquence exacte du
   démarrage, sur une base vierge ;
2. **il est REJOUABLE dans une transaction annulée** — c'est ce qui rend le garde-fou
   utilisable ailleurs qu'en CI : `oto-mcp maintenance check-boot` le joue contre la
   base SERVIE sans y laisser de trace ;
3. **il est IDEMPOTENT** — rejoué, il rend le même schéma. Pas « il ne lève pas » :
   la même empreinte de colonnes, d'index et de contraintes. Un bloc qui recrée, qui
   renomme ou qui écrit à chaque passage se voit là, et nulle part ailleurs.

⚠️ Ce test est le cliquet de l'ADR 0065 : il vaut pour les blocs qui RESTENT au boot.
Ceux qui en sont sortis (purge, re-projection, index de clé métier) ne sont plus dans
la séquence — s'ils y revenaient, ils devraient repasser par ici.
"""
from __future__ import annotations

import pytest

# Empreinte du schéma : ce qu'un rejeu ne doit PAS faire bouger. On prend les
# colonnes (nom + type + nullabilité + défaut), les index (leur définition SQL
# complète) et les contraintes — soit exactement ce que les incidents passés ont
# fait bouger : une colonne ajoutée deux fois, un index recréé sur une autre
# expression, une PK reposée.
_EMPREINTE = {
    "colonnes": """
        SELECT table_name, column_name, data_type, is_nullable, column_default
          FROM information_schema.columns WHERE table_schema = 'public'
         ORDER BY table_name, column_name
    """,
    "index": """
        SELECT tablename, indexname, indexdef FROM pg_indexes
         WHERE schemaname = 'public' ORDER BY tablename, indexname
    """,
    "contraintes": """
        SELECT conrelid::regclass::text AS t, conname, pg_get_constraintdef(oid) AS def
          FROM pg_constraint WHERE connamespace = 'public'::regnamespace
         ORDER BY 1, 2
    """,
}


def _empreinte(conn) -> dict:
    out = {nom: [tuple(r.values()) for r in conn.execute(sql).fetchall()]
           for nom, sql in _EMPREINTE.items()}
    # Une empreinte VIDE ferait passer toutes les comparaisons ci-dessous sans rien
    # prouver — le mode d'échec le plus vicieux d'un test de ce genre. Relevé le
    # 2026-08-28 sur une base fraîchement bootée : 67 tables, 620 colonnes, 177 index,
    # 141 contraintes. Les bornes sont larges : elles disent « la base est bootée »,
    # pas « le schéma est celui-ci » (ça, c'est test_schema_assembly_frozen).
    assert len(out["colonnes"]) > 300 and len(out["index"]) > 100, (
        f"empreinte anormalement pauvre — la base est-elle bootée ? { {k: len(v) for k, v in out.items()} }")
    return out


@pytest.fixture(scope="module")
def base_bootee(pg_dsn):
    """Une base JETABLE **À NOUS**, bootée par le VRAI chemin de démarrage.

    ⚠️ Une base à part, et pas celle du conteneur partagé : `pg_dsn` est
    session-scopé, donc un boot complet dedans laisserait ~67 tables et leurs FK
    derrière lui — et les tests qui se contentent de recréer deux tables autonomes
    (`test_run_retention` fait un `DROP TABLE runs` nu) n'y arriveraient plus. Vécu
    en écrivant ce fichier : 34 tests d'autres fichiers rougis d'un coup, sans
    rapport apparent avec le lot. Même recette que `test_connector_instances_l6_live`.
    """
    import os
    import uuid

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_boot_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom

    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        with dbconn._connect() as c:
            yield c
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


def test_l_ordre_du_boot_se_rejoue_en_transaction_annulee(base_bootee):
    """Le rejeu passe, et ne laisse RIEN derrière lui.

    C'est la propriété qui rend le garde-fou jouable contre la base de production :
    une transaction annulée, donc un diagnostic sans effet. Si un jour un bloc du boot
    devenait non transactionnel (un `CREATE INDEX CONCURRENTLY` glissé dans la
    séquence, par exemple), ce test tomberait ici — et c'est la bonne place pour
    l'apprendre."""
    from oto_mcp.db._init import replay_boot_schema_dry
    avant = _empreinte(base_bootee)
    replay_boot_schema_dry(base_bootee)
    apres = _empreinte(base_bootee)
    for nom in _EMPREINTE:
        assert avant[nom] == apres[nom], (
            f"le rejeu ANNULÉ a laissé une trace dans « {nom} » — la transaction "
            f"n'a donc pas tout englobé")


def test_le_boot_rejoue_rend_le_meme_schema(base_bootee):
    """Idempotence, au sens fort : deux boots, une seule empreinte.

    `init_db()` est appelé une seconde fois, exactement comme un `systemctl restart`
    sans nouveau code le ferait. Un bloc qui ne serait pas idempotent — un `ADD
    COLUMN` sans `IF NOT EXISTS`, un index recréé sur une expression différente, un
    `RENAME` qui se rejoue — fait diverger l'empreinte, et le message dit lequel."""
    from oto_mcp.db import init_db
    avant = _empreinte(base_bootee)
    init_db()
    apres = _empreinte(base_bootee)
    for nom in _EMPREINTE:
        manquants = [x for x in avant[nom] if x not in apres[nom]]
        surnumeraires = [x for x in apres[nom] if x not in avant[nom]]
        assert not manquants and not surnumeraires, (
            f"un second boot a changé « {nom} » — disparus : {manquants[:3]} ; "
            f"apparus : {surnumeraires[:3]}")


def test_la_maintenance_n_est_plus_dans_la_sequence_du_boot():
    """Le cliquet de l'ADR 0065 : ce qui est sorti du boot ne doit pas y revenir.

    Sonde le SOURCE d'`init_db` (et non un comportement), parce que c'est le geste
    qu'on veut interdire : rappeler au démarrage un travail dont le coût suit la
    taille de la base. Un lot qui en aurait besoin doit passer par
    `oto_mcp/maintenance.py` et son timer — ou changer ce test en disant pourquoi."""
    import inspect

    from oto_mcp.db import _init
    source = inspect.getsource(_init.init_db) + inspect.getsource(_init.apply_boot_schema)
    for interdit in ("prune_tool_calls", "prune_run_messages",
                     "backfill_node_blocks", "_ensure_datastore_key_indexes",
                     "migrate_business_key_indexes"):
        assert interdit not in source, (
            f"`{interdit}` est revenu dans la séquence du boot. Ces travaux ont la "
            f"forme d'un cron et le coût d'un cron ; leur place est "
            f"`oto_mcp/maintenance.py`, tirée par oto-mcp-maintenance.timer "
            f"(ADR 0065, lot 0 — oto-backend#426).")


def test_les_travaux_de_maintenance_sont_tous_nommes_et_a_blanc(base_bootee):
    """Chaque travail est une commande jouable seule, et chacun sait ne rien faire.

    Le `--dry-run` n'est pas un confort : sur une base PARTAGÉE prod/preprod, la
    première question devant une purge est « combien de lignes ? », et il faut pouvoir
    y répondre sans les supprimer."""
    from oto_mcp import maintenance
    for nom in ("retention", "blocks", "key-indexes"):
        out = maintenance._TRAVAUX[nom](dry_run=True)
        assert isinstance(out, dict) and out, f"{nom} à blanc ne rend rien"
    assert maintenance.run(list(maintenance._ALL), dry_run=True, strict=True) == 0


def test_la_preparation_de_la_base_ne_tourne_qu_une_fois(monkeypatch):
    """Un seul `init_db` et un seul tour de backfills par process (ADR 0065, lot 0).

    Le boot en faisait trois et deux : `_build_mcp` est appelé deux fois (instance
    anonyme au niveau module, instance authentifiée dans `main`) et `main` rappelait
    `init_db` par-dessus. Mesuré sur la base servie : 2,8 s pour les trois `init_db`,
    1,2 s pour les deux `backfill_personal_orgs`. Ce test ne mesure pas des secondes
    — il compte les appels, ce qui ne dépend d'aucune machine."""
    from oto_mcp import server
    appels: list[str] = []
    monkeypatch.setattr(server, "_PREPARED", False, raising=False)
    monkeypatch.setattr(server.db, "init_db", lambda: appels.append("init_db"))
    import oto_mcp.org_store as org_store
    monkeypatch.setattr(org_store, "backfill_personal_orgs",
                        lambda: appels.append("personal"))
    monkeypatch.setattr(org_store, "backfill_org_front", lambda: appels.append("front"))
    import oto_mcp.credentials_store as creds
    monkeypatch.setattr(creds, "backfill_member_scope", lambda: appels.append("member"))
    monkeypatch.setattr(server.db, "backfill_unipile_member_scope",
                        lambda: appels.append("unipile"))
    import oto_mcp.guide_store as guide_store
    monkeypatch.setattr(guide_store, "seed_platform_guides", lambda: appels.append("guides"))

    server._prepare_database()
    server._prepare_database()          # le second `_build_mcp` du boot
    server._prepare_database()          # l'ex-`db.init_db()` nu de `main`
    assert appels.count("init_db") == 1, f"init_db joué {appels.count('init_db')} fois"
    assert appels.count("personal") == 1
    assert len(appels) == 6, f"un backfill est joué plus d'une fois : {appels}"
