"""Le semis des guides plateforme ATTEINT un environnement existant (oto#236).

Le semis n'insérait que les slugs absents : une mise à jour d'un guide dans le dépôt
n'atteignait donc jamais une base déjà peuplée. Mesuré le 13/09/2026 sur
`datastore-semantics`, ~250 lignes de retard en production, plusieurs livraisons —
un texte SERVI À L'AGENT, qui lui faisait appliquer des gestes retirés.

Le contrat tenu ici (validé le 13/09/2026) :

- la base reste la source ÉDITABLE (ADR 0042), le fichier est la source du SEMIS ;
- le semis pose l'empreinte de ce qu'il écrit, et au démarrage suivant :
  fichier changé + base intacte → mise à jour ; base éditée → conservée et SIGNALÉE ;
  même empreinte → aucune écriture ;
- une ligne d'avant l'empreinte n'est jamais devinée au démarrage ;
- rien ne refuse le démarrage : le témoin est un défaut de santé d'instance.

Les bancs `live` exigent un vrai PostgreSQL : c'est une décision prise SUR la donnée
écrite (l'empreinte relue, la ligne verrouillée), pas une logique pure — un stub la
rendrait verte sans rien prouver de ce qui a échoué en production.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

from oto_mcp import guide_store as G


# --------------------------------------------------------------------------- #
# Le rapport et ses défauts — sans base
# --------------------------------------------------------------------------- #

@pytest.fixture
def dossier_de_guides(tmp_path, monkeypatch):
    """Un dossier de guides à nous : la racine du dépôt n'est pas un banc."""
    monkeypatch.setattr(G, "_GUIDES_DIR", tmp_path)
    return tmp_path


def _ecrire(dossier, slug: str, corps: str, titre: str = "T", desc: str = "D") -> None:
    (dossier / f"{slug}.md").write_text(
        f"---\ntitle: {titre}\ndescription: {desc}\n---\n{corps}\n", encoding="utf-8")


def test_un_fichier_porte_l_empreinte_de_ce_que_le_semis_poserait(dossier_de_guides):
    _ecrire(dossier_de_guides, "g", "corps")
    from oto_mcp import db
    g = G.list_file_guides()[0]
    assert g["seed_sha256"] == db.empreinte_de_couche("T", "D", "corps")


def test_le_titre_compte_dans_l_empreinte(dossier_de_guides):
    """Un titre corrigé dans le dépôt doit atteindre la base : c'est ce que l'agent
    lit dans l'index avant de charger quoi que ce soit."""
    from oto_mcp import db
    assert (db.empreinte_de_couche("A", "D", "corps")
            != db.empreinte_de_couche("B", "D", "corps"))


def test_chaque_verdict_a_sa_case_dans_le_rapport(dossier_de_guides, monkeypatch):
    for slug in ("a", "b", "c", "d", "e"):
        _ecrire(dossier_de_guides, slug, "corps")
    verdicts = {"a": "seme", "b": "mis_a_jour", "c": "inchange",
                "d": "diverge", "e": "sans_empreinte"}
    from oto_mcp import db
    monkeypatch.setattr(db, "seed_guide_db",
                        lambda scope, owner, slug, *a, **k: verdicts[slug])
    monkeypatch.setattr(G, "_alerte", lambda *a, **k: None)
    r = G.seed_platform_guides()
    assert r["semes"] == ["a"] and r["mis_a_jour"] == ["b"] and r["inchanges"] == ["c"]
    assert r["divergents"] == ["d"] and r["sans_empreinte"] == ["e"] and not r["echecs"]


def test_un_guide_en_echec_ne_casse_pas_le_semis_des_autres(dossier_de_guides, monkeypatch):
    """Pas de refus au démarrage : la fenêtre du healthcheck est finie, et un boot
    qui ne bascule pas coûte plus cher qu'un guide non semé qui se DIT."""
    _ecrire(dossier_de_guides, "casse", "corps")
    _ecrire(dossier_de_guides, "sain", "corps")

    def semis(scope, owner, slug, *a, **k):
        if slug == "casse":
            raise RuntimeError("base injoignable")
        return "seme"

    from oto_mcp import db
    monkeypatch.setattr(db, "seed_guide_db", semis)
    alertes: list[str] = []
    monkeypatch.setattr(G, "_alerte", lambda m, *a: alertes.append(m % a if a else m))
    r = G.seed_platform_guides()
    assert r["semes"] == ["sain"] and "casse" in r["echecs"]
    assert any("guides non semés" in a and "casse" in a for a in alertes)
    defauts = {d["code"] for d in G.sante_du_semis()["defauts"]}
    assert defauts == {"guides_non_semes"}


def test_une_divergence_est_signalee_une_par_une(dossier_de_guides, monkeypatch):
    _ecrire(dossier_de_guides, "x", "corps")
    _ecrire(dossier_de_guides, "y", "corps")
    from oto_mcp import db
    monkeypatch.setattr(db, "seed_guide_db", lambda *a, **k: "diverge")
    alertes: list[str] = []
    monkeypatch.setattr(G, "_alerte", lambda m, *a: alertes.append(m % a if a else m))
    G.seed_platform_guides()
    assert len(alertes) == 2 and all("guide divergent" in a for a in alertes)
    sante = G.sante_du_semis()
    assert [d["slugs"] for d in sante["defauts"]] == [["x"], ["y"]]


def test_sans_empreinte_ne_part_PAS_au_suivi_d_erreurs(dossier_de_guides, monkeypatch):
    """Toute la population d'avant le lot est dans cet état : une alerte par guide
    et par démarrage, jusqu'à ce que le geste de maintenance tourne, serait du bruit —
    le défaut de santé, lui, reste servi."""
    _ecrire(dossier_de_guides, "vieux", "corps")
    from oto_mcp import db
    monkeypatch.setattr(db, "seed_guide_db", lambda *a, **k: "sans_empreinte")
    alertes: list[str] = []
    monkeypatch.setattr(G, "_alerte", lambda m, *a: alertes.append(m))
    G.seed_platform_guides()
    assert alertes == []
    assert [d["code"] for d in G.sante_du_semis()["defauts"]] == ["guides_sans_empreinte"]


def test_un_process_qui_n_a_rien_seme_le_DIT(monkeypatch):
    monkeypatch.setattr(G, "_DERNIER_SEMIS", {"fait": False})
    sante = G.sante_du_semis()
    assert sante["fait"] is False
    assert [d["code"] for d in sante["defauts"]] == ["semis_absent"]


# --------------------------------------------------------------------------- #
# Sur une VRAIE base : base neuve, fichier modifié, base éditée, rejeu
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def base_jetable(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_semis_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom

    url_prec, pool_prec = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_prec
        if url_prec is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_prec
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


@pytest.fixture
def live(base_jetable, dossier_de_guides, monkeypatch):
    monkeypatch.setattr(G, "_alerte", lambda *a, **k: None)
    return dossier_de_guides


def _servi(slug: str) -> dict:
    from oto_mcp import db
    return db.get_guide_db("platform", G.PLATFORM_OWNER, slug)


def test_live_base_neuve_puis_fichier_modifie_puis_rejeu(live):
    slug = "semis-" + uuid.uuid4().hex[:6]
    _ecrire(live, slug, "version un")
    assert G.seed_platform_guides()["semes"] == [slug]
    assert _servi(slug)["body_md"] == "version un"

    # Rejeu du démarrage sur une base existante : aucune écriture.
    assert G.seed_platform_guides()["inchanges"] == [slug]

    # Le fichier change : la base était encore au dernier semis → elle suit.
    _ecrire(live, slug, "version deux")
    assert G.seed_platform_guides()["mis_a_jour"] == [slug]
    assert _servi(slug)["body_md"] == "version deux"
    # …et le rejeu qui suit ne réécrit rien : le semis est idempotent.
    assert G.seed_platform_guides()["inchanges"] == [slug]


def test_live_une_edition_en_base_est_CONSERVEE_et_signalee(live, monkeypatch):
    from oto_mcp import db
    slug = "edite-" + uuid.uuid4().hex[:6]
    _ecrire(live, slug, "du dépôt")
    G.seed_platform_guides()

    db.set_guide_db("platform", G.PLATFORM_OWNER, slug, "écrit par un admin", "T", "D")
    _ecrire(live, slug, "du dépôt, version deux")

    alertes: list[str] = []
    monkeypatch.setattr(G, "_alerte", lambda m, *a: alertes.append(m % a if a else m))
    r = G.seed_platform_guides()
    assert r["divergents"] == [slug] and r["mis_a_jour"] == []
    assert _servi(slug)["body_md"] == "écrit par un admin"   # la base fait foi
    assert any("guide divergent" in a for a in alertes)


def test_live_une_ligne_sans_empreinte_n_est_pas_devinee(live):
    from oto_mcp import db
    slug = "vieux-" + uuid.uuid4().hex[:6]
    _ecrire(live, slug, "du dépôt")
    # Une ligne posée comme AVANT le lot : la prose, pas d'empreinte.
    db.set_guide_db("platform", G.PLATFORM_OWNER, slug, "posé avant #236", "T", "D")
    r = G.seed_platform_guides()
    assert r["sans_empreinte"] == [slug]
    assert _servi(slug)["body_md"] == "posé avant #236"

    # Le geste de maintenance, LUI, tranche — et alors le semis reprend la main.
    f = {g["slug"]: g for g in G.list_file_guides()}[slug]
    assert db.aligner_guide_db("platform", G.PLATFORM_OWNER, slug, f["body_md"],
                               f["title"], f["description"],
                               seed_sha256=f["seed_sha256"])
    assert _servi(slug)["body_md"] == "du dépôt"
    assert G.seed_platform_guides()["inchanges"] == [slug]


def test_live_l_empreinte_posee_est_relue_par_list_et_get(live):
    """`scripts/aligner_guides_plateforme.py` relit `db.list_guides_db(...)` juste
    après un `--aligner` pour vérifier que l'estampillage a pris — s'il ne voit pas
    l'empreinte qu'il vient de poser, il rapporte « empreinte ABSENTE » sur un guide
    qu'il vient d'aligner, et un opérateur le rejoue sans fin (oto#236)."""
    from oto_mcp import db
    slug = "estampille-" + uuid.uuid4().hex[:6]
    _ecrire(live, slug, "du dépôt")
    G.seed_platform_guides()

    empreinte = db.empreinte_de_couche("T", "D", "du dépôt")
    assert db.aligner_guide_db("platform", G.PLATFORM_OWNER, slug, "du dépôt",
                               "T", "D", seed_sha256=empreinte)

    en_liste = {g["slug"]: g for g in db.list_guides_db("platform", G.PLATFORM_OWNER)}
    assert en_liste[slug].get("seed_sha256") == empreinte
    assert _servi(slug).get("seed_sha256") == empreinte


def test_live_le_semis_tient_dans_la_fenetre_du_healthcheck(live):
    """120 s est la fenêtre du healthcheck : un semis qui la dépasse empêcherait
    toute bascule de version. On mesure une population comparable à la production."""
    for i in range(25):
        _ecrire(live, f"charge-{i:02d}", "x" * 20_000)
    debut = time.monotonic()
    G.seed_platform_guides()
    pose = time.monotonic() - debut
    rejeu_debut = time.monotonic()
    G.seed_platform_guides()
    rejeu = time.monotonic() - rejeu_debut
    assert pose < 30 and rejeu < 30, f"semis {pose:.1f}s, rejeu {rejeu:.1f}s"
