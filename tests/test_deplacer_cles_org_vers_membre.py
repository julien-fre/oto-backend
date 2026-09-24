"""Le script qui déplace des clés d'org vers les clés personnelles d'un membre, contre un
vrai PostgreSQL (`scripts/deplacer_cles_org_vers_membre.py`).

Ce qui coûterait le plus cher à rater, dans l'ordre :
1. **un secret affiché** — aucune sortie ni aucun log ne porte la valeur ;
2. **une clé perdue ou transplantée** — relue au niveau membre à l'identique,
   indéchiffrable avec le sceau de l'org, ligne d'org partie, et rien du tout si la
   relecture échoue (ROLLBACK) ;
3. **une clé du membre écrasée** — jamais : renommage côté clé déplacée, ou refus ;
4. **un objet qui casse en silence** — un lien de projet qui désigne l'instance d'org
   fait refuser `--apply`, sauf option explicite.
"""
from __future__ import annotations

import logging
import os

import pytest

from oto_mcp import credentials_store as cs, crypto, instance_refs

ORG = 7301
AUTRE_ORG = 7302
CIBLE = "usr_cible"
ADMIN = "usr_admin"
MEMBRE = "usr_membre"
ETRANGER = "usr_etranger"
SECRET = "sk-FICTIF-ne-doit-jamais-sortir-9f3a"
MULTI = "hunter"          # multi-compte, clé perso ET clé d'org
MONO = "pennylaneged"     # mono-compte, clé perso ET clé d'org
ORG_SEULE = "http"        # clé d'org seulement : pas de clé personnelle


def _exec(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(sql, params)


def _one(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute(sql, params).fetchone()


def _poser(entity_type, entity_id, connector, account, secret=SECRET, meta=None, set_by=ADMIN):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        cs._upsert(conn, entity_type, str(entity_id), connector, account, secret,
                   set_by, meta or {"note": "posée par le banc"})


def _ligne(entity_type, entity_id, connector, account):
    return _one("SELECT secret_enc, meta, set_by FROM connector_credentials "
                "WHERE entity_type=%s AND entity_id=%s AND connector=%s AND account=%s",
                (entity_type, str(entity_id), connector, account))


@pytest.fixture(scope="module")
def base(live):
    avant = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ.setdefault("OTO_MCP_MASTER_KEY", "0" * 64)
    for org in (ORG, AUTRE_ORG):
        _exec("INSERT INTO orgs (id, name) VALUES (%s, 'o') ON CONFLICT DO NOTHING", (org,))
    for s in (CIBLE, ADMIN, MEMBRE, ETRANGER):
        _exec("INSERT INTO users (sub) VALUES (%s) ON CONFLICT DO NOTHING", (s,))
    for s, role in ((CIBLE, "org_member"), (ADMIN, "org_admin"), (MEMBRE, "org_member")):
        _exec("INSERT INTO org_members (org_id, sub, org_role) VALUES (%s, %s, %s) "
              "ON CONFLICT DO NOTHING", (ORG, s, role))
    _exec("INSERT INTO org_members (org_id, sub, org_role) VALUES (%s, %s, 'org_member') "
          "ON CONFLICT DO NOTHING", (AUTRE_ORG, ETRANGER))
    yield
    if avant is None:
        os.environ.pop("OTO_MCP_MASTER_KEY", None)


@pytest.fixture
def propre(base):
    _exec("DELETE FROM connector_credentials WHERE entity_type IN ('org','member')")
    _exec("DELETE FROM project_links")
    yield


def _lancer(*connectors, **kw):
    from scripts.deplacer_cles_org_vers_membre import deplacer
    sortie = []
    code = deplacer(kw.pop("org", ORG), kw.pop("sub", CIBLE), list(connectors),
                    out=sortie.append, **kw)
    return code, "\n".join(sortie)


def test_dry_run_rejoue_puis_annule(propre):
    _poser(cs.ORG, ORG, MULTI, "")
    code, sortie = _lancer(MULTI)
    assert code == 0, sortie
    assert "ANNULÉ" in sortie
    assert _ligne(cs.ORG, ORG, MULTI, "") is not None
    assert _ligne(cs.MEMBER, cs.member_id(ORG, CIBLE), MULTI, "") is None


def test_apply_deplace_et_garde_meta_et_set_by(propre):
    _poser(cs.ORG, ORG, MULTI, "", meta={"note": "gardée"}, set_by=CIBLE)
    code, sortie = _lancer(MULTI, apply=True)
    assert code == 0, sortie
    eid = cs.member_id(ORG, CIBLE)
    row = _ligne(cs.MEMBER, eid, MULTI, "")
    assert row is not None and _ligne(cs.ORG, ORG, MULTI, "") is None
    assert row["set_by"] == CIBLE
    assert row["meta"]["note"] == "gardée"
    assert row["meta"]["_deplacement"]["from"] == f"org:{ORG}"
    # Relu à l'identique au niveau membre, et INDÉCHIFFRABLE avec le sceau de l'org.
    assert crypto.decrypt(row["secret_enc"], cs._aad(cs.MEMBER, eid, MULTI, "")) == SECRET
    with pytest.raises(Exception):
        crypto.decrypt(row["secret_enc"], cs._aad(cs.ORG, str(ORG), MULTI, ""))


def test_instance_d_org_archivee_et_instance_membre_nee(propre):
    from oto_mcp.db import connector_instances as ci
    _poser(cs.ORG, ORG, MULTI, "")
    avant = ci.instance_id_for_vault_row(cs.ORG, str(ORG), MULTI, "")
    assert avant is not None
    assert _lancer(MULTI, apply=True)[0] == 0
    assert ci.instance_id_for_vault_row(cs.ORG, str(ORG), MULTI, "") is None
    assert ci.instance_id_for_vault_row(cs.MEMBER, cs.member_id(ORG, CIBLE), MULTI, "") not in (None, avant)


def test_collision_multi_compte_renomme_la_cle_deplacee(propre):
    eid = cs.member_id(ORG, CIBLE)
    _poser(cs.MEMBER, eid, MULTI, "principal", secret="cle-perso-intacte")
    _poser(cs.ORG, ORG, MULTI, "")
    code, sortie = _lancer(MULTI, apply=True)
    assert code == 0, sortie
    assert "renommé" in sortie
    perso = _ligne(cs.MEMBER, eid, MULTI, "principal")
    assert crypto.decrypt(perso["secret_enc"], cs._aad(cs.MEMBER, eid, MULTI, "principal")) \
        == "cle-perso-intacte"
    deplacee = _ligne(cs.MEMBER, eid, MULTI, f"principal-org-{ORG}")
    assert deplacee is not None


def test_collision_mono_compte_refuse_sans_rien_ecrire(propre):
    eid = cs.member_id(ORG, CIBLE)
    _poser(cs.MEMBER, eid, MONO, "", secret="cle-perso-intacte")
    _poser(cs.ORG, ORG, MONO, "")
    code, sortie = _lancer(MONO, apply=True)
    assert code == 3, sortie
    assert "REFUS" in sortie
    assert _ligne(cs.ORG, ORG, MONO, "") is not None


def test_connecteur_sans_cle_perso_ou_sans_cle_d_org_refuse_tout(propre):
    _poser(cs.ORG, ORG, MULTI, "")
    code, sortie = _lancer(MULTI, ORG_SEULE, "apify", apply=True)
    assert code == 3, sortie
    assert f"REFUS {ORG_SEULE}" in sortie and "REFUS apify" in sortie
    # Échec d'ensemble : même la clé déplaçable reste où elle était.
    assert _ligne(cs.ORG, ORG, MULTI, "") is not None


def test_sub_hors_de_l_org_refuse(propre):
    _poser(cs.ORG, ORG, MULTI, "")
    code, sortie = _lancer(MULTI, sub=ETRANGER, apply=True)
    assert code == 2, sortie
    assert _ligne(cs.ORG, ORG, MULTI, "") is not None


def test_lien_de_projet_sur_l_instance_refuse_sans_option(propre):
    _poser(cs.ORG, ORG, MULTI, "")
    _exec("INSERT INTO projects (id, name, owner_type, owner_id) VALUES (91001, 'p', 'org', %s) "
          "ON CONFLICT DO NOTHING", (str(ORG),))
    ref = instance_refs.make_org_ref(ORG, MULTI, "")
    _exec("INSERT INTO project_links (project_id, target_type, target_ref, config) "
          "VALUES (91001, 'connecteur', %s, jsonb_build_object('instance_ref', %s::text))",
          (MULTI, ref))
    code, sortie = _lancer(MULTI, apply=True)
    assert code == 3, sortie
    assert "liens de projet 1" in sortie
    assert _ligne(cs.ORG, ORG, MULTI, "") is not None
    code, sortie = _lancer(MULTI, apply=True, force_orphan_bindings=True)
    assert code == 0, sortie


def test_impact_compte_les_membres_qui_perdent(propre):
    _poser(cs.ORG, ORG, MULTI, "")
    code, sortie = _lancer(MULTI, show_members=True)
    assert code == 0, sortie
    assert "autres membres de l'org : 2" in sortie
    assert f"{MULTI} : 2 membre(s)" in sortie
    assert f"membre : {ADMIN}" in sortie


def test_relecture_en_echec_annule_tout(propre, monkeypatch):
    import scripts.deplacer_cles_org_vers_membre as script
    _poser(cs.ORG, ORG, MULTI, "")
    empreintes = iter(["a", "b"])            # l'écriture et la relecture divergent
    monkeypatch.setattr(script, "_empreinte", lambda _secret: next(empreintes))
    code, sortie = _lancer(MULTI, apply=True)
    assert code == 4, sortie
    assert "ROLLBACK" in sortie
    assert _ligne(cs.ORG, ORG, MULTI, "") is not None
    assert _ligne(cs.MEMBER, cs.member_id(ORG, CIBLE), MULTI, "") is None


def test_aucun_secret_dans_la_sortie_ni_les_logs(propre, caplog, capsys):
    _poser(cs.ORG, ORG, MULTI, "")
    caplog.set_level(logging.DEBUG)
    from scripts.deplacer_cles_org_vers_membre import main
    assert main(["--org", str(ORG), "--sub", CIBLE, "--connectors", MULTI,
                 "--show-members"]) == 0
    assert main(["--org", str(ORG), "--sub", CIBLE, "--connectors", MULTI, "--apply"]) == 0
    capture = capsys.readouterr()
    for texte in (capture.out, capture.err, caplog.text):
        assert SECRET not in texte
