"""Pipecorn dans une RECETTE, à l'échelle : une recette `for_each` parcourt un tableau de
contacts, appelle `pipecorn op=find` (la recherche synchrone) pour chaque ligne et écrit
chaque valeur trouvée (`results`) dans un tableau cible — sur un vrai store, avec le VRAI
outil (seul le client Pipecorn est simulé). Ce que ça verrouille :

- l'outil est appelable par une recette (déclaré en lecture) et sa réponse se lit par
  `source.items: "results"`, une ligne par mobile ou email ;
- chaque ligne parente reçoit son état (`done`, `empty` sur un échec) et n'est pas
  repayée à l'exécution suivante ;
- la dépense se compte en crédits Pipecorn (`quantity`) : `limits.max_units` arrête
  proprement, et la reprise finit le travail sans repayer.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest

# Importés AVANT tout patch du lecteur d'identité : ils le lient à leur import, et un
# import pendant le test lierait le faux pour tout le worker (#1111).
import oto_mcp.call_axes  # noqa: F401,E402
import oto_mcp.capabilities._mcp_adapter  # noqa: F401,E402
import oto_mcp.tools.meta  # noqa: F401,E402

SUB = "sub-recettes-pipecorn"
N = 30   # contacts ; un sur trois sans rien chez Pipecorn


def _reponse(slug: str) -> dict:
    n = int(slug.rsplit("-", 1)[1])
    if n % 3 == 0:
        return {"status": "finished", "phones": [], "phone_details": [],
                "phone_status": "not_found", "email": None, "email_status": None}
    return {"status": "finished", "email": f"{slug}@acme.test", "email_status": "deliverable",
            "phones": [f"+3361234{n:04d}"],
            "phone_details": [{"number": f"+3361234{n:04d}", "type": "mobile"}],
            "phone_status": "valid"}


@pytest.fixture(scope="module")
def compte(live):
    from oto_mcp import db
    db.upsert_user(SUB, email=f"{SUB}@acme.test", name=SUB)
    return SUB


@pytest.fixture
def serveur(compte, monkeypatch):
    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.auth import hooks
    from oto_mcp.tools import pipecorn
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: SUB)
    monkeypatch.setattr(hooks, "current_user_sub_from_token", lambda: SUB)
    monkeypatch.setattr(access, "resolve_api_key", lambda p, account=None, units=1: ("pc_k", True))
    appels: list[str] = []
    with patch("oto.tools.pipecorn.PipecornClient") as cls:
        def trouver(kinds, *, linkedin_url=None, **kw):
            slug = linkedin_url.rstrip("/").rsplit("/", 1)[1]
            appels.append(slug)
            return _reponse(slug)
        cls.return_value.enrich_contact.side_effect = trouver
        m = FastMCP("t-recettes-pipecorn")
        pipecorn.register(m)
        yield m, appels


def _tableau(schema: dict, lignes=(), nom: str = "") -> int:
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    nom = nom or f"pc-{uuid.uuid4().hex[:6]}"
    ns_id = db.create_datastore("user", SUB, nom)
    store = make_store(SUB)
    store.set_schema(nom, schema)
    if lignes:
        store.write_rows(nom, list(lignes))
    return ns_id


def _contacts() -> int:
    return _tableau({"key": "linkedin", "fields": [
        {"key": "linkedin", "type": "text"}, {"key": "pipecorn_status", "type": "text"}]},
        [{"linkedin": f"https://www.linkedin.com/in/jane-{i}/"} for i in range(1, N + 1)])


CIBLE = {"key": "contact_key", "fields": [
    {"key": "contact_key", "type": "text"}, {"key": "linkedin", "type": "text"},
    {"key": "kind", "type": "text"}, {"key": "value", "type": "text"}]}


def _corps(parents: int, **surcharge) -> dict:
    from oto_mcp.recipes import contrat
    corps = {"tool": "pipecorn",
             "arguments": {"op": "find", "find": ["phone", "email"],
                           "linkedin_url": "{{row.linkedin}}", "phone_type": "mobile"},
             "for_each": {"datastore": parents, "status_column": "pipecorn_status"},
             "source": {"items": "results"},
             "map": {"kind": "kind", "value": "value", "type": "type", "status": "status"},
             "values": {"linkedin": "{{row.linkedin}}"},
             "key": {"column": "contact_key",
                     "template": "{{row.linkedin|linkedin_slug}}::{{item.kind}}::{{item.value}}"},
             "limits": {"max_units": 10_000}}
    corps.update(surcharge)
    return contrat.valider(corps)


def _executer(m, corps, ns, **kw):
    from oto_mcp.recipes import moteur
    return asyncio.run(moteur.executer(corps, {}, fastmcp=m, sub=SUB, datastore=ns, **kw))


def _lignes(ns) -> list[dict]:
    from oto_mcp.datastore.core import make_store
    return make_store(SUB).cursor_rows(str(ns), limit=500)["rows"]


def _etats(parents) -> dict:
    return {l["linkedin"]: l.get("pipecorn_status") for l in _lignes(parents)}


def _jusqu_au_bout(m, corps, cible) -> dict:
    recu = _executer(m, corps, cible)
    while recu.get("resume"):
        recu = _executer(m, corps, cible, reprise=recu["resume"])
    return recu


def test_une_recette_ecrit_chaque_mobile_et_email_de_chaque_contact(serveur):
    m, appels = serveur
    parents, cible = _contacts(), _tableau(CIBLE)
    recu = _jusqu_au_bout(m, _corps(parents), cible)
    trouves = N - N // 3
    assert recu["done"] and len(appels) == N
    lignes = _lignes(cible)
    assert len(lignes) == 2 * trouves     # un mobile et un email par contact trouvé
    assert {l["kind"] for l in lignes} == {"phone", "email"}
    etats = _etats(parents)
    assert sum(e == "done" for e in etats.values()) == trouves
    assert sum(e == "empty" for e in etats.values()) == N // 3
    # Une exécution suivante ne repaie aucune ligne faite.
    recu = _executer(m, _corps(parents), cible)
    assert len(appels) == N and recu["parents"] == {"done": 0, "empty": 0, "failed": 0}


def test_la_depense_se_compte_en_credits_pipecorn_et_la_reprise_finit(serveur):
    m, appels = serveur
    parents, cible = _contacts(), _tableau(CIBLE)
    # 33 crédits par contact trouvé (30 le mobile, 3 l'email) : 100 en couvrent 3.
    corps = _corps(parents, limits={"max_units": 100})
    recu = _executer(m, corps, cible)
    assert recu["stopped"] == "spend_cap", recu
    assert recu["units_basis"] == "billed"
    faits = len(appels)
    assert faits < N
    # Une exécution NEUVE repart d'une dépense à 0 et ne repaie pas les lignes faites.
    while len(appels) < N:
        avant = len(appels)
        recu = _executer(m, corps, cible)
        assert len(appels) > avant, recu
    assert len(set(appels)) == N           # chaque contact payé une seule fois
    assert len(_lignes(cible)) == 2 * (N - N // 3)
