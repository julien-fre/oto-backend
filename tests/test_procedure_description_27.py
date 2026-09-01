"""Corriger la vitrine d'une procédure sans repasser son corps (issue `oto`#27).

**Le défaut.** `title` et `description` sont la ligne que l'agent lit dans le
catalogue pour CHOISIR sa procédure — donc celle qui vieillit le plus vite. Les
corriger n'avait qu'un chemin : `op=set`, qui exige `body_md`. Corriger une
description imposait de retranscrire plusieurs milliers de caractères de prose
qu'on ne voulait pas toucher, et une retranscription peut dégrader ce qu'elle
recopie. Le coût était mesuré : deux procédures dont le corps était juste et la
description périmée sont restées en l'état, le geste de correction étant plus
risqué que le défaut. Une carte périmée est pire que pas de carte — elle est lue
avec confiance.

**Ce que ce fichier fige :**

1. `describe` change la vitrine et laisse le corps IDENTIQUE À L'OCTET — c'est
   toute la promesse du verbe, et le seul test qui la prouve ;
2. il reste VERSIONNÉ : la version monte, l'état antérieur part en révision, donc
   `from_version` défait une mauvaise correction comme n'importe quelle écriture ;
3. il ne fait rien d'autre : ni `title` ni `description` ⟹ refus nommé, aucune
   version consommée ; slug absent ⟹ 404 ; `expected_version` périmée ⟹ 409 ;
4. le catalogue que l'agent lit affiche bien la correction (c'est le point) ;
5. `set` n'a pas bougé : `body_md` y reste EXIGÉ. Le verbe est à part précisément
   pour ne pas échanger ce refus bruyant contre un glissement muet, où l'appelant
   qui voulait réécrire le corps et n'a rien produit repartirait avec une retouche
   de vitrine en croyant avoir écrit.

**Contre un vrai PostgreSQL, sur le CHEMIN SERVI** — même recette que #662/#681 :
le geste passe par la capacité avec sa règle d'autz déclarée. Le refus de conflit
est levé sous le verrou advisory, dans la transaction d'écriture : aucun stub ne
prouve ça.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from oto_mcp.capabilities._types import AuthzDenied, RawCtx


# Sans blanc de bord : l'écriture `strip()` le corps, donc un corps qui en porte ne
# se relit pas à l'octet près et l'assertion pointerait un faux coupable.
_CORPS = ("> **Self-improvement digest** — jamais déroulée.\n\n"
          "# Clôture annuelle\n\n```\n[Début] --> [Fin]\n```\n\n"
          "La prose QU'ON NE VEUT PAS RETRANSCRIRE.")


@pytest.fixture(scope="module")
def monde(pg_dsn):
    """Base JETABLE bootée par le vrai `init_db` — même recette que #662/#681."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_27_" + uuid.uuid4().hex[:8]
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
    """La procédure telle que la LECTURE la sert — ce que voit le client qui relit."""
    return _appel(sub, op="get", slug=slug, **args)


def _slug(prefixe: str) -> str:
    return f"{prefixe}-{uuid.uuid4().hex[:6]}"


# ── 1. La promesse : la vitrine change, le corps ne bouge pas ───────────────

def test_corriger_la_description_laisse_le_corps_identique_a_loctet(monde):
    """LE test du lot. Avant #27 il fallait repasser `_CORPS` pour arriver là, et
    c'est cette retranscription qui pouvait dégrader la prose."""
    slug = _slug("cloture")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS,
           title="Clôture annuelle", description="PÉRIMÉE : parle de l'ancien outil")

    out = _appel("u-admin", op="describe", slug=slug,
                 description="À JOUR : décrit le nouvel outil")
    assert out["ok"] is True
    assert out["description"] == "À JOUR : décrit le nouvel outil"

    relue = _lu("u-admin", slug)
    assert relue["body_md"] == _CORPS, "le corps devait être reconduit tel quel"
    assert relue["description"] == "À JOUR : décrit le nouvel outil"


def test_le_titre_seul_se_corrige_et_lecho_rend_la_vitrine_entiere(monde):
    """Le champ non fourni est RECONDUIT, et l'écho le dit : l'appelant qui n'a
    envoyé qu'un titre voit ce que le catalogue affiche désormais, sans relire."""
    slug = _slug("relance")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS,
           title="Relence clients", description="Relancer les impayés")

    out = _appel("u-admin", op="describe", slug=slug, title="Relance clients")
    assert out["title"] == "Relance clients"
    assert out["description"] == "Relancer les impayés"   # reconduite, pas vidée

    relue = _lu("u-admin", slug)
    assert (relue["title"], relue["description"]) == ("Relance clients",
                                                      "Relancer les impayés")


def test_la_correction_apparait_dans_le_catalogue_que_lagent_lit(monde):
    """Le point de l'issue : c'est la ligne du CATALOGUE qui était périmée, et c'est
    donc là qu'il faut vérifier que la correction est arrivée."""
    slug = _slug("qualification")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS,
           title="Qualification", description="AVANT")
    _appel("u-admin", op="describe", slug=slug, description="APRÈS")

    entree = next(p for p in _appel("u-admin", op="list")["guides"]
                  if p["slug"] == slug)
    assert entree["description"] == "APRÈS"


# ── 2. C'est une écriture : versionnée, donc réversible ─────────────────────

def test_la_correction_monte_la_version_et_reste_defaisable(monde):
    """Une correction de vitrine est une écriture — elle se voit dans l'historique et
    se défait par `from_version`, comme les autres. C'est ce qui rend le geste
    ouvrable au même palier de droits que `set`."""
    slug = _slug("cloture")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS, description="v1")
    assert _appel("u-admin", op="describe", slug=slug,
                  description="v2")["version"] == 2

    versions = _lu("u-admin", slug, with_history=True)["versions"]
    assert [v["version"] for v in versions] == [2, 1]

    # Et la v1 se restaure : la révision archivée porte bien la vitrine d'avant.
    _appel("u-admin", op="set", slug=slug, from_version=1)
    relue = _lu("u-admin", slug)
    assert relue["description"] == "v1" and relue["body_md"] == _CORPS


def test_la_version_reste_un_instantane_complet_slots_compris(monde):
    """Le corps ET les slots sont reconduits : sans ça, restaurer la version issue
    d'une correction de vitrine rendrait une procédure amputée de ses slots."""
    slug = _slug("avec-slots")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS, description="v1",
           slots=[{"name": "clients", "type": "tableau"}])
    _appel("u-admin", op="describe", slug=slug, description="v2")

    relue = _lu("u-admin", slug)
    assert [s["name"] for s in relue["slots"]] == ["clients"]
    assert relue["body_md"] == _CORPS


# ── 3. Le verbe ne fait QUE ça ──────────────────────────────────────────────

def test_sans_titre_ni_description_le_geste_refuse_sans_consommer_de_version(monde):
    """Une écriture qui ne change rien mais monte la version est un défaut, pas un
    no-op : refus nommé, et la version doit être RESTÉE la même."""
    slug = _slug("cloture")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS, description="intacte")

    with pytest.raises(AuthzDenied) as refus:
        _appel("u-admin", op="describe", slug=slug)
    assert (refus.value.status, refus.value.code) == (400, "nothing_to_describe")

    relue = _lu("u-admin", slug)
    assert relue["version"] == 1 and relue["description"] == "intacte"


def test_decrire_une_procedure_absente_rend_404_et_ne_la_cree_pas(monde):
    """Pas de création déguisée par ce chemin : créer, c'est fournir un corps."""
    slug = _slug("jamais-vue")
    with pytest.raises(AuthzDenied) as refus:
        _appel("u-admin", op="describe", slug=slug, description="peu importe")
    assert (refus.value.status, refus.value.code) == (404, "not_found")
    assert slug not in {p["slug"] for p in _appel("u-admin", op="list")["guides"]}


def test_set_exige_toujours_un_corps(monde):
    """Le pendant du verbe à part : `set` n'a pas été relâché. Sans ce test, quelqu'un
    « simplifierait » un jour en rendant `body_md` facultatif — et le refus bruyant
    deviendrait le glissement muet que ce lot a refusé d'introduire."""
    slug = _slug("cloture")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS)
    with pytest.raises(AuthzDenied) as refus:
        _appel("u-admin", op="set", slug=slug, title="titre seul")
    assert (refus.value.status, refus.value.code) == (400, "body_md_required")


# ── 4. L'édition concurrente, comme sur `set` ───────────────────────────────

def test_corriger_avec_une_version_perimee_refuse_et_conserve_la_vitrine(monde):
    """Deux correcteurs, une procédure : celui qui a lu la v1 ne doit pas effacer la
    correction posée entre-temps."""
    slug = _slug("cloture")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS, description="v1")
    _appel("u-admin", op="describe", slug=slug, description="posée par l'autre")

    with pytest.raises(AuthzDenied) as refus:
        _appel("u-admin", op="describe", slug=slug, description="la mienne",
               expected_version=1)
    assert (refus.value.status, refus.value.code) == (409, "version_conflict")
    assert refus.value.details["current_version"] == 2
    assert _lu("u-admin", slug)["description"] == "posée par l'autre"


def test_corriger_avec_la_version_a_jour_ecrit(monde):
    """La garde ne doit pas bloquer le correcteur qui a relu."""
    slug = _slug("cloture")
    _appel("u-admin", op="create", slug=slug, body_md=_CORPS, description="v1")
    out = _appel("u-admin", op="describe", slug=slug, description="v2",
                 expected_version=1)
    assert out["version"] == 2 and out["description"] == "v2"


# ── 5. Le palier équipe : même garde que `set` ──────────────────────────────

def test_un_membre_dequipe_corrige_la_vitrine_de_sa_procedure(monde):
    """Corriger la vitrine est une écriture : au palier équipe elle demande d'être
    MEMBRE, comme `set` (#681) — celui qui déroule la procédure est celui qui
    l'améliore, et le geste se défait."""
    slug = _slug("cloture-equipe")
    _appel("u-membre", op="create", scope="group", slug=slug, body_md=_CORPS,
           description="AVANT")

    out = _appel("u-membre", op="describe", scope="group", slug=slug,
                 description="APRÈS")
    assert out["group_id"] == monde["equipe"] and out["scope"] == "group"
    relue = _lu("u-membre", slug, scope="group")
    assert relue["description"] == "APRÈS" and relue["body_md"] == _CORPS
