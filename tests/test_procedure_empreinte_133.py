"""L'écriture d'une procédure rend l'empreinte du corps STOCKÉ (oto#133).

Avant : rien dans la réponse ne permettait de vérifier que le texte enregistré était
celui qu'on avait envoyé — le contrôle manuel qui l'a fait a trouvé dix-huit lignes
servies aux agents qu'aucun source ne contenait.

Contre un vrai PostgreSQL et par le CHEMIN SERVI (capacité + autz déclarée) : ce qu'on
prouve, c'est que l'empreinte est celle de ce que la base a gardé. Le corps envoyé finit
par un saut de ligne que le store retire — l'empreinte rendue doit donc être celle du
corps stocké, et PAS celle de l'envoi brut.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid

import pytest

from oto_mcp.capabilities._types import RawCtx

_CORPS = "# Relance\n\n## Étapes\n\nRelancer le client.\n"


def _sha(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def monde(pg_dsn):
    """Base JETABLE bootée par le vrai `init_db` (recette de `test_procedure_paliers_681`)."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_133_" + uuid.uuid4().hex[:8]
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
        org_store.set_active_org("u-admin", org)
        equipe = group_store.create_group(org, "Compta")
        group_store.add_group_member(equipe, "u-admin", "group_admin")
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


def _appel(key: str, sub: str, **args):
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next(c for c in CAPABILITIES if c.key == key)
    inp = cap.Input(**args)
    out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def _stocke(owner_type, owner_id, slug) -> str:
    from oto_mcp import org_store
    return org_store.get_instruction(owner_type, owner_id, slug)["body_md"]


def test_oto_procedure_set_rend_l_empreinte_du_corps_stocke(monde):
    out = _appel("org.procedure.console", "u-admin", op="set", scope="org",
                 slug="relance", body_md=_CORPS)
    assert out["body_sha256"] == _sha(_stocke("org", monde["org"], "relance"))
    # Le store retire les blancs de fin : l'empreinte est celle du corps GARDÉ, pas de
    # l'envoi — sans quoi elle ne prouverait rien de ce que la base sert.
    assert out["body_sha256"] == _sha(_CORPS.strip())
    assert out["body_sha256"] != _sha(_CORPS)


def test_la_creation_rend_aussi_l_empreinte(monde):
    out = _appel("org.procedure.console", "u-admin", op="create", scope="org",
                 slug="nouvelle", body_md=_CORPS + "Encore.\n")
    assert out["body_sha256"] == _sha(_stocke("org", monde["org"], "nouvelle"))


def test_la_face_rest_de_l_equipe_rend_l_empreinte(monde):
    out = _appel("group.instruction.set", "u-admin", group_id=monde["equipe"],
                 slug="cloture", body_md=_CORPS)
    assert out["body_sha256"] == _sha(_stocke("group", monde["equipe"], "cloture"))


def test_l_empreinte_est_celle_de_la_version_ecrite(monkeypatch):
    """Relue sur la RÉVISION de la version posée, pas sur la ligne vivante : une édition
    concurrente arrivée entre l'écriture et la relecture ne doit pas prêter son corps."""
    from oto_mcp import org_store, procedure_empreinte
    corps = {3: "le mien", 4: "celui du voisin"}
    monkeypatch.setattr(org_store, "get_instruction",
                        lambda ot, oid, slug, version=None: {"body_md": corps[version or 4]})
    assert procedure_empreinte.empreinte_check("org", 1, "s", 3) == {
        "body_sha256": _sha("le mien")}


def test_une_version_introuvable_leve_plutot_que_de_rendre_une_preuve_vide(monkeypatch):
    from oto_mcp import org_store, procedure_empreinte
    monkeypatch.setattr(org_store, "get_instruction", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="introuvable"):
        procedure_empreinte.empreinte_check("org", 1, "s", 3)
