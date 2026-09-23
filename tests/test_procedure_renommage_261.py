"""Renommer une procédure sans perdre son identité (issue `oto`#261).

**Le défaut.** La liste des opérations d'`oto_procedure` était fermée et aucune ne
changeait le slug. Le seul chemin — créer sous le nouveau nom, supprimer l'ancien —
frappait deux invariants à la fois : l'`id` stable dont dépendent les liens de projet
et les partages (`project_links.target_ref`, `resource_grants.resource_id`), et
l'historique de versions, que la suppression emporte sans corbeille.

**Ce que ce fichier fige :**

1. `rename` change le slug et GARDE l'`id`, la version, le corps et TOUT
   l'historique (chaque révision est relisible sous le nouveau nom) ;
2. l'ancien slug ne résout plus : pas d'alias implicite, un 404 franc ;
3. le lien de projet et la lecture par `guide_id` survivent au renommage — et la
   lecture par slug REND cet id (elle ne le rendait pas : l'identité à préserver
   était illisible) ;
4. un slug déjà pris est refusé (`slug_taken`), rien n'est écrasé ;
5. le RUNNER suit le nouveau nom dans la même transaction — déclencheur, campagne et
   travaux en attente (instruction de départ comprise) ; un travail déjà pris est
   nommé, jamais réécrit ; un déclencheur dont le slug résout vers une AUTRE
   procédure (la personnelle de son auteur) ne suit pas ;
6. la face REST (`POST /api/me/instructions/{slug}/rename`) et la console sont le
   même geste ;
7. palier équipe : un MEMBRE renomme (le geste se défait en renommant de nouveau).

**Contre un vrai PostgreSQL, sur le CHEMIN SERVI** — même recette que #27/#662 : le
geste passe par la capacité avec sa règle d'autz déclarée.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from oto_mcp.capabilities._types import AuthzDenied, RawCtx


@pytest.fixture(scope="module")
def monde(pg_dsn):
    """Base JETABLE bootée par le vrai `init_db` — même recette que #27/#662."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_261_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    dbconn._pool = None
    try:
        from oto_mcp import group_store, org_store
        from oto_mcp.db import init_db
        init_db()
        org = org_store.create_org("Acme", created_by="u-admin")
        org_store.add_org_member(org, "u-admin", "org_admin")
        org_store.add_org_member(org, "u-membre", "org_member")
        equipe = group_store.create_group(org, "Compta")
        group_store.add_group_member(equipe, "u-membre", "group_member")
        for sub in ("u-admin", "u-membre"):
            org_store.set_active_org(sub, org)
        group_store.set_active_group("u-membre", equipe)
        yield {"org": org, "equipe": equipe}
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


def _capacite(cle: str, sub: str, **args):
    """UN appel d'une capacité par le chemin servi : autz DÉCLARÉE puis handler."""
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next(c for c in CAPABILITIES if c.key == cle)
    inp = cap.Input(**args)
    out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)      # ← la porte
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def _appel(sub: str, **args):
    return _capacite("org.procedure.console", sub, **args)


def _slug(prefixe: str) -> str:
    return f"{prefixe}-{uuid.uuid4().hex[:6]}"


def _deux_versions(slug: str, **axes) -> dict:
    _appel("u-admin", op="create", slug=slug, body_md="Version UN.", title="T", **axes)
    _appel("u-admin", op="set", slug=slug, body_md="Version DEUX.", **axes)
    return _appel("u-admin", op="get", slug=slug, **axes)


# ── 1. L'identité et l'historique survivent ─────────────────────────────────

def test_renommer_garde_l_id_la_version_et_tout_l_historique(monde):
    ancien, neuf = _slug("cloture"), _slug("cloture-annuelle")
    avant = _deux_versions(ancien, scope="org")

    out = _appel("u-admin", op="rename", slug=ancien, new_slug=neuf, scope="org")
    assert out["ok"] is True
    assert (out["guide_id"], out["slug"], out["previous_slug"]) == (avant["guide_id"], neuf, ancien)

    apres = _appel("u-admin", op="get", slug=neuf, scope="org")
    assert apres["guide_id"] == avant["guide_id"]
    assert apres["version"] == avant["version"] == 2, "renommer n'est pas une écriture"
    assert apres["body_md"] == "Version DEUX."
    v1 = _appel("u-admin", op="get", slug=neuf, scope="org", version=1)
    assert v1["body_md"] == "Version UN.", "l'historique doit suivre le nouveau nom"

    from oto_mcp import org_store
    versions = org_store.list_instruction_versions("org", monde["org"], neuf)
    assert [v["version"] for v in versions] == [2, 1]
    assert org_store.list_instruction_versions("org", monde["org"], ancien) == []


def test_l_ancien_slug_ne_resout_plus(monde):
    ancien, neuf = _slug("a"), _slug("b")
    _deux_versions(ancien, scope="org")
    _appel("u-admin", op="rename", slug=ancien, new_slug=neuf, scope="org")
    with pytest.raises(AuthzDenied) as e:
        _appel("u-admin", op="get", slug=ancien, scope="org")
    assert e.value.status == 404


def test_le_lien_de_projet_et_la_lecture_par_id_survivent(monde):
    from oto_mcp import db
    ancien, neuf = _slug("liee"), _slug("liee-renommee")
    avant = _deux_versions(ancien, scope="org")
    pid = db.create_project("org", str(monde["org"]), "Mission", created_by="u-admin")
    db.add_project_link(pid, "procedure", str(avant["guide_id"]))

    _appel("u-admin", op="rename", slug=ancien, new_slug=neuf, scope="org")

    liens = [l for l in db.list_project_links(pid) if l["target_type"] == "procedure"]
    assert [l["target_ref"] for l in liens] == [str(avant["guide_id"])]
    par_id = _appel("u-admin", op="get", guide_id=avant["guide_id"])
    assert par_id["slug"] == neuf


# ── 2. Les refus ────────────────────────────────────────────────────────────

def test_un_slug_pris_est_refuse_sans_rien_ecraser(monde):
    a, b = _slug("a"), _slug("b")
    _deux_versions(a, scope="org")
    _appel("u-admin", op="create", slug=b, body_md="L'autre.", scope="org")
    with pytest.raises(AuthzDenied) as e:
        _appel("u-admin", op="rename", slug=a, new_slug=b, scope="org")
    assert (e.value.status, e.value.code) == (409, "slug_taken")
    assert _appel("u-admin", op="get", slug=b, scope="org")["body_md"] == "L'autre."
    assert _appel("u-admin", op="get", slug=a, scope="org")["body_md"] == "Version DEUX."


def test_slug_absent_404_et_meme_slug_400(monde):
    with pytest.raises(AuthzDenied) as e:
        _appel("u-admin", op="rename", slug=_slug("fantome"), new_slug=_slug("x"),
               scope="org")
    assert e.value.status == 404
    a = _slug("a")
    _deux_versions(a, scope="org")
    with pytest.raises(AuthzDenied) as e:
        _appel("u-admin", op="rename", slug=a, new_slug=a.upper(), scope="org")
    assert (e.value.status, e.value.code) == (400, "same_slug")
    with pytest.raises(AuthzDenied) as e:
        _appel("u-admin", op="rename", slug=a, scope="org")
    assert (e.value.status, e.value.code) == (400, "missing_new_slug")


# ── 3. Le runner suit le nouveau nom ─────────────────────────────────────────

def _instruction_derivee(slug: str) -> str:
    from oto_mcp.capabilities import _instruction
    return _instruction.derivee(slug)


def test_l_automatisation_la_campagne_et_les_travaux_suivent_le_nouveau_nom(monde):
    """Le runner désigne la procédure par son SLUG (`runner_triggers.procedure`,
    `runner_fleets.procedure`, `runner_jobs.payload`) et son instruction de départ le
    cite : renommer sous lui, c'était un agent planifié qui cherche un nom disparu."""
    from oto_mcp import db
    from oto_mcp.capabilities import _instruction
    ancien, neuf = _slug("planifiee"), _slug("planifiee-neuve")
    _deux_versions(ancien, scope="org")
    trig = db.create_trigger(monde["org"], "u-admin", procedure=ancien,
                             tz="Europe/Paris", tools=["oto_procedure"],
                             cron="0 8 * * *", input=_instruction.derivee(ancien))
    flotte = db.create_fleet(monde["org"], "u-admin", label="passage", procedure=ancien,
                             tools=["oto_procedure"], namespace="leads",
                             input=_instruction.de_file(ancien, "leads", None))
    attente = db.enqueue_job(monde["org"], "start", sub="u-admin", payload={
        "procedure": ancien, "input": _instruction.derivee(ancien), "tools": []})
    en_vol = db.enqueue_job(monde["org"], "start", sub="u-admin", payload={
        "procedure": ancien, "input": _instruction.derivee(ancien), "tools": []})
    with db._connect() as conn:
        conn.execute("UPDATE runner_jobs SET status = 'claimed' WHERE id = %s",
                     (en_vol["id"],))

    out = _appel("u-admin", op="rename", slug=ancien, new_slug=neuf, scope="org")

    assert out["runner"]["triggers"] == [trig["id"]]
    assert out["runner"]["fleets"] == [flotte["id"]]
    assert out["runner"]["jobs"] == [attente["id"]]
    assert out["runner"]["in_flight_jobs"] == [en_vol["id"]]
    assert out["runner"]["inputs_to_review"] == []
    t = db.get_trigger(trig["id"], monde["org"])
    assert (t["procedure"], t["input"]) == (neuf, _instruction.derivee(neuf))
    f = db.get_fleet(flotte["id"], monde["org"])
    assert (f["procedure"], f["input"]) == (neuf, _instruction.de_file(neuf, "leads", None))
    j = db.get_job(attente["id"], monde["org"])
    assert (j["payload"]["procedure"], j["payload"]["input"]) == (
        neuf, _instruction.derivee(neuf))
    # Le travail déjà PRIS tourne avec ce qu'il a lu : nommé, jamais réécrit.
    assert db.get_job(en_vol["id"], monde["org"])["payload"]["procedure"] == ancien


def test_une_instruction_libre_qui_cite_l_ancien_nom_est_nommee(monde):
    from oto_mcp import db
    ancien, neuf = _slug("libre"), _slug("libre-neuve")
    _deux_versions(ancien, scope="org")
    trig = db.create_trigger(monde["org"], "u-admin", procedure=ancien,
                             tz="Europe/Paris", tools=["oto_procedure"],
                             cron="0 8 * * *", input=f"Déroule {ancien} chaque matin.")
    out = _appel("u-admin", op="rename", slug=ancien, new_slug=neuf, scope="org")
    assert out["runner"]["triggers"] == [trig["id"]]
    assert out["runner"]["inputs_to_review"] == [f"triggers:{trig['id']}"]


def test_un_declencheur_dont_le_slug_resout_ailleurs_ne_suit_pas(monde):
    """La cascade de lecture de l'agent passe par SA procédure personnelle avant celle
    de l'org : un déclencheur de quelqu'un qui porte le même slug chez lui désigne
    celle-là, et renommer celle de l'org ne doit pas le déplacer."""
    from oto_mcp import db
    ancien = _slug("homonyme")
    _deux_versions(ancien, scope="org")
    _appel("u-membre", op="create", slug=ancien, body_md="La mienne.", scope="user")
    trig = db.create_trigger(monde["org"], "u-membre", procedure=ancien,
                             tz="Europe/Paris", tools=["oto_procedure"], cron="0 8 * * *")
    out = _appel("u-admin", op="rename", slug=ancien, new_slug=_slug("b"), scope="org")
    assert out["runner"]["triggers"] == []
    assert db.get_trigger(trig["id"], monde["org"])["procedure"] == ancien


# ── 4. Les faces et les paliers ─────────────────────────────────────────────

def test_la_face_rest_est_le_meme_geste(monde):
    ancien, neuf = _slug("rest"), _slug("rest-neuf")
    avant = _deux_versions(ancien, scope="org")
    out = _capacite("org.instruction.rename", "u-admin", slug=ancien, new_slug=neuf)
    assert (out["guide_id"], out["slug"], out["org_id"]) == (avant["guide_id"], neuf, monde["org"])


def test_un_membre_renomme_la_procedure_de_son_equipe(monde):
    ancien, neuf = _slug("equipe"), _slug("equipe-neuf")
    _appel("u-membre", op="create", slug=ancien, body_md="Corps.", scope="group")
    out = _appel("u-membre", op="rename", slug=ancien, new_slug=neuf, scope="group")
    assert (out["slug"], out["scope"]) == (neuf, "group")
    assert _appel("u-membre", op="get", slug=neuf, scope="group")["body_md"] == "Corps."


def test_un_membre_ne_renomme_pas_la_procedure_de_l_org(monde):
    a = _slug("org")
    _deux_versions(a, scope="org")
    with pytest.raises(AuthzDenied) as e:
        _appel("u-membre", op="rename", slug=a, new_slug=_slug("b"), scope="org")
    assert e.value.status == 403
