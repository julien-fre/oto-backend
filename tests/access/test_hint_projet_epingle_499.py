"""oto-backend#499 — le hint « rien ne résout » nomme le projet qui épingle déjà.

Vécu : clé posée au niveau équipe, projet de travail qui épingle cette instance depuis
des semaines, appels passés SANS `_project=`. Chaque refus finissait par « Durable :
lie l'instance à ton projet » — un geste déjà fait — et ne nommait jamais celui qui
débloque (porter le projet sur l'appel). Une quinzaine d'appels à tâtons.

Figé ici : le hint nomme le projet LISIBLE qui épingle, et le geste `_project=<id>` ;
le conseil de lier ne sort qu'à défaut ; seuls les projets lisibles par l'appelant
sont interrogés ; un hoquet laisse le hint d'avant, jamais une exception.
"""
from __future__ import annotations

import pytest

from oto_mcp import access, db, ownership, providers


@pytest.fixture
def wired(monkeypatch):
    """Une clé `zoho` sur l'équipe « sales » à portée ; le projet 59 est lisible."""
    monkeypatch.setattr(access, "reachable_instances",
                        lambda sub, org, p: [{"kind": "group", "id": 2, "name": "sales"}])
    lus: dict = {}

    def _lisibles(sub, org, want="read"):
        lus["appel"] = (sub, org)
        return [59, 60]

    monkeypatch.setattr(ownership, "accessible_project_ids", _lisibles)
    return lus


def _epingles(rows):
    def _f(ids, connector):
        _f.appels.append((list(ids), connector))
        return rows
    _f.appels = []
    return _f


def test_un_projet_lisible_qui_epingle_est_NOMME_avec_son_geste(wired, monkeypatch):
    f = _epingles([{"id": 59, "name": "Développement commercial"}])
    monkeypatch.setattr(db, "projects_pinning_instance", f)
    txt = access._reachable_hint("u1", 35, "zoho")
    assert "le projet #59 « Développement commercial » épingle déjà" in txt
    assert "_project=59" in txt
    assert "lie l'instance à ton projet" not in txt, (
        "le conseil de lier renvoie refaire un geste déjà fait")
    # Les instances à portée restent listées : le hint ajoute, il ne remplace pas.
    assert "group=2" in txt


def test_sans_projet_qui_epingle_le_conseil_de_lier_reste(wired, monkeypatch):
    monkeypatch.setattr(db, "projects_pinning_instance", _epingles([]))
    txt = access._reachable_hint("u1", 35, "zoho")
    assert "lie l'instance à ton projet (oto_project op=link)" in txt
    assert "_project=" not in txt


def test_seuls_les_projets_LISIBLES_sont_interroges(wired, monkeypatch):
    """Le hint est un objet de visibilité : il ne lit que les ids que l'appelant peut
    lire dans son org, jamais un balayage de tous les liens."""
    f = _epingles([])
    monkeypatch.setattr(db, "projects_pinning_instance", f)
    access._reachable_hint("u1", 35, "zoho")
    assert wired["appel"] == ("u1", 35)
    assert f.appels == [([59, 60], "zoho")]


def test_le_lien_se_cherche_sous_le_PORTEUR(wired, monkeypatch):
    """La résolution lit le binding sous le porteur du credential
    (`project_pinned_instance(porteur)`) : le hint cherche au même endroit."""
    f = _epingles([])
    monkeypatch.setattr(db, "projects_pinning_instance", f)
    monkeypatch.setattr(providers, "credential_provider",
                        lambda n: "unipile" if n == "whatsapp" else n)
    access._reachable_hint("u1", 35, "whatsapp")
    assert f.appels[0][1] == "unipile"


def test_un_projet_qui_epingle_suffit_meme_sans_cle_a_portee(wired, monkeypatch):
    monkeypatch.setattr(access, "reachable_instances", lambda sub, org, p: [])
    monkeypatch.setattr(db, "projects_pinning_instance",
                        _epingles([{"id": 59, "name": "Dev"}]))
    txt = access._reachable_hint("u1", 35, "zoho")
    assert "_project=59" in txt
    # La réserve « aux frais de l'entité » vaut aussi pour un projet épinglé.
    assert "aux frais de l'entité" in txt


def test_rien_a_portee_ni_epingle_rend_une_chaine_vide(wired, monkeypatch):
    monkeypatch.setattr(access, "reachable_instances", lambda sub, org, p: [])
    monkeypatch.setattr(db, "projects_pinning_instance", _epingles([]))
    assert access._reachable_hint("u1", 35, "zoho") == ""


def test_un_hoquet_de_lecture_rend_le_hint_d_avant(wired, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db")
    monkeypatch.setattr(ownership, "accessible_project_ids", _boom)
    txt = access._reachable_hint("u1", 35, "zoho")
    assert "group=2" in txt
    assert "lie l'instance à ton projet" in txt


# ── la requête, sur une vraie base ───────────────────────────────────────────

def test_la_requete_ne_rend_que_les_projets_passes_non_archives_qui_epinglent(live):
    a = db.create_project("org", "35", "A")
    b = db.create_project("org", "35", "B")
    c = db.create_project("org", "35", "C archivé")
    d = db.create_project("org", "99", "D non passé")
    e = db.create_project("org", "35", "E sans instance")
    for pid in (a, c, d):
        db.add_project_link(pid, "connecteur", "zoho",
                            config={"instance_ref": "group:2:zoho"})
    db.add_project_link(b, "connecteur", "serper",
                        config={"instance_ref": "group:2:serper"})
    db.add_project_link(e, "connecteur", "zoho")
    db.archive_project(c)
    rows = db.projects_pinning_instance([a, b, c, e], "zoho")
    assert rows == [{"id": a, "name": "A"}]
    assert db.projects_pinning_instance([], "zoho") == []
