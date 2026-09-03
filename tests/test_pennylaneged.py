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

---

**2026-09-03 — la sonde de login était couplée à une route métier.** Pennylane a
déplacé le portefeuille de `/crm/flow_companies` vers `/portfolio/crm/flow_companies`.
`_verify_session` sondait CETTE route et faisait `return res == 200` : elle a reçu 404,
rendu False, et `browser_session.finalize` est sorti AVANT `_persist()` — plus aucune
cliente ne pouvait connecter sa GED, et le message accusait l'authentification. Une
matinée perdue chez une cliente (cabinet Fidens) et chez son agent.

Deux faits mesurés le jour même sur `app.pennylane.com`, qui fondent le correctif :
une route VIVANTE répond **401** à une session anonyme (`/portfolio/crm/flow_companies`
→ 401), une route DISPARUE répond **404** (`/crm/flow_companies` → 404). Un 404 dit donc
« l'endpoint a bougé », jamais « tu n'es pas connecté ».

D'où la seconde salve de tests : la sonde ne tape plus une route métier mais
`/users/me` (200 dans les deux cas, verdict dans le CORPS), un 404 de la sonde ne
bloque PLUS la persistance, un 401 la bloque toujours, et un 404 sur un appel métier
se dit comme un déménagement de route.
"""
from __future__ import annotations

import asyncio

import pytest
from oto_mcp.mcp_errors import McpError
from oto_mcp import browser_session, browserbase as B
from oto_mcp.tools import pennylaneged as P
from oto_mcp.tools import pennylaneged_session as S


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


# --- La sonde de login (2026-09-03) -----------------------------------------

def test_la_sonde_ne_tape_pas_une_route_metier():
    """LE défaut structurant : vérifier le login sur une vue MÉTIER, c'est faire
    dépendre la connexion de tous les clients du découpage des URL du produit. La
    sonde doit taper une route de SESSION — et surtout jamais celle du portefeuille,
    qui a précisément déménagé."""
    assert S._PROBE_PATH == "/users/me"
    for interdit in ("flow_companies", "/crm/", "/portfolio/", "/dms/", "/companies/"):
        assert interdit not in S._PROBE_JS, \
            f"la sonde de login ne doit rien savoir de {interdit!r}"


@pytest.mark.parametrize("res,connecte,motif", [
    ({"status": 200, "json": True, "logged_in": True}, True, browser_session.LOGGED_IN),
    ({"status": 200, "json": True, "logged_in": False}, False, browser_session.NO_SESSION),
    ({"status": 401}, False, browser_session.AUTH_REJECTED),
    ({"status": 403}, False, browser_session.AUTH_REJECTED),
    ({"status": 200, "json": True, "logged_in": True, "login_page": True},
     False, browser_session.AUTH_REJECTED),
    ({"status": 0, "error": "TypeError: Failed to fetch"}, False,
     browser_session.NO_SESSION),
], ids=["logue", "anonyme", "401", "403", "page-de-login", "reseau-mort"])
def test_le_verdict_de_la_sonde_vient_du_corps_pas_du_code(res, connecte, motif):
    """`/users/me` répond 200 logué comme délogué : c'est `user` qui tranche.

    Et un refus n'est PAS un booléen : « tu n'as pas fini de te loguer » (`no_session`)
    et « Pennylane t'a refusé » (`auth_rejected`) appellent deux conduites différentes.
    Sans le motif, l'agent n'a qu'une option — recommencer, en boucle."""
    v = S._read_probe(res)
    assert (v.connected, v.reason) == (connecte, motif)
    if not v.connected:
        assert v.detail and v.retry is True, \
            "un refus qui vient de l'utilisateur se répare en recommençant, et le dit"


@pytest.mark.parametrize("st", [404, 500, 502])
def test_une_sonde_sans_verdict_leve_au_lieu_de_dire_pas_logue(st):
    """Un 404 dit « cet endpoint n'existe plus », pas « tu n'es pas connecté ». Le
    confondre avec un refus d'authentification est exactement ce qui a cassé toutes
    les connexions le 2026-09-03 — et le message accusait le mot de passe."""
    with pytest.raises(browser_session.ProbeUnavailable) as e:
        S._read_probe({"status": st})
    msg = str(e.value)
    assert S._PROBE_PATH in msg and str(st) in msg, \
        "l'anomalie nomme l'endpoint sondé et ce qu'il a répondu"
    assert "ne recommence pas" in msg.lower(), \
        "et elle COUPE la boucle : le problème n'est pas chez l'utilisateur"
    if st == 404:
        assert "déplacée" in msg or "n'existe plus" in msg


def _finalize_avec(verify, monkeypatch, nom):
    """Joue `finalize` de bout en bout sur un connecteur jetable — seule l'écriture au
    coffre est doublée, la décision « persister ou non » reste celle du seam."""
    persiste: list = []
    monkeypatch.setattr(browser_session, "_persist",
                        lambda *a, **k: persiste.append(a))
    browser_session.register(nom, verify)
    browser_session._PENDING[("sub-1", "ctx-1", "ses-1")] = float("inf")
    out = asyncio.run(browser_session.finalize("sub-1", nom, "ctx-1", "ses-1"))
    return out, persiste


def test_un_404_de_la_sonde_ne_bloque_plus_la_persistance(monkeypatch):
    """LE correctif. La session vient d'être loguée à la main dans la Live View :
    refuser de l'écrire parce que la SONDE est hors service rend le connecteur
    inconnectable pour tout le monde. On persiste — et on remonte l'anomalie, on ne
    l'avale pas."""
    async def _sonde_muette(_sid):
        return S._read_probe({"status": 404})

    out, persiste = _finalize_avec(_sonde_muette, monkeypatch, "_test_pl_404")
    assert out.connected is True, "un endpoint disparu n'est pas un refus de login"
    assert len(persiste) == 1, "le Context DOIT être écrit au coffre"
    assert S._PROBE_PATH in out.warning, \
        "et l'appelant apprend que le login n'a pas pu être confirmé"
    assert (out.reason, out.retry) == (browser_session.PROBE_UNAVAILABLE, False), \
        "`retry: false` — la panne est chez nous, se reconnecter n'y changera rien"


def test_un_401_de_la_sonde_bloque_toujours_la_persistance(monkeypatch):
    """Le pendant : un vrai signal d'authentification reste bloquant. Persister un
    Context non logué poserait au coffre un credential mort — la sonde garde tout son
    sens, elle ne devient pas permissive."""
    async def _pas_logue(_sid):
        return S._read_probe({"status": 401})

    out, persiste = _finalize_avec(_pas_logue, monkeypatch, "_test_pl_401")
    assert out.connected is False and out.warning == ""
    assert persiste == [], "rien ne s'écrit au coffre tant que le login n'est pas fait"
    assert (out.reason, out.retry) == (browser_session.AUTH_REJECTED, True), \
        "celui-là, en revanche, se répare en refaisant le login"


def test_le_portefeuille_tape_la_route_deplacee(substrat):
    """La route relevée dans le bundle de la SPA le 2026-09-03 (`getCRMFlowCompanies`).
    L'ancienne, `/crm/flow_companies`, répond 404 : la garder revenait à ne jamais
    pouvoir lister le portefeuille d'un cabinet."""
    vu: dict = {}

    async def _eval(ctx, app, js, arg):
        vu["path"] = arg["path"]
        return {"status": 200, "data": {"companies": [], "pagination": {"page": 2}}}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    asyncio.run(_tool("pennylaneged_companies")(page=2))
    assert vu["path"] == "/portfolio/crm/flow_companies?page=2"


def test_un_404_metier_se_dit_comme_un_demenagement_de_route(substrat):
    """Le message qui aurait épargné la matinée du 2026-09-03 : sur cette API interne
    un 404 est un endpoint disparu, pas une session expirée (ça, c'est 401/403)."""
    async def _eval(ctx, app, js, arg):
        return {"status": 404, "data": {"status": 404, "error": "Not Found"}}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_companies")())
    msg = str(e.value)
    assert "404" in msg and "/portfolio/crm/flow_companies" in msg
    assert "ENDPOINT" in msg or "n'existe plus" in msg
    assert "401" in msg, "et il rappelle à quoi ressemble une VRAIE session expirée"
    assert "NE RELANCE PAS" in msg, \
        "il coupe la boucle de reconnexion : six essais chez la cliente le 2026-09-03"
    assert "AUTRES outils" in msg and "company_id" in msg, \
        "et il dit que le connecteur n'est pas mort pour autant"


def test_la_liste_des_societes_a_une_voie_de_secours_independante(substrat):
    """Point de passage obligé = point de panne unique. `minimal=True` passe par
    `/navbar/companies` (le sélecteur de société de la SPA), qui ne partage RIEN avec la
    route du portefeuille : quand l'une tombe, l'autre résout encore le `company_id`."""
    vu: dict = {}

    async def _eval(ctx, app, js, arg):
        vu["path"] = arg["path"]
        return {"status": 200, "data": {"companies": [{"id": 239568}]}}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    out = asyncio.run(_tool("pennylaneged_companies")(page=1, minimal=True))
    assert vu["path"].startswith("/navbar/companies?")
    assert "flow_companies" not in vu["path"], \
        "la voie de secours ne doit pas dépendre de la route en panne"
    assert out["companies"][0]["id"] == 239568
