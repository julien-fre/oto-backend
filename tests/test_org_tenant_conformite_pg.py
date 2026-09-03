"""Le rattachement d'une org à son tenant : posé à la naissance, et vérifiable.

Deux moitiés d'un même lot, et elles ne valent que l'une par l'autre :

- **`create_org` DÉCLARE le tenant** (`orgs.tenant_id`), dérivé de l'émetteur du
  jeton de son créateur. Avant ce lot la colonne restait au DEFAULT : 165 orgs sur
  165 portaient le tenant primaire, dont les 65 qui vivent chez un partenaire.
- **`db.orgs_tenant_mismatches()` JUGE** ce rattachement, en le confrontant à ce
  qu'on dérive du front et des membres.

Contre un VRAI PostgreSQL, parce qu'il n'y a rien d'autre à exercer ici : l'écriture
est un `INSERT … COALESCE((SELECT id FROM tenants WHERE slug = %s), 1)` et le
contrôle est une comparaison de deux expressions SQL. Un stub validerait la forme du
dict et laisserait passer très exactement ce qui casse — une sous-requête qui rend
NULL, un `IS DISTINCT FROM` retourné, un COALESCE qui absorbe la faute.

⚠️ **Ce que ce fichier surveille par-dessus tout : que le contrôle ne devienne pas
une tautologie.** La dérivation qu'il compare au rattachement déclaré doit IGNORER
`orgs.tenant_id` — sinon elle se compare à elle-même et ne rougit jamais.
`test_le_controle_n_est_pas_une_tautologie` tient cette propriété en exerçant le cas
des 65 : une org non déclarée est précisément ce qu'une dérivation tautologique ne
peut PAS produire.

⚠️ **Et la dérivation a trois états, pas deux.** Elle rend un tenant, ou elle se tait
— elle ne dit jamais « celle-ci est à nous ». La première version du contrôle repliait
son silence sur le tenant primaire et accusait donc toute org fraîchement créée par le
chemin nominal (le rattachement est posé à l'INSERT, le premier membre arrive à
l'appel suivant). C'est `test_une_plateforme_saine_ne_rapporte_rien` qui l'a montré,
pas la relecture — d'où sa présence ici.

Sauté proprement sans PostgreSQL joignable (fixture `pg_dsn`).
"""
from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from oto_mcp import tenancy

# `acme` DÉCLARE un dashboard, `globex` non — et cet écart est le sujet d'un test.
# `config.front_for` n'écrit la marque de front que si le tenant a une adresse ; le
# RATTACHEMENT, lui, n'en a pas besoin. Une org de `globex` naît donc sans
# `front_brand` et doit malgré tout déclarer son tenant.
_ACME_ISS = "https://auth.acme.test/oidc"
_GLOBEX_ISS = "https://auth.globex.test/oidc"


@pytest.fixture()
def base(pg_dsn, monkeypatch):
    """Une BASE DÉDIÉE, montée par le vrai `init_db()`, plus deux tenants tiers.

    ⚠️ **Base dédiée, pas la base partagée du conteneur** — patron de
    `test_row_lock_native.py`. `init_db()` monte le schéma ENTIER, dont les clés
    étrangères `run_messages → runs` et `runner_jobs → runs` ; le laisser tomber dans
    la base commune casse, plus loin dans la session, tout test qui fait
    `DROP TABLE runs` sans CASCADE (`test_run_single_source`, `test_run_retention`,
    `test_runs_sans_projet`). La panne est un ORDRE ALPHABÉTIQUE de noms de fichiers :
    invisible en lançant ce fichier seul, et attribuée à la mauvaise pièce en suite
    complète. 29 erreurs mesurées ainsi le 2026-09-03. Une base par module rend ces
    tests indépendants de leur voisinage.
    """
    import os
    import uuid
    psycopg_ = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_tenant_" + uuid.uuid4().hex[:8]
    root = psycopg_.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom

    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    monkeypatch.setenv("OTO_CONFIG_DISABLE_SOPS", "1")
    monkeypatch.setattr(dbconn, "_database_url", lambda: dsn)
    dbconn._pool = None
    try:
        from oto_mcp import db
        db.init_db()
        monkeypatch.setattr(tenancy, "_INSTALLED", tenancy.IssuerRegistry(tenancy.build(
            "https://auth.oto.ninja/oidc",
            tenants=[{"slug": "acme", "issuer": _ACME_ISS, "dashboard_url": "https://app.acme.test"},
                     {"slug": "globex", "issuer": _GLOBEX_ISS}])), raising=False)
        with psycopg_.connect(dsn, row_factory=dict_row, autocommit=True) as c:
            c.execute("INSERT INTO tenants (slug, name, issuer, dashboard_url) VALUES "
                      "('acme', 'Acme', %s, 'https://app.acme.test'), "
                      "('globex', 'Globex', %s, NULL)", (_ACME_ISS, _GLOBEX_ISS))
            ids = {r["slug"]: r["id"] for r in
                   c.execute("SELECT slug, id FROM tenants").fetchall()}
            for sub in ("alice", "acme:carla", "globex:gina"):
                c.execute("INSERT INTO users (sub, email) VALUES (%s, %s)",
                          (sub, f"{sub}@ex.test"))
        yield {"dsn": dsn, "ids": ids}
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


def _tenant_de(dsn: str, org_id: int) -> int:
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        return c.execute("SELECT tenant_id FROM orgs WHERE id = %s",
                         (org_id,)).fetchone()["tenant_id"]


# ── La naissance : l'org DÉCLARE son tenant ──────────────────────────────────

def test_une_org_nait_sur_le_tenant_de_son_createur(base):
    """Le cas qu'on a payé : un sub qualifié, une org qui doit le déclarer."""
    from oto_mcp import org_store
    oid = org_store.create_org("Espace Acme", created_by="acme:carla")
    assert _tenant_de(base["dsn"], oid) == base["ids"]["acme"], (
        "une org créée par un compte qualifié `acme:` doit naître SUR le tenant acme. "
        "Restée sur le tenant primaire, elle rejoint les 65 orgs que ce lot répare — "
        "et il faudra un second geste de migration dans six mois.")


def test_le_rattachement_ne_depend_pas_du_dashboard_du_tenant(base):
    """Un tenant SANS `dashboard_url` : pas de marque de front, mais un tenant.

    C'est l'angle mort qu'une implémentation dérivée de `front_brand` aurait gardé :
    `config.front_for` se tait faute d'adresse, et l'org repartirait au primaire.
    """
    from oto_mcp import org_store
    oid = org_store.create_org("Espace Globex", created_by="globex:gina")
    with psycopg.connect(base["dsn"], row_factory=dict_row, autocommit=True) as c:
        row = c.execute("SELECT tenant_id, front_brand FROM orgs WHERE id = %s",
                        (oid,)).fetchone()
    assert row["front_brand"] is None, (
        "prémisse du test : sans `dashboard_url`, `front_for` ne pose pas de marque. "
        "Si elle en pose une, ce test n'exerce plus l'angle mort qu'il vise.")
    assert row["tenant_id"] == base["ids"]["globex"], (
        "le RATTACHEMENT n'a pas besoin d'une adresse de dashboard : une org d'un "
        "tenant sans front déclaré doit quand même dire de qui elle relève.")


def test_le_tenant_suit_le_responsable_pas_l_operateur(base):
    """Chemin console admin : `front_of` porte le compte pour QUI l'org est créée."""
    from oto_mcp import org_store
    oid = org_store.create_org("Provisionnée", created_by="alice", front_of="acme:carla")
    assert _tenant_de(base["dsn"], oid) == base["ids"]["acme"], (
        "provisionner l'org d'un tenant tiers depuis un compte oto doit produire une "
        "org de CE tenant — même règle que `front_base_url`/`front_brand`.")


def test_un_sub_nu_reste_sur_le_tenant_primaire(base):
    from oto_mcp import org_store
    oid = org_store.create_org("Chez nous", created_by="alice")
    assert _tenant_de(base["dsn"], oid) == base["ids"]["oto"]


# ── Le contrôle : il rougit sur une org mal rattachée, il verdit quand elle l'est ──

def _semer(dsn, nom, *, tenant_id, front_brand=None, membre=None, archivee=False):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        oid = c.execute(
            "INSERT INTO orgs (name, created_by, tenant_id, front_brand, archived_at) "
            "VALUES (%s, 'alice', %s, %s, CASE WHEN %s THEN NOW() END) RETURNING id",
            (nom, tenant_id, front_brand, archivee),
        ).fetchone()["id"]
        if membre:
            c.execute("INSERT INTO org_members (org_id, sub, org_role) "
                      "VALUES (%s, %s, 'org_admin')", (oid, membre))
    return oid


def _compte_orgs(dsn: str) -> int:
    """Le nombre RÉEL d'orgs en base — lu, jamais déduit des créations du test.

    Un test qui compte ses propres `create_org` raterait les orgs personnelles semées
    par le chemin nominal, et la somme de la portée paraîtrait fausse alors que c'est
    l'attente qui l'est."""
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        return int(c.execute("SELECT COUNT(*) AS n FROM orgs").fetchone()["n"])


def _fautives(ids_rapportees):
    return {o["id"] for o in ids_rapportees["orgs"]}


def test_une_org_non_declaree_est_rapportee_puis_ne_l_est_plus(base):
    """Les deux sens sur la MÊME org : rouge mal rattachée, vert une fois corrigée.

    C'est l'épreuve à rebours. Un contrôle qui ne rapporte jamais rien serait vert
    ici aussi — seule la bascule de l'un à l'autre prouve qu'il regarde vraiment.
    """
    from oto_mcp import db
    oid = _semer(base["dsn"], "Restée au défaut",
                 tenant_id=base["ids"]["oto"], front_brand="acme")

    avant = db.orgs_tenant_mismatches()
    assert oid in _fautives(avant), (
        "une org qui porte le tenant primaire alors que sa marque de front dit `acme` "
        "est exactement l'état des 65 orgs : le contrôle DOIT la nommer.")
    faute = next(o for o in avant["orgs"] if o["id"] == oid)
    assert (faute["declare"], faute["derive"]) == ("oto", "acme"), (
        "le rapport doit dire les DEUX valeurs — sans elles, on sait qu'il y a un "
        "écart mais pas vers quoi corriger.")
    assert faute["faute"] == "non_declaree"

    with psycopg.connect(base["dsn"], autocommit=True) as c:
        c.execute("UPDATE orgs SET tenant_id = %s WHERE id = %s",
                  (base["ids"]["acme"], oid))
    assert oid not in _fautives(db.orgs_tenant_mismatches()), (
        "rattachée correctement, l'org sort du rapport. Si elle y reste, le contrôle "
        "signale une faute qu'on ne peut pas corriger — il devient du bruit.")


def test_le_membre_qualifie_suffit_a_rendre_l_org_fautive(base):
    """L'axe (3) seul : ni marque de front, ni rattachement — juste un membre.

    C'est l'axe qui couvre l'angle mort historique de la marque (des orgs de
    partenaire nées avant que la dérivation soit confiée à l'écrivain unique).
    """
    from oto_mcp import db
    oid = _semer(base["dsn"], "Sans marque", tenant_id=base["ids"]["oto"],
                 membre="acme:carla")
    assert oid in _fautives(db.orgs_tenant_mismatches())


def test_le_controle_n_est_pas_une_tautologie(base):
    """⚠️ Le test qui tient tout le fichier — et il se joue sur le cas des 65.

    Si la dérivation repartait de `orgs.tenant_id` — le réflexe, puisque c'est
    l'expression déjà écrite (`_ORG_TENANT_EXPR`) et qu'elle COMMENCE par cet axe —
    alors `derive` vaudrait toujours `declare`, l'écart serait vide, et le contrôle
    sortirait vert **sur toute la plateforme, pour toujours**. Une org non déclarée
    est précisément ce qu'une dérivation tautologique ne peut pas produire.

    On l'exerce ici en confrontant les deux expressions sur la MÊME org : celle qui
    sert la question « à qui est cette org ? » rend `acme` (elle a le droit de partir
    du déclaré), celle qui JUGE doit rendre `acme` sans lui.
    """
    from oto_mcp import db
    oid = _semer(base["dsn"], "Restée au défaut",
                 tenant_id=base["ids"]["oto"], front_brand="acme")
    assert db.org_tenant_slug(oid) == "acme", (
        "prémisse : l'union des trois axes rattache bien cette org à acme.")
    fautes = {o["id"]: o for o in db.orgs_tenant_mismatches()["orgs"]}
    assert oid in fautes, (
        "le contrôle doit voir qu'acme n'est PAS déclaré. S'il ne le voit pas, sa "
        "dérivation part de `orgs.tenant_id` et ne rougira jamais.")
    assert fautes[oid]["faute"] == "non_declaree"


def test_une_declaration_contredite_est_rapportee(base):
    """La seconde faute : déclarée chez un partenaire, dérivée chez un autre."""
    from oto_mcp import db
    oid = _semer(base["dsn"], "Mal aiguillée",
                 tenant_id=base["ids"]["globex"], front_brand="acme")
    fautes = {o["id"]: o for o in db.orgs_tenant_mismatches()["orgs"]}
    assert oid in fautes and fautes[oid]["faute"] == "contredite", (
        "une org déclarée `globex` dont la marque de front dit `acme` est mal "
        "rattachée : les deux valeurs doivent remonter pour qu'on puisse trancher.")
    assert (fautes[oid]["declare"], fautes[oid]["derive"]) == ("globex", "acme")


def test_le_silence_de_la_derivation_n_accuse_personne(base):
    """Le troisième état, et pourquoi il n'est PAS une faute.

    Une org déclarée chez un partenaire sans marque de front ni membre qualifié :
    la dérivation ne dit rien. Rien n'est pas « elle est à nous ». Deux populations
    réelles vivent là — l'org d'un tenant sans `dashboard_url`, et l'org qui vient de
    naître (le rattachement est posé à l'INSERT, le premier membre arrive après).

    ⚠️ Ce test a été écrit APRÈS coup : la première version du contrôle repliait la
    dérivation sur le tenant primaire, et accusait donc toute org fraîchement créée
    par le chemin nominal. C'est `test_une_plateforme_saine_ne_rapporte_rien` qui l'a
    montré, pas la relecture.
    """
    from oto_mcp import db
    oid = _semer(base["dsn"], "Déclarée, non corroborée", tenant_id=base["ids"]["acme"])
    assert oid not in _fautives(db.orgs_tenant_mismatches()), (
        "la dérivation se tait sur cette org ; l'accuser reviendrait à lire une "
        "absence de signal comme une preuve du contraire.")


def test_une_org_archivee_est_jugee_comme_les_autres(base):
    """Une archivée reste rattachée à quelqu'un — et c'est sur elle qu'un faux
    rattachement survit le plus longtemps sans que personne le voie."""
    from oto_mcp import db
    oid = _semer(base["dsn"], "Ancienne", tenant_id=base["ids"]["oto"],
                 front_brand="acme", archivee=True)
    assert oid in _fautives(db.orgs_tenant_mismatches()), (
        "exclure les archivées du contrôle laisserait une population entière hors "
        "de sa portée, sans que le chiffre rendu le dise.")


def test_une_plateforme_saine_ne_rapporte_rien(base):
    """La vacuité : sur une population correcte, le rapport est VIDE.

    Sans ce test, tous les précédents seraient satisfaits par un contrôle qui
    rapporte l'univers.
    """
    from oto_mcp import db, org_store
    org_store.create_org("Chez nous", created_by="alice")
    org_store.create_org("Espace Acme", created_by="acme:carla")
    org_store.create_org("Espace Globex", created_by="globex:gina")
    rapport = db.orgs_tenant_mismatches()
    assert (rapport["total"], rapport["orgs"], rapport["tronque"]) == (0, [], False), (
        f"des orgs nées par le chemin nominal sont jugées fautives : {rapport}. "
        "Le contrôle et l'écrivain doivent tenir la MÊME règle — sinon toute org "
        "créée demain naît fautive.")


def test_le_rapport_dit_ce_quil_na_PAS_pu_juger(base):
    """`total: 0` veut dire « rien de FAUTIF parmi ce que j'ai su juger », pas « tout
    est conforme ». Le rapport doit donc porter sa PORTÉE.

    Sans elle, un zéro se lit comme un succès alors que la dérivation peut être muette
    sur la majorité de la table — c'est ce silence qui a laissé, en production, huit
    espaces personnels hors de la migration d'un partenaire sans que rien ne le
    signale. Trois états : conforme, fautif, et « je ne sais pas », compté à part.
    """
    from oto_mcp import db, org_store
    # Une org dont la dérivation PARLE (marque de front dérivée du créateur qualifié).
    org_store.create_org("Espace Acme", created_by="acme:carla")
    # Une org sur laquelle la dérivation se TAIT : créateur nu, aucune marque.
    _semer(base["dsn"], "Muette", tenant_id=base["ids"]["oto"], front_brand=None)

    rapport = db.orgs_tenant_mismatches()
    total_orgs = _compte_orgs(base["dsn"])
    assert rapport["jugees"] + rapport["indeterminees"] == total_orgs, (
        "la portée doit couvrir TOUTE la table : ce qui n'est ni jugé ni indéterminé "
        "n'existe nulle part, et disparaîtrait du rapport sans laisser de trace.")
    assert rapport["indeterminees"] >= 1, (
        "une org sans marque et sans membre qualifié n'est PAS jugeable ; la compter "
        "comme jugée ferait passer un silence pour un verdict.")
    assert rapport["jugees"] >= 1, (
        "une org dont la marque parle EST jugeable ; ne pas la compter viderait la "
        "portée de son sens.")


def test_le_total_est_compte_a_part_de_la_liste(base):
    """`total` compte la population, `orgs` en sert une page.

    Un plafond posé sur une lecture qui tronque déjà annoncerait le chiffre de la
    page — inatteignable, et vert pour toujours.
    """
    from oto_mcp import db
    for i in range(4):
        _semer(base["dsn"], f"Fautive {i}", tenant_id=base["ids"]["oto"],
               front_brand="acme")
    rapport = db.orgs_tenant_mismatches(limit=2)
    assert rapport["total"] == 4, "le total doit compter TOUTE la population fautive"
    assert len(rapport["orgs"]) == 2
    assert rapport["tronque"] is True, (
        "servir 2 sur 4 sans le dire fait lire « 2 orgs à corriger » à qui en a 4.")
