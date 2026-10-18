"""Une copie PERSONNELLE qui masque la procédure de l'org le DIT (#1156, exigences 4 et 6).

**Ce qui s'est passé du 09 au 10/09/2026.** Dans une org, deux procédures personnelles
portant le slug de deux procédures de l'org ont été servies à l'agent à la place de
celles-ci, à chaque chargement par nom : 210 travaux de production ont tourné plus d'un
jour sur la mauvaise copie. La cascade de lecture (`op=get` sans `scope` : la copie perso
d'abord, l'org ensuite) faisait ce qu'elle dit ; c'est le silence qui a coûté :

- la lecture par nom disait `scope: "user"`, sans nommer la copie de l'org écartée ;
- la lecture par identifiant stable confirmait le bon texte — celui de l'org ;
- l'écriture qui avait créé la copie perso n'avait rien dit du masque.

La cascade n'est PAS remise en cause ici : on vérifie qu'elle se DIT, sur le chemin
servi (capacité + autz déclarée) et contre un vrai PostgreSQL — ce sont des clés de
propriété et des paliers, qu'aucun double ne prouve.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from oto_mcp.capabilities._types import RawCtx

_ORG = "> Procédure de l'org, celle que la campagne doit suivre.\n\n# Campagne\n\nÉtapes d'org.\n"
_PERSO = "> Copie perso, écrite sans `scope`.\n\n# Campagne\n\nÉtapes perso.\n"


@pytest.fixture(scope="module")
def monde(pg_module_dsn):
    """Une org, son admin (le compte sous lequel tournent les campagnes), un simple
    membre, une équipe active pour l'admin."""
    pytest.importorskip("psycopg")
    url_avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp import group_store, org_store
        from oto_mcp.db import init_db
        init_db()
        org = org_store.create_org("Acme", created_by="u-admin")
        org_store.add_org_member(org, "u-admin", "org_admin")
        org_store.add_org_member(org, "u-membre", "org_member")
        org_store.set_active_org("u-admin", org)
        org_store.set_active_org("u-membre", org)
        equipe = group_store.create_group(org, "Prospection")
        group_store.add_group_member(equipe, "u-admin", "group_admin")
        yield {"org": org, "equipe": equipe}
    finally:
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant


def _cap(cle: str):
    from oto_mcp.capabilities.registry import CAPABILITIES
    return next(c for c in CAPABILITIES if c.key == cle)


def _appel(sub: str, cle: str = "org.procedure.console", **args):
    """UN appel par le chemin servi : autz DÉCLARÉE puis handler."""
    cap = _cap(cle)
    inp = cap.Input(**args)
    out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


@pytest.fixture
def campagne(monde):
    """Le cas des 210 travaux : la procédure de l'org, puis une copie perso du même slug
    écrite SANS `scope` par le compte qui fait tourner la campagne. Rend les deux
    accusés ; nettoie derrière lui."""
    from oto_mcp import group_store, org_store
    org_ack = _appel("u-admin", op="set", scope="org", slug="campagne", body_md=_ORG,
                     title="Campagne (org)")
    org_ack = _appel("u-admin", op="set", scope="org", slug="campagne", body_md=_ORG + "v2\n",
                     title="Campagne (org)")
    perso_ack = _appel("u-admin", op="set", slug="campagne", body_md=_PERSO,
                       title="Campagne (perso)")
    yield {"org": org_ack, "perso": perso_ack}
    group_store.clear_active_group("u-admin")
    for otype, oid in (("user", "u-admin"), ("org", monde["org"]),
                       ("group", monde["equipe"])):
        org_store.delete_instruction(otype, oid, "campagne")


# ── Exigence 6 : l'écriture qui crée le masque le dit ─────────────────────────

def test_lecriture_perso_qui_masque_lorg_le_dit_et_la_nomme(campagne):
    ack, org = campagne["perso"], campagne["org"]
    assert ack["ok"] and ack["scope"] == "user"          # pas un refus : la cascade reste
    assert ack["other_scopes"] == [{
        "scope": "org", "guide_id": org["guide_id"], "version": org["version"],
        "updated_at": ack["other_scopes"][0]["updated_at"]}]
    alerte = ack["shadow_warning"]
    assert "SHADOWS" in alerte and "PERSONAL" in alerte
    assert f"`guide_id` {org['guide_id']}" in alerte and f"v{org['version']}" in alerte
    assert "scope='org'" in alerte                       # le geste qui écrit celle de l'org
    assert "op=archive scope='user'" in alerte           # le geste qui lève le masque
    assert "op=delete scope='user'" in alerte            # et celui qui le lève en détruisant


def test_lecriture_dorg_masquee_par_ma_copie_perso_le_dit(campagne):
    """L'admin corrige la procédure de l'org… que sa propre copie masque pour ses
    agents. Sans ce signal, la correction ne sert à aucun travail qui tourne sous son
    compte — la forme exacte de l'incident."""
    ack = _appel("u-admin", op="set", scope="org", slug="campagne", body_md=_ORG + "v3\n")
    assert ack["scope"] == "org"
    assert [a["scope"] for a in ack["other_scopes"]] == ["user"]
    assert ack["other_scopes"][0]["guide_id"] == campagne["perso"]["guide_id"]
    assert "You just wrote the ORG procedure" in ack["shadow_warning"]
    assert "SHADOWS" in ack["shadow_warning"]


def test_une_ecriture_sans_homonyme_ne_porte_rien(monde):
    """Un champ servi à chaque écriture serait du bruit qu'on apprend à ignorer."""
    from oto_mcp import org_store
    ack = _appel("u-admin", op="set", slug="solo-1156", body_md=_PERSO)
    try:
        assert "other_scopes" not in ack and "shadow_warning" not in ack
    finally:
        org_store.delete_instruction("user", "u-admin", "solo-1156")


# ── Exigence 4 : la résolution par nom dit qui a répondu et ce qu'elle a écarté ──

def test_la_lecture_par_nom_dit_la_copie_perso_et_lorg_ecartee(campagne):
    """Ce que lit l'agent d'un travail : `op=get slug=…`, sans `scope`."""
    lu = _appel("u-admin", op="get", slug="campagne")
    org = campagne["org"]
    assert lu["scope"] == "user" and "Étapes perso" in lu["body_md"]   # la cascade reste
    assert lu["other_scopes"][0] == {
        "scope": "org", "guide_id": org["guide_id"], "version": org["version"],
        "updated_at": lu["other_scopes"][0]["updated_at"]}
    assert lu["other_scopes"][0]["updated_at"]                          # la date
    alerte = lu["shadow_warning"]
    assert "You are reading your PERSONAL copy" in alerte and "SKIPPED" in alerte
    assert f"`guide_id` {org['guide_id']}" in alerte
    assert "scope='org'" in alerte


def test_la_lecture_dorg_dit_quune_copie_perso_la_masque(campagne):
    lu = _appel("u-admin", op="get", slug="campagne", scope="org")
    assert lu["scope"] == "org" and "Étapes d'org" in lu["body_md"]
    assert "You are reading the ORG procedure" in lu["shadow_warning"]
    assert f"`guide_id` {campagne['perso']['guide_id']}" in lu["shadow_warning"]


def test_la_verification_par_identifiant_stable_ne_dit_plus_tout_va_bien(campagne):
    """Pendant l'incident, la relecture par id stable confirmait le bon texte, celui de
    l'org — l'agent, qui charge par NOM, en recevait un autre."""
    lu = _appel("u-admin", op="get", guide_id=campagne["org"]["guide_id"])
    assert lu["scope"] == "org"
    assert "SHADOWS" in lu["shadow_warning"]
    assert lu["other_scopes"][0]["scope"] == "user"


def test_la_face_rest_de_lorg_le_dit_aussi(campagne):
    """`GET /api/me/instructions/{slug}` — l'écran lit l'org."""
    lu = _appel("u-admin", "org.instruction.get", slug="campagne")
    assert lu["other_scopes"][0]["scope"] == "user"
    assert "SHADOWS" in lu["shadow_warning"]


def test_un_autre_membre_ne_voit_ni_masque_ni_la_copie_dautrui(campagne):
    """La copie perso de l'admin ne masque rien pour un autre compte — et elle ne doit
    pas fuiter dans ce qu'il lit (ADR 0068 : privée)."""
    lu = _appel("u-membre", op="get", slug="campagne")
    assert lu["scope"] == "org"
    assert "other_scopes" not in lu and "shadow_warning" not in lu


def test_lequipe_est_nommee_mais_jamais_dite_masquee(monde, campagne):
    """L'équipe n'est pas dans la cascade par nom (`scope='group'` explicite) : quand le
    geste la vise, les autres paliers du slug sont NOMMÉS ; elle ne masque ni n'est
    masquée, et une lecture par nom ne la mentionne pas."""
    from oto_mcp import group_store
    group_store.set_active_group("u-admin", monde["equipe"])
    eq = _appel("u-admin", op="set", scope="group", slug="campagne", body_md=_ORG)
    assert {a["scope"] for a in eq["other_scopes"]} == {"user", "org"}
    assert "shadow_warning" not in eq
    lu_eq = _appel("u-admin", op="get", slug="campagne", scope="group")
    assert lu_eq["scope"] == "group" and "shadow_warning" not in lu_eq
    lu = _appel("u-admin", op="get", slug="campagne")
    assert lu["scope"] == "user"
    assert [a["scope"] for a in lu["other_scopes"]] == ["org"]
    assert "team" not in lu["shadow_warning"].lower()


def test_supprimer_la_copie_perso_leve_le_masque(campagne):
    _appel("u-admin", op="delete", slug="campagne", scope="user")
    lu = _appel("u-admin", op="get", slug="campagne")
    assert lu["scope"] == "org" and "Étapes d'org" in lu["body_md"]
    assert "other_scopes" not in lu and "shadow_warning" not in lu


def test_la_face_admin_na_pas_de_cascade_donc_rien_a_dire(monde, campagne):
    lu = _appel("u-admin", "org.guide.admin_get", org_id=monde["org"], slug="campagne")
    assert lu["scope"] == "org"
    assert "other_scopes" not in lu and "shadow_warning" not in lu


# ── Décision du 14/10/2026 : une copie perso ARCHIVÉE ne masque plus ─────────────

def test_une_copie_perso_archivee_ne_masque_plus_lorg(campagne):
    """Archiver la copie perso lève le masque : la lecture par nom sert l'org, et ne
    dit plus de masque. La copie archivée reste NOMMÉE (elle existe, avec sa date)."""
    _appel("u-admin", op="archive", slug="campagne", scope="user")
    lu = _appel("u-admin", op="get", slug="campagne")
    assert lu["scope"] == "org" and "Étapes d'org" in lu["body_md"]
    assert lu["version"] == campagne["org"]["version"]
    assert "shadow_warning" not in lu
    [perso] = lu["other_scopes"]
    assert perso["scope"] == "user" and perso["guide_id"] == campagne["perso"]["guide_id"]
    assert perso["archived_at"]


def test_une_copie_perso_archivee_reste_lisible_explicitement(campagne):
    _appel("u-admin", op="archive", slug="campagne", scope="user")
    lu = _appel("u-admin", op="get", slug="campagne", scope="user")
    assert lu["scope"] == "user" and "Étapes perso" in lu["body_md"]
    assert lu["archived_at"]
    assert "shadow_warning" not in lu                     # elle ne masque rien
    assert [a["scope"] for a in lu["other_scopes"]] == ["org"]
    # Par identifiant stable, même chose.
    par_id = _appel("u-admin", op="get", guide_id=campagne["perso"]["guide_id"])
    assert par_id["scope"] == "user" and "shadow_warning" not in par_id


def test_lorg_ecrite_ou_lue_nest_plus_dite_masquee_par_une_archivee(campagne):
    """Même règle à l'écriture qu'à la lecture : la procédure de l'org que masquait la
    copie perso ne l'est plus une fois celle-ci archivée."""
    _appel("u-admin", op="archive", slug="campagne", scope="user")
    ack = _appel("u-admin", op="set", scope="org", slug="campagne", body_md=_ORG + "v3\n")
    assert [a["scope"] for a in ack["other_scopes"]] == ["user"]
    assert "shadow_warning" not in ack
    lu = _appel("u-admin", op="get", slug="campagne", scope="org")
    assert "shadow_warning" not in lu
    rest = _appel("u-admin", "org.instruction.get", slug="campagne")
    assert "shadow_warning" not in rest


def test_desarchiver_la_copie_perso_la_remet_en_tete(campagne):
    _appel("u-admin", op="archive", slug="campagne", scope="user")
    assert _appel("u-admin", op="get", slug="campagne")["scope"] == "org"
    _appel("u-admin", op="unarchive", slug="campagne", scope="user")
    lu = _appel("u-admin", op="get", slug="campagne")
    assert lu["scope"] == "user" and "Étapes perso" in lu["body_md"]
    assert "You are reading your PERSONAL copy" in lu["shadow_warning"]
