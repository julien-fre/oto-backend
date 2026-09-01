"""Créer une procédure ne peut plus en écraser une (oto-backend#662).

**Le défaut, tel qu'un front tiers l'a payé.** Le domaine n'avait aucun verbe de
CRÉATION : `PUT /api/me/instructions/{slug}` upsertait, et un slug fabriqué côté
client — `qualification` pour un agent neuf — retombait sur la procédure d'org qui
portait déjà ce nom. Elle était remplacée en bloc, la réponse disait `ok: true`, et
c'est en relisant qu'on l'apprenait. Rien n'était perdu en base (la version montait,
l'état antérieur partait en révision) mais **rien ne prévenait** — la définition
même d'une perte de données silencieuse pour qui ne relit pas.

**Ce que ce fichier fige, dans cet ordre :**

1. le geste d'écriture RESTE un upsert (`test_ecrire_sur_un_slug_pris_remplace…`) —
   c'est la reproduction du défaut ET une décision : `PUT …/{slug}` est aussi le
   chemin de l'édition, y refuser l'existant casserait toute écriture sur une
   procédure en place ;
2. la création REFUSE (`slug_taken`) et ne touche à rien ;
3. l'édition concurrente refuse aussi, quand le client dit ce qu'il a lu
   (`expected_version` → `version_conflict`) — le pendant d'`expected_rev` sur les
   pages, et le parti pris d'ADR 0044 pour les instances de connecteur.

**Contre un vrai PostgreSQL, et sur le CHEMIN SERVI.** Les deux refus sont levés
sous le verrou advisory, dans la transaction d'écriture : aucun stub ne prouve ça,
et un pré-check hors verrou laisserait passer deux créations simultanées. Les
gestes passent par la capacité avec sa règle d'autz déclarée (même recette que
`test_procedure_paliers_681`) — vérifier le store laisserait l'appelant, qui est ce
que le front appelle, hors du champ.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from oto_mcp.capabilities._types import AuthzDenied, RawCtx


# Sans blanc de bord : l'écriture `strip()` le corps, donc un corps qui en porte ne
# se relit pas à l'octet près et l'assertion pointerait un faux coupable.
_CORPS_A = ("> **Self-improvement digest** — jamais déroulée.\n\n"
            "# Qualification\n\n```\n[Début] --> [Fin]\n```\n\nLa procédure D'ORG.")
_CORPS_B = ("> **Self-improvement digest** — jamais déroulée.\n\n"
            "# Qualification\n\n```\n[Début] --> [Fin]\n```\n\nCELLE DE L'AGENT NEUF.")


@pytest.fixture(scope="module")
def monde(pg_dsn):
    """Base JETABLE bootée par le vrai `init_db` — même recette que #681.

    À part de la base du conteneur partagé : un boot complet y laisse ~67 tables et
    leurs FK, et les tests qui recréent deux tables autonomes n'y arrivent plus."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_662_" + uuid.uuid4().hex[:8]
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


def _appel(sub: str, **args):
    """UN appel d'`oto_procedure` par le chemin servi : autz DÉCLARÉE puis handler."""
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next(c for c in CAPABILITIES if c.key == "org.procedure.console")
    inp = cap.Input(**args)
    out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)      # ← la porte
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def _lu(sub: str, slug: str, **args) -> dict:
    """La procédure telle que la LECTURE la sert (corps + version), pas telle que le
    store la range : c'est ce que voit le client qui relit après une écriture."""
    return _appel(sub, op="get", slug=slug, **args)


def _slug(prefixe: str) -> str:
    return f"{prefixe}-{uuid.uuid4().hex[:6]}"


# ── 1. La reproduction : l'écriture reste un upsert, et c'est VOULU ─────────

def test_ecrire_sur_un_slug_pris_remplace_le_corps_sans_le_dire(monde):
    """Le défaut #662 tel quel — conservé sur `set`, qui EST le geste d'édition.

    Ce test n'est pas une régression à corriger : c'est le contrat de `set` mis noir
    sur blanc. Ce que le lot ajoute, c'est un geste qui NE fait pas ça (`create`) et
    un moyen de s'en prémunir sur `set` (`expected_version`) — pas un refus dur sur
    ce chemin-ci, qui empêcherait d'éditer une procédure existante."""
    slug = _slug("qualification")
    assert _appel("u-admin", op="set", slug=slug, body_md=_CORPS_A)["version"] == 1

    ecrasement = _appel("u-admin", op="set", slug=slug, body_md=_CORPS_B)
    assert ecrasement["ok"] is True and ecrasement["version"] == 2
    # Aucun signal dans la réponse : rien ne dit qu'on vient de remplacer un existant.
    assert "slug_taken" not in str(ecrasement)
    assert _lu("u-admin", slug)["body_md"] == _CORPS_B


# ── 2. La création : elle refuse, et elle ne touche à rien ──────────────────

def test_creer_sur_un_slug_pris_refuse_et_laisse_la_procedure_intacte(monde):
    """Le cœur du lot. Le refus doit être NOMMÉ (`slug_taken`, 409) et la procédure
    en place doit ressortir mot pour mot — la version n'a même pas bougé, donc
    aucune révision parasite n'a été archivée."""
    slug = _slug("qualification")
    assert _appel("u-admin", op="create", slug=slug, body_md=_CORPS_A)["version"] == 1

    with pytest.raises(AuthzDenied) as refus:
        _appel("u-admin", op="create", slug=slug, body_md=_CORPS_B)
    assert (refus.value.status, refus.value.code) == (409, "slug_taken")
    assert slug in refus.value.message                      # actionnable, pas un code nu
    assert refus.value.details["version"] == 1

    relue = _lu("u-admin", slug)
    assert (relue["body_md"], relue["version"]) == (_CORPS_A, 1)


def test_creer_sur_un_slug_libre_ecrit_la_version_1(monde):
    """Le pendant : la garde ne doit rien fermer d'autre que la collision."""
    slug = _slug("relance")
    out = _appel("u-admin", op="create", slug=slug, body_md=_CORPS_A,
                 title="Relance clients")
    assert out["ok"] is True and out["version"] == 1 and out["slug"] == slug
    assert out["org_id"] == monde["org"] and out["scope"] == "org"
    assert _lu("u-admin", slug)["body_md"] == _CORPS_A


def test_creer_sur_un_slug_archive_refuse_en_disant_quil_est_archive(monde):
    """Le piège que la garde doit couvrir aussi.

    Une procédure archivée sort de TOUS les listings : le slug paraît libre. Il ne
    l'est pas — l'unicité vivante `(owner_type, owner_id, slug)` ignore l'archivage,
    et écrire par-dessus ne désarchive pas la ligne. Sans ce cas, la « création »
    aurait réussi et serait née invisible."""
    from oto_mcp import org_store
    slug = _slug("cloture")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS_A)
    assert org_store.archive_instruction("org", monde["org"], slug)
    assert slug not in {p["slug"] for p in _appel("u-admin", op="list")["guides"]}

    with pytest.raises(AuthzDenied) as refus:
        _appel("u-admin", op="create", slug=slug, body_md=_CORPS_B)
    assert (refus.value.status, refus.value.code) == (409, "slug_taken")
    assert refus.value.details["archived"] is True
    assert "archiv" in refus.value.message


def test_creer_au_palier_equipe_refuse_le_slug_pris_de_lequipe(monde):
    """La garde suit la clé de PROPRIÉTÉ, pas l'org : deux paliers, deux espaces de
    noms. Un membre d'équipe crée chez elle (#681), et retombe sur le même refus."""
    slug = _slug("cloture-equipe")
    out = _appel("u-membre", op="create", scope="group", slug=slug, body_md=_CORPS_A)
    assert out["group_id"] == monde["equipe"] and out["scope"] == "group"

    with pytest.raises(AuthzDenied) as refus:
        _appel("u-membre", op="create", scope="group", slug=slug, body_md=_CORPS_B)
    assert (refus.value.status, refus.value.code) == (409, "slug_taken")
    assert _lu("u-membre", slug, scope="group")["body_md"] == _CORPS_A
    # Le MÊME slug est libre au palier org : la collision est scopée, pas globale.
    assert _appel("u-admin", op="create", slug=slug, body_md=_CORPS_B)["version"] == 1


# ── 3. L'édition concurrente : le client dit ce qu'il a lu ──────────────────

def test_editer_avec_une_version_perimee_refuse_et_conserve_le_corps(monde):
    """Deux éditeurs, une procédure. Le second a lu la v1, quelqu'un a posé la v2 :
    sans `expected_version` il écrase (cas 1 de ce fichier), avec il prend un 409 et
    le travail de l'autre survit."""
    slug = _slug("qualification")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS_A)   # v1, lue par les deux
    _appel("u-admin", op="set", slug=slug, body_md=_CORPS_B)      # v2, posée par l'autre

    with pytest.raises(AuthzDenied) as refus:
        _appel("u-admin", op="set", slug=slug, body_md=_CORPS_A, expected_version=1)
    assert (refus.value.status, refus.value.code) == (409, "version_conflict")
    assert refus.value.details["current_version"] == 2
    assert _lu("u-admin", slug)["body_md"] == _CORPS_B


def test_editer_avec_la_version_a_jour_ecrit(monde):
    """La garde ne doit pas bloquer l'éditeur qui a relu."""
    slug = _slug("qualification")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS_A)
    out = _appel("u-admin", op="set", slug=slug, body_md=_CORPS_B, expected_version=1)
    assert out["version"] == 2
    assert _lu("u-admin", slug)["body_md"] == _CORPS_B


def test_expected_version_sur_une_procedure_absente_refuse_aussi(monde):
    """Annoncer une version attendue, c'est affirmer avoir lu quelque chose : une
    procédure absente dément cette lecture autant qu'un numéro différent. La laisser
    passer créerait une v1 là où le client croyait éditer une v3 supprimée."""
    with pytest.raises(AuthzDenied) as refus:
        _appel("u-admin", op="set", slug=_slug("jamais-vue"), body_md=_CORPS_A,
               expected_version=3)
    assert (refus.value.status, refus.value.code) == (409, "version_conflict")
    assert refus.value.details["current_version"] is None
