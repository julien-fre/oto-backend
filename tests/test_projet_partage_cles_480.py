"""Projet PARTAGÉ : le bénéficiaire travaille avec SES clés (#480, arbitrage du 23/09).

Le trou : `_project=` co-pose l'org propriétaire comme contexte d'appel, et le barreau
ORG de la cascade n'était gardé par aucune appartenance — un bénéficiaire hors de
l'org agissait sous ses clés d'org, sans que personne l'ait décidé.

La règle : ses propres clés par défaut ; celles du propriétaire seulement si le
partageur les prête (`credentials="inherit"`), borné à ses propres droits, révocable.
**Iso sur les trois types de bénéficiaire** (personne, équipe, org) : chaque test de
comportement est paramétré sur les trois, et c'est ce paramétrage qui prouve l'iso.

Exercé en SQL réel (walker, cascade, partage) : la faille vivait dans l'assemblage
— pose de l'axe, contexte, barreau — qu'aucun stub ne rejoue.
"""
from __future__ import annotations

import os
import uuid

import pytest

from oto_mcp import call_axes, session_org

CONNECTEUR = "lemlist"          # byo_user + byo_org, sans palier plateforme
TYPES = ("person", "team", "org")


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_cles480_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "4" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url),
                            ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture
def monde(live):
    """Org A (propriétaire du projet, détient une clé d'org) ; org B, où vit le
    bénéficiaire P, avec une équipe G qui le contient. Le partageur est membre des
    deux (un partage d'équipe exige d'être membre de l'org de l'équipe)."""
    from oto_mcp import db, group_store, org_store
    u = uuid.uuid4().hex[:8]
    owner, benef = f"owner_{u}", f"benef_{u}"
    for s in (owner, benef):
        db.upsert_user(s, email=f"{s}@example.test")
    org_a = org_store.create_org(f"a_{u}", created_by=owner)
    org_b = org_store.create_org(f"b_{u}", created_by=benef)
    org_store.add_org_member(org_a, owner, "org_admin")
    org_store.add_org_member(org_b, owner)
    org_store.add_org_member(org_b, benef)
    team = group_store.create_group(org_b, f"g_{u}")
    group_store.add_group_member(team, benef)
    org_store.set_org_secret(org_a, CONNECTEUR, "CLE-DE-L-ORG-A", set_by=owner)
    pid = db.create_project("org", str(org_a), f"p_{u}", created_by=owner)
    return {"owner": owner, "benef": benef, "org_a": org_a, "org_b": org_b,
            "team": team, "pid": pid}


def _destinataire(m: dict, type_: str) -> dict:
    return {"person": {"email": f"{m['benef']}@example.test"},
            "team": {"group_id": m["team"]},
            "org": {"org_id": m["org_b"]}}[type_]


def _partager(m: dict, type_: str, **kw):
    from oto_mcp.capabilities import resources as R
    from oto_mcp.capabilities._types import ResolvedCtx
    return R._resources(ResolvedCtx(sub=m["owner"], org_id=m["org_a"]),
                        R.ResourceInput(op="share", resource_type="project",
                                        resource_id=str(m["pid"]), role="editor",
                                        **_destinataire(m, type_), **kw))


def _resoudre_dans_le_projet(m: dict, monkeypatch):
    """Ce que P résout, en appelant AVEC `_project=` — la vraie pose de l'axe."""
    import asyncio

    from oto_mcp import access
    monkeypatch.setattr(call_axes, "require_axis_sub", lambda axis: m["benef"])
    monkeypatch.setattr(session_org, "current_subdomain_candidate", lambda: None)
    axe = next(a for a in call_axes.AXES if a.param == "_project")

    async def _appel():
        # Pose et résolution dans le MÊME contexte, comme le middleware d'appel.
        undo = await axe.pin(m["pid"])
        try:
            return access.resolve_credential(CONNECTEUR, "byo", sub=m["benef"],
                                             emit_on_failure=False)
        finally:
            for reset, tok in reversed(undo):
                reset(tok)
    return asyncio.run(_appel())


# ── La faille fermée : rouges sur le code d'avant, un par type ─────────────────

@pytest.mark.parametrize("type_", TYPES)
def test_le_beneficiaire_hors_org_n_atteint_pas_la_cle_d_org_du_proprietaire(
        monde, monkeypatch, type_):
    from oto_mcp.mcp_errors import McpError
    _partager(monde, type_)
    with pytest.raises(McpError) as e:
        rc = _resoudre_dans_le_projet(monde, monkeypatch)
        pytest.fail(f"clé servie au bénéficiaire ({type_}) : mode={rc.mode} "
                    f"entité={rc.entity_type}:{rc.entity_id}")
    # Le refus dit quoi faire : poser SA clé, ou demander l'héritage au partageur.
    msg = e.value.error.message
    assert "credentials='inherit'" in msg and "propre clé" in msg


@pytest.mark.parametrize("type_", TYPES)
def test_le_beneficiaire_travaille_avec_sa_propre_cle(monde, monkeypatch, type_):
    """Sa clé posée chez lui (org B) le suit dans le projet — et prime : l'ancien code
    lui servait la clé d'org du propriétaire alors même qu'il avait la sienne."""
    from oto_mcp import db
    db.set_member_api_key(monde["benef"], monde["org_b"], CONNECTEUR, "SA-CLE")
    _partager(monde, type_)
    rc = _resoudre_dans_le_projet(monde, monkeypatch)
    assert (rc.mode, rc.entity_id) == ("user", f"{monde['org_b']}:{monde['benef']}")


# ── L'héritage : déclaré, visible, borné, révocable — le même pour les trois ────

@pytest.mark.parametrize("type_", TYPES)
def test_l_heritage_declare_prete_la_cle_puis_se_revoque(monde, monkeypatch, type_):
    from oto_mcp.capabilities import resources as R
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.mcp_errors import McpError

    out = _partager(monde, type_, credentials="inherit")
    assert out["credentials"] == "inherit"
    rc = _resoudre_dans_le_projet(monde, monkeypatch)
    assert (rc.mode, rc.entity_id) == ("org", str(monde["org_a"]))

    # Visible dans la lecture du partage.
    vue = R._resources(ResolvedCtx(sub=monde["owner"], org_id=monde["org_a"]),
                       R.ResourceInput(op="get", resource_type="project",
                                       resource_id=str(monde["pid"])))
    assert [g["credentials"] for g in vue["grants"]] == ["inherit"]

    # Un re-partage muet (changer le rôle) ne retire pas le prêt…
    _partager(monde, type_)
    assert _resoudre_dans_le_projet(monde, monkeypatch).mode == "org"
    # …`own` le retire, sans retirer l'accès.
    _partager(monde, type_, credentials="own")
    with pytest.raises(McpError):
        _resoudre_dans_le_projet(monde, monkeypatch)


@pytest.mark.parametrize("type_", TYPES)
def test_l_heritage_s_eteint_avec_les_droits_du_partageur(monde, monkeypatch, type_):
    """La borne se relit à chaque appel : le partageur sorti de l'org propriétaire,
    son prêt ne vaut plus rien."""
    from oto_mcp import org_store
    from oto_mcp.mcp_errors import McpError
    _partager(monde, type_, credentials="inherit")
    org_store.remove_org_member(monde["org_a"], monde["owner"])
    with pytest.raises(McpError):
        _resoudre_dans_le_projet(monde, monkeypatch)


@pytest.mark.parametrize("type_", TYPES)
def test_unshare_retire_aussi_le_pret(monde, monkeypatch, type_):
    """Une arête laissée vivante rendrait l'héritage au premier re-partage."""
    from oto_mcp.capabilities import resources as R
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.mcp_errors import McpError
    _partager(monde, type_, credentials="inherit")
    R._resources(ResolvedCtx(sub=monde["owner"], org_id=monde["org_a"]),
                 R.ResourceInput(op="unshare", resource_type="project",
                                 resource_id=str(monde["pid"]),
                                 **_destinataire(monde, type_)))
    _partager(monde, type_)
    with pytest.raises(McpError):
        _resoudre_dans_le_projet(monde, monkeypatch)


@pytest.mark.parametrize("type_", TYPES)
def test_on_ne_prete_que_ce_qu_on_atteint(monde, type_):
    """Un partageur hors de l'org propriétaire (ici : gérant du projet, membre de
    l'org B seulement) ne peut pas déclarer l'héritage."""
    from oto_mcp import ownership
    from oto_mcp.capabilities import resources as R
    from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
    ownership.grant("project", str(monde["pid"]), "user", monde["benef"], role="manager",
                    granted_by=monde["owner"])
    with pytest.raises(AuthzDenied) as e:
        R._resources(ResolvedCtx(sub=monde["benef"], org_id=monde["org_b"]),
                     R.ResourceInput(op="share", resource_type="project",
                                     resource_id=str(monde["pid"]), credentials="inherit",
                                     **_destinataire(monde, type_)))
    assert (e.value.status, e.value.code) == (403, "inherit_beyond_sharer_rights")


def test_le_membre_de_l_org_n_est_pas_touche(monde, monkeypatch):
    """Garde-fou : un membre de l'org propriétaire résout la clé d'org comme avant."""
    from oto_mcp import access, org_store
    org_store.add_org_member(monde["org_a"], monde["benef"])
    rc = _resoudre_dans_le_projet(monde, monkeypatch)
    assert (rc.mode, rc.entity_id) == ("org", str(monde["org_a"]))
    assert access  # import gardé : la surface plate reste le point d'appel
