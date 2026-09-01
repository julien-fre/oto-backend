"""L'ORDRE DU BOOT, rejoué — le garde-fou qui manquait le 2026-08-27 (#450, #426).

Ce soir-là un push a cassé le boot preprod (`column "status" does not exist`, rollback
auto) : un index posé dans le DDL de base sur une colonne qui naît d'un `ALTER` plus
bas. **Ni le DDL seul ni la migration seule ne pouvaient l'attraper — chacun passait de
son côté.** Ce qui échouait, c'était leur ORDRE, et rien ne jouait cet ordre ailleurs
qu'au démarrage d'un vrai serveur contre une vraie base.

Quatre choses vérifiées ici, contre un PostgreSQL réel (le seul instrument qui prouve
quoi que ce soit sur du DDL) :

1. **l'ordre passe** — `_SCHEMA` assemblé puis les ALTER, dans la séquence exacte du
   démarrage, sur une base vierge ;
2. **il est REJOUABLE dans une transaction annulée** — c'est ce qui rend le garde-fou
   utilisable ailleurs qu'en CI : `oto-mcp maintenance check-boot` le joue contre la
   base SERVIE sans y laisser de trace ;
3. **il est IDEMPOTENT** — rejoué, il rend le même schéma. Pas « il ne lève pas » :
   la même empreinte de colonnes, d'index et de contraintes. Un bloc qui recrée, qui
   renomme ou qui écrit à chaque passage se voit là, et nulle part ailleurs ;
4. **il passe sur une base QUI EXISTE DÉJÀ** — la seule configuration où le défaut
   du 27/08 existe, et la seule que ce fichier ne jouait pas. Ajouté le 2026-09-01
   (#781), cf. ci-dessous.

## ⚠️ Ce fichier a été AVEUGLE au piège pour lequel il a été écrit (jusqu'au 01/09/2026)

Les points 1 à 3 bootent une base **vierge** (`CREATE DATABASE` + `init_db()`). Or sur
une base vierge, le `CREATE TABLE IF NOT EXISTS` pose la colonne **inline** : le
`CREATE INDEX` du DDL trouve donc toujours sa colonne, et **le défaut visé ne peut pas
se produire**. Il n'apparaît que là où le `CREATE TABLE` est SAUTÉ — c'est-à-dire sur
la préproduction et la production, qui partagent une base construite par les troncs
précédents.

Mesuré le 2026-09-01 sur un lot qui portait exactement ce motif : CI verte, 87 tests
verts sur les 7 fichiers de garde du domaine boot, **boot réel rouge**. Un garde-fou
qu'on n'a jamais vu refuser n'atteste rien.

Le point 4 fabrique donc l'état d'avant en **défaisant l'état d'après** : on part de
la base neuve, on RETIRE une colonne que le boot pose par `ALTER`, et on rejoue. Une
base à qui manque cette colonne, c'est exactement une base d'avant le lot qui l'a
introduite — sans dépendre d'un commit à extraire, ni d'un jeu de DDL figé qui se
périmerait. Et ce n'est pas fait pour UNE colonne choisie à la main : la liste est
**observée** sur le SQL que le boot exécute vraiment (132 colonnes au 01/09), donc un
lot qui en ajoute une la fait entrer dans le cliquet sans que personne y pense.

⚠️ Ce test est le cliquet de l'ADR 0065 : il vaut pour les blocs qui RESTENT au boot.
Ceux qui en sont sortis (purge, re-projection, index de clé métier) ne sont plus dans
la séquence — s'ils y revenaient, ils devraient repasser par ici.
"""
from __future__ import annotations

import re

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


# ─────────────────────────────────────────────────────────────────────────────
# 4. LE BOOT SUR UNE BASE QUI EXISTE DÉJÀ  (#781, 2026-09-01)
# ─────────────────────────────────────────────────────────────────────────────

class _OrdresEnregistres:
    """Une connexion qui NOTE le SQL qu'on lui passe, puis le transmet inchangé.

    Pourquoi observer plutôt que lire `_init.py` : une partie des `ADD COLUMN` naît
    de f-strings dans des boucles (`tool_calls`, `billing_payments`…). Un relevé sur
    la SOURCE en manquerait, et le manquerait *en silence* — le mode de panne exact
    qu'on ferme ici. Le SQL exécuté, lui, est déjà interpolé et déjà déroulé."""

    def __init__(self, conn):
        self._conn = conn
        self.sql: list[str] = []

    def execute(self, query, params=None, **kw):
        self.sql.append(query if isinstance(query, str) else str(query))
        if params is None:
            return self._conn.execute(query, **kw)
        return self._conn.execute(query, params, **kw)

    def __getattr__(self, nom):
        return getattr(self._conn, nom)


_ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?\"?(\w+)\"?\s+"
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?",
    re.IGNORECASE | re.DOTALL)

# Ce qu'une base D'AVANT ne retrouve PAS en se remettant à niveau, au 2026-09-01.
# Toutes de la même famille : une contrainte déclarée **inline dans le CREATE
# TABLE** (PK, UNIQUE, CHECK, FK) portant une colonne que le boot pose AUSSI par
# `ALTER`. Sur une base existante le CREATE TABLE est sauté, l'ALTER rend la
# colonne mais pas la contrainte — la base neuve a la contrainte, la production
# ne l'a pas, et **rien ne rougit jamais**, puisque les deux « marchent ».
#
# Elles ne sont pas corrigées ici, et c'est délibéré : reposer une PK ou un NOT
# NULL est un ordre NON additif (`DROP CONSTRAINT`) sur une base partagée
# prod/preprod — un ACTE, pas une ligne de boot (cf. le commentaire d'
# `unipile_accounts` dans `_init.py`, et `docs/live-migrations.md`). Ce qui est
# fait ici, c'est de les NOMMER et de les compter : le cliquet ci-dessous refuse
# la NEUVIÈME. Cette liste ne doit que RÉTRÉCIR — une entrée s'en retire quand la
# divergence est réellement réparée, à la main, dans le commit qui la répare.
_DIVERGENCES_CONNUES = frozenset({
    "projects.mcp_slug",                    # UNIQUE (mcp_slug)
    "org_instructions.owner_type",          # PRIMARY KEY (owner_type, owner_id, slug)
    "org_instructions.owner_id",            # idem
    "org_instruction_revisions.owner_type",  # PK (owner_type, owner_id, slug, version)
    "org_instruction_revisions.owner_id",   # idem
    "unipile_accounts.org_id",              # PK + FK ON DELETE CASCADE + NOT NULL
    "unipile_accounts.provider",            # PRIMARY KEY (sub, org_id, provider)
    "resource_grants.role",                 # CHECK (role IN (…))
})


@pytest.fixture(scope="module")
def colonnes_posees_par_alter(base_bootee) -> list[tuple[str, str]]:
    """Les colonnes que le boot ajoute par `ALTER` — OBSERVÉES sur le SQL exécuté.

    C'est la liste des colonnes qui peuvent MANQUER à une base qui existe déjà : par
    doctrine (`docs/live-migrations.md`), toute colonne ajoutée à une table existante
    passe par un `ALTER` d'`_init.py`. Donc « la base d'avant » = la base neuve moins
    l'une de ces colonnes."""
    from oto_mcp.db._init import apply_boot_schema

    enregistreur = _OrdresEnregistres(base_bootee)
    with base_bootee.transaction(force_rollback=True):
        apply_boot_schema(enregistreur)
    colonnes: list[tuple[str, str]] = []
    for sql in enregistreur.sql:
        for table, colonne in _ADD_COLUMN.findall(sql):
            colonnes.append((table.lower(), colonne.lower()))
    colonnes = list(dict.fromkeys(colonnes))
    # La garde PROPRE du cliquet : sans elle, réécrire les ALTER autrement (SQL
    # construit ailleurs, ordres groupés) rendrait tout ce qui suit inerte **en
    # vert**. 132 relevées le 2026-09-01 ; le plancher est large exprès, il dit
    # « on regarde encore quelque chose », pas « on regarde exactement ça ».
    assert len(colonnes) >= 100, (
        f"seulement {len(colonnes)} colonnes posées par ALTER ont été VUES dans les "
        f"{len(enregistreur.sql)} ordres du boot — le relevé ne reconnaît plus la "
        f"forme des ALTER, et tout ce fichier passerait à vide")
    return colonnes


@pytest.fixture(scope="module")
def rejeu_sans_chaque_colonne(base_bootee, colonnes_posees_par_alter):
    """Pour CHAQUE colonne posée par un ALTER : la retirer, rejouer le boot, relever.

    Une passe unique, deux constats lus par les deux tests qui suivent — parce que
    c'est le même travail (≈ 30 ms par colonne, ≈ 4 s au total le 01/09) et qu'il
    n'y a aucune raison de le payer deux fois.

    Tout se joue en transaction ANNULÉE : la base neuve du module en ressort
    intacte, et les tests d'idempotence qui la partagent ne voient rien passer."""
    from oto_mcp.db._init import apply_boot_schema

    neuve = _empreinte(base_bootee)
    rouges: list[tuple[str, str]] = []
    divergentes: dict[str, list[str]] = {}
    for table, colonne in colonnes_posees_par_alter:
        cible = f"{table}.{colonne}"
        try:
            with base_bootee.transaction(force_rollback=True):
                # CASCADE : retirer la colonne emporte ce qui en dépend (index,
                # contraintes, FK d'en face) — c'est bien l'état d'une base qui ne
                # l'a jamais eue.
                base_bootee.execute(
                    f'ALTER TABLE "{table}" DROP COLUMN "{colonne}" CASCADE')
                apply_boot_schema(base_bootee)
                apres = _empreinte(base_bootee)
        except AssertionError:
            raise
        except Exception as e:  # noqa: BLE001 — on CLASSE l'échec, on ne l'étouffe pas
            rouges.append((cible, f"{type(e).__name__}: {' '.join(str(e).split())[:200]}"))
            continue
        manque = [f"{nom} : {x}"
                  for nom in _EMPREINTE
                  for x in sorted(set(neuve[nom]) - set(apres[nom]))]
        if manque:
            divergentes[cible] = manque
    return rouges, divergentes


def test_le_boot_passe_sur_une_base_a_qui_manque_une_colonne(rejeu_sans_chaque_colonne):
    """LE cas visé : la base existe déjà, donc le `CREATE TABLE` y est SAUTÉ.

    Un `CREATE INDEX` du DDL assemblé sur une colonne née d'un `ALTER` s'exécute
    quand même, contre la table d'avant → `column "…" does not exist`, init_db KO,
    service down, rollback auto (vécu le 20/07 et le 27/08). Idem pour tout ordre
    d'`_init.py` — un `UPDATE`, un index — placé AVANT l'`ALTER` qui pose sa colonne.

    Trois violations dormaient sur le tronc au 2026-09-01, inertes uniquement parce
    que la production a ces colonnes depuis longtemps : l'index `idx_unipile_accounts_org`
    posé dans `_schema.py` (il l'était déjà dans `_init.py`, juste après l'ALTER),
    les index de recherche `guides` dont le prédicat lit `delivery`, et la conversion
    #317 qui lit `user_datastores.schema`. Les trois sont corrigées dans le même
    commit — sans quoi ce test serait né rouge, et serait né avec une liste
    d'exceptions."""
    rouges, _ = rejeu_sans_chaque_colonne
    assert not rouges, (
        "le boot MEURT sur une base qui existe déjà mais n'a pas encore ces "
        "colonnes — c'est la préproduction et la production, pas un cas d'école :\n"
        + "\n".join(f"  • {cible} → {err}" for cible, err in rouges)
        + "\n\nRègle (docs/live-migrations.md) : ce qui DÉPEND d'une colonne posée "
          "par un ALTER se place APRÈS cet ALTER, dans `_init.py` — jamais dans le "
          "DDL assemblé, qui s'exécute en premier.")


def test_une_base_remise_a_niveau_rend_le_meme_schema_qu_une_base_neuve(
        rejeu_sans_chaque_colonne):
    """Le boot ne doit pas seulement PASSER, il doit CONVERGER.

    Un `ALTER` qui pose la colonne mais pas la contrainte que le `CREATE TABLE`
    porte inline donne une base neuve avec la contrainte et une production sans —
    deux schémas différents sous le même code, et aucun des deux ne lève. C'est le
    corollaire silencieux du même piège : la FK qui ne mord pas, l'unicité qui
    n'unifie pas, le CHECK qui n'a jamais refusé.

    On compare donc dans un seul sens : tout ce que porte la base NEUVE doit se
    retrouver sur la base remise à niveau. L'inverse est toléré — une base d'avant
    garde légitimement des objets que le tronc courant ne crée plus."""
    _, divergentes = rejeu_sans_chaque_colonne
    nouvelles = {c: m for c, m in divergentes.items() if c not in _DIVERGENCES_CONNUES}
    assert not nouvelles, (
        "une base remise à niveau n'a PAS retrouvé ce que porte une base neuve — "
        "la contrainte est donc déclarée inline dans le `CREATE TABLE` (sauté sur "
        "une base existante) et l'`ALTER` ne la rejoue pas :\n"
        + "\n".join(f"  • {cible}\n      " + "\n      ".join(m)
                    for cible, m in nouvelles.items()))
    reparees = _DIVERGENCES_CONNUES - set(divergentes)
    assert not reparees, (
        f"ces divergences ne se produisent plus : {sorted(reparees)}. Le cliquet ne "
        f"doit que RÉTRÉCIR — retire-les de `_DIVERGENCES_CONNUES` dans le commit "
        f"qui les répare, sinon la prochaine réapparaîtra sans faire rougir personne.")


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
