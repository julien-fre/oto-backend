"""Pennylane GED (DMS) — bac documentaire via l'API PRIVÉE de la SPA.

⚠️ La GED de Pennylane **n'est pas exposée par l'API publique** (le connecteur
keyé `pennylane` ne peut donc pas y écrire — son token ne porte aucun scope DMS).
Elle l'est par l'**API interne** de `app.pennylane.com` (cookie de session + CSRF
tournant), sous le scope société `/companies/{cid}/dms/…`. C'est un connecteur
**distinct** de `pennylane` : credential de nature différente (session navigateur,
pas une clé API).

Exécution — **Browserbase** (`oto_mcp/browserbase.py`), même substrat que
`crunchbase`/`brevo` : l'API interne n'accepte les appels que depuis une **session
navigateur vivante** (un `httpx` brut risque le blocage Cloudflare, et une session
ne se transplante pas par export de cookie). L'utilisateur se logue UNE fois via la
**Live View** (`pennylaneged_connect_start`), sa session persiste dans un **Context**
Browserbase (= le credential per-user, coffre `pennylaneged`), et chaque appel DMS
s'exécute en `fetch()` DANS une session éphémère du Context, same-origin
`app.pennylane.com`. Creds plateforme = env `BROWSERBASE_API_KEY` / `BROWSERBASE_PROJECT_ID`.

**Exigences de l'API interne** (gérées par le JS in-page `_FETCH_JS`) : header
`accept: application/json` (sinon 404 HTML — contrainte Rails), `x-requested-with:
XMLHttpRequest`, et sur les écritures `x-csrf-token` = valeur **tournante** du cookie
`my_csrf_token` (relue à CHAQUE appel — le `<meta csrf-token>` est périmé dès le 1er XHR).

**Split data-plane (RGPD)** — l'upload d'un fichier NE fait PAS transiter les octets
par Oto (cf. ADR / issue #31). `pennylaneged_request_upload` (control plane) demande
une **URL S3 présignée** ; l'agent LOCAL fait le `PUT` des octets **directement** sur
S3 (jamais par Oto, jamais via MCP) ; puis `pennylaneged_finalize` (control plane)
crée l'entrée DMS depuis le `signed_id`. Les octets vont `local → S3 Pennylane`, leur
destination de toute façon.

**GED cible (une par client)** — le cabinet gère N sociétés clientes, chacune
avec SA GED. Chaque tool prend un `company_id` **obligatoire** : aucun défaut
mémorisé, pour ne jamais risquer d'écrire dans la GED du mauvais client.
`pennylaneged_companies` liste les sociétés pour résoudre le `company_id` cible —
mais ce n'est PAS un passage obligé, et il ne faut pas le croire : le `company_id` est
lisible **dans l'URL de la SPA** (`app.pennylane.com/companies/<company_id>/…`, visible
dès qu'on ouvre un dossier), et `pennylaneged_companies(minimal=True)` le rend par une
route indépendante. Quand la liste tombe, les trois autres outils (arborescence, fiche,
dépôt) marchent toujours — le 2026-09-03, une cliente a passé sa matinée à croire le
connecteur mort parce que SEULE cette liste l'était.

⚠️ **Une API interne BOUGE, et son 404 le dit.** Vérifié le 2026-09-03 : une route
VIVANTE répond **401** à une session anonyme, une route DISPARUE répond **404**. Ici un
404 = « endpoint déplacé/renommé », jamais « session expirée » (ça, c'est 401/403) ; la
nouvelle route se relève dans le bundle de la SPA (`assets.pennylane.com/assets/
application-*.js` + ses chunks). Le portefeuille a ainsi migré de `/crm/flow_companies`
vers `/portfolio/crm/flow_companies`.

Le LOGIN (Live View, sonde de vérification, persistance de la session au coffre) vit
dans le module frère `pennylaneged_session.py` — ici, on suppose la session acquise.

Statut : flux RE **validé manuellement** (18/06, compte test client) ; **reste à
smoker en live** sur le substrat Browserbase (CSRF in-page + longévité de session).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR

from .. import access, browserbase
from ..auth.hooks import current_user_sub_from_token

# Origine de la SPA — toutes les routes internes (DMS, direct_uploads, crm) en
# dérivent. La page chargée pour porter la session est same-origin (un chemin de
# cette origine), donc `fetch("/companies/…")` porte les cookies.
_ORIGIN = "https://app.pennylane.com"

# JS in-page propre à Pennylane : lit le CSRF tournant du cookie `my_csrf_token` à
# l'instant de l'appel et pose les headers Rails attendus. `path` est un chemin
# absolu de l'origine `app.pennylane.com` (le `fetch` est donc same-origin).
_FETCH_JS = """async ({path, method, body}) => {
    const m = document.cookie.match(/(?:^|;\\s*)my_csrf_token=([^;]+)/);
    const headers = {"accept": "application/json", "x-requested-with": "XMLHttpRequest"};
    if (m) headers["x-csrf-token"] = decodeURIComponent(m[1]);
    if (body) headers["content-type"] = "application/json";
    const r = await fetch(path, {
        method, credentials: "include", headers,
        body: body ? JSON.stringify(body) : undefined,
    });
    // Le corps d'une Response ne se lit qu'UNE fois : `r.json()` VERROUILLE le
    // flux avant même d'échouer, donc un `catch` qui rappelle `r.text()` lève
    // « body stream already read » et masque la réponse RÉELLE. Vécu en prod le
    // 27/08 sur un DELETE de dossier GED : la suppression était partie, le tool
    // a répondu « Erreur interne du serveur. » (signal #600, tool_calls#1039702).
    // On lit le texte une seule fois, puis on le parse.
    const txt = await r.text();
    let data;
    try { data = txt ? JSON.parse(txt) : null; }
    catch (e) { data = {raw: txt.slice(0, 400)}; }
    return {status: r.status, data};
}"""


def _err(msg: str, code: int = INVALID_PARAMS) -> McpError:
    return McpError(ErrorData(code=code, message=msg))


def _sub() -> str:
    sub = None
    try:
        sub = current_user_sub_from_token()
    # noqa: SILENT — dette déclarée : sub avalé (#424, verdict C — seam commun)
    except Exception:
        pass
    if not sub:
        raise _err("Auth requise — ce tool ne marche que sur le transport HTTP authentifié.")
    return sub


def _context_id() -> str:
    """Context Browserbase de l'utilisateur (= sa session Pennylane loguée), résolu du
    coffre. Lève une McpError actionnable si la GED n'est pas connectée."""
    try:
        return access.resolve_credential("pennylaneged", want="byo").key
    except McpError:
        raise _err("Pennylane GED non connecté. Lance `pennylaneged_connect_start` pour "
                   "te loguer (une fois) à Pennylane via la Live View.")


def _company_app(company_id: int) -> str:
    """Page à charger pour amorcer le contexte société (la SPA exige une navigation
    sur la vue DMS de la société avant que `/companies/{cid}/context` réponde 200)."""
    return f"{_ORIGIN}/companies/{int(company_id)}/dms/items"



async def _call_raw(app: str, path: str, method: str = "GET",
                    body: Optional[dict] = None) -> dict:
    """L'appel d'API interne, rendu BRUT : `{status, data}`.

    Séparé de `_call` parce qu'une ÉCRITURE a besoin du `status` pour se
    prononcer : un `DELETE` réussi répond `204` **sans corps**, et `_call` en
    faisait un `{}` indistinguable d'une réponse vide (signal #600).

    ⚠️ Le `except` ne peut pas se limiter à `BrowserbaseError` : la panne de
    #600 était une erreur **playwright** (`Page.evaluate: TypeError…`) levée
    depuis la page, d'une classe que le substrat ne convertit pas. Elle
    remontait donc nue jusqu'à la taxonomie d'erreurs, qui l'a servie à l'agent
    en « Erreur interne du serveur. » — un message qui ne dit ni ce qu'on a
    tenté, ni que l'écriture était peut-être passée. On nomme les deux."""
    if not browserbase.is_configured():
        raise _err("Browserbase non configuré côté plateforme "
                   "(BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID).", code=INTERNAL_ERROR)
    ctx_id = _context_id()
    try:
        res = await browserbase.run_page_eval(
            ctx_id, app, _FETCH_JS, {"path": path, "method": method, "body": body})
    except McpError:
        raise
    except Exception as e:  # noqa: BLE001 — re-levée nommée juste en dessous
        # La requête peut être PARTIE avant la panne : sur une écriture, dire
        # « échec » serait affirmer plus qu'on ne sait (#600).
        incertitude = ("" if method.upper() == "GET" else
                       " L'appel était peut-être déjà parti : l'écriture PEUT AVOIR "
                       "eu lieu — relis l'arborescence (`pennylaneged_tree`) avant "
                       "de retenter.")
        raise _err(f"Appel Pennylane GED échoué — {method.upper()} {path} : "
                   f"{type(e).__name__}: {e}.{incertitude}",
                   code=INTERNAL_ERROR) from e
    st = res.get("status")
    if st in (401, 403):
        raise _err("Session Pennylane expirée / déconnectée — relance `pennylaneged_connect_start`.")
    if st == 404:
        # Le 404 de cette API n'est PAS ambigu (cf. en-tête) : la route n'existe plus.
        # Le taire a coûté une matinée de chasse à l'authentification le 2026-09-03.
        raise _err(f"Pennylane a répondu 404 sur {method.upper()} {path} — sur cette API "
                   "interne un 404 dit que l'ENDPOINT N'EXISTE PLUS (route déplacée ou "
                   "renommée par Pennylane), PAS que la session est expirée (ça, c'est "
                   "401/403). Conduite à tenir : NE RELANCE PAS `pennylaneged_connect_"
                   "start` — ta session est bonne, se reconnecter ne changera rien. Le "
                   "correctif est chez nous (relever la nouvelle route dans le bundle de "
                   "la SPA). En attendant, les AUTRES outils du connecteur fonctionnent "
                   "sans doute très bien : essaies-en un avant de conclure que la GED "
                   "est en panne, et lis le `company_id` dans l'URL de la SPA si c'est "
                   "la liste des sociétés qui manque.", code=INTERNAL_ERROR)
    if not (200 <= (st or 0) < 300):
        raise _err(f"Pennylane GED a renvoyé {st} : {str(res.get('data'))[:200]}",
                   code=INTERNAL_ERROR)
    return {"status": st, "data": res.get("data")}


async def _call(app: str, path: str, method: str = "GET",
                body: Optional[dict] = None) -> dict:
    """`_call_raw` réduit au CORPS décodé — la forme qu'attendent les lectures.

    C'est ici que vit la mise en forme pour l'agent, pas dans `_call_raw` : une
    écriture a besoin du `status` brut pour se prononcer sur son propre acte."""
    data = (await _call_raw(app, path, method, body)).get("data")
    # L'API interne renvoie parfois un TABLEAU nu (ex. `/dms/items/tree`). MCP exige
    # que le structured_content d'un tool soit un objet (dict) ou None — JAMAIS une
    # liste (sinon `ValueError: structured_content must be a dict` → tool cassé, vu
    # en prod sur `pennylaneged_tree`). On enveloppe toute liste sous `items` : forme
    # uniforme et sérialisable pour l'agent.
    if isinstance(data, list):
        return {"items": data}
    return data or {}




def register(mcp: FastMCP) -> None:

    # --- Résolution « où » (control plane) ----------------------------------
    @mcp.tool()
    async def pennylaneged_companies(page: int = 1, minimal: bool = False) -> dict:
        """Liste les sociétés du portefeuille (côté cabinet) — résout le `company_id`
        cible d'une opération GED, et porte la fiche de gestion de chaque dossier.

        ⚠️ **Le portefeuille d'un cabinet vit ICI**, pas dans le connecteur keyé
        `pennylane` : son API publique est MONO-SOCIÉTÉ et ses « customers » sont les
        clients FACTURÉS par une société, pas les dossiers gérés. Cherché là, le
        portefeuille est introuvable — vécu par une cliente le 2026-08-28.

        ⚠️ **La route a déménagé** (bundle de la SPA, chunk `list-*.js`,
        `getCRMFlowCompanies`, relevé le 2026-09-03) : `/crm/flow_companies` →
        `/portfolio/crm/flow_companies`. Un 404 ici = « elle a encore bougé », pas
        « déloguée ». Renvoie la réponse BRUTE :
        `{companies: [...], pagination: {page, pageSize, pages, totalEntries,
        hasNextPage}}`. **20 sociétés par page** — un portefeuille de cabinet se
        parcourt donc en plusieurs appels, pilotés par `hasNextPage`/`pages`.

        Chaque société porte BIEN PLUS que son `id` (= `company_id`) et son `name` —
        c'est la fiche de gestion complète du dossier (relevé 2026-08-28) :

        - **identité** : `legal_form` (forme juridique, ex. `fr_sas`), `trade_name`,
          `client_code`, `file_type`, `is_demo`/`is_training`/`is_fake` ;
        - **fiscal** : `vat_regime` + `vat_frequency` (régime de TVA et périodicité —
          réglages SÉPARÉS), `current_fiscal_year` (`{start, finish}`),
          `cash_based_accounting`, `number_of_employees` ;
        - **équipe du dossier** : `accountant` (collaborateur en charge, avec email),
          `accounting_supervisor`, `accounting_manager`, `substitute_accountant`,
          `manager`, `legal_manager`, `social_manager`, `legal_collaborator`,
          `social_collaborator`, `external_auditor` ;
        - **état d'avancement** : `transactions` (`pending`, `accounting_needed`,
          `validation_needed`…), `supplier_invoices`, `customer_invoices`,
          `document_requests` (pièces réclamées au client), `bank_accounts`
          (connectées / déconnectées / importées à la main) ;
        - **abonnement** : `subscription_plan`, `saas_plan`, `churns_on`, `confidential`.

        De quoi bâtir un tableau de bord de portefeuille, pas seulement résoudre un id.

        ⚠️ Deux valeurs à NE PAS interpréter à l'aveugle, faute de doc Pennylane : les
        valeurs de `vat_regime` (`standard` observé ; les trois régimes FR sont franchise
        en base / réel simplifié / réel normal) et la forme du `client_code` (UUID sur un
        dossier de TEST, alors que Pennylane documente un « code client » saisissable au
        paramétrage). Relever les valeurs distinctes sur un VRAI portefeuille avant d'en
        faire une colonne lisible ou une clé de rapprochement.

        ABSENTS d'ici : le SIREN, et la **catégorie fiscale** (IS/IR) — Pennylane la
        distingue du « régime fiscal » et la range dans les paramètres du dossier. Les
        deux se cherchent ailleurs : `/companies/{id}/context` (`reg_no`) ou la page de
        paramétrage du dossier.

        ⚠️ Coût : UNE session navigateur par appel — 350 dossiers = 18 pages = 18
        sessions ouvertes puis refermées.

        **Si cet outil tombe, le connecteur n'est PAS mort.** Il n'est le passage obligé
        que pour la fiche de gestion : le `company_id` seul se lit dans l'URL de la SPA
        (`app.pennylane.com/companies/<company_id>/…`), et `minimal=True` le rend par une
        route INDÉPENDANTE de celle du portefeuille. Arborescence, fiche société et dépôt
        marchent sans passer par ici.

        Args:
            page: page de pagination (1-based).
            minimal: prendre la voie LÉGÈRE — `/navbar/companies`, la route du sélecteur
                de société de la SPA, qui rend `{companies: [...]}` sans la fiche de
                gestion. Deux usages : résoudre un `company_id` à moindre coût, et
                surtout garder une voie ouverte quand la route du portefeuille est en
                panne (elles ne partagent rien). Champs OBSERVÉS sous session loguée
                le 2026-09-03 : `id` (= le `company_id`), `display_name`, `source_id`,
                `saas_plan`, `uc_exists`, `is_demo`/`is_training`/`is_fake`, `firm`,
                `company_group` — de quoi identifier un dossier, RIEN de la fiche de
                gestion (ni forme juridique, ni TVA, ni équipe, ni reste-à-faire).
        """
        if minimal:
            qs = urlencode({"page": max(1, int(page)), "per_page": 20})
            return await _call(f"{_ORIGIN}/", f"/navbar/companies?{qs}")
        qs = urlencode({"page": max(1, int(page))})
        return await _call(f"{_ORIGIN}/", f"/portfolio/crm/flow_companies?{qs}")

    @mcp.tool()
    async def pennylaneged_company(company_id: int) -> dict:
        """Fiche d'UNE société : identité légale + paramétrage FISCAL et TVA.

        Complète `pennylaneged_companies` (le portefeuille) là où elle s'arrête. C'est
        ICI, et nulle part ailleurs, que vivent les trois réglages que Pennylane
        distingue et qu'AUCUNE API publique ne rend (relevé 2026-08-28) :

        - `fiscal_category` — catégorie fiscale, ex. `bic_is` (BIC à l'IS) : c'est le
          « IS / IR » du dossier permanent ;
        - `fiscal_regime` — régime fiscal, ex. `fr_rn` (réel normal) ;
        - `vat_frequency`, `vat_day_of_month`, `submitted_to_vat_from`, `vat_number`,
          `default_input_vat_rate` / `default_output_vat_rate` (ex. `FR_200`) — la TVA.

        ⚠️ **Trois champs, trois notions — ne pas les fondre en une colonne.** Le
        `vat_regime` que rend `pennylaneged_companies` (ex. `standard`) est le régime de
        TVA ; il est DISTINCT de `fiscal_regime` (`fr_rn`) et de `fiscal_category`
        (`bic_is`). Les confondre produit un export faux.

        Porte aussi ce que la liste n'a pas : `reg_no` (**le SIREN**), `legal_form_code`
        (code INSEE de forme juridique, ex. `5710` = SAS), `share_capital`,
        `creation_date` / `cessation_date`, `address` / `postal_code` / `city`,
        `business_description`, `invoicing_software`, `cash_based_accounting`,
        `resumption_status`, `dms_activated`.

        Tape `/companies/{cid}/context`. Les blocs de drapeaux de fonctionnalité de la
        réponse (`experiments`, `companyFeaturesAbility`, `userFeaturesAbility`) sont
        ÉCARTÉS : volumineux et sans valeur métier, ils noieraient la fiche.

        ⚠️ UN appel = UNE société = UNE session navigateur. Enrichir un portefeuille
        entier coûte donc un appel PAR dossier — à mettre en regard du volume.

        Args:
            company_id: id de la société (cf. `pennylaneged_companies`).
        """
        cid = int(company_id)
        res = await _call(_company_app(cid), f"/companies/{cid}/context")
        company = res.get("company")
        if not company:
            raise _err(f"Réponse `context` inattendue pour la société {cid} : "
                       f"{str(res)[:200]}", code=INTERNAL_ERROR)
        return {"company": company, "firm": res.get("firm"),
                "user_role": res.get("userRole")}

    # --- Arborescence / dossiers --------------------------------------------
    @mcp.tool()
    async def pennylaneged_tree(company_id: int,
                                item_type: str = "DmsFolder") -> dict:
        """Lit l'arborescence GED d'une société.

        Renvoie `{items: [{id, name, itemable_type, parent_id, folders_count, …}]}`
        (l'API renvoie un tableau, enveloppé sous `items`) — utilise les `id`/`parent_id`
        pour cibler un `parent_id` de création ou un item à supprimer.

        Args:
            company_id: id de la société (cf. `pennylaneged_companies`).
            item_type: type d'items listés — `DmsFolder` (dossiers, défaut) ou `DmsFile`.
        """
        cid = int(company_id)
        qs = urlencode({"item_type": item_type})
        return await _call(_company_app(cid), f"/companies/{cid}/dms/items/tree?{qs}")

    @mcp.tool()
    async def pennylaneged_create_folder(company_id: int, name: str,
                                         parent_id: Optional[int] = None) -> dict:
        """Crée un dossier dans la GED d'une société.

        Renvoie le `DmsFolder` créé (dont son `id`, à réutiliser comme `parent_id`).

        Args:
            name: nom du dossier (sous sa forme finale — pas de rename séparé ensuite).
            company_id: id de la société (cf. `pennylaneged_companies`).
            parent_id: id du dossier parent (None = racine de la GED).
        """
        cid = int(company_id)
        item: dict = {"name": name}
        if parent_id is not None:
            item["parent_id"] = int(parent_id)
        return await _call(_company_app(cid), f"/companies/{cid}/dms/items", "POST",
                           {"dms_items": [item]})

    # --- Upload (control plane ; octets PUT en LOCAL, jamais par Oto) --------
    @mcp.tool()
    async def pennylaneged_request_upload(
        company_id: int, filename: str, content_type: str,
        byte_size: int, checksum: str,
    ) -> dict:
        """Étape 1/2 d'un upload GED — demande une **URL S3 présignée** (control plane).

        ⚠️ Ne lit PAS le fichier (RGPD : les octets ne transitent JAMAIS par Oto).
        Calcule EN LOCAL, AVANT cet appel : `byte_size` (taille) et `checksum` (MD5 du
        fichier, encodé **base64**). Tape `direct_uploads` (ActiveStorage) et renvoie
        `{signed_id, put_url, put_headers}`.

        Puis, EN LOCAL (pas via MCP, pas par Oto) : **PUT** les octets du fichier
        directement sur `put_url` en passant `put_headers` (Content-Type, Content-MD5).
        Enfin appelle `pennylaneged_finalize(name, signed_id, parent_id)`.

        Args:
            filename: nom du fichier source.
            content_type: type MIME (ex. `application/pdf`).
            byte_size: taille du fichier en octets (calculée en local).
            checksum: MD5 du fichier encodé en base64 (calculé en local).
            company_id: id de la société (cf. `pennylaneged_companies`).
        """
        cid = int(company_id)
        res = await _call(
            _company_app(cid),
            f"/companies/{cid}/direct_uploads", "POST",
            {"blob": {"filename": filename, "content_type": content_type,
                      "byte_size": int(byte_size), "checksum": checksum}})
        direct = res.get("direct_upload") or {}
        signed_id = res.get("signed_id")
        put_url = direct.get("url")
        if not signed_id or not put_url:
            raise _err(f"Réponse direct_uploads inattendue : {str(res)[:200]}",
                       code=INTERNAL_ERROR)
        return {"signed_id": signed_id, "put_url": put_url,
                "put_headers": direct.get("headers") or {}}

    @mcp.tool()
    async def pennylaneged_finalize(company_id: int, name: str, signed_id: str,
                                    parent_id: Optional[int] = None) -> dict:
        """Étape 2/2 d'un upload GED — crée l'entrée DMS depuis un `signed_id` (control plane).

        À appeler APRÈS avoir PUT les octets en local sur l'`put_url` (cf.
        `pennylaneged_request_upload`). Le `name` est le nom **final** dans la GED
        (renommage standardisé = ce champ, pas d'appel rename séparé).

        Renvoie le `DmsFile` créé.

        Args:
            name: nom final du fichier dans la GED.
            signed_id: `signed_id` renvoyé par `pennylaneged_request_upload`.
            company_id: id de la société — DOIT être la même que celle du
                `pennylaneged_request_upload`.
            parent_id: id du dossier cible (None = racine).
        """
        cid = int(company_id)
        item: dict = {"name": name, "file": signed_id}
        if parent_id is not None:
            item["parent_id"] = int(parent_id)
        return await _call(_company_app(cid), f"/companies/{cid}/dms/items", "POST",
                           {"dms_items": [item]})

    @mcp.tool()
    async def pennylaneged_delete(company_id: int, item_id: int) -> dict:
        """Supprime un item (dossier ou fichier) de la GED d'une société.

        ⚠️ Suppression — n'appeler qu'après confirmation. Un dossier supprimé emporte
        son contenu.

        Args:
            item_id: id de l'item DMS à supprimer (cf. `pennylaneged_tree`).
            company_id: id de la société (cf. `pennylaneged_companies`).
        """
        cid, iid = int(company_id), int(item_id)
        # Une DELETE réussie répond 204 SANS corps : rendre le corps (`{}`) ne
        # dit rien à l'agent de l'acte qu'il vient de commettre, et c'est
        # précisément ce qui lui manquait dans #600. On confirme ce qu'on a
        # supprimé, avec le code qui l'atteste.
        res = await _call_raw(_company_app(cid),
                              f"/companies/{cid}/dms/items/{iid}", "DELETE")
        return {"deleted": True, "item_id": iid, "company_id": cid,
                "status": res.get("status")}
