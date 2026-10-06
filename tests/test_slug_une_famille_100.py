"""Un slug, une famille, par portée : guide à charger ↔ procédure (otomata-tech/oto#100).

**Le défaut, mesuré en production.** Les guides à charger (`nodes`, `oto_guide`) et les
procédures (`org_instructions`, `oto_procedure`, le runner) partagent l'espace des slugs
d'une portée, dans deux stockages sans lien. Rien ne refusait qu'un slug existe des deux
côtés : l'agent recevait l'un ou l'autre selon l'outil appelé, et là où les deux
existaient, le guide était une copie PÉRIMÉE de la procédure — servie sans un mot.

**Ce que ce fichier fige :**

1. une CRÉATION qui ferait coexister les deux familles sous un slug, dans la même
   portée, est refusée (`family_conflict`, 409) par les deux surfaces — le refus nomme
   l'objet en place, et rien n'est écrit ;
2. un RENOMMAGE de procédure vers un slug qu'un guide porte est refusé de même ;
3. la MISE À JOUR d'un doublon déjà en base reste permise (ils se reconnaissent avant
   de se refuser, cf. l'issue) ;
4. la LECTURE par l'agent d'un guide en double ne le sert pas : elle renvoie à la
   procédure, qui reste servie par `oto_procedure` ; l'écran lit encore le guide ;
5. deux portées différentes ne se gênent pas, et les chemins qui SUFFIXENT un slug
   pris (copie, fork) le suffixent aussi devant un guide.

**Contre un vrai PostgreSQL, sur le CHEMIN SERVI** (autz déclarée puis handler) : la
garde est prise sous le verrou advisory commun aux deux familles, dans la transaction
qui écrit — aucun double ne prouve ça.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from oto_mcp.capabilities._types import AuthzDenied, RawCtx

_CORPS_P = "# Qualification\n\nLa procédure, tenue à jour."
_CORPS_G = "# Qualification\n\nUn guide à charger."


@pytest.fixture(scope="module")
def monde(pg_module_dsn):
    """Une base neuve pour ce module (cf. `conftest.pg_module_dsn`), bootée par le vrai
    `init_db`, avec une org et son admin."""
    pytest.importorskip("psycopg")
    url_avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp import org_store
        from oto_mcp.db import init_db
        init_db()
        org = org_store.create_org("Acme", created_by="u-admin")
        org_store.add_org_member(org, "u-admin", "org_admin")
        org_store.set_active_org("u-admin", org)
        yield {"org": org}
    finally:
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant


def _cap(key: str, sub: str, **args):
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next(c for c in CAPABILITIES if c.key == key)
    inp = cap.Input(**args)
    out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def _guide(sub: str, **args):
    return _cap("me.guide", sub, **args)


def _procedure(sub: str, **args):
    return _cap("org.procedure.console", sub, **args)


def _slug(prefixe: str) -> str:
    return f"{prefixe}-{uuid.uuid4().hex[:6]}"


def _doublon_herite(monkeypatch, scope: str, owner: str, slug: str) -> None:
    """Pose un GUIDE sur le slug d'une procédure existante, comme la base de production
    en porte déjà — en levant la garde le temps de l'écriture, seul moyen d'y arriver
    désormais."""
    from oto_mcp.db import guides as store
    with monkeypatch.context() as m:
        m.setattr(store, "_exiger_aucune_procedure", lambda *a: None)
        assert store.set_guide_db(scope, owner, slug, "Copie ancienne.", "Ancien titre")


# ── 1. La création refuse, dans les deux sens, et n'écrit rien ─────────────

def test_une_procedure_ne_se_cree_pas_sur_le_slug_d_un_guide(monde):
    from oto_mcp import db, org_store
    org, slug = str(monde["org"]), _slug("qualif")
    _guide("u-admin", op="write", scope="org", owner_id=org, slug=slug,
           body_md=_CORPS_G, title="Le guide")

    for op in ("create", "set"):
        with pytest.raises(AuthzDenied) as refus:
            _procedure("u-admin", op=op, scope="org", slug=slug, body_md=_CORPS_P)
        assert (refus.value.status, refus.value.code) == (409, "family_conflict")
        # Le refus NOMME ce qui occupe la place, et comment en sortir.
        message = str(refus.value)
        assert "GUIDE" in message and "Le guide" in message and "oto_guide" in message
        assert refus.value.details["famille"] == "guide"
        assert refus.value.details["public_id"].startswith("nod_")

    assert org_store.get_instruction("org", org, slug) is None
    assert db.get_guide_db("org", org, slug)["body_md"] == _CORPS_G


def test_un_guide_ne_se_cree_pas_sur_le_slug_d_une_procedure(monde):
    """Le défaut d'origine, au palier par défaut des DEUX outils (`user`) : un agent qui
    écrit sans `scope` par `oto_procedure` puis par `oto_guide` faisait naître un
    doublon chez lui."""
    from oto_mcp import db
    slug = _slug("veille")
    ecrite = _procedure("u-admin", op="create", slug=slug, body_md=_CORPS_P)
    assert ecrite["scope"] == "user"

    with pytest.raises(AuthzDenied) as refus:
        _guide("u-admin", op="write", slug=slug, body_md=_CORPS_G)
    assert (refus.value.status, refus.value.code) == (409, "family_conflict")
    message = str(refus.value)
    assert "PROCÉDURE" in message and f"#{ecrite['guide_id']}" in message
    assert "oto_procedure" in message
    assert db.get_guide_db("user", "u-admin", slug) is None


def test_une_procedure_ARCHIVEE_tient_aussi_son_slug(monde):
    """Remise en service, elle recréerait le doublon : sa place reste prise."""
    slug = _slug("retiree")
    _procedure("u-admin", op="create", scope="org", slug=slug, body_md=_CORPS_P)
    _cap("org.instruction.archive", "u-admin", slug=slug)

    with pytest.raises(AuthzDenied) as refus:
        _guide("u-admin", op="write", scope="org", owner_id=str(monde["org"]),
               slug=slug, body_md=_CORPS_G)
    assert refus.value.code == "family_conflict" and "archivée" in str(refus.value)


# ── 2. Le renommage refuse de même ──────────────────────────────────────────

def test_un_renommage_ne_va_pas_sur_le_slug_d_un_guide(monde):
    from oto_mcp import org_store
    org, slug, pris = str(monde["org"]), _slug("avant"), _slug("pris")
    _procedure("u-admin", op="create", scope="org", slug=slug, body_md=_CORPS_P)
    _guide("u-admin", op="write", scope="org", owner_id=org, slug=pris, body_md=_CORPS_G)

    with pytest.raises(AuthzDenied) as refus:
        _procedure("u-admin", op="rename", scope="org", slug=slug, new_slug=pris)
    assert (refus.value.status, refus.value.code) == (409, "family_conflict")
    assert org_store.get_instruction("org", org, slug)["body_md"] == _CORPS_P
    assert org_store.get_instruction("org", org, pris) is None


# ── 3. Deux portées ne se gênent pas ────────────────────────────────────────

def test_le_meme_slug_dans_deux_portees_reste_permis(monde):
    slug = _slug("partage")
    _procedure("u-admin", op="create", scope="org", slug=slug, body_md=_CORPS_P)
    assert _guide("u-admin", op="write", scope="user", slug=slug,
                  body_md=_CORPS_G)["scope"] == "user"


# ── 4. Les doublons déjà en base : écrits, oui ; servis à l'agent, non ──────

def test_un_doublon_herite_se_met_a_jour_mais_n_est_pas_servi_a_l_agent(monde, monkeypatch):
    org, slug = str(monde["org"]), _slug("campagne")
    p = _procedure("u-admin", op="create", scope="org", slug=slug, body_md=_CORPS_P)
    _doublon_herite(monkeypatch, "org", org, slug)

    # Reconnaître d'abord, refuser ensuite : la mise à jour d'un doublon existant passe.
    assert _guide("u-admin", op="write", scope="org", owner_id=org, slug=slug,
                  body_md="Copie retouchée.")["slug"] == slug

    # La LECTURE par l'agent, avec ou sans scope : le guide n'est pas servi, le refus
    # nomme la procédure et dit comment retirer le doublon.
    for args in ({}, {"scope": "org"}):
        with pytest.raises(AuthzDenied) as refus:
            _guide("u-admin", op="read", slug=slug, **args)
        assert (refus.value.status, refus.value.code) == (409, "family_conflict")
        message = str(refus.value)
        assert f"#{p['guide_id']}" in message and "oto_procedure" in message
        assert "op='delete'" in message

    # La procédure, elle, reste la consigne servie.
    assert _procedure("u-admin", op="get", scope="org", slug=slug)["body_md"] == _CORPS_P
    # L'écran lit encore le guide : c'est de là qu'on le retire.
    assert _cap("me.guides.get", "u-admin", scope="org", slug=slug)["body_md"] == (
        "Copie retouchée.")
    # Retiré, le guide ne gêne plus rien.
    _guide("u-admin", op="delete", scope="org", owner_id=org, slug=slug)
    with pytest.raises(AuthzDenied) as absent:
        _guide("u-admin", op="read", slug=slug, scope="org")
    assert absent.value.code == "not_found"


def test_un_doublon_dont_la_procedure_est_retiree_sert_le_guide(monde, monkeypatch):
    """Une procédure ARCHIVÉE n'est plus une consigne : le guide ne rivalise avec rien."""
    org, slug = str(monde["org"]), _slug("ancienne")
    _procedure("u-admin", op="create", scope="org", slug=slug, body_md=_CORPS_P)
    _doublon_herite(monkeypatch, "org", org, slug)
    _cap("org.instruction.archive", "u-admin", slug=slug)
    assert _guide("u-admin", op="read", slug=slug, scope="org")["body_md"] == (
        "Copie ancienne.")


# ── 5. Les chemins qui suffixent un slug pris le suffixent devant un guide ──

def test_la_copie_et_le_fork_contournent_le_slug_d_un_guide(monde):
    from oto_mcp import org_store
    org, slug = str(monde["org"]), _slug("livree")
    source = _procedure("u-admin", op="create", scope="org", slug=slug, body_md=_CORPS_P)
    _guide("u-admin", op="write", scope="user", slug=slug, body_md=_CORPS_G)

    copie = org_store.copy_instruction_to_owner(source["guide_id"], "user", "u-admin")
    assert copie["slug"] == f"{slug}-2"

    fork = _slug("publique")
    _guide("u-admin", op="write", scope="org", owner_id=org, slug=fork, body_md=_CORPS_G)
    entree = org_store.publish_guide(slug=fork, title="T", body_md=_CORPS_P,
                                     author_kind="otomata", published_by="u-admin")
    assert org_store.fork_library_entry(entry_id=entree["id"], owner_type="org",
                                        owner_id=int(org),
                                        set_by="u-admin")["slug"] == f"{fork}-2"
    # Le dédoublonnage se fait DANS le palier visé : le guide d'org ne gêne pas une
    # copie personnelle, un guide personnel du même slug, si.
    assert org_store.fork_library_entry(entry_id=entree["id"], owner_type="user",
                                        owner_id="u-admin",
                                        set_by="u-admin")["slug"] == fork
    perso = _slug("perso")
    _guide("u-admin", op="write", scope="user", slug=perso, body_md=_CORPS_G)
    entree_p = org_store.publish_guide(slug=perso, title="T", body_md=_CORPS_P,
                                       author_kind="otomata", published_by="u-admin")
    assert org_store.fork_library_entry(entry_id=entree_p["id"], owner_type="user",
                                        owner_id="u-admin",
                                        set_by="u-admin")["slug"] == f"{perso}-2"
