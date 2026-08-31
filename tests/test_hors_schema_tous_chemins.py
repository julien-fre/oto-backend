"""`hors_schema` parle sur TOUS les chemins d'écriture, ou il ne sert à rien (#647).

**Le fait, campagne du 31/08/2026.** Une colonne non déclarée est née pendant un
passage sur un tableau `strict` — absente de l'instantané de départ, présente en base
à l'arrivée. **Cent trois travaux ont relevé `hors_schema` : tous à zéro.** Trois
lectures indépendantes disent zéro, la table dit un.

⚠️ **Ce que ça coûte, et pourquoi ce banc existe.** La valeur écrite est une donnée que
le contrat de la campagne interdit. Rangée hors schéma, elle est **invisible à tous les
contrôles qui lisent le schéma** — dont celui qui compte précisément ce type de donnée.
*Un rapporteur qui se tait par intermittence est pire qu'un rapporteur absent : son
zéro se lit comme une mesure.*

Ce banc pose donc la question sur la MATRICE, pas sur le cas vu : chaque chemin
d'écriture × colonne créée / colonne déjà là × `id` explicite / alias de réservation.
Il tourne sur une vraie base, par les mêmes appels que la campagne, et il interroge le
STOCKAGE pour établir que la colonne existe — jamais le retour de l'écriture.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_hs_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


# Le tableau de la campagne, réduit à ce qui porte la règle : `strict`, une clé
# métier, et une colonne-liste (les contacts, où le défaut du 29/08 s'était logé).
SCHEMA = {
    "key": "siren",
    "strict": True,
    "fields": [
        {"key": "siren", "type": "text"},
        {"key": "raison_sociale", "type": "text"},
        {"key": "contacts", "type": "list",
         "of": {"type": "object", "fields": [{"key": "nom"}, {"key": "fonction"}]}},
    ],
}

HORS = "entreprise_instagram"   # le nom réel de la colonne née le 31/08


def _store():
    from oto_mcp.datastore.core import make_store
    return make_store("sub-test")


@pytest.fixture
def table(live):
    from oto_mcp import db
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", "sub-test", ns)
    st = _store()
    st.set_schema(ns, SCHEMA)
    row = st.append_row(ns, {"siren": "552032534", "raison_sociale": "TEMOIN"})
    return st, ns, ns_id, row["_id"]


def _colonnes_en_base(ns_id: int, row_id: str) -> set:
    """Ce que porte LA BASE. Le relevé doit s'accorder avec elle, pas avec lui-même."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        r = conn.execute(
            "SELECT data FROM datastore_rows WHERE ns_id = %s AND row_id = %s",
            (ns_id, row_id)).fetchone()
    return set((r or {}).get("data") or {})


def _releve(st) -> list:
    return st.off_schema_report().get("hors_schema", [])


# ── La matrice : chaque chemin, colonne CRÉÉE ────────────────────────────────

def test_patch_par_id_releve_la_colonne_creee(table):
    """⚠️ LE chemin de l'incident : `data_write(id=…)` — le geste le plus courant
    d'un agent, et celui que l'alias de réservation emprunte."""
    st, ns, ns_id, rid = table
    st.update_row(ns, rid, {HORS: "https://example.invalid/x"})

    assert HORS in _colonnes_en_base(ns_id, rid), "la colonne EST née en base"
    assert HORS in _releve(st), "…et le relevé doit la nommer"


def test_append_releve_la_colonne_creee(table):
    st, ns, ns_id, _ = table
    row = st.append_row(ns, {"siren": "301234567", HORS: "x"})
    assert HORS in _colonnes_en_base(ns_id, row["_id"])
    assert HORS in _releve(st)


def test_le_lot_releve_la_colonne_creee(table):
    """Le lot fusionne sur la clé métier : c'est le chemin `write_rows`."""
    st, ns, ns_id, _ = table
    st.write_rows(ns, [{"siren": "552032534", HORS: "x"}])
    assert HORS in _releve(st)


def test_upsert_par_id_releve_la_colonne_creee(table):
    st, ns, ns_id, rid = table
    st.upsert_row(ns, rid, {"siren": "552032534", HORS: "x"})
    assert HORS in _releve(st)


# ── La matrice : colonne DÉJÀ hors schéma, réécrite ──────────────────────────

def test_la_colonne_DEJA_hors_schema_se_releve_a_chaque_ecriture(table):
    """⚠️ Le cas qui rend le relevé utile dans la durée : une fois la colonne née,
    chaque écriture qui la touche doit continuer de la nommer. Sinon le signal ne
    parle qu'une fois — au moment où personne ne regardait."""
    st, ns, ns_id, rid = table
    st.update_row(ns, rid, {HORS: "premier"})
    st2 = _store()                                  # relevé neuf, comme un autre appel
    st2.update_row(ns, rid, {HORS: "second"})
    assert HORS in _releve(st2), "la deuxième écriture doit la nommer aussi"


def test_une_ecriture_qui_ne_TOUCHE_pas_la_colonne_ne_la_releve_pas(table):
    """La borne du relevé, et elle est voulue : il nomme ce que LE GESTE pose, pas
    ce que la ligne porte. Sinon toute écriture sur une ligne déjà salie crierait,
    et le signal deviendrait un bruit de fond qu'on cesse de lire."""
    st, ns, ns_id, rid = table
    st.update_row(ns, rid, {HORS: "x"})
    st2 = _store()
    st2.update_row(ns, rid, {"raison_sociale": "AUTRE"})
    assert _releve(st2) == [], "un geste qui ne pose pas la colonne ne la relève pas"


# ── Deux formes qui ne sont pas RELEVÉES parce qu'elles sont REFUSÉES ────────
# Écrit après coup : j'attendais un relevé, la plateforme rend un refus. C'est mieux
# — une colonne qui n'existe pas n'a pas besoin d'être signalée — et il faut le figer,
# sinon une main future « corrigera » le silence en rouvrant la porte.

def test_une_cle_hors_schema_DANS_un_contact_est_REFUSEE(table):
    """Le défaut du cinquième passage (`contacts[].email_pattern`) ne peut plus se
    produire : un composite déclaré ferme ses attributs. *Contrairement au premier
    niveau, un attribut inconnu ne crée AUCUNE colonne libre* — il serait stocké là
    où rien ne le lit."""
    from oto_mcp.datastore.core import RowValidationError
    st, ns, ns_id, rid = table
    with pytest.raises(RowValidationError) as e:
        st.update_row(ns, rid, {"contacts": [{"nom": "Jo", "email_pro": "jo@x.fr"}]})
    assert "email_pro" in str(e.value) and "Rien n'a été écrit" in str(e.value)
    assert "email_pro" not in str(_colonnes_en_base(ns_id, rid))


# ── Le témoin négatif : pas de faux positif ──────────────────────────────────

def test_une_ecriture_entierement_declaree_ne_releve_RIEN(table):
    st, ns, ns_id, rid = table
    st.update_row(ns, rid, {"raison_sociale": "ACME",
                            "contacts": [{"nom": "Jo", "fonction": "Gérante"}]})
    assert _releve(st) == []


# ── Les FORMES de l'écriture, et c'est là que le silence se loge ─────────────
# ⚠️ Écrit après avoir constaté que les cinq chemins parlent : si le rapporteur se
# tait quand même en production, ce n'est pas le chemin qui varie, c'est la FORME de
# ce qu'on écrit. Une colonne du datastore s'écrit de trois façons — valeur nue,
# objet à couches, clé plate pointée — et un détecteur qui n'en connaît qu'une se
# tait sur les deux autres sans que rien ne le dise.

def test_colonne_hors_schema_ecrite_en_OBJET_A_COUCHES(table):
    """La forme que la campagne emploie partout : `{valeur, comment}`."""
    st, ns, ns_id, rid = table
    st.update_row(ns, rid, {HORS: {"valeur": "https://x.invalid/a",
                                   "comment": "site — pied de page"}})
    assert HORS in _colonnes_en_base(ns_id, rid), "la colonne EST née"
    assert HORS in _releve(st), "…et le relevé doit la nommer"


def test_une_cle_PLATE_POINTEE_est_REFUSEE_declaree_ou_non(table):
    """`entreprise_instagram.comment` en clé littérale : refusé des deux côtés, que
    la colonne de base soit déclarée ou non. Une colonne dont le nom porte un point
    serait invisible au filtre et au tri du même nom."""
    from oto_mcp.datastore.core import RowValidationError
    st, ns, ns_id, rid = table
    for cle in (f"{HORS}.comment", "raison_sociale.comment"):
        with pytest.raises(RowValidationError) as e:
            st.update_row(ns, rid, {cle: "site — pied de page"})
        assert "n'est pas un nom de colonne" in str(e.value)
    assert not any("." in k for k in _colonnes_en_base(ns_id, rid))
