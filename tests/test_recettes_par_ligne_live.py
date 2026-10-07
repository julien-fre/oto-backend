"""`per_row` sur un vrai store : une ligne en attente, un appel, son résultat écrit dans
CETTE ligne — cases vides seulement par défaut, état posé, rien d'écrasé."""
from __future__ import annotations

import asyncio
import uuid

import pytest

SUB = "sub-recettes-pl"
SOCIETES = {"111": {"name": "Acme", "naf": "62.01Z", "city": "Lyon"},
            "222": {"name": "Globex", "naf": "70.22Z", "city": None}}


@pytest.fixture(scope="module")
def compte(live):
    from oto_mcp import db
    db.upsert_user(SUB, email=f"{SUB}@acme.test", name=SUB)
    return SUB


@pytest.fixture
def serveur(compte, monkeypatch):
    from fastmcp import FastMCP
    from mcp.types import INVALID_PARAMS, ErrorData

    from oto_mcp import access, session_org
    from oto_mcp.auth import hooks
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools.lecture import LECTURE
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: SUB)
    monkeypatch.setattr(hooks, "current_user_sub_from_token", lambda: SUB)
    appels: list[str] = []
    m = FastMCP("t-recettes-pl")

    @m.tool(annotations=LECTURE)
    def acme_company(siren: str, hint: str = "") -> dict:
        appels.append(siren)
        session_org.note_call_trace(quantity=1)
        if not siren.isdigit():
            raise McpError(ErrorData(code=INVALID_PARAMS, message="bad siren"))
        if siren == "404":
            import httpx
            req = httpx.Request("GET", "https://api.acme.test/company")
            raise httpx.HTTPStatusError("missing", request=req,
                                        response=httpx.Response(404, request=req))
        return {"company": SOCIETES.get(siren)}

    @m.tool(annotations=LECTURE)
    def acme_matches(siren: str) -> dict:
        appels.append(siren)
        return {"results": [SOCIETES["111"], SOCIETES["222"]]}
    return m, appels


SCHEMA = {"fields": [{"key": "siren", "type": "text"}, {"key": "name", "type": "text"},
                     {"key": "naf", "type": "text"}, {"key": "fr_status", "type": "text"}]}


def _tableau(lignes) -> int:
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    nom = f"pl-{uuid.uuid4().hex[:6]}"
    ns_id = db.create_datastore("user", SUB, nom)
    store = make_store(SUB)
    store.set_schema(nom, SCHEMA)
    store.write_rows(nom, list(lignes))
    return ns_id


def _corps(**surcharge) -> dict:
    from oto_mcp.recipes import contrat
    corps = {"mode": "per_row", "tool": "acme_company",
             "arguments": {"siren": "{{row.siren}}"},
             "rows": {"status_column": "fr_status"},
             "source": {"items": "company"},
             "map": {"name": "name", "naf": "naf", "city": "city"},
             "limits": {"max_units": 50}}
    corps.update(surcharge)
    return contrat.valider(corps)


def _executer(m, corps, ns, **kw):
    from oto_mcp.recipes import moteur
    # Le budget d'horloge (30 s) n'est pas ce qu'on éprouve ici : une machine chargée
    # le dépasserait au premier appel, et le reçu dirait `time_budget`.
    kw.setdefault("budget_s", 600)
    return asyncio.run(moteur.executer(corps, {}, fastmcp=m, sub=SUB, datastore=ns, **kw))


def _lignes(ns) -> dict:
    from oto_mcp.datastore.core import make_store
    return {l.get("siren"): l for l in make_store(SUB).cursor_rows(str(ns), limit=50)["rows"]}


def test_chaque_ligne_recoit_son_resultat_et_son_etat(serveur):
    m, appels = serveur
    ns = _tableau([{"siren": "111"}, {"siren": "222", "name": "Kept by hand"},
                   {"siren": "333"}, {"name": "no siren yet"}])
    recu = _executer(m, _corps(), ns)
    assert recu["done"] and recu["rows"] == {"done": 2, "not_found": 1, "failed": 0}, recu
    assert recu["created_columns"] == ["city"] and recu["updated"] == 2
    lignes = _lignes(ns)
    assert (lignes["111"]["name"], lignes["111"]["city"], lignes["111"]["fr_status"]) == \
        ("Acme", "Lyon", "done")
    # `fill_empty` : la saisie reste ; une valeur absente ne vide rien.
    assert (lignes["222"]["name"], lignes["222"]["naf"]) == ("Kept by hand", "70.22Z")
    assert lignes["333"]["fr_status"] == "not_found"
    # La ligne sans `siren` n'est pas appelée, et attend.
    assert lignes[None]["fr_status"] is None and sorted(appels) == ["111", "222", "333"]
    # Une exécution suivante ne repaie rien.
    assert _executer(m, _corps(), ns)["rows"] == {"done": 0, "not_found": 0, "failed": 0}
    assert len(appels) == 3


def test_update_ecrase_les_colonnes_de_la_correspondance(serveur):
    m, _ = serveur
    ns = _tableau([{"siren": "222", "name": "Old"}])
    _executer(m, _corps(on_existing="update"), ns)
    assert _lignes(ns)["222"]["name"] == "Globex"


def test_une_liste_sans_pick_marque_la_ligne_ambigue(serveur):
    m, _ = serveur
    ns = _tableau([{"siren": "111"}])
    recu = _executer(m, _corps(tool="acme_matches", source={"items": "results"}), ns)
    assert recu["rows"]["failed"] == 1
    assert _lignes(ns)["111"]["fr_status"] == "failed:ambiguous"
    ns = _tableau([{"siren": "111"}])
    _executer(m, _corps(tool="acme_matches", source={"items": "results"}, pick="first"), ns)
    assert _lignes(ns)["111"]["name"] == "Acme"


def test_une_entree_refusee_marque_la_ligne_et_continue(serveur):
    m, _ = serveur
    ns = _tableau([{"siren": "abc"}, {"siren": "111"}])
    recu = _executer(m, _corps(), ns)
    assert recu["rows"] == {"done": 1, "not_found": 0, "failed": 1}
    assert _lignes(ns)["abc"]["fr_status"] == "failed:invalid_input"


def test_l_epreuve_n_ecrit_rien(serveur):
    m, _ = serveur
    ns = _tableau([{"siren": "333"}, {"siren": "111"}])
    recu = _executer(m, _corps(), ns, ecrire=False)
    assert recu["rows_built"] == 1 and recu["fill"]["name"] == 1.0
    assert all(l.get("fr_status") is None and l.get("name") is None
               for l in _lignes(ns).values())


def test_une_saisie_pendant_l_execution_n_est_pas_ecrasee(serveur, monkeypatch):
    """La ligne change entre sa lecture et l'écriture : rien n'est écrit, elle attend."""
    from oto_mcp.datastore.core import make_store
    from oto_mcp.recipes import ecriture
    m, _ = serveur
    ns = _tableau([{"siren": "111"}])
    vrai = ecriture.ecrire_dans_la_ligne

    def concurrent(p, rid, patch, revision):
        make_store(SUB).update_row(str(ns), rid, {"name": "Typed meanwhile"})
        return vrai(p, rid, patch, revision)
    monkeypatch.setattr(ecriture, "ecrire_dans_la_ligne", concurrent)
    recu = _executer(m, _corps(), ns)
    ligne = _lignes(ns)["111"]
    assert ligne["name"] == "Typed meanwhile" and ligne["fr_status"] is None
    assert recu["failed"] == {"row_changed": 1}


def test_le_contrat_de_per_row():
    from oto_mcp.recipes import contrat
    base = {"mode": "per_row", "tool": "acme_company", "arguments": {"siren": "{{row.siren}}"},
            "map": {"name": "name"}, "limits": {"max_units": 5}}
    with pytest.raises(contrat.RecetteInvalide) as e:
        contrat.valider({**base, "key": {"column": "k"}, "pick": "best",
                         "source": {"pagination": {"type": "page", "param": "p"}}})
    probs = " ".join(e.value.problemes)
    for attendu in ("rows", "`key`", "pagination", "`pick`"):
        assert attendu in probs
    ok = contrat.valider({**base, "rows": {"status_column": "st"}})
    assert ok["on_existing"] == "fill_empty" and ok["units"] == "calls"
    with pytest.raises(contrat.RecetteInvalide):
        contrat.valider({**base, "rows": {"status_column": "name"}})


def test_require_ne_demande_que_les_colonnes_dites(serveur):
    """Sans `require`, chaque colonne citée doit être remplie ; avec, seules les dites."""
    m, appels = serveur
    ns = _tableau([{"siren": "111"}])
    args = {"siren": "{{row.siren}}", "hint": "{{row.name}}"}
    assert _executer(m, _corps(arguments=args), ns)["rows"]["done"] == 0 and appels == []
    recu = _executer(m, _corps(arguments=args, rows={"status_column": "fr_status",
                                                     "require": ["siren"]}), ns)
    assert recu["rows"]["done"] == 1


def test_un_resultat_vide_n_est_pas_un_succes(serveur, monkeypatch):
    m, _ = serveur
    ns = _tableau([{"siren": "111"}])
    recu = _executer(m, _corps(map={"name": "nope", "naf": "nada"},
                               values={"source": "acme"}), ns)
    assert recu["rows"] == {"done": 0, "not_found": 1, "failed": 0}
    assert _lignes(ns)["111"].get("source") is None


def test_un_404_est_not_found_et_une_entree_vide_n_appelle_pas(serveur):
    m, appels = serveur
    ns = _tableau([{"siren": "404"}, {"siren": "n/a"}, {"siren": "4x0x4x"}])
    corps = _corps(arguments={"siren": "{{row.siren|digits}}"})
    recu = _executer(m, corps, ns)
    lignes = _lignes(ns)
    assert lignes["404"]["fr_status"] == "not_found"
    assert lignes["n/a"]["fr_status"] == "failed:invalid_input"
    assert "n/a" not in appels and recu["rows"]["not_found"] == 2
