"""La migration du rattachement d'org, contre un VRAI PostgreSQL.

`scripts/migrate_org_tenant.py` réécrit `orgs.tenant_id` sur les données d'un
partenaire. Trois propriétés à prouver, et aucune ne se prouve sans base :

1. **le décompte à blanc n'écrit RIEN** — c'est la promesse qui autorise à le jouer
   en prod pour décider ;
2. **il annonce exactement ce qu'il touchera** — les lignes affichées sont celles que
   `--apply` repointe, ni plus ni moins. On le vérifie en comparant les deux modes
   sur la même population ;
3. **les deux refus mordent** — un désaccord entre les dérivations, et une org déjà
   déclarée que sa dérivation contredit, arrêtent la migration AVANT toute écriture.

⚠️ Le décompte à blanc et l'application partagent le même `UPDATE … RETURNING` : le
premier le défait par `ROLLBACK`. Un test qui exercerait une requête de comptage
écrite à côté ne dirait rien de la migration — il dirait que deux SQL différents se
ressemblent, ce qui est exactement le mode de panne qu'on ferme.

Sauté proprement sans PostgreSQL joignable (fixture `pg_dsn`).
"""
from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from oto_mcp import tenancy

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
            tenants=[{"slug": "acme", "issuer": _ACME_ISS},
                     {"slug": "globex", "issuer": _GLOBEX_ISS}])), raising=False)
        with psycopg_.connect(dsn, row_factory=dict_row, autocommit=True) as c:
            c.execute("INSERT INTO tenants (slug, name, issuer, dashboard_url) VALUES "
                      "('acme', 'Acme', %s, NULL), "
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


def _org(dsn, nom, *, tenant, front_brand=None, membre=None, archivee=False):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        oid = c.execute(
            "INSERT INTO orgs (name, created_by, tenant_id, front_brand, archived_at) "
            "VALUES (%s, 'alice', %s, %s, CASE WHEN %s THEN NOW() END) RETURNING id",
            (nom, tenant, front_brand, archivee)).fetchone()["id"]
        if membre:
            c.execute("INSERT INTO org_members (org_id, sub, org_role) "
                      "VALUES (%s, %s, 'org_admin')", (oid, membre))
    return oid


def _rattachements(dsn) -> dict:
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        return {r["id"]: r["tenant_id"] for r in
                c.execute("SELECT id, tenant_id FROM orgs").fetchall()}


@pytest.fixture()
def population(base):
    """La forme de la prod, en miniature : des orgs du partenaire non déclarées
    (par la marque, par un membre, archivée), et des orgs à nous."""
    d, ids = base["dsn"], base["ids"]
    return {
        "marque": _org(d, "Par la marque", tenant=ids["oto"], front_brand="acme"),
        "membre": _org(d, "Par un membre", tenant=ids["oto"], membre="acme:carla"),
        "archivee": _org(d, "Archivée", tenant=ids["oto"], front_brand="acme",
                         archivee=True),
        "globex": _org(d, "Chez globex", tenant=ids["oto"], membre="globex:gina"),
        "nous": _org(d, "Chez nous", tenant=ids["oto"]),
        "deja": _org(d, "Déjà déclarée", tenant=ids["acme"], front_brand="acme"),
    }


def test_le_decompte_a_blanc_n_ecrit_rien(base, population, capsys):
    """La promesse qui autorise à le jouer en prod pour décider."""
    from scripts import migrate_org_tenant
    avant = _rattachements(base["dsn"])
    assert migrate_org_tenant.main(apply=False) == 0
    assert _rattachements(base["dsn"]) == avant, (
        "le décompte à blanc a MODIFIÉ des lignes. C'est la seule chose qu'il ne doit "
        "jamais faire : on le joue sur les données d'un partenaire pour décider.")
    sortie = capsys.readouterr().out
    assert "rien n'a été écrit" in sortie


def test_le_decompte_a_blanc_annonce_exactement_ce_qu_il_touchera(base, population,
                                                                 capsys):
    """Les ids annoncés à blanc == les lignes réellement repointées par --apply.

    C'est la propriété qui rend le décompte utilisable comme base de décision. On la
    prouve en confrontant les deux modes, pas en relisant le SQL.
    """
    from scripts import migrate_org_tenant
    migrate_org_tenant.main(apply=False)
    annonces = capsys.readouterr().out
    ids_annonces = set()
    for ligne in annonces.splitlines():
        if ligne.strip().startswith("ids :"):
            ids_annonces |= {int(x) for x in ligne.split("ids :")[1].split(",")}

    avant = _rattachements(base["dsn"])
    assert migrate_org_tenant.main(apply=True) == 0
    apres = _rattachements(base["dsn"])
    reellement_touchees = {i for i in avant if avant[i] != apres[i]}

    assert ids_annonces == reellement_touchees, (
        f"annoncé {sorted(ids_annonces)}, touché {sorted(reellement_touchees)}. Un "
        "décompte à blanc qui annonce autre chose que ce qu'il fait est pire qu'aucun "
        "décompte : on décide sur lui.")
    assert reellement_touchees == {population["marque"], population["membre"],
                                   population["archivee"], population["globex"]}


def test_l_application_repointe_puis_n_a_plus_rien_a_faire(base, population, capsys):
    """Idempotence : rejouer ne retouche rien, et le dit."""
    from scripts import migrate_org_tenant
    assert migrate_org_tenant.main(apply=True) == 0
    etat = _rattachements(base["dsn"])
    ids = base["ids"]
    assert etat[population["marque"]] == ids["acme"]
    assert etat[population["membre"]] == ids["acme"]
    assert etat[population["archivee"]] == ids["acme"], (
        "une org archivée reste rattachée à quelqu'un : l'exclure la laisserait "
        "porter un faux rattachement pour toujours.")
    assert etat[population["globex"]] == ids["globex"]
    assert etat[population["nous"]] == ids["oto"], (
        "une org que rien ne rattache à un partenaire ne doit PAS bouger.")

    capsys.readouterr()
    assert migrate_org_tenant.main(apply=True) == 0
    assert _rattachements(base["dsn"]) == etat
    assert "rien à faire" in capsys.readouterr().out


def test_apres_migration_le_controle_de_conformite_est_vide(base, population):
    """La boucle se ferme : ce que la migration écrit, le garde-fou l'accepte."""
    from scripts import migrate_org_tenant
    from oto_mcp import db
    assert db.orgs_tenant_mismatches()["total"] == 4, (
        "prémisse : les 4 orgs du partenaire restées au défaut, et elles seules — "
        "« Chez nous » n'a aucun signal partenaire, « Déjà déclarée » est correcte. "
        "Le compte doit être connu d'avance, sinon l'assertion d'après ne prouve rien.")
    migrate_org_tenant.main(apply=True)
    assert db.orgs_tenant_mismatches() == {"total": 0, "orgs": [], "tronque": False}


# ── Les deux refus ───────────────────────────────────────────────────────────

def test_un_desaccord_entre_derivations_arrete_tout(base, population, capsys):
    """⚠️ Le refus qui porte le lot : c'est la CONCORDANCE des deux axes qui autorise
    à écrire sans deviner. Une seule org en désaccord et on ne devine plus, on
    s'arrête — y compris pour les 4 autres, qui étaient pourtant sûres."""
    from scripts import migrate_org_tenant
    _org(base["dsn"], "Contradictoire", tenant=base["ids"]["oto"],
         front_brand="acme", membre="globex:gina")
    avant = _rattachements(base["dsn"])
    assert migrate_org_tenant.main(apply=True) == 2
    assert _rattachements(base["dsn"]) == avant, (
        "le refus doit tomber AVANT toute écriture — pas après en avoir repointé "
        "quelques-unes.")
    sortie = capsys.readouterr().out
    assert "REFUS" in sortie and "marque=acme" in sortie and "membre=globex" in sortie


def test_une_org_declaree_et_contredite_arrete_tout(base, population, capsys):
    """L'écraser effacerait l'information qui dit qu'il y a un problème."""
    from scripts import migrate_org_tenant
    _org(base["dsn"], "Mal aiguillée", tenant=base["ids"]["globex"],
         front_brand="acme")
    avant = _rattachements(base["dsn"])
    assert migrate_org_tenant.main(apply=True) == 2
    assert _rattachements(base["dsn"]) == avant
    assert "déclaré=globex" in capsys.readouterr().out


def test_le_refus_ne_se_confond_pas_avec_un_succes(base, population):
    """Un code de sortie distinct : 2 pour un refus, 0 pour un passage.

    Sans lui, un opérateur qui enchaîne les commandes lit « c'est passé » sur un
    arrêt — le zéro qui ressemble à un succès.
    """
    from scripts import migrate_org_tenant
    assert migrate_org_tenant.main(apply=False) == 0
    _org(base["dsn"], "Contradictoire", tenant=base["ids"]["oto"],
         front_brand="acme", membre="globex:gina")
    assert migrate_org_tenant.main(apply=False) == 2
