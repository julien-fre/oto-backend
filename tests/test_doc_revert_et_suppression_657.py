"""Les pages savent revenir en arrière, et la suppression dit ce qu'elle emporte (#657).

Retour d'un **front tiers**, consommateur pur de l'API REST : deux trous, tous deux
vérifiés dans le code avant d'être bouchés.

1. **Le snapshot était pris, la restauration n'existait pas.** `update_doc` archive
   l'état antérieur dans `doc_revisions` depuis toujours ; aucune `op` ne le REPOSAIT.
   Le retour arrière se faisait à la main — lire `op=revisions`, republier le corps par
   `op=update` — ce qu'un front ne peut pas offrir comme un geste. Le précédent existait
   ailleurs (`org.instruction.revert`), et c'est son régime qu'on reprend : **on restaure
   EN AVANT**. L'état courant est snapshotté à son tour, donc rien n'est perdu et un
   revert se re-revert.
2. **La suppression cascadait en silence.** La FK auto-référente emporte tout le
   sous-arbre (et avec lui `doc_revisions`, `doc_change_requests`, `doc_links`) ; la
   réponse disait `{ok, id, deleted}` et rien d'autre. Un front ne pouvait donc ni
   annoncer « ceci supprimera N pages », ni prévenir que c'était sans retour.

⚠️ **Deux gestes différents, pas un.** `revert` restaure une VERSION d'une page qui
existe encore ; il ne ressuscite pas une page supprimée — ses révisions sont parties
avec elle. La confusion est le piège de cette issue, et deux tests la fixent : celui
qui vérifie que le refus le DIT, et le live qui prouve qu'après un `delete` il ne reste
aucune ligne à restaurer.

Le banc live n'est pas un luxe : ce qui est réellement neuf ici est du SQL — une
suppression récursive qui compte ce qu'elle retire, et une lecture de révision bornée
par `doc_id`. Ni l'une ni l'autre ne s'exerce contre un stub.
"""
from __future__ import annotations

import uuid

import pytest

from oto_mcp import db, ownership
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.docs import core as D

CTX = ResolvedCtx(sub="u1", org_id=None)
DOC = {"id": 3, "project_id": 7, "parent_id": None, "title": "Page", "body_md": "neuf",
       "kind": "doc", "created_at": "2026-08-31", "updated_at": "2026-08-31"}
REV = {"id": 42, "doc_id": 3, "title": "Titre d'avant", "body_md": "corps d'avant",
       "edited_by": "u1", "created_at": "2026-08-30"}


@pytest.fixture
def seams(monkeypatch):
    rec = {"update": [], "delete": [], "revision": [], "count": []}
    monkeypatch.setattr(ownership, "can_access", lambda sub, t, rid, want="read": True)
    monkeypatch.setattr(db, "get_doc_by_id", lambda i: dict(DOC, id=i) if i == 3 else None)
    monkeypatch.setattr(db, "update_doc",
                        lambda did, title=None, body_md=None, kind=None, edited_by=None,
                        description=None, expected_rev=None:
                        rec["update"].append((did, title, body_md, edited_by, expected_rev)))
    monkeypatch.setattr(db, "get_doc_revision",
                        lambda did, rid: rec["revision"].append((did, rid)) or (
                            dict(REV) if (did, rid) == (3, 42) else None))
    monkeypatch.setattr(db, "delete_doc",
                        lambda did: rec["delete"].append(did) or 4)
    monkeypatch.setattr(db, "count_doc_descendants",
                        lambda did: rec["count"].append(did) or 4)
    monkeypatch.setattr(db, "log_project_activity", lambda *a, **k: None)
    return rec


# ── op=revert ────────────────────────────────────────────────────────────────

def test_revert_repose_le_titre_et_le_corps_de_la_version_visee(seams):
    out = D._doc(CTX, D.DocInput(op="revert", doc_id=3, revision_id=42))
    assert seams["revision"] == [(3, 42)]
    # Titre ET corps : une version restaurée sous le titre courant serait un hybride
    # qui n'a jamais existé.
    assert seams["update"] == [(3, "Titre d'avant", "corps d'avant", "u1", None)]
    assert out["reverted_from"] == 42


def test_revert_passe_par_update_doc_donc_snapshotte_l_etat_courant(seams):
    """Le régime « en avant » ne tient pas parce qu'on le dit : il tient parce que
    l'écriture emprunte `update_doc`, seul chemin qui archive l'état antérieur (et qui
    re-résout les backlinks, propage un renommage, garde le conflit optimiste). Un
    UPDATE de son cru perdrait les quatre d'un coup, et personne ne le verrait."""
    D._doc(CTX, D.DocInput(op="revert", doc_id=3, revision_id=42))
    assert len(seams["update"]) == 1, "l'écriture ne doit passer que par update_doc"


def test_revert_honore_expected_rev(seams):
    """Restaurer sans garde, c'est écraser l'édition qu'un pair vient de faire — les
    pages ont une garde optimiste, `revert` la porte comme `update` et `patch`."""
    D._doc(CTX, D.DocInput(op="revert", doc_id=3, revision_id=42, expected_rev="abc"))
    assert seams["update"][0][-1] == "abc"


def test_revert_sur_une_page_modifiee_entre_temps_rend_409(seams, monkeypatch):
    def _conflit(*a, **k):
        raise db.DocConflict("cafe1234")
    monkeypatch.setattr(db, "update_doc", _conflit)
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="revert", doc_id=3, revision_id=42, expected_rev="vieux"))
    assert (e.value.status, e.value.code) == (409, "conflict")
    assert "cafe1234" in e.value.message


def test_revert_sans_revision_id_refuse_en_nommant_ou_la_trouver(seams):
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="revert", doc_id=3))
    assert (e.value.status, e.value.code) == (400, "missing_revision")
    assert "op=revisions" in e.value.message
    assert not seams["update"], "rien ne doit être écrit sur un refus"


def test_revert_vers_une_revision_inconnue_rend_404_et_n_ecrit_rien(seams):
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="revert", doc_id=3, revision_id=999))
    assert (e.value.status, e.value.code) == (404, "unknown_revision")
    assert not seams["update"]


def test_revert_exige_l_ecriture(seams, monkeypatch):
    monkeypatch.setattr(ownership, "can_access",
                        lambda sub, t, rid, want="read": want == "read")
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="revert", doc_id=3, revision_id=42))
    assert (e.value.status, e.value.code) == (403, "forbidden")


def test_revert_n_est_pas_servi_a_un_lecteur_de_projet_publie(seams):
    """Un projet publié sans login est en LECTURE seule : `revert` est une écriture,
    il tombe avec les autres — sinon un visiteur anonyme rembobinerait la page."""
    with pytest.raises(AuthzDenied) as e:
        D._doc(ResolvedCtx(sub=None, org_id=None),
               D.DocInput(op="revert", doc_id=3, revision_id=42))
    assert (e.value.status, e.value.code) == (403, "forbidden")


def test_revision_id_sur_une_autre_op_est_REFUSE_pas_avale(seams):
    """Un argument accepté-et-ignoré coûte ce qu'il prétendait économiser (#461) : ici
    l'appelant croirait avoir restauré."""
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="get", doc_id=3, revision_id=42))
    assert (e.value.status, e.value.code) == (400, "unsupported_revision_id")


# ── op=delete : annoncer avant, déclarer après ───────────────────────────────

def test_delete_declare_le_nombre_de_pages_emportees(seams):
    out = D._doc(CTX, D.DocInput(op="delete", doc_id=3))
    assert seams["delete"] == [3]
    assert (out["deleted"], out["descendants"]) == (True, 4)
    # La perte se dit au moment où elle a lieu (même posture que `removed_subsections`
    # sur op=patch), sinon elle ne se découvre qu'à l'usage.
    assert "4 sous-page(s)" in out["warning"]


def test_dry_run_ne_supprime_rien_et_rend_le_meme_compte(seams):
    out = D._doc(CTX, D.DocInput(op="delete", doc_id=3, dry_run=True))
    assert seams["delete"] == [], "un dry_run qui supprime est le pire des deux mondes"
    assert seams["count"] == [3]
    assert (out["deleted"], out["dry_run"], out["descendants"]) == (False, True, 4)


def test_dry_run_dit_que_revert_ne_defait_pas_une_suppression(seams):
    """Le piège de l'issue : `revert` restaure une version, il ne ressuscite pas une
    page. Un front qui confondrait les deux proposerait une annulation qui n'existe
    pas — le seul moment où on peut le dire est AVANT."""
    out = D._doc(CTX, D.DocInput(op="delete", doc_id=3, dry_run=True))
    assert "op=revert" in out["hint"] and "SANS RETOUR" in out["hint"]


def test_dry_run_exige_l_ecriture_comme_la_suppression(seams, monkeypatch):
    """Le compte des pages d'un sous-arbre est une information sur le contenu : il ne
    se donne pas plus largement que le geste qu'il prépare."""
    monkeypatch.setattr(ownership, "can_access",
                        lambda sub, t, rid, want="read": want == "read")
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="delete", doc_id=3, dry_run=True))
    assert (e.value.status, e.value.code) == (403, "forbidden")


def test_dry_run_sur_une_autre_op_est_REFUSE_pas_avale(seams):
    """Le refus le plus important du lot : `dry_run` avalé par une op qui écrit pour de
    bon détruirait exactement ce que l'appelant croyait simuler."""
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="update", doc_id=3, body_md="x", dry_run=True))
    assert (e.value.status, e.value.code) == (400, "unsupported_dry_run")
    assert not seams["update"]


# ── Le SQL, contre un vrai PostgreSQL ────────────────────────────────────────

@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_doc657_" + uuid.uuid4().hex[:8]
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


def _projet(nom: str) -> int:
    from oto_mcp import db
    return db.create_project("user", "u_657", nom, created_by="u_657")


def test_live_le_compte_couvre_tout_le_SOUS_ARBRE_pas_les_enfants_directs(live):
    """La cascade suit `parent_id` jusqu'au bout : un compte des seuls enfants directs
    annoncerait 1 là où 3 pages partent."""
    from oto_mcp import db
    pid = _projet("Arbre")
    racine = db.create_doc(pid, "Racine", created_by="u_657")
    enfant = db.create_doc(pid, "Enfant", parent_id=racine, created_by="u_657")
    db.create_doc(pid, "Petit-enfant", parent_id=enfant, created_by="u_657")

    assert db.count_doc_descendants(racine) == 2
    assert db.count_doc_descendants(enfant) == 1
    assert db.count_doc_descendants(db.create_doc(pid, "Feuille", created_by="u_657")) == 0


def test_live_delete_rend_ce_qu_il_a_retire_et_le_retire_vraiment(live):
    """La question n'est pas ce que la réponse annonce, mais ce que la base a perdu."""
    from oto_mcp import db
    pid = _projet("Suppression")
    racine = db.create_doc(pid, "Racine", created_by="u_657")
    enfant = db.create_doc(pid, "Enfant", parent_id=racine, created_by="u_657")
    petit = db.create_doc(pid, "Petit", parent_id=enfant, created_by="u_657")
    voisine = db.create_doc(pid, "Voisine", created_by="u_657")

    assert db.delete_doc(racine) == 2
    assert [db.get_doc_by_id(d) for d in (racine, enfant, petit)] == [None, None, None]
    assert db.get_doc_by_id(voisine) is not None, "la cascade ne déborde pas du sous-arbre"


def test_live_une_page_supprimee_emporte_ses_revisions(live):
    """Pourquoi `revert` ne peut pas défaire une suppression, prouvé plutôt qu'affirmé :
    après le DELETE il ne reste aucune ligne à restaurer."""
    from oto_mcp import db
    pid = _projet("Révisions perdues")
    did = db.create_doc(pid, "Page", body_md="v1", created_by="u_657")
    db.update_doc(did, body_md="v2", edited_by="u_657")
    assert len(db.list_doc_revisions(did)) == 1

    db.delete_doc(did)
    assert db.list_doc_revisions(did) == []


def test_live_une_revision_d_une_AUTRE_page_ne_se_lit_pas(live):
    """`doc_id` est dans le WHERE, pas seulement dans la signature. Sans lui, un id de
    révision emprunté à une autre page restaurerait son contenu ici — l'autz du
    call-site, qui ne connaît que `doc_id`, ne verrait rien passer."""
    from oto_mcp import db
    pid = _projet("Cloison")
    a = db.create_doc(pid, "A", body_md="a1", created_by="u_657")
    b = db.create_doc(pid, "B", body_md="b1", created_by="u_657")
    db.update_doc(a, body_md="a2", edited_by="u_657")
    rev_de_a = db.list_doc_revisions(a)[0]["id"]

    assert db.get_doc_revision(a, rev_de_a) is not None
    assert db.get_doc_revision(b, rev_de_a) is None


def test_live_un_revert_se_re_revert(live):
    """Le régime « en avant » de bout en bout : restaurer v1 archive v2, donc v2 reste
    atteignable. Rien n'est jamais rembobiné, l'historique ne fait que s'allonger."""
    from oto_mcp import db
    pid = _projet("Aller-retour")
    did = db.create_doc(pid, "Titre v1", body_md="corps v1", created_by="u_657")
    db.update_doc(did, title="Titre v2", body_md="corps v2", edited_by="u_657")

    v1 = db.get_doc_revision(did, db.list_doc_revisions(did)[0]["id"])
    db.update_doc(did, title=v1["title"], body_md=v1["body_md"], edited_by="u_657")
    apres = db.get_doc_by_id(did)
    assert (apres["title"], apres["body_md"]) == ("Titre v1", "corps v1")

    # L'état qu'on vient de quitter a été archivé à son tour : deux versions, pas une.
    versions = db.list_doc_revisions(did)
    assert [(r["title"], r["body_md"]) for r in versions] == [
        ("Titre v2", "corps v2"), ("Titre v1", "corps v1")]


def test_live_revert_de_bout_en_bout_par_la_capacite(live, monkeypatch):
    """Le geste tel qu'un front l'appelle : `op=revisions` puis `op=revert`, sur la
    vraie base. C'est la couture — la capacité, `get_doc_revision` et `update_doc`
    ensemble — que les doubles ne peuvent pas prouver."""
    from oto_mcp import db
    monkeypatch.setattr(ownership, "can_access", lambda sub, t, rid, want="read": True)
    pid = _projet("Bout en bout")
    did = db.create_doc(pid, "Avant", body_md="texte d'avant", created_by="u_657")
    D._doc(CTX, D.DocInput(op="update", doc_id=did, title="Après", body_md="texte d'après"))

    versions = D._doc(CTX, D.DocInput(op="revisions", doc_id=did))["revisions"]
    out = D._doc(CTX, D.DocInput(op="revert", doc_id=did, revision_id=versions[0]["id"]))

    assert out["reverted_from"] == versions[0]["id"]
    page = db.get_doc_by_id(did)
    assert (page["title"], page["body_md"]) == ("Avant", "texte d'avant")


def test_live_delete_de_bout_en_bout_annonce_puis_supprime(live, monkeypatch):
    """`dry_run` puis le vrai geste : le compte annoncé est celui qui part."""
    from oto_mcp import db
    monkeypatch.setattr(ownership, "can_access", lambda sub, t, rid, want="read": True)
    pid = _projet("Annonce")
    racine = db.create_doc(pid, "Racine", created_by="u_657")
    for i in range(3):
        db.create_doc(pid, f"Enfant {i}", parent_id=racine, created_by="u_657")

    annonce = D._doc(CTX, D.DocInput(op="delete", doc_id=racine, dry_run=True))
    assert (annonce["deleted"], annonce["descendants"]) == (False, 3)
    assert db.get_doc_by_id(racine) is not None, "un dry_run ne supprime rien"

    fait = D._doc(CTX, D.DocInput(op="delete", doc_id=racine))
    assert (fait["deleted"], fait["descendants"]) == (True, 3)
    assert db.get_doc_by_id(racine) is None
