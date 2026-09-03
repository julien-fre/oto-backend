"""Signal #699 — `oto_project(op=unlink)` répondait `ok: true` sans rien retirer.

Deux défauts d'un même geste, tels que vécus sur le projet 59 :

1. le SILENCE — la suppression touchait zéro ligne, et la réponse ne portait aucune
   trace de ce non-événement (le lien visé figurait encore dans les `links` rendus par
   ce même appel) ;
2. la ref INDÉLOGEABLE — un lien `tableau` stocké sous le NOM de son namespace (ligne
   d'avant la normalisation nom→id de `op=link`) alors que l'unlink canonisait la ref
   demandée en id AVANT de supprimer : on effaçait « 108 », la ligne s'appelle
   « suivi-commercial-index ». Même classe côté `procedure` (slug stocké vs id demandé).

Le banc passe par le HANDLER SERVI — `P._project`, celui que monte l'outil `oto_project`
et que sert `POST /api/me/projects` — et n'assert que sur ce que la surface EXPOSE
(la réponse, ou le refus levé). Jamais `remove_project_link` seule : son `rowcount` était
justement le fait que personne ne lisait.

La doublure de base imite la clause SQL réelle (égalité EXACTE sur `target_ref`,
`identity_ref` NULL = tous les bindings) — sans quoi le banc serait vert par indulgence.
"""
from __future__ import annotations

import types

import pytest

from oto_mcp.capabilities import projects as P
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="u1", org_id=2)
ROW = {"id": 59, "owner_type": "org", "owner_id": "2", "name": "Développement commercial",
       "brief_md": "", "created_by": "u1", "archived_at": None,
       "created_at": "2026-07-02", "updated_at": "2026-09-03"}

# Le datastore de l'org 2 : ces NOMS résolvent. C'est cette résolution — légitime au
# link — qui faisait rater la suppression quand la ligne, elle, porte le nom.
NAMESPACES = {"suivi-commercial-index": 108, "vivier-eti-13": 48, "linkedin-feed": 12}
GUIDES = {"prospection": 4, "relance": 94}


@pytest.fixture
def surface(monkeypatch):
    """Le projet 59 et ses liens, avec les DEUX écritures qui coexistent en base :
    l'id canonique (« 12 ») et le nom/slug d'avant la normalisation."""
    etat = {
        "links": [
            {"target_type": "tableau", "target_ref": "12", "label": "Feed"},
            {"target_type": "tableau", "target_ref": "suivi-commercial-index",
             "slot": "index"},
            {"target_type": "procedure", "target_ref": "prospection"},
            {"target_type": "connecteur", "target_ref": "folk", "identity_ref": None},
            {"target_type": "connecteur", "target_ref": "folk", "identity_ref": "acc-2"},
        ],
        "activite": [],
    }

    def _remove(pid, tt, tr, identity_ref=None):
        # Miroir de la clause SQL de `db.remove_project_link` : égalité EXACTE sur la
        # ref stockée ; `identity_ref=None` emporte TOUS les bindings de l'entité.
        garde = [l for l in etat["links"]
                 if not (l["target_type"] == tt and l["target_ref"] == tr
                         and (identity_ref is None
                              or l.get("identity_ref") == identity_ref))]
        n = len(etat["links"]) - len(garde)
        etat["links"] = garde
        return n

    monkeypatch.setattr(P.db, "get_project_by_id",
                        lambda pid: dict(ROW) if pid == 59 else None)
    monkeypatch.setattr(P.db, "list_project_links", lambda pid: [dict(l) for l in etat["links"]])
    monkeypatch.setattr(P.db, "remove_project_link", _remove)
    monkeypatch.setattr(P.db, "add_project_link", lambda *a, **k: None)
    monkeypatch.setattr(P.db, "log_project_activity",
                        lambda pid, sub, action, detail=None: etat["activite"].append(action))
    monkeypatch.setattr(P.db, "get_datastore_namespace",
                        lambda ot, oid, name: ({"id": NAMESPACES[name], "namespace": name}
                                               if (ot, oid) == ("org", "2")
                                               and name in NAMESPACES else None))
    monkeypatch.setattr(P.org_store, "get_instruction",
                        lambda ot, oid, slug: ({"id": GUIDES[slug], "slug": slug}
                                               if slug in GUIDES else None))
    monkeypatch.setattr(P.ownership, "can_access", lambda sub, t, rid, want="read": True)
    monkeypatch.setattr(P.ownership, "visible_in_org", lambda sub, org, t, rid: True)
    monkeypatch.setattr(P.group_store, "get_group",
                        lambda gid: {"id": gid, "org_id": 2, "name": "pôle"})
    monkeypatch.setattr(P.ownership, "accessor_scope",
                        lambda sub: types.SimpleNamespace(owner_pairs=lambda: [("org", "2")]))
    return etat


def _unlink(**kw):
    return P._project(CTX, P.ProjectInput(op="unlink", project_id=59, **kw))


def _refs(out, target_type):
    return [l["target_ref"] for l in out["links"] if l["target_type"] == target_type]


# --- (b) la ref indélogeable ------------------------------------------------------

def test_unlink_dun_tableau_stocke_sous_son_nom_retire_la_ligne(surface):
    """Le cas d'Alexis, mot pour mot : ref demandée = le nom, ligne = le nom, et une
    résolution nom→id entre les deux."""
    out = _unlink(target_type="tableau", target_ref="suivi-commercial-index")
    assert "suivi-commercial-index" not in _refs(out, "tableau")
    assert out["removed"] == 1


def test_unlink_par_id_atteint_la_ligne_stockee_sous_son_nom(surface):
    """L'autre sens — et la SEULE branche qui exerce vraiment la canonisation : la ref
    donnée est l'id, la ligne porte le nom. Sans elle, le test précédent passerait
    encore avec un canonisateur cassé (les deux chaînes sont égales)."""
    out = _unlink(target_type="tableau", target_ref="108")
    assert "suivi-commercial-index" not in _refs(out, "tableau")
    assert out["removed"] == 1


def test_unlink_dune_procedure_stockee_sous_son_slug(surface):
    """Même classe côté `procedure` : la ligne porte le slug, `link` canonise en id."""
    out = _unlink(target_type="procedure", target_ref="prospection")
    assert _refs(out, "procedure") == []
    assert out["removed"] == 1


def test_un_tableau_encore_lie_survit_a_lunlink_de_son_voisin(surface):
    """Le rattrapage ne déborde pas : délier l'un ne déliera pas l'autre."""
    out = _unlink(target_type="tableau", target_ref="suivi-commercial-index")
    assert _refs(out, "tableau") == ["12"]


# --- (a) le silence ---------------------------------------------------------------

def test_unlink_sans_cible_refuse_au_lieu_de_repondre_ok(surface):
    with pytest.raises(AuthzDenied) as e:
        _unlink(target_type="tableau", target_ref="jamais-lie")
    assert e.value.code == "link_not_found" and e.value.status == 404
    # La face MCP ne rend que le MESSAGE : il doit donc porter le fait ET la suite.
    assert "RIEN n'a été retiré" in e.value.message
    assert "« 12 »" in e.value.message and "« suivi-commercial-index »" in e.value.message


def test_un_unlink_refuse_ne_touche_ni_les_liens_ni_le_journal(surface):
    with pytest.raises(AuthzDenied):
        _unlink(target_type="tableau", target_ref="jamais-lie")
    assert len(surface["links"]) == 5
    assert surface["activite"] == []          # pas d'entrée « project.unlink » fantôme


def test_le_refus_dit_quil_ny_a_aucun_lien_de_ce_type(surface):
    surface["links"] = [l for l in surface["links"] if l["target_type"] != "connecteur"]
    with pytest.raises(AuthzDenied) as e:
        _unlink(target_type="connecteur", target_ref="folk")
    assert "aucun lien `connecteur`" in e.value.message


def test_unlink_reussi_compte_les_bindings_retires(surface):
    """`removed` compte des LIGNES, pas des appels : un connecteur lié deux fois (deux
    identités) part en une fois, et le dit."""
    out = _unlink(target_type="connecteur", target_ref="folk")
    assert _refs(out, "connecteur") == []
    assert out["removed"] == 2
    assert surface["activite"] == ["project.unlink"]


def test_unlink_dun_binding_precis_ne_prend_que_le_sien(surface):
    out = _unlink(target_type="connecteur", target_ref="folk", identity_ref="acc-2")
    assert out["removed"] == 1
    assert [l.get("identity_ref") for l in surface["links"]
            if l["target_type"] == "connecteur"] == [None]
