"""Archiver un espace rend sa place au quota de création — et l'espace personnel
n'en a jamais pris.

Le compte derrière le plafond de `org.create` était `COUNT(*) … WHERE created_by = %s`,
sans autre clause. Deux conséquences, qui se composent en cul-de-sac :

- **l'archivage ne libérait rien.** `archive_org` pose `archived_at` (soft-delete : il
  n'existe aucun hard-delete, les FK restent pour l'audit) et l'org quitte tous les
  listings — mais elle continuait de compter. Or c'est le SEUL geste par lequel un
  compte peut redescendre sous le plafond : arrivé au plafond, il y restait, et le
  refus portait sur un nombre que plus rien ne pouvait faire baisser ;
- **l'espace personnel occupait une place.** Il est posé d'office par
  `ensure_personal_org` (avec `created_by = sub`, donc compté) et son archivage est
  refusé — le plafond RÉEL valait donc un de moins que celui annoncé par le message.

Banc : un PostgreSQL **réel**. Le défaut est un prédicat SQL — deux clauses `WHERE`
absentes — et un prédicat ne s'exerce pas contre un stub, qui ne mesurerait que sa
propre fidélité. Les tables viennent du DDL réel (`db/_schema.py`) et les colonnes
`archived_at` / `personal_of`, qui portent tout le correctif, des migrations réelles
(`db/_init.py`) : elles n'existent QUE là, un banc qui les reconstituerait à la main
testerait la représentation qu'on s'en fait.
"""
from __future__ import annotations

import ast
import re
from contextlib import contextmanager

import pytest

from oto_mcp import org_store, session_org
from oto_mcp.capabilities import orgs, orgs_update
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.db import _conn as _conn_mod, _init, _schema, users as db_users

# Identités synthétiques : le défaut se reproduit avec n'importe quel compte.
SUB = "sub-test-1"
AUTRE = "sub-test-2"


# ── le banc : DDL réel + migrations réelles ──────────────────────────────────

def _real_ddl(table: str) -> str:
    """Le `CREATE TABLE` de `table`, extrait du schéma RÉEL (`db/_schema.py`)."""
    m = re.search(rf"^CREATE TABLE IF NOT EXISTS {table} \(.*?^\);",
                  _schema._SCHEMA, re.S | re.M)
    assert m, f"DDL de `{table}` introuvable dans _schema.py"
    return m.group(0)


def _real_orgs_migrations() -> list[str]:
    """Les migrations RÉELLES de `orgs` (`db/_init.py`), lues à l'AST.

    `archived_at` et `personal_of` — les deux colonnes du correctif — sont ajoutées
    par migration, pas par `_SCHEMA` : les recopier ici ferait diverger le banc du
    système au premier changement de type ou de nom.

    On écarte les seules migrations porteuses d'un `REFERENCES` (`tenant_id`,
    `kb_project_id`) : elles exigeraient `tenants`/`projects`, hors sujet ici, et les
    inventer serait exactement le DDL de complaisance qu'on refuse. Le filtre est sur
    la FORME du statement, pas sur une liste de colonnes — une colonne ajoutée demain
    entre donc dans le banc toute seule.
    """
    tree = ast.parse(open(_init.__file__, encoding="utf-8").read())
    out = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and (node.value.startswith("ALTER TABLE orgs ADD COLUMN IF NOT EXISTS")
             or node.value.startswith("CREATE UNIQUE INDEX IF NOT EXISTS uq_orgs_"))
        and "REFERENCES" not in node.value
    ]
    for col in ("archived_at", "personal_of"):
        assert any(col in s for s in out), f"migration de `orgs.{col}` introuvable"
    return out


@pytest.fixture()
def conn(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    # La row factory RÉELLE : les rows du serveur sont des dicts dont les dates sont
    # déjà normalisées en chaînes. Un `dict_row` nu ferait diverger le banc du système
    # sur le seul type que le code historique suppose.
    with psycopg.connect(pg_dsn, row_factory=_conn_mod._str_dict_row,
                         autocommit=True) as c:
        for t in ("org_group_members", "org_groups", "org_members", "orgs", "users"):
            c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        for t in ("users", "orgs", "org_members", "org_groups", "org_group_members"):
            c.execute(_real_ddl(t))
        for stmt in _real_orgs_migrations():
            c.execute(stmt)
        yield c


@pytest.fixture()
def store(conn, monkeypatch):
    """`org_store` (et l'`upsert_user` qu'il appelle) branchés sur la connexion du
    banc. Les deux modules importent `_connect` dans LEUR namespace : patcher le pool
    seul laisserait `db.users` parler à la vraie base."""
    @contextmanager
    def _connect_test():
        yield conn

    monkeypatch.setattr(org_store, "_connect", _connect_test)
    monkeypatch.setattr(db_users, "_connect", _connect_test)
    # Comptes déjà inscrits : `upsert_user` ne doit PAS rejouer ici ses effets de
    # première inscription (réconciliation d'invitation, `ensure_personal_org`), qui
    # poseraient un espace personnel dans le dos du test — c'est ce test qui décide
    # quel compte en a un, et quand.
    for sub in (SUB, AUTRE):
        conn.execute("INSERT INTO users (sub) VALUES (%s)", (sub,))
    return org_store


def _espace(store, sub: str, name: str) -> int:
    """Un espace créé par `sub`, dont il est admin — la forme que produit `org.create`."""
    oid = store.create_org(name, created_by=sub)
    store.add_org_member(oid, sub, "org_admin")
    return oid


def _perso(store, sub: str, name: str = "Mon espace") -> int:
    """L'espace PERSONNEL de `sub` : `ensure_personal_org` le crée exactement ainsi
    (créé par lui, puis marqué `personal_of`)."""
    oid = _espace(store, sub, name)
    with store._connect() as c:
        c.execute("UPDATE orgs SET personal_of = %s WHERE id = %s", (sub, oid))
    return oid


# ── le compte lui-même ───────────────────────────────────────────────────────

def test_archiver_rend_la_place(store):
    """Le cœur du défaut : l'org quittait les listings sans rendre sa place."""
    _espace(store, SUB, "Espace A")
    b = _espace(store, SUB, "Espace B")
    assert store.count_orgs_created_by(SUB) == 2

    assert store.archive_org(b) is True
    assert store.count_orgs_created_by(SUB) == 1
    # L'org est bien sortie des listings — c'est la moitié qui marchait déjà, et ce
    # qui rendait le symptôme illisible : l'utilisateur ne la voyait plus.
    assert b not in [o["org_id"] for o in store.list_orgs_for_user(SUB)]


def test_l_espace_personnel_ne_prend_pas_de_place(store):
    """Il est posé d'office, sans que le compte l'ait demandé : le facturer rendait
    le plafond réel inférieur d'un au plafond annoncé."""
    _perso(store, SUB)
    assert store.count_orgs_created_by(SUB) == 0

    _espace(store, SUB, "Espace A")
    assert store.count_orgs_created_by(SUB) == 1


def test_le_compte_reste_celui_des_espaces_CREES(store):
    """Le message dit « créés » ; l'axe `created_by` n'a pas bougé. Rejoindre l'espace
    d'autrui n'a jamais consommé de place et ne doit pas commencer."""
    autrui = _espace(store, AUTRE, "Espace d'autrui")
    store.add_org_member(autrui, SUB, "org_member")

    assert store.count_orgs_created_by(SUB) == 0
    assert store.count_orgs_created_by(AUTRE) == 1


def test_archiver_deux_fois_ne_rend_pas_deux_places(store):
    """`archive_org` est idempotent (False au 2ᵉ appel) : le compte ne doit pas
    dériver sous zéro à coups de rejeux."""
    a = _espace(store, SUB, "Espace A")
    assert store.archive_org(a) is True
    assert store.archive_org(a) is False
    assert store.count_orgs_created_by(SUB) == 0


# ── la séquence complète, par la capacité ────────────────────────────────────

@pytest.fixture()
def plafond(monkeypatch):
    """Plafond ramené à 3 : le défaut ne dépend pas de sa valeur, et un banc à 10
    n'exercerait rien de plus."""
    monkeypatch.setattr(orgs, "_MAX_ORGS_PER_USER", 3)
    return 3


def _creer(sub: str, name: str) -> dict:
    return orgs._create_org(ResolvedCtx(sub=sub), orgs.CreateOrgInput(name=name))


def _archiver(sub: str, org_id: int) -> dict:
    return orgs_update._archive_org(ResolvedCtx(sub=sub, org_id=org_id),
                                    orgs_update.OrgIdInput(org_id=org_id))


def test_la_sequence_du_cul_de_sac(store, plafond, monkeypatch):
    """La reproduction, de bout en bout : plafond atteint → refus ; archiver l'espace
    personnel → refusé ; archiver un autre espace → la création repasse."""
    monkeypatch.setattr(session_org, "current_session_id", lambda: None)

    _perso(store, SUB)
    ids = [_creer(SUB, f"Espace {i}")["org_id"] for i in range(plafond)]

    with pytest.raises(AuthzDenied) as refus:
        _creer(SUB, "Un de trop")
    assert refus.value.status == 429 and refus.value.code == "org_quota"

    # L'espace personnel d'AUTRUI ne peut pas être rendu : il n'est de toute façon
    # pas une de nos places (depuis 2026-08-25 le sien, lui, est supprimable — voir
    # `test_le_solo_supprime_son_espace_personnel`).
    with pytest.raises(AuthzDenied) as perso:
        _archiver(SUB, _perso(store, AUTRE, "L'espace d'autrui"))
    assert perso.value.code == "personal_org"

    assert _archiver(SUB, ids[0])["archived"] is True

    # C'est l'étape qui échouait : le même refus, mot pour mot, après l'archivage.
    assert _creer(SUB, "Le remplaçant")["org_id"] not in ids
    assert store.count_orgs_created_by(SUB) == plafond


def test_le_refus_dit_le_compte_et_le_remede(store, plafond, monkeypatch):
    """Un refus qui n'annonce que son plafond laisse l'appelant sans savoir ce qui
    l'occupe ni comment redescendre — l'archivage n'est deviné par personne."""
    monkeypatch.setattr(session_org, "current_session_id", lambda: None)
    _perso(store, SUB)
    for i in range(plafond):
        _creer(SUB, f"Espace {i}")

    with pytest.raises(AuthzDenied) as refus:
        _creer(SUB, "Un de trop")
    msg = refus.value.message
    assert f"{plafond}/{plafond}" in msg      # le compte courant, pas que le plafond
    assert "rchive" in msg                    # le geste qui libère une place


# ── l'espace personnel d'un compte SOLO ──────────────────────────────────────
#
# Un user seul n'a QUE son espace personnel : le refuser en bloc le laissait sans
# aucun moyen de supprimer quoi que ce soit (front : le bouton était même caché).
# Ce qui reste refusé, c'est ce qui n'est pas à lui, ou ce qui strandrait autrui.


def test_le_solo_supprime_son_espace_personnel(store, monkeypatch):
    """Le geste que le compte solo n'avait pas : son unique espace s'archive, sort
    des listings, et relâche le slot `personal_of` (sinon `ensure_personal_org`
    boucle sur une UniqueViolation à chaque boot)."""
    monkeypatch.setattr(session_org, "current_session_id", lambda: None)
    perso = _perso(store, SUB)

    assert _archiver(SUB, perso)["archived"] is True

    with store._connect() as c:
        row = c.execute("SELECT personal_of, archived_at FROM orgs WHERE id = %s",
                        (perso,)).fetchone()
    assert row["personal_of"] is None and row["archived_at"] is not None
    assert perso not in [o["org_id"] for o in store.list_orgs_for_user(SUB)]


def test_le_solo_ne_reste_pas_sans_aucune_org(store, monkeypatch):
    """« Tout user a TOUJOURS une org maison » (db/users.py) est un invariant que le
    reste du backend suppose : le geste qui vide le compte le rétablit dans la foulée,
    au lieu de le laisser org-less jusqu'au prochain boot. L'espace reposé est NEUF —
    l'ancien reste archivé, hors de tous les listings."""
    monkeypatch.setattr(session_org, "current_session_id", lambda: None)
    perso = _perso(store, SUB)

    _archiver(SUB, perso)

    repose = store.get_personal_org(SUB)
    assert repose is not None and repose != perso
    assert store.get_active_org(SUB) == repose
    assert [o["org_id"] for o in store.list_orgs_for_user(SUB)] == [repose]


def test_l_espace_repose_ne_l_est_que_si_le_compte_est_VIDE(store, monkeypatch):
    """Le filet ne se déclenche que sur zéro org restante : archiver un espace parmi
    d'autres ne doit pas faire surgir un espace perso que le compte n'a pas demandé."""
    monkeypatch.setattr(session_org, "current_session_id", lambda: None)
    a = _espace(store, SUB, "Espace A")
    _espace(store, SUB, "Espace B")

    _archiver(SUB, a)

    assert store.get_personal_org(SUB) is None
    assert len(store.list_orgs_for_user(SUB)) == 1


def test_l_espace_personnel_d_autrui_reste_refuse(store, monkeypatch):
    """`ORG_ADMIN_OF` s'obtient aussi par escalade platform_admin : sans cette garde,
    ce chemin self-service effacerait l'espace PRIVÉ d'un tiers."""
    monkeypatch.setattr(session_org, "current_session_id", lambda: None)
    autrui = _perso(store, AUTRE)

    with pytest.raises(AuthzDenied) as refus:
        _archiver(SUB, autrui)
    assert refus.value.status == 400 and refus.value.code == "personal_org"
    assert store.get_personal_org(AUTRE) == autrui   # intact


def test_un_espace_perso_qui_gagne_un_membre_n_est_plus_perso(store, monkeypatch):
    """Pourquoi la capacité ne porte PAS de garde « et seulement s'il est seul » :
    elle serait morte. Le store tient déjà l'invariant — `add_org_member` efface
    `personal_of` au 2ᵉ membre (correctif 2026-08-04) — donc « perso » implique
    « solo », et l'espace qui a gagné un coéquipier s'archive par la voie ordinaire."""
    monkeypatch.setattr(session_org, "current_session_id", lambda: None)
    perso = _perso(store, SUB)
    store.add_org_member(perso, AUTRE, "org_member")

    assert store.is_personal_org(perso) is False
    assert store.get_personal_org(SUB) is None
    assert _archiver(SUB, perso)["archived"] is True
