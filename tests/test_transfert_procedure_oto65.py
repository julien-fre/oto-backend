"""Une procédure se TRANSFÈRE d'une org à une autre sous son nom : `procedure` (oto#65).

Arbitrage d'Alexis du 23/09/2026 sur otomata-tech/oto#65 : « doctrine est l'ancien
terme, ne doit plus apparaître ». Le transfert, lui, existait — `oto_resource
op=transfer` déplace une procédure avec son historique depuis le 31/08 (#681) — mais
seulement sous le mot retiré : un agent qui écrivait `procedure` recevait un refus, et
rien de servi ne disait que le geste emportait l'historique.

Ce fichier tient trois choses :

1. **Le déplacement d'org A à org B, par le chemin SERVI** (autz déclarée puis
   handler, contre un vrai PostgreSQL) : même id, toutes les révisions, le lien de
   projet et le partage suivent. Jusqu'ici c'était prouvé entre paliers d'une MÊME org
   (`test_procedure_paliers_681.py`, par `ownership.transfer` en direct) et, d'org à
   org, par une sonde jamais commitée.
2. **L'alias daté** : un appelant vivant (l'écran de partage du dashboard) envoie
   encore l'ancien nom. Il est servi, sous le nom d'aujourd'hui, avec un avis qui dit
   quoi envoyer et jusqu'à quand.
3. **Le texte servi** : ni l'énuméré publié ni les descriptions ne portent l'ancien
   mot, et la description dit ce que `transfer` fait d'une procédure — et que la
   cascade d'un projet, elle, COPIE (#52).
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

from oto_mcp import deprecations
from oto_mcp.capabilities._types import RawCtx

ANCIEN = "doc" + "trine"   # la valeur d'hier, telle qu'un appelant l'envoie encore


@pytest.fixture(scope="module")
def monde(pg_dsn):
    """Base JETABLE bootée par le vrai `init_db` (recette de `test_procedure_paliers_681`) :
    deux orgs dont `u-admin` administre les deux, une troisième qui a reçu un partage."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_65_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    dbconn._pool = None
    try:
        from oto_mcp import org_store
        from oto_mcp.db import init_db
        init_db()
        org_a = org_store.create_org("Acme", created_by="u-admin")
        org_b = org_store.create_org("Beta", created_by="u-admin")
        cliente = org_store.create_org("Cliente", created_by="u-admin")
        org_store.add_org_member(org_a, "u-admin", "org_admin")
        org_store.add_org_member(org_b, "u-admin", "org_admin")
        org_store.set_active_org("u-admin", org_a)
        yield {"a": org_a, "b": org_b, "cliente": cliente}
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


def _cap(key: str):
    from oto_mcp.capabilities.registry import CAPABILITIES
    return next(c for c in CAPABILITIES if c.key == key)


def _appel(key: str, sub: str, **args) -> dict:
    """UN appel par le chemin servi : entrée validée, autz DÉCLARÉE, puis handler."""
    cap = _cap(key)
    inp = cap.Input(**args)
    out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def _procedure_chargee(monde, slug: str, versions: int = 5):
    """Une procédure d'org A avec un historique, un lien de projet et un partage."""
    from oto_mcp import db, org_store, ownership
    for i in range(versions):
        org_store.set_instruction("org", monde["a"], slug, f"# Relance\n\nrévision {i}\n",
                                  title="Relance", set_by="u-admin")
    proc = org_store.get_instruction("org", monde["a"], slug)
    projet = db.create_project("org", str(monde["a"]), f"Projet {slug}",
                               created_by="u-admin")
    db.add_project_link(projet, "procedure", str(proc["id"]), label="Relance")
    ownership.grant(ownership.TYPE_RESSOURCE_PROCEDURE, str(proc["id"]), "org",
                    str(monde["cliente"]), role="viewer", granted_by="u-admin")
    return proc, projet


# ── 1. Le déplacement d'une org à l'autre, par le chemin servi ───────────────

def test_transferer_une_procedure_d_une_org_a_l_autre_emporte_son_historique(monde):
    from oto_mcp import db, org_store, ownership
    proc, projet = _procedure_chargee(monde, "relance")
    avant = org_store.list_instruction_versions("org", monde["a"], "relance")
    assert len(avant) == 5

    out = _appel("resources.govern", "u-admin", op="transfer", resource_type="procedure",
                 resource_id=str(proc["id"]), new_owner_org=monde["b"])
    assert out["ok"] is True and out["resource_id"] == str(proc["id"])
    assert "deprecation_warning" not in out, "le nom d'aujourd'hui ne porte aucun avis"

    apres = org_store.get_instruction_by_id(proc["id"])
    assert apres["id"] == proc["id"], "même id : c'est un DÉPLACEMENT, pas une copie"
    assert (apres["owner_type"], apres["owner_id"]) == ("org", str(monde["b"]))
    assert apres["org_id"] == monde["b"]
    assert apres["version"] == 5 and "révision 4" in apres["body_md"]

    # L'historique a suivi, entier ; il n'est plus lisible sous l'org de départ.
    suivi = org_store.list_instruction_versions("org", monde["b"], "relance")
    assert [v["version"] for v in suivi] == [v["version"] for v in avant]
    assert org_store.list_instruction_versions("org", monde["a"], "relance") == []

    # Lien de projet et partage désignent le même id : ils n'ont rien à savoir.
    assert [(l["target_type"], l["target_ref"]) for l in db.list_project_links(projet)] \
        == [("procedure", str(proc["id"]))]
    fiche = _appel("resources.govern", "u-admin", op="get", resource_type="procedure",
                   resource_id=str(proc["id"]))
    assert fiche["resource_type"] == "procedure"
    assert (fiche["owner_type"], fiche["owner_id"]) == ("org", str(monde["b"]))
    assert ("org", str(monde["cliente"])) in {
        (g["principal_type"], g["principal_id"]) for g in fiche["grants"]}
    assert ownership.owner_of(ownership.TYPE_RESSOURCE_PROCEDURE, str(proc["id"])) \
        == ("org", str(monde["b"]))


def test_op_list_sert_les_procedures_sous_leur_nom(monde):
    _procedure_chargee(monde, "listee", versions=1)
    out = _appel("resources.govern", "u-admin", op="list", resource_type="procedure")
    assert out["resource_type"] == "procedure"
    assert out["resources"], "u-admin gouverne des procédures dans ses deux orgs"
    assert {r["resource_type"] for r in out["resources"]} == {"procedure"}


# ── 2. L'alias daté : servi, sous le nom d'aujourd'hui, avec son avis ────────

@pytest.mark.parametrize("key", ["resources.govern", "resources.govern.v2"])
def test_l_ancien_nom_est_servi_avec_un_avis_date(monde, key):
    proc, _ = _procedure_chargee(monde, "alias-" + key.replace(".", "-"), versions=1)
    out = _appel(key, "u-admin", op="get", resource_type=ANCIEN,
                 resource_id=str(proc["id"]))
    assert out["resource_type"] == "procedure", "la réponse ne sert que le nom d'aujourd'hui"
    avis = out["deprecation_warning"]
    assert "resource_type=procedure" in avis
    assert deprecations.RETRAIT_PROCEDURE.strftime("%d/%m/%Y") in avis


def test_l_alias_couvre_aussi_le_transfert(monde):
    """Le geste qui compte : l'écran du dashboard partage et transfère sous l'ancien
    nom. L'autz et `ownership` ne voient que la famille d'aujourd'hui."""
    from oto_mcp import org_store
    proc, _ = _procedure_chargee(monde, "relance-bis", versions=2)
    out = _appel("resources.govern", "u-admin", op="transfer", resource_type=ANCIEN,
                 resource_id=str(proc["id"]), new_owner_org=monde["b"])
    assert out["ok"] is True and "deprecation_warning" in out
    assert org_store.get_instruction_by_id(proc["id"])["owner_id"] == str(monde["b"])


def test_la_date_de_retrait_tient_le_preavis_contractuel():
    assert deprecations.RETRAIT_PROCEDURE == deprecations._plus_de_mois(
        deprecations.ANNONCE_PROCEDURE, deprecations.PREAVIS_MOIS)


# ── 3. Le texte servi ────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["resources.govern", "resources.govern.v2"])
def test_la_description_dit_deplacement_et_copie_en_cascade(key):
    d = _cap(key).description
    assert "resource_type=procedure" in d
    assert "MOVES the procedure with its history" in d
    assert "every revision, its project links and its shares" in d
    assert "COPIES its linked procedures" in d


@pytest.mark.parametrize("key", ["resources.govern", "resources.govern.v2"])
def test_rien_de_servi_ne_porte_l_ancien_nom(key):
    """Descriptions, schéma d'entrée et contrat de sortie : l'ancien mot n'est plus
    publié nulle part — l'alias l'ACCEPTE, il ne l'ENSEIGNE pas."""
    cap = _cap(key)
    servi = json.dumps([cap.description, cap.Input.model_json_schema(),
                        cap.Output.model_json_schema(),
                        [e.when for e in cap.errors]], ensure_ascii=False)
    assert ANCIEN not in servi.lower()
