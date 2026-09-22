"""#708 — une procédure dit QUI l'a écrite en dernier, et de quoi le nommer.

`set_by` (un identifiant de compte) était écrit à chaque écriture mais ne sortait pas dans
ce que lit un agent : `oto_procedure op=get` ne le portait pas, et l'historique le servait
nu. Depuis que l'écriture d'équipe est ouverte aux membres (#695), le moment qui doit se
voir est « une opératrice écrit la v27 d'une procédure dont les v1 à v26 sont du
prestataire ».

Contre un vrai PostgreSQL : le nom vient d'une jointure, pas d'un simulacre.
"""
from __future__ import annotations

import uuid

import pytest

from oto_mcp import org_store
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.capabilities.orgs import instructions as oi

ORG = 708
PRESTATAIRE = "sub-prestataire-708"
OPERATRICE = "sub-operatrice-708"
SANS_COMPTE = "sub-sans-fiche-708"


@pytest.fixture
def procedure(live):
    from oto_mcp.db._conn import _connect
    with _connect() as c:
        c.execute("INSERT INTO orgs (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                  (ORG, "org du banc"))
        c.execute("INSERT INTO users (sub, name, email) VALUES (%s, %s, %s), (%s, NULL, %s) "
                  "ON CONFLICT DO NOTHING",
                  (PRESTATAIRE, "Pat Prestataire", "pat@example.invalid",
                   OPERATRICE, "op@example.invalid"))
    slug = "proc-" + uuid.uuid4().hex[:6]
    org_store.set_instruction("org", ORG, slug, "v1", title="T", set_by=PRESTATAIRE)
    org_store.set_instruction("org", ORG, slug, "v2", set_by=OPERATRICE)
    return slug


def _lire(slug, **kw):
    out, _ = oi._read_guide(ResolvedCtx(sub=OPERATRICE, org_id=ORG, channel="mcp"),
                            oi.GuideGetInput(slug=slug, scope="org", **kw))
    return out


def test_la_lecture_agent_porte_l_auteur_de_la_derniere_ecriture(procedure):
    out = _lire(procedure)
    assert out["set_by"] == OPERATRICE
    # Pas de nom en base : repli sur l'email, jamais l'inverse ni un identifiant nu.
    assert out["set_by_name"] == "op@example.invalid"


def test_l_historique_nomme_chaque_version(procedure):
    versions = _lire(procedure, with_history=True)["versions"]
    # Une révision par version POSÉE, la courante comprise : c'est le contrat de la table.
    assert [(v["version"], v["set_by"], v["set_by_name"]) for v in versions] == [
        (2, OPERATRICE, "op@example.invalid"), (1, PRESTATAIRE, "Pat Prestataire")]


def test_une_version_archivee_dit_son_propre_auteur(procedure):
    out = _lire(procedure, version=1)
    assert (out["set_by"], out["set_by_name"]) == (PRESTATAIRE, "Pat Prestataire")


def test_une_absence_se_sert_comme_une_absence(live):
    """Procédure ancienne, sans auteur : rien n'est déduit rétroactivement."""
    slug = "anc-" + uuid.uuid4().hex[:6]
    from oto_mcp.db._conn import _connect
    with _connect() as c:
        c.execute("INSERT INTO orgs (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                  (ORG, "org du banc"))
    org_store.set_instruction("org", ORG, slug, "corps", set_by=None)
    out = _lire(slug)
    assert (out["set_by"], out["set_by_name"]) == (None, None)


def test_un_auteur_sans_fiche_se_replie_sur_l_identifiant(live):
    slug = "sf-" + uuid.uuid4().hex[:6]
    from oto_mcp.db._conn import _connect
    with _connect() as c:
        c.execute("INSERT INTO orgs (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                  (ORG, "org du banc"))
    org_store.set_instruction("org", ORG, slug, "corps", set_by=SANS_COMPTE)
    assert _lire(slug)["set_by_name"] == SANS_COMPTE


def test_les_faces_rest_declarent_le_nom(procedure):
    ctx = ResolvedCtx(sub=OPERATRICE, org_id=ORG, channel="rest")
    vue = oi.InstructionView(**oi._instruction_get(ctx, oi.InstrGetInput(slug=procedure)))
    assert vue.set_by_name == "op@example.invalid"
    hist = oi.InstructionVersions(**oi._instruction_versions(
        ctx, oi.SlugInput(slug=procedure)))
    assert [v.set_by_name for v in hist.versions] == ["op@example.invalid", "Pat Prestataire"]
