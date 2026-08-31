"""Pennylane GED — ce que le tool dit d'une écriture qui a EU LIEU (signal #600).

Signal #600 (27/08, `wrong_result`) : supprimer un dossier GED répond « Erreur
interne du serveur. » **alors que la suppression a bien eu lieu** (le dossier
disparaît de `pennylaneged_tree`, son contenu répond 404 ensuite). L'agent croit
à un échec et retente.

Le journal des appels de prod nomme la panne exacte (`tool_calls#1039702`,
27/08 18:58:44 GMT, `item_id=1924300902400`, 12 190 ms, `ok=false`) :

    Page.evaluate: TypeError: Failed to execute 'text' on 'Response':
    body stream already read

…et l'appel suivant (`#1039709`, 16 s plus tard) répond `404` sur le fichier
que ce dossier contenait : la suppression était bien partie.

C'est le JS in-page qui lit le corps DEUX fois :

    try { data = await r.json(); }
    catch (e) { data = {raw: (await r.text()).slice(0, 400)}; }

`r.json()` consomme (et VERROUILLE) le flux du corps avant même d'échouer ; le
`catch` rappelle `r.text()` sur un flux déjà lu → TypeError. Reproduit à
l'identique sous node 24 le 28/08 (`Body is unusable: Body has already been
read`, la formulation V8 de la même erreur) ; la forme corrigée rend `null` sur
un 204 et `{raw}` sur un corps non-JSON, sans jamais lever.

Trois conséquences, un test chacune : le corps ne se lit qu'UNE fois, une
suppression réussie se dit, et une panne du substrat est NOMMÉE plutôt que
traduite en « Erreur interne du serveur ».
"""
from __future__ import annotations

import asyncio

import pytest
from oto_mcp.mcp_errors import McpError
from oto_mcp import browserbase as B
from oto_mcp.tools import pennylaneged as P


def _tool(name: str):
    from fastmcp import FastMCP

    m = FastMCP("t")
    P.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def substrat(monkeypatch):
    """Session Browserbase et credential simulés — on n'exerce ici QUE la
    traduction d'une réponse d'API interne en réponse de tool."""
    monkeypatch.setattr(P.browserbase, "is_configured", lambda: True)
    monkeypatch.setattr(P, "_context_id", lambda: "ctx-1")
    return monkeypatch


@pytest.mark.parametrize("js,ou", [(P._FETCH_JS, "pennylaneged"),
                                   (B._FETCH_JS, "browserbase")],
                         ids=["pennylaneged", "browserbase"])
def test_le_corps_d_une_reponse_ne_se_lit_qu_une_fois(js, ou):
    """`r.json()` verrouille le flux même quand il échoue : l'appeler dans un
    `try` dont le `catch` relit le corps EST la panne de #600. La forme sûre lit
    le texte une seule fois, puis le parse.

    Le JS de `browserbase` porte le même défaut et sert `crunchbase` et
    `brevoauto` — il se corrige dans le même geste.

    On n'inspecte que le CODE : les lignes de commentaire sont retirées, sinon
    l'explication du défaut (qui doit citer `r.json()`) ferait tomber le test
    censé interdire l'appel."""
    code = "\n".join(l for l in js.splitlines()
                     if not l.lstrip().startswith("//"))
    assert "r.json()" not in code, (
        f"_FETCH_JS ({ou}) : `r.json()` consomme le corps avant d'échouer — "
        "lire `await r.text()` UNE fois, puis `JSON.parse` (#600)")
    assert code.count("await r.text()") == 1, (
        f"_FETCH_JS ({ou}) : le corps doit être lu exactement une fois (#600)")


def test_une_suppression_qui_reussit_le_dit(substrat):
    """Une DELETE 204 ne rend AUCUN corps, et `_call` en faisait `{}` : l'agent
    n'obtenait aucun verdict sur l'acte qu'il venait de commettre. Le tool doit
    confirmer ce qu'il a supprimé (#600)."""
    async def _eval(ctx, app, js, arg):
        assert arg["method"] == "DELETE"
        assert arg["path"] == "/companies/239568/dms/items/1924300902400"
        return {"status": 204, "data": None}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    out = asyncio.run(_tool("pennylaneged_delete")(company_id=239568,
                                                   item_id=1924300902400))
    assert out["deleted"] is True
    assert out["item_id"] == 1924300902400
    assert out["company_id"] == 239568
    assert out["status"] == 204


def test_une_panne_du_substrat_est_nommee(substrat):
    """La panne de #600 n'était pas une `BrowserbaseError` : c'était une erreur
    playwright, que `_call` n'attrapait pas — elle remontait nue jusqu'à la
    taxonomie, qui la traduisait en « Erreur interne du serveur. ». Un refus doit
    nommer ce qu'on a tenté ET la panne d'origine."""
    async def _eval(ctx, app, js, arg):
        raise RuntimeError("Page.evaluate: TypeError: quelque chose a cassé")

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_delete")(company_id=239568, item_id=42))
    msg = str(e.value)
    assert "DELETE" in msg and "/dms/items/42" in msg, \
        "le refus dit la route et le verbe tentés"
    assert "Page.evaluate" in msg, \
        "et la panne d'origine, pas un message générique"


def test_une_ecriture_dont_l_issue_est_inconnue_ne_passe_pas_pour_un_succes(substrat):
    """Corollaire de #600 dans l'autre sens : si le substrat casse APRÈS avoir
    lancé la requête, on ne sait pas si l'écriture a eu lieu — le refus doit le
    DIRE, puisque c'est exactement l'information qui manquait à l'agent."""
    async def _eval(ctx, app, js, arg):
        raise RuntimeError("Page.evaluate: TypeError: body stream already read")

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_delete")(company_id=1, item_id=2))
    msg = str(e.value).lower()
    assert "peut avoir" in msg or "vérifie" in msg, (
        "une écriture au sort inconnu doit inviter à RELIRE l'arbre, pas "
        "laisser croire à un échec franc (#600)")


def test_la_fiche_societe_porte_le_fiscal_et_ecarte_les_drapeaux(substrat):
    """Les trois réglages fiscaux d'un dossier (catégorie IS/IR, régime fiscal,
    TVA) ne sont dans AUCUNE API publique Pennylane et ne sont pas non plus dans
    la liste du portefeuille : ils vivent dans `context`. Le tool doit les rendre
    — et ne pas les noyer sous les drapeaux de fonctionnalité, qui pèsent lourd
    et ne disent rien du dossier."""
    async def _eval(ctx, app, js, arg):
        assert arg["path"] == "/companies/239568/context"
        assert app.endswith("/companies/239568/dms/items"), (
            "la SPA exige d'être sur la vue DMS de la société avant que `context` "
            "réponde 200")
        return {"status": 200, "data": {
            "company": {"id": 239568, "fiscal_category": "bic_is",
                        "fiscal_regime": "fr_rn", "reg_no": "123456789",
                        "legal_form_code": "5710"},
            "firm": None, "userRole": "external_accountant",
            "experiments": ["x"] * 200, "companyFeaturesAbility": {"a": 1},
            "userFeaturesAbility": {"b": 2}}}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    out = asyncio.run(_tool("pennylaneged_company")(company_id=239568))
    assert out["company"]["fiscal_category"] == "bic_is"
    assert out["company"]["fiscal_regime"] == "fr_rn"
    assert out["company"]["reg_no"] == "123456789", \
        "le SIREN est ici, alors qu'il manque à la liste du portefeuille"
    assert out["user_role"] == "external_accountant"
    assert "experiments" not in out and "companyFeaturesAbility" not in out


def test_une_fiche_sans_societe_est_refusee_pas_rendue_vide(substrat):
    """Rendre `{}` sur une réponse inattendue ferait passer une panne pour un
    dossier sans paramétrage — l'agent construirait un export faux sans le
    savoir. Le refus doit nommer la société visée."""
    async def _eval(ctx, app, js, arg):
        return {"status": 200, "data": {"redirect_to_onboarding_form": True}}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_company")(company_id=777))
    assert "777" in str(e.value)
