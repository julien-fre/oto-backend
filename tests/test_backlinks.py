"""Backlinks [[…]] (lot 3 Ship 4) — extraction, résolution (précédence projet > KB,
N=0 souche, N>1 déterministe, pas d'auto-citation), hook db, op=backlinks filtré.

Extraction = pure. Résolution/hook = _connect factice (rows en mémoire).
"""
import pytest

from oto_mcp import db, ownership
from oto_mcp.db import backlinks as B


# ── extraction ───────────────────────────────────────────────────────────────

def test_extract_titles_dedup_and_normalize():
    body = "Voir [[Marché]] et [[ marché ]] puis [[Deal X]].\nEncore [[Deal X]]."
    # casse/espaces normalisés pour la clé → « Marché » une fois ; ordre d'apparition
    assert B.extract_titles(body) == ["Marché", "Deal X"]


def test_extract_ignores_empty_and_multiline():
    assert B.extract_titles("[[]] [[ \t ]] texte [[OK ici]]") == ["OK ici"]
    assert B.extract_titles("pas de lien") == []
    assert B.extract_titles("") == []


# ── résolution (conn factice) ────────────────────────────────────────────────

class _Cur:
    def __init__(self, rows): self._rows = rows
    def fetchone(self): return self._rows[0] if self._rows else None
    def fetchall(self): return self._rows


class _Conn:
    """Renvoie des rows scénarisés par motif SQL ; enregistre les INSERT doc_links."""
    def __init__(self, *, project=None, kb=None, docs=None):
        self.project = project        # row projects (owner_type/owner_id/context_org_id)
        self.kb = kb                  # kb_project_id de l'org
        self.docs = docs or []        # docs candidats (id/project_id/title)
        self.inserted: list[tuple] = []
        self.deleted = False

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("DELETE FROM doc_links"):
            self.deleted = True
            return _Cur([])
        if "FROM projects WHERE id" in s:
            return _Cur([self.project] if self.project else [])
        if "kb_project_id FROM orgs" in s:
            return _Cur([{"kb_project_id": self.kb}] if self.kb is not None else [{"kb_project_id": None}])
        if "FROM docs WHERE project_id = ANY" in s:
            scope = params[0]
            return _Cur([d for d in self.docs if d["project_id"] in scope])
        if s.startswith("INSERT INTO doc_links"):
            self.inserted.append((params[0], params[1]))
            return _Cur([])
        if "FROM doc_links l JOIN docs d" in s:
            return _Cur(self.docs)
        return _Cur([])


def _proj(owner_type="org", owner_id="7", ctx=None):
    return {"owner_type": owner_type, "owner_id": owner_id, "context_org_id": ctx}


def test_resolve_precedence_project_over_kb():
    # « Marché » existe dans le projet courant (1) ET la KB (9) → le projet gagne.
    c = _Conn(project=_proj(), kb=9, docs=[
        {"id": 100, "project_id": 1, "title": "Marché"},
        {"id": 200, "project_id": 9, "title": "Marché"},
    ])
    B.refresh_links(c, from_doc=5, project_id=1, body_md="cf [[Marché]]")
    assert c.deleted and c.inserted == [(5, 100)]


def test_resolve_falls_back_to_kb():
    c = _Conn(project=_proj(), kb=9, docs=[
        {"id": 200, "project_id": 9, "title": "Marché"},
    ])
    B.refresh_links(c, from_doc=5, project_id=1, body_md="[[marche]]" )  # casse/accent ? -> non
    # 'marche' (sans accent) ne matche pas 'Marché' → lien-souche, rien
    assert c.inserted == []
    c2 = _Conn(project=_proj(), kb=9, docs=[{"id": 200, "project_id": 9, "title": "Marché"}])
    B.refresh_links(c2, from_doc=5, project_id=1, body_md="[[Marché]]")
    assert c2.inserted == [(5, 200)]


def test_ambiguity_picks_lowest_id_same_tier():
    c = _Conn(project=_proj(), kb=None, docs=[
        {"id": 30, "project_id": 1, "title": "Note"},
        {"id": 12, "project_id": 1, "title": "Note"},
    ])
    B.refresh_links(c, from_doc=5, project_id=1, body_md="[[Note]]")
    assert c.inserted == [(5, 12)]           # N>1 même tier → plus petit id, JAMAIS création


def test_no_self_citation():
    c = _Conn(project=_proj(), kb=None, docs=[{"id": 5, "project_id": 1, "title": "Moi"}])
    B.refresh_links(c, from_doc=5, project_id=1, body_md="[[Moi]]")
    assert c.inserted == []


def test_stub_when_absent_still_clears_old():
    c = _Conn(project=_proj(), kb=None, docs=[])
    B.refresh_links(c, from_doc=5, project_id=1, body_md="[[Inconnu]]")
    assert c.deleted and c.inserted == []    # N=0 = souche (UI), rien stocké


def test_member_project_uses_context_org_kb():
    # projet perso (user) avec context_org_id → KB de cette org.
    c = _Conn(project=_proj(owner_type="user", owner_id="sub1", ctx=7), kb=9, docs=[
        {"id": 200, "project_id": 9, "title": "Charte"},
    ])
    B.refresh_links(c, from_doc=5, project_id=1, body_md="[[Charte]]")
    assert c.inserted == [(5, 200)]


# ── op=backlinks : filtrage d'accès ──────────────────────────────────────────

def test_op_backlinks_filters_unreadable_projects(monkeypatch):
    from oto_mcp.capabilities.docs import core as D
    from oto_mcp.capabilities._types import ResolvedCtx
    monkeypatch.setattr(db, "get_doc_by_id",
                        lambda did: {"id": did, "project_id": 1, "title": "Cible"})
    monkeypatch.setattr(db, "doc_backlinks", lambda did: [
        {"id": 10, "project_id": 1, "title": "Page lisible"},
        {"id": 11, "project_id": 99, "title": "Page d'un projet interdit"},
    ])
    # lisible : projet 1 ; interdit : projet 99
    monkeypatch.setattr(ownership, "can_access",
                        lambda sub, t, rid, want="read": str(rid) == "1")
    out = D._doc(ResolvedCtx(sub="u1", org_id=1), D.DocInput(op="backlinks", doc_id=5))
    assert out["count"] == 1 and out["backlinks"][0]["id"] == 10


def test_des_citations_MASQUEES_par_l_acces_sont_dites(monkeypatch):
    """oto#42, entrée 4. Le filtrage retirait des citations en silence, et quand il
    les retirait TOUTES, le hint affirmait « personne ne cite encore cette page ».

    Une phrase fausse servie à un agent qui n'avait aucun moyen de le savoir — et les
    deux situations appellent des gestes OPPOSÉS : demander un accès, ou écrire le
    lien qui manque. C'est le mode de panne de la classe : l'agent cherche, ne voit
    rien, et conclut à une absence.

    ⚠️ Le NOMBRE de citations masquées n'est pas rendu : il révélerait combien de
    pages existent dans des projets fermés à l'appelant. Le fait qu'il y en ait
    suffit à corriger le geste."""
    from oto_mcp.capabilities.docs import core as D
    from oto_mcp.capabilities._types import ResolvedCtx
    monkeypatch.setattr(db, "get_doc_by_id",
                        lambda did: {"id": did, "project_id": 1, "title": "Cible"})
    monkeypatch.setattr(db, "doc_backlinks", lambda did: [
        {"id": 11, "project_id": 99, "title": "Citation d'un projet fermé"},
    ])
    monkeypatch.setattr(ownership, "can_access",
                        lambda sub, t, rid, want="read": str(rid) == "1")
    out = D._doc(ResolvedCtx(sub="u1", org_id=1), D.DocInput(op="backlinks", doc_id=5))
    assert out["count"] == 0 and out["backlinks"] == []
    assert out["hidden_by_access"] is True
    assert "PARTIEL" in out["hidden_hint"]
    # LE point : le hint « personne ne cite » ne doit PAS être servi ici.
    assert "hint" not in out, (
        "« Personne ne cite encore cette page » alors que trois pages la citent — "
        "c'est la phrase fausse que cette entrée corrige")
    # Et le nombre ne fuit pas.
    assert "1" not in out.get("hidden_hint", "")


def test_aucune_citation_ni_masquee_garde_le_hint_pedagogique(monkeypatch):
    """Le vrai zéro garde son conseil : c'est le cas #244, où trois formats de lien
    avaient été essayés en vain sans le moindre indice. On ne l'a pas remplacé, on l'a
    borné au cas où il est VRAI."""
    from oto_mcp.capabilities.docs import core as D
    from oto_mcp.capabilities._types import ResolvedCtx
    monkeypatch.setattr(db, "get_doc_by_id",
                        lambda did: {"id": did, "project_id": 1, "title": "Cible"})
    monkeypatch.setattr(db, "doc_backlinks", lambda did: [])
    monkeypatch.setattr(ownership, "can_access", lambda sub, t, rid, want="read": True)
    out = D._doc(ResolvedCtx(sub="u1", org_id=1), D.DocInput(op="backlinks", doc_id=5))
    assert "Personne ne cite encore" in out["hint"]
    assert "hidden_by_access" not in out


def test_op_backlinks_empty_says_how_a_backlink_is_made(monkeypatch):
    """Un zéro muet se lit comme « la fonction est cassée » : trois formats de lien
    ont été essayés en vrai, tous inertes, sans le moindre indice (signal #244)."""
    from oto_mcp.capabilities.docs import core as D
    from oto_mcp.capabilities._types import ResolvedCtx
    monkeypatch.setattr(db, "get_doc_by_id",
                        lambda did: {"id": did, "project_id": 1, "title": "Charte"})
    monkeypatch.setattr(db, "doc_backlinks", lambda did: [])
    monkeypatch.setattr(ownership, "can_access", lambda sub, t, rid, want="read": True)
    out = D._doc(ResolvedCtx(sub="u1", org_id=1), D.DocInput(op="backlinks", doc_id=5))
    assert out["count"] == 0
    assert "[[Charte]]" in out["hint"]          # le titre exact, prêt à copier
    assert "doc:ID" in out["hint"]              # …et ce qui NE marche pas


def test_op_backlinks_non_empty_has_no_hint(monkeypatch):
    from oto_mcp.capabilities.docs import core as D
    from oto_mcp.capabilities._types import ResolvedCtx
    monkeypatch.setattr(db, "get_doc_by_id",
                        lambda did: {"id": did, "project_id": 1, "title": "Charte"})
    monkeypatch.setattr(db, "doc_backlinks",
                        lambda did: [{"id": 10, "project_id": 1, "title": "P"}])
    monkeypatch.setattr(ownership, "can_access", lambda sub, t, rid, want="read": True)
    out = D._doc(ResolvedCtx(sub="u1", org_id=1), D.DocInput(op="backlinks", doc_id=5))
    assert "hint" not in out


# ── Le fait mesuré : résolution ÉTROITE, rendu SANS PORTÉE (signal #696) ─────
#
# Deux surfaces se contredisaient pour un agent qui réorganisait une org en
# projets : l'accusé d'écriture jurait que la résolution ne regarde que « ce
# projet puis la base de connaissance de l'org — et rien d'autre », pendant que
# `op=backlinks` rendait, sur une page, des liens entrants venus d'un AUTRE
# projet. L'agent, faute de savoir laquelle fait foi, a réécrit tous ses renvois
# inter-projets en clair et perdu la navigation.
#
# Les deux disent vrai, parce qu'elles ne parlent pas du même périmètre :
#   · à l'ÉCRITURE, `refresh_links` résout contre `[projet, KB]` (backlinks.py) ;
#   · à la LECTURE, `backlinks_of` rend TOUTE ligne `doc_links` pointant ici,
#     sans le moindre filtre de projet — seul l'accès borne (reads.py).
# Et rien ne recale les liens ENTRANTS d'une page déplacée : `move_doc_to_project`
# ne re-résout que les liens SORTANTS des pages déplacées. Une ligne stockée
# survit donc au déplacement de sa cible, hors de toute portée de résolution,
# et ne meurt qu'à la prochaine écriture de la page qui cite.
#
# Ce banc MESURE cette survie sur un vrai PostgreSQL, par le chemin servi, puis
# exige que l'avertissement d'écriture dise ce qui vient d'être mesuré.

import os
import uuid


@pytest.fixture(scope="module")
def monde(pg_dsn):
    """Base JETABLE bootée par le vrai `init_db` (recette #662 : la base du
    conteneur est PARTAGÉE, y monter le schéma entier casse d'autres fichiers).

    Deux projets PERSO, sans org : `_kb_project_of` rend None, donc la portée de
    résolution est le seul projet courant — le cas le plus étroit possible."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_696_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        sub = "u-696"
        a = db.create_project("user", sub, "Projet A", created_by=sub)
        b = db.create_project("user", sub, "Projet B", created_by=sub)
        yield {"sub": sub, "a": int(a), "b": int(b)}
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


def _servi(sub: str, **args) -> dict:
    """UN appel d'`oto_doc` par le chemin servi (dispatcher + gates), pas par le
    store : c'est ce que voit l'agent, et c'est là que vivent les deux textes."""
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.docs import core as D
    return D._doc(ResolvedCtx(sub=sub, org_id=None), D.DocInput(**args))


def test_un_lien_STOCKE_survit_au_deplacement_de_sa_cible_et_reste_rendu(monde):
    """La contradiction du signal #696, reproduite de bout en bout — puis l'aveu.

    Rendre un lien entrant venu d'un projet hors de portée n'est PAS un démenti
    de l'avertissement : c'est une ligne que plus aucune écriture ne referait, et
    que la prochaine écriture de la page qui cite effacera sans un mot."""
    sub, a, b = monde["sub"], monde["a"], monde["b"]
    cible = _servi(sub, op="create", project_id=a, title="Outbound Email Conventions")
    citante = _servi(sub, op="create", project_id=a, title="Playbook",
                     body_md="cf [[Outbound Email Conventions]]")
    assert _servi(sub, op="backlinks", doc_id=cible["id"])["count"] == 1

    # La cible part dans un AUTRE projet — l'exact geste « réorganiser en projets ».
    _servi(sub, op="move", doc_id=cible["id"], to_project=b)

    vus = _servi(sub, op="backlinks", doc_id=cible["id"])
    assert vus["count"] == 1, (
        "le lien entrant survit au déplacement : rien ne recale les liens ENTRANTS")
    assert vus["backlinks"][0]["project_id"] == a, (
        "…et il est rendu depuis un projet qui n'est NI le projet de la cible NI "
        "une KB : le rendu n'a aucune portée, seul l'accès le borne")

    # Or la même citation, réécrite aujourd'hui, ne résout plus rien : le lien
    # affiché est un RESTE, pas une preuve que la portée serait plus large.
    accuse = _servi(sub, op="update", doc_id=citante["id"],
                    body_md="cf [[Outbound Email Conventions]]")
    assert accuse["citations_sans_cible"] == ["Outbound Email Conventions"]
    assert _servi(sub, op="backlinks", doc_id=cible["id"])["count"] == 0, (
        "la réécriture l'a effacé — c'est la « navigation qui disparaît » du signal")

    # L'avertissement doit dire CE QUI VIENT D'ÊTRE MESURÉ, sinon il pousse à
    # nouveau son lecteur à croire l'une des deux surfaces au hasard.
    hint = accuse["citations_sans_cible_hint"]
    assert "op=backlinks" in hint, (
        "l'autre surface doit être NOMMÉE : c'est elle qui affiche le contraire")
    assert "déplac" in hint.casefold(), (
        "…et la cause de l'écart — un lien stocké avant un déplacement — sinon "
        "le lecteur conclut que l'avertissement ment")
