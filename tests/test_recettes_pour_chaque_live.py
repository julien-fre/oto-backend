"""`for_each`, les correspondances avec un tableau et la dérive, sur un vrai store : les
lignes d'un tableau PARENT déclenchent l'appel, reçoivent leur état, et une exécution
suivante ne les repaie pas."""
from __future__ import annotations

import asyncio
import uuid

import pytest

SUB = "sub-recettes-fe"
# Trois sociétés : Acme a 5 personnes, Globex aucune, Initech 2.
PERSONNES = {
    "acme": [{"id": f"a{i}", "name": f"A {i}", "site": "https://www.acme.test/team"}
             for i in range(5)],
    "globex": [],
    "initech": [{"id": f"i{i}", "name": f"I {i}", "site": "initech.test"} for i in range(2)],
}
CIBLE = {"key": "person_key", "fields": [
    {"key": "person_key", "type": "text"}, {"key": "name", "type": "text"},
    {"key": "company", "type": "text"}]}


@pytest.fixture(scope="module")
def compte(live):
    from oto_mcp import db
    db.upsert_user(SUB, email=f"{SUB}@acme.test", name=SUB)
    return SUB


@pytest.fixture
def serveur(compte, monkeypatch):
    from fastmcp import FastMCP

    from oto_mcp import access, session_org
    from oto_mcp.auth import hooks
    from oto_mcp.tools.lecture import LECTURE
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: SUB)
    monkeypatch.setattr(hooks, "current_user_sub_from_token", lambda: SUB)
    appels: list[dict] = []
    m = FastMCP("t-recettes-fe")

    @m.tool(annotations=LECTURE)
    def acme_people_of(company: str = "", page: int = 0, size: int = 2) -> dict:
        tous = PERSONNES.get(company, [])
        lot = tous[page * size:(page + 1) * size]
        appels.append({"company": company, "page": page})
        session_org.note_call_trace(quantity=len(lot))
        return {"content": lot, "last": (page + 1) * size >= len(tous)}

    @m.tool(annotations=LECTURE)
    def acme_flat(page: int = 0, size: int = 12) -> dict:
        # Douze éléments sans `name` : la forme a changé.
        appels.append({"company": None, "page": page})
        return {"content": [{"id": f"f{i}"} for i in range(12)], "last": True}
    return m, appels


def _tableau(schema: dict, lignes: list[dict] = ()) -> int:
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    nom = f"fe-{uuid.uuid4().hex[:6]}"
    ns_id = db.create_datastore("user", SUB, nom)
    store = make_store(SUB)
    store.set_schema(nom, schema)
    if lignes:
        store.write_rows(nom, list(lignes))
    return ns_id


def _societes() -> int:
    return _tableau({"key": "slug", "fields": [{"key": "slug", "type": "text"},
                                               {"key": "people_status", "type": "text"}]},
                    [{"slug": s} for s in ("acme", "globex", "initech")])


def _corps(parents: int, **surcharge) -> dict:
    from oto_mcp.recipes import contrat
    corps = {"tool": "acme_people_of", "arguments": {"company": "{{row.slug}}"},
             "for_each": {"datastore": parents, "status_column": "people_status"},
             "source": {"items": "content", "pagination": {
                 "type": "page", "param": "page", "size": 2, "size_param": "size",
                 "last": "last"}},
             "map": {"name": "name"},
             "values": {"company": "{{row.slug}}"},
             "key": {"column": "person_key", "template": "{{row.slug}}::{{item.id}}"},
             "limits": {"max_units": 100}}
    corps.update(surcharge)
    return contrat.valider(corps)


def _executer(m, corps, ns, **kw):
    from oto_mcp.recipes import moteur
    return asyncio.run(moteur.executer(corps, {}, fastmcp=m, sub=SUB, datastore=ns, **kw))


def _lignes(ns) -> list[dict]:
    from oto_mcp.datastore.core import make_store
    return make_store(SUB).cursor_rows(str(ns), limit=100)["rows"]


def _etats(parents) -> dict:
    return {l["slug"]: l.get("people_status") for l in _lignes(parents)}


def test_chaque_ligne_parente_declenche_l_appel_puis_recoit_son_etat(serveur):
    m, appels = serveur
    parents, cible = _societes(), _tableau(CIBLE)
    recu = _executer(m, _corps(parents), cible)
    assert recu["done"] and recu["written"] == 7
    assert recu["parents"] == {"done": 2, "empty": 1}
    assert _etats(parents) == {"acme": "done", "globex": "empty", "initech": "done"}
    assert {l["company"] for l in _lignes(cible)} == {"acme", "initech"}
    # Une exécution suivante ne repaie aucune ligne faite.
    avant = len(appels)
    recu = _executer(m, _corps(parents), cible)
    assert recu["done"] and recu["parents"] == {"done": 0, "empty": 0}
    assert len(appels) == avant


def test_max_items_per_row_borne_chaque_parent(serveur):
    m, _ = serveur
    parents, cible = _societes(), _tableau(CIBLE)
    corps = _corps(parents, for_each={"datastore": parents, "status_column": "people_status",
                                      "max_items_per_row": 3})
    recu = _executer(m, corps, cible)
    assert recu["done"] and recu["written"] == 5  # 3 chez Acme, 2 chez Initech
    assert _etats(parents)["acme"] == "done"


def test_un_plafond_au_milieu_d_un_parent_le_laisse_en_attente_et_la_reprise_le_finit(serveur):
    m, appels = serveur
    parents, cible = _societes(), _tableau(CIBLE)
    corps = _corps(parents, limits={"max_units": 100, "max_pages": 2})
    recu = _executer(m, corps, cible)
    assert recu["stopped"] == "max_pages" and recu["resume"]
    assert _etats(parents)["acme"] is None  # coupé : toujours en attente
    suite = _executer(m, corps, cible, reprise=recu["resume"])
    # La reprise repart à la page 2 d'Acme, pas à la première.
    assert appels[2] == {"company": "acme", "page": 2}
    while suite.get("resume"):
        suite = _executer(m, corps, cible, reprise=suite["resume"])
    assert suite["done"] and len(_lignes(cible)) == 7
    assert _etats(parents) == {"acme": "done", "globex": "empty", "initech": "done"}


def test_l_epreuve_essaie_plusieurs_parents_et_n_ecrit_rien(serveur):
    """La première ligne parente peut ne rien rendre : l'épreuve passe à la suivante,
    et ni la cible ni l'état des parents ne sont écrits."""
    m, _ = serveur
    parents = _tableau({"key": "slug", "fields": [{"key": "slug", "type": "text"},
                                                  {"key": "people_status", "type": "text"}]},
                       [{"slug": "globex"}, {"slug": "acme"}])
    cible = _tableau(CIBLE)
    recu = _executer(m, _corps(parents), cible, ecrire=False, pages_max=1)
    assert recu["rows_built"] == 2 and recu["stopped"] is None
    assert recu["fill"] == {"name": 1.0}
    assert _lignes(cible) == [] and set(_etats(parents).values()) == {None}


def test_le_tableau_parent_ne_peut_pas_etre_la_cible(serveur):
    from oto_mcp.recipes import moteur
    m, appels = serveur
    parents = _societes()
    with pytest.raises(moteur.RecetteRefusee) as e:
        _executer(m, _corps(parents), parents)
    assert e.value.code == "for_each_same_table" and appels == []


def test_not_in_table_ecarte_par_valeur_normalisee(serveur):
    m, _ = serveur
    parents, cible = _societes(), _tableau(CIBLE)
    exclus = _tableau({"fields": [{"key": "domain", "type": "text"}]},
                      [{"domain": "ACME.test"}])
    corps = _corps(parents, where=[{"path": "site", "op": "not_in_table", "table": exclus,
                                    "column": "domain", "normalize": "domain"}])
    recu = _executer(m, corps, cible)
    assert recu["skipped_where"] == 5 and recu["written"] == 2
    assert {l["company"] for l in _lignes(cible)} == {"initech"}


def test_une_colonne_pleine_a_la_publication_qui_revient_vide_arrete_l_execution(serveur):
    from oto_mcp.recipes import contrat
    m, _ = serveur
    cible = _tableau(CIBLE)
    corps = contrat.valider({"tool": "acme_flat", "source": {"items": "content"},
                             "map": {"name": "name"},
                             "key": {"column": "person_key", "template": "{{item.id}}"},
                             "limits": {"max_units": 100}})
    recu = _executer(m, corps, cible, temoin={"fill": {"name": 1.0}})
    assert recu["stopped"] == "mapping_drift" and recu["drifted_columns"] == ["name"]
    assert _lignes(cible) == []
    # Une colonne clairsemée à la publication n'est pas surveillée.
    recu = _executer(m, corps, cible, temoin={"fill": {"name": 0.4}})
    assert recu["done"] and recu["written"] == 12


def test_le_contrat_de_for_each():
    from oto_mcp.recipes import contrat
    base = {"tool": "acme_people_of", "source": {"items": "content"},
            "map": {"name": "name"}, "key": {"column": "k", "template": "{{row.slug}}"},
            "limits": {"max_units": 10}}
    with pytest.raises(contrat.RecetteInvalide) as e:
        contrat.valider(base)
    assert any("unknown scope(s) ['row']" in p for p in e.value.problemes)
    with pytest.raises(contrat.RecetteInvalide) as e:
        contrat.valider({**base, "for_each": {"datastore": "companies"}})
    assert any("for_each.datastore" in p for p in e.value.problemes)
    assert any("for_each.status_column" in p for p in e.value.problemes)
    ok = contrat.valider({**base, "for_each": {"datastore": 12, "status_column": "st"}})
    assert ok["for_each"]["max_parents"] == contrat.MAX_PARENTS_DEFAUT
    with pytest.raises(contrat.RecetteInvalide) as e:
        contrat.valider({**base, "key": {"column": "k", "template": "{{item.id}}"},
                         "where": [{"path": "d", "op": "not_in_table", "table": 3},
                                   {"path": "d", "op": "eq", "value": "x",
                                    "normalize": "rot13"}]})
    assert len(e.value.problemes) == 2
