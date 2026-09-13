"""« Ajouter à mon Oto » fait naître un projet PRIVÉ — et ses tableaux avec lui.

ADR 0068 : ce qui naît appartient à la personne qui l'a créé, jamais au contexte. Le
recensement du 08/09/2026 a trouvé `me.import_project` seul en dehors : il posait
`("org", org_active)` en dur, sans qu'aucun paramètre ne l'ait demandé et sans que la
réponse le dise. C'était le dernier chemin de contenu à hériter du contexte en silence.

⚠️ **Ce banc vérifie le PROPRIÉTAIRE DES OBJETS CRÉÉS, pas le succès de l'appel.** Un
import qui rend 200 ne dit rien de ce qu'il a posé — c'est exactement le trou que
l'inventaire a mis une matinée à ouvrir.

⚠️ Et il descend jusqu'aux TABLEAUX. Un projet devenu privé dont les tableaux
resteraient à l'org serait pire que l'état d'avant : incohérent, donc impossible à
expliquer à qui le découvre. Le chemin qui les provisionne (`_provision_tableau`) prend
le propriétaire de la copie en paramètre — ce banc joue la VRAIE duplication pour le
prouver, et ne se contente pas de regarder ce que la capacité passe.
"""
from __future__ import annotations

import pytest

import oto_mcp.db.projects as PJ
import oto_mcp.media_store as MS
from oto_mcp.capabilities import projects as P
from oto_mcp.capabilities._types import ResolvedCtx

CTX = ResolvedCtx(sub="u1", org_id=99)

# La source : un projet PUBLIÉ, possédé par une AUTRE org (77) — le cas nominal du
# fork depuis une vitrine.
_PUB = {"id": 42, "owner_type": "org", "owner_id": "77", "name": "Prospection FT",
        "brief_md": "le brief", "mcp_access": "secret", "mcp_slug": "demo-x"}


@pytest.fixture
def monde(monkeypatch):
    """Les seams de la duplication RÉELLE + ceux de l'import. Aucune base.

    On ne double PAS `duplicate_project` : c'est lui qui décide du propriétaire des
    tableaux provisionnés, et le doubler reviendrait à tester notre propre idée de ce
    qu'il fait."""
    fait = {"projets": [], "ns": [], "liens": [], "activite": []}
    compteur = {"pid": 100, "ns": 500}

    def create_project(ot, oid, name, brief_md="", created_by=None, copied_from=None,
                       context_org_id=None):
        compteur["pid"] += 1
        fait["projets"].append({"id": compteur["pid"], "owner": (ot, oid), "nom": name,
                                "context_org_id": context_org_id,
                                "copied_from": copied_from})
        return compteur["pid"]

    def create_datastore(ot, oid, name):
        compteur["ns"] += 1
        fait["ns"].append({"id": compteur["ns"], "owner": (ot, oid), "nom": name})
        return compteur["ns"]

    # — les feuilles que `duplicate_project` traverse —
    monkeypatch.setattr(PJ, "get_project_by_id", lambda pid: dict(_PUB) if pid == 42 else
                        next((dict(_PUB, id=p["id"], owner_type=p["owner"][0],
                                   owner_id=p["owner"][1],
                                   context_org_id=p["context_org_id"])
                              for p in fait["projets"] if p["id"] == pid), None))
    monkeypatch.setattr(PJ, "create_project", create_project)
    monkeypatch.setattr(PJ, "list_docs_for_project", lambda pid: [])
    monkeypatch.setattr(PJ, "list_project_files", lambda pid: [])
    monkeypatch.setattr(PJ, "log_project_activity",
                        lambda pid, sub, action, detail=None: None)
    monkeypatch.setattr(MS, "copy_object", lambda k, p, o: k)
    monkeypatch.setattr(PJ, "add_project_link",
                        lambda pid, tt, tr, label=None, role=None, config=None, slot=None:
                        fait["liens"].append((pid, tt, tr)))
    # Un tableau LIÉ, possédé par l'org source → re-provisionné chez le nouveau
    # propriétaire (anti-fuite inter-org). C'est le tableau dont on suit la propriété.
    monkeypatch.setattr(PJ, "list_project_links", lambda pid: [
        {"target_type": "tableau", "target_ref": "5", "label": "Vivier",
         "role": "leads", "config": None, "slot": None}])
    monkeypatch.setattr(PJ, "get_datastore_by_id", lambda nid: (
        {"id": 5, "datastore": "vivier", "owner_type": "org", "owner_id": "77",
         "schema": None} if nid == 5 else None))
    monkeypatch.setattr(PJ, "create_datastore", create_datastore)
    monkeypatch.setattr(PJ, "set_datastore_schema", lambda nid, s: None)
    monkeypatch.setattr(PJ, "datastore_list_rows", lambda nid, limit=None: [])
    monkeypatch.setattr(PJ, "datastore_insert_row", lambda nid, rid, d: None)

    # — les seams propres à la capacité d'import —
    monkeypatch.setattr(P.db, "get_project_by_mcp_slug",
                        lambda slug: dict(_PUB) if slug == "demo-x" else None)
    monkeypatch.setattr(P.db, "log_project_activity",
                        lambda pid, sub, action, detail=None:
                        fait["activite"].append((pid, action)))
    fait["_deja"] = {}
    monkeypatch.setattr(P.db, "find_copied_project",
                        lambda ot, oid, sid: fait["_deja"].get((ot, str(oid))))
    monkeypatch.setattr(P.db, "get_project_by_id", PJ.get_project_by_id)
    return fait


def test_le_projet_importe_naît_PERSONNEL(monde):
    P._import_project(CTX, P.ImportProjectInput(slug="demo-x"))
    assert len(monde["projets"]) == 1
    assert monde["projets"][0]["owner"] == ("user", "u1"), (
        "l'import posait le projet chez l'org active — le dernier chemin de contenu "
        "à hériter du contexte (ADR 0068)")


def test_le_projet_importe_reste_RANGÉ_dans_l_org_de_travail(monde):
    """Privé n'est pas hors-sol : un projet perso se liste dans son org de contexte.

    Sans ce rangement, la copie devient invisible partout, y compris pour son
    propriétaire (`db.list_member_projects` filtre sur `context_org_id`)."""
    P._import_project(CTX, P.ImportProjectInput(slug="demo-x"))
    assert monde["projets"][0]["context_org_id"] == 99


def test_les_TABLEAUX_de_l_import_naissent_personnels_AUSSI(monde):
    """Le point qui décide de la cohérence du lot.

    Un projet privé dont les tableaux restent à l'org n'est pas un demi-correctif :
    c'est un état que personne ne peut expliquer."""
    P._import_project(CTX, P.ImportProjectInput(slug="demo-x"))
    assert len(monde["ns"]) == 1, "le tableau lié doit être re-provisionné chez la copie"
    assert monde["ns"][0]["owner"] == ("user", "u1"), (
        "le tableau provisionné est resté chez l'org — le projet et ses tableaux "
        "doivent basculer ENSEMBLE")


def test_la_réponse_dit_QUI_voit_le_projet_importé(monde):
    """La surprise change de camp, elle ne disparaît pas : hier le collègue voyait
    sans qu'on l'ait voulu, demain il ne verra plus sans qu'on l'ait dit. La réponse
    doit porter la même information que partout ailleurs — mêmes noms de champs."""
    out = P._import_project(CTX, P.ImportProjectInput(slug="demo-x"))
    assert out["owner_type"] == "user" and out["owner_id"] == "u1"
    assert "toi seul" in out["visible_to"]


def test_une_copie_d_ORG_déjà_importée_n_est_pas_dupliquée(monde):
    """Le parcours doit rester utilisable pour qui a déjà importé AVANT la bascule.

    Ne chercher que la copie personnelle ferait réapparaître le bouton « importer »
    sur un projet déjà présent, et poserait un doublon à chaque clic."""
    monde["_deja"][("org", "99")] = {"id": 55, "name": "Prospection FT"}
    out = P._import_project(CTX, P.ImportProjectInput(slug="demo-x"))
    assert out["imported"] is False and out["project_id"] == 55
    assert out["reason"] == "already_imported"
    assert monde["projets"] == [], "aucun doublon ne doit être créé"


def test_une_copie_PERSONNELLE_déjà_importée_n_est_pas_dupliquée(monde):
    monde["_deja"][("user", "u1")] = {"id": 56, "name": "Prospection FT"}
    out = P._import_project(CTX, P.ImportProjectInput(slug="demo-x"))
    assert out["imported"] is False and out["project_id"] == 56
    assert monde["projets"] == []


def test_la_DESCRIPTION_SERVIE_de_l_import_ne_promet_plus_l_org():
    """Le texte servi est du code de production — c'est toute la leçon du 08/09/2026.

    Cette description-ci annonce « into your ACTIVE org » depuis toujours. Elle part
    dans l'`openapi.json` public et dans le catalogue des capacités : la laisser
    derrière le comportement, c'est refaire à l'identique le défaut qu'on vient de
    fermer sur `data_create_datastore`, une heure plus tôt, dans le même dépôt."""
    from oto_mcp.capabilities.registry import CAPABILITIES

    cap = next(c for c in CAPABILITIES if c.key == "me.import_project")
    d = " ".join((cap.description or "").split())
    assert "into your ACTIVE org" not in d, (
        "la copie n'appartient plus à l'org active mais à la personne (ADR 0068)")
    assert "PRIVATE" in d or "private" in d, (
        "ce que la copie devient doit être dit là où on le lit")
