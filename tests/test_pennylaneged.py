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
matinée perdue chez une cliente (un cabinet comptable) et chez son agent.

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
    assert "may have" in msg or "re-read" in msg, (
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


def test_la_fiche_ne_sert_ni_jeton_ni_identifiant_de_prestataire(substrat):
    """`context` porte la plomberie de Pennylane : un jeton d'accès à son canal
    temps réel, les identifiants qu'il tient chez sa banque et son CRM. Rien de
    cela ne décrit le dossier, et un jeton n'a rien à faire dans la réponse d'un
    agent. On écarte par famille — un nom neuf de la même famille tombe aussi."""
    async def _eval(ctx, app, js, arg):
        return {"status": 200, "data": {"company": {
            "id": 1, "fiscal_category": "bic_is", "vat_number": "FR00000000000",
            "pusher_channel": "private-x", "pusher_channel_access_token": "t0k",
            "swan_id": "s", "swan_onboarding_email": "a@b.c",
            "salesforce_id": "001", "salesforce_business_segmentation": None,
            "un_futur_refresh_token": "r"}, "firm": None, "userRole": "x"}}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    fiche = asyncio.run(_tool("pennylaneged_company")(company_id=1))["company"]
    assert fiche == {"id": 1, "fiscal_category": "bic_is",
                     "vat_number": "FR00000000000"}, \
        "le métier reste (le numéro de TVA n'est pas de la plomberie), le reste part"


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
    assert "do not retry" in msg.lower(), \
        "et elle COUPE la boucle : le problème n'est pas chez l'utilisateur"
    if st == 404:
        assert "moved" in msg or "no longer exists" in msg


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


def _sonde_cassee_par_la_fenetre(monkeypatch, statut):
    async def _sonde(_sid):
        raise RuntimeError("connect_over_cdp: WebSocket error: 410 Gone")
    monkeypatch.setattr(browser_session.browserbase, "session_status",
                        lambda _sid: statut)
    return _sonde


def test_une_fenetre_fermee_ne_dit_pas_de_reessayer(monkeypatch):
    """Mesuré le 07/10 : la Live View a expiré pendant le login, la sonde s'est
    cassée sur un 410, et le refus disait « réessaie » — ce qui ne peut pas
    réussir. Le motif dit ce qui est arrivé et les deux seules issues."""
    sonde = _sonde_cassee_par_la_fenetre(monkeypatch, "TIMED_OUT")
    out, persiste = _finalize_avec(sonde, monkeypatch, "_test_pl_410")
    assert (out.connected, out.reason, out.retry) == (
        False, browser_session.WINDOW_CLOSED, False)
    assert persiste == [], "rien n'est écrit sans le geste explicite `force`"
    assert "force=true" in out.detail and "relance" in out.detail
    assert "réessaie" not in out.detail


def test_une_sonde_cassee_sur_une_fenetre_vivante_reste_un_refus_generique(monkeypatch):
    """Le pendant : si la fenêtre tourne encore, la panne est ailleurs — on ne
    prétend pas qu'elle est fermée."""
    sonde = _sonde_cassee_par_la_fenetre(monkeypatch, "RUNNING")
    with pytest.raises(browser_session.SessionError):
        _finalize_avec(sonde, monkeypatch, "_test_pl_vivante")


def test_le_motif_window_closed_est_servi_a_l_agent():
    from fastmcp import FastMCP

    m = FastMCP("t")
    S.register(m)
    doc = asyncio.run(m.get_tool("pennylaneged_connect_status")).fn.__doc__
    assert "`window_closed`" in doc and "force=true" in doc


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


def test_un_404_metier_ne_se_dit_pas_comme_une_session_expiree(substrat):
    """Le message qui aurait épargné la matinée du 2026-09-03 : sur cette API interne
    un 404 n'est pas une session expirée (ça, c'est 401/403).

    ⚠️ Ce test EXIGEAIT « ENDPOINT … n'existe plus » jusqu'au 2026-09-10. Il ne
    protégeait donc pas une garantie : il protégeait une affirmation devenue fausse,
    et il aurait défendu l'erreur contre sa correction. La mesure du 10/09 — même
    route, 200 pour un compte de cabinet et 404 pour un compte d'entreprise, à la
    même heure — a montré que le 404 a DEUX causes. Ce qu'il garde de vrai est ici ;
    ce qui manquait est dans `test_un_404_nomme_les_DEUX_causes_et_le_test_qui_tranche`."""
    async def _eval(ctx, app, js, arg):
        return {"status": 404, "data": {"status": 404, "error": "Not Found"}}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_companies")())
    msg = str(e.value)
    assert "404" in msg and "/portfolio/crm/flow_companies" in msg
    assert "401" in msg, "et il rappelle à quoi ressemble une VRAIE session expirée"
    assert "DO NOT RERUN" in msg, \
        "il coupe la boucle de reconnexion : six essais chez la cliente le 2026-09-03"
    assert "minimal=true" in msg, \
        "et il donne la voie qui reste ouverte — sans promettre que le connecteur est mort"


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


def test_la_garde_sur_l_espace_d_id_est_dans_CHAQUE_outil_qui_le_consomme():
    """Un piège se documente là où l'on TOMBE, pas là où l'on aurait dû passer.

    L'avertissement « le portefeuille n'est pas dans le connecteur `pennylane` »
    existait depuis le 28/08 — mais dans la description de `pennylaneged_companies`,
    l'outil qui REND l'identifiant. Le 2026-09-03 un agent est tombé exactement dans
    le même trou en appelant `pennylaneged_tree` : il n'avait aucune raison de lire la
    description d'un outil qu'il n'appelait pas, et quand la liste est tombée en panne
    il ne pouvait même plus la croiser par hasard. Un renvoi ne protège personne."""
    from fastmcp import FastMCP

    m = FastMCP("t")
    P.register(m)
    consommateurs = ["pennylaneged_company", "pennylaneged_tree",
                     "pennylaneged_create_folder", "pennylaneged_request_upload",
                     "pennylaneged_finalize", "pennylaneged_delete"]
    for nom in consommateurs:
        t = asyncio.run(m.get_tool(nom))
        # La section `Args:` sort de la description de l'outil pour alimenter le
        # SCHÉMA du paramètre : c'est là que l'agent lit la garde, donc là qu'on la
        # vérifie. La chercher dans la description passerait à côté.
        doc = ((t.parameters.get("properties") or {}).get("company_id") or {}).get(
            "description", "")
        assert "pennylane`" in doc and "NOT" in doc, (
            f"{nom} prend un `company_id` sans dire de quel ESPACE il vient : "
            "l'id de l'API publique n'est pas celui de la GED")
        assert "app.pennylane.com/companies/" in doc, (
            f"{nom} doit dire où LIRE le bon id — sans quoi l'agent ne peut pas se "
            "corriger quand `pennylaneged_companies` est en panne")


def test_un_refus_sur_une_societe_n_accuse_pas_la_session(substrat):
    """401/403 a TROIS causes et une seule était nommée — la moins probable.

    Le 2026-09-03, deux agents indépendants ont conclu « session expirée » et
    reconnecté en boucle : l'un tenait un identifiant venu du connecteur `pennylane`,
    l'autre avait une session parfaitement vivante. Le message doit nommer l'espace
    d'identifiants EN PREMIER, et donner le moyen de trancher sans reconnecter."""
    async def _eval(ctx, app, js, arg):
        return {"status": 403, "data": {"error": "Forbidden"}}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_tree")(company_id=23077330))
    msg = str(e.value)
    assert "23077330" in msg, "le refus nomme la société visée"
    assert "pennylane" in msg and "public" in msg, \
        "et la cause la plus fréquente : l'id vient de l'autre connecteur"
    assert "ANOTHER company" in msg or "another company" in msg, \
        "et comment trancher sans reconnecter"


def test_un_refus_hors_societe_accuse_bien_la_session(substrat):
    """Le pendant : quand l'appel ne vise AUCUNE société, l'espace d'identifiants
    est hors de cause et la session est bien la seule explication. Ne pas le dire
    renverrait l'agent chercher un id fautif qui n'existe pas."""
    async def _eval(ctx, app, js, arg):
        return {"status": 401, "data": None}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_companies")(page=1))
    assert "connect_start" in str(e.value)


# --- le 404 a DEUX causes, pas une (mesuré le 2026-09-10) --------------------
#
# Le message d'erreur affirmait une cause unique — « l'ENDPOINT N'EXISTE PLUS » —
# et concluait « le correctif est chez nous ». Mesuré ce jour-là sur la production :
# `/portfolio/crm/flow_companies` répondait 200 pour un compte de cabinet et 404
# pour un compte d'entreprise, à la même heure, la même route. La route n'avait pas
# bougé : elle n'existe pas pour un compte qui n'est rattaché à aucun cabinet.
#
# Le défaut n'est pas d'avoir corrigé le cas du 03/09, il est de l'avoir gravé comme
# LA cause. Un message qui n'admet qu'une explication envoie chercher le correctif
# là où il n'y a rien à corriger — ici, dans le bundle d'un fournisseur.

def test_un_404_nomme_les_DEUX_causes_et_le_test_qui_tranche(substrat):
    """Le refus doit laisser à l'appelant de quoi décider lui-même, sans nous."""
    async def _eval(ctx, app, js, arg):
        return {"status": 404, "data": {"error": "Not Found"}}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_companies")(page=1))
    msg = str(e.value)

    # La cause qui manquait, et qui est la plus fréquente sur une route de cabinet.
    assert "SCOPE" in msg, (
        "le refus doit nommer le cas « ton compte n'a pas ce périmètre » : sans lui, "
        "un compte d'entreprise ordinaire est renvoyé chercher un bug chez nous")
    # Celle d'origine, conservée : elle reste vraie, elle n'était pas seule.
    assert "MOVED" in msg or "moved" in msg
    # Et de quoi trancher, sans reconnecter ni attendre notre diagnostic.
    assert "minimal=true" in msg, (
        "le refus doit donner le test discriminant — une autre route, même session")


def test_un_404_ne_dit_jamais_de_se_reconnecter(substrat):
    """Non-régression du 03/09 : c'est cette confusion-là qui avait coûté une
    matinée. Un 404 n'est pas une session morte, et le dire reste juste."""
    async def _eval(ctx, app, js, arg):
        return {"status": 404, "data": {}}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_companies")(page=1))
    msg = str(e.value)
    assert "DO NOT RERUN" in msg and "session is good" in msg


def test_le_texte_servi_ne_promet_plus_une_cause_unique():
    """Le module l'affirmait à trois endroits — en-tête, message, docstring de
    l'outil. Les trois se lisent, et les trois envoyaient au même endroit."""
    import inspect

    entete = P.__doc__ or ""
    assert "two causes" in entete.lower(), entete[:300]
    doc = inspect.getdoc(_tool("pennylaneged_companies")) or ""
    assert "firm" in doc and "minimal=true" in doc, (
        "la docstring de l'outil doit porter la même nuance que le refus : c'est "
        "elle que l'agent lit AVANT d'appeler")


@pytest.mark.parametrize("status", [401, 403])
def test_un_refus_hors_societe_declare_la_session_morte(substrat, status):
    """Relevé le 07/10/2026 : sept 401 Pennylane sur une route qui ne vise aucune
    société, servis « la session est en cause », sans que la session soit peinte en
    rouge — le statut vient du fetch de la page, aucune exception ne le porte."""
    from oto_mcp.error_taxonomy import credential_rejected_in_chain

    async def _eval(ctx, app, js, arg):
        return {"status": status, "data": None}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_companies")(minimal=True))
    assert credential_rejected_in_chain(e.value) is True


def test_un_refus_sur_une_societe_ne_declare_pas_la_session_morte(substrat):
    """Un refus qui vise une société accuse d'abord l'espace d'ids : la session n'est
    pas peinte en rouge pour lui."""
    from oto_mcp.error_taxonomy import credential_rejected_in_chain

    async def _eval(ctx, app, js, arg):
        return {"status": 401, "data": None}

    substrat.setattr(P.browserbase, "run_page_eval", _eval)
    with pytest.raises(McpError) as e:
        asyncio.run(_tool("pennylaneged_tree")(company_id=239568))
    assert credential_rejected_in_chain(e.value) is False
