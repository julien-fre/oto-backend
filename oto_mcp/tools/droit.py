"""Info légale FR — jurisprudence, codes consolidés, conventions collectives.

Référence légale française (par opposition à l'identité entreprise, namespace `fr`) :
le DROIT applicable, pas les données d'une société. Trois namespaces sous une même
carte de connecteur (`droit` au registre, `providers/droit.py`) :

- `juris_*` — jurisprudence (fonds DILA Cass/CE + CEDH/CJUE/Judilibre live) ;
- `loi_*`   — codes consolidés versionnés (LEGI, texte en vigueur à une date) ;
- `ccn_*`   — conventions collectives de branche (KALI/DILA).

Toutes ces sources sont servies par le **service FOD** (`fod_juris`/`fod_loi`/`fod_ccn`
→ HTTP, `FOD_BASE_URL`), pas par un client lib en direct. Extraites du connecteur
`sirene`/`fr` (elles y étaient crammées sous « INSEE SIRENE », publisher trompeur).

Connecteur open-data : pas de credential. Gaté par activation DB (ADR 0010).

**Surface consolidée (ADR 0047 §Amendement, appliqué au connecteur droit)** : un tool
par OBJET métier, le verbe en paramètre `op` — 9 → 5 tools. La consolidation se fait
**DANS chaque namespace, jamais entre eux** : `namespace_of` résout sur le préfixe
DÉCLARÉ au registre (`juris`/`loi`/`ccn`), donc un tool `droit_*` — ou un tool qui
mélangerait deux corpus — tomberait hors du gate de visibilité/activation. Chaque
corpus garde le même couple « objet + résolveur de périmètre » :

- `ccn_article` (op=search|get) + `ccn_conventions` (résout l'IDCC) ;
- `loi_article` (op=get|versions|search) + `loi_codes` (résout l'alias de code) ;
- `juris_decision` (op=search|get) — son périmètre (`fond`) est un enum fermé
  documenté dans le tool, donc sans résolveur à part.

Les deux résolveurs restent SEULS : ils rendent un CONTENEUR (une convention KALI,
un code LEGI), pas un article, et leur `query` est un substring de titre (ILIKE), pas
la requête FTS à stemming français des tools d'article — même mot, sémantique
différente. Tout est en LECTURE (open data) : aucune op n'écrit, ne supprime, ni ne
consomme de crédit ; le défaut de chaque tool est donc une lecture sans risque.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Argument obligatoire pour CET op — erreur actionnable qui NOMME l'op et
    l'argument, jamais un fallback (une citation de droit tirée d'un argument
    deviné est fausse en silence)."""
    if value is None:
        raise _bad(f"op='{op}' requiert {name}")
    return value


def register(mcp: FastMCP) -> None:
    # --- Conventions collectives (KALI, via service FOD) ---
    # Stock DILA complet (~290k articles, ~1,4k conteneurs) indexé FTS french +
    # filtre IDCC par france-opendata-service (#6). Complément de fr_accords_* :
    # ACCO = accords d'ENTREPRISE (qui a négocié quoi), KALI = le DROIT de la
    # BRANCHE (le texte applicable : minima, congés, primes, classifications).

    @mcp.tool()
    def ccn_article(
        op: Literal["search", "get"] = "search",
        query: Optional[str] = None,
        idcc: Optional[str] = None,
        en_vigueur: bool = True,
        limit: int = 20,
        sort: str = "relevance",
        kali_id: Optional[str] = None,
    ) -> dict:
        """An article of a French collective agreement (convention collective,
        KALI/DILA) — search the full text, or read one article in full.

        `op`:
        - **"search"** (default): search the full text of French collective
          agreements: articles, avenants, salary schedules, extension orders.
          Returns {count, articles: [{id, num, texte_titre, idcc, convention,
          extrait, permalien, lien_construit, …}]}. Fetch full text with op="get".
        - **"get"**: full consolidated text of a collective-agreement article
          (KALIARTI…), with its parent text (avenant/accord), convention (IDCC),
          a verifiable `permalien` and a best-effort Légifrance `lien_construit`.

        Args:
            op: search (default) | get.
            query: op="search" — full-text query (websearch syntax: phrases in
                quotes, OR, -). French stemming applied ("congés payés" matches
                "congé payé").
            idcc: op="search" — restrict to one branch agreement (4-digit IDCC,
                ex "1285" spectacle vivant public, "3090" spectacle vivant
                privé). Use ccn_conventions or fr_search(idcc=…) to resolve an
                IDCC.
            en_vigueur: op="search" — only in-force article versions (default
                True — salary schedules exist in many superseded versions).
            limit: op="search" — max results (default 20, max 50).
            sort: op="search" — "relevance" (FTS rank, default) | "recent" (date
                d'effet first — use for salary schedules where the latest avenant
                wins).
            kali_id: op="get" — DILA article id returned by op="search"
                (KALIARTI000…).
        """
        from .. import fod_ccn

        if op == "search":
            return fod_ccn.search(_need(query, "query", op), idcc=idcc,
                                  en_vigueur=en_vigueur, limit=limit, sort=sort)
        if op == "get":
            return fod_ccn.article(_need(kali_id, "kali_id", op))
        raise _bad("op doit être 'search' ou 'get'")

    @mcp.tool()
    def ccn_conventions(
        idcc: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> dict:
        """List French branch collective agreements (conventions collectives) by
        exact IDCC or title substring. Resolve "which convention is 3090?" or
        "conventions du spectacle" before searching articles with
        ccn_article(op="search").

        Args:
            idcc: Exact 4-digit IDCC.
            query: Title substring (ILIKE), ex "spectacle vivant". NOT the
                full-text query of ccn_article — this one matches the convention
                TITLE only.
            limit: Max results (default 20, max 100).
        """
        from .. import fod_ccn
        return fod_ccn.conventions(idcc=idcc, query=query, limit=limit)

    # --- Codes consolidés (LEGI, via service FOD) ---
    # 22 codes français AVEC versions historiques : l'article en vigueur à une
    # date donnée (une décision de 1992 cite l'art. 1128 CC → texte d'époque).

    @mcp.tool()
    def loi_article(
        op: Literal["get", "versions", "search"] = "get",
        code: Optional[str] = None,
        num: Optional[str] = None,
        date: Optional[str] = None,
        query: Optional[str] = None,
        en_vigueur: bool = True,
        limit: int = 20,
    ) -> dict:
        """An article of a French consolidated code (LEGI) — its text as in force
        at a given date, its version timeline, or finding it by concept.

        THE tool for citing law: exact text + verifiable Légifrance URL.

        `op`:
        - **"get"** (default): consolidated text of a French code article
          (`code` + `num`), as in force at a given date.
        - **"versions"**: full version timeline of a code article (`code` +
          `num`) — every rewriting with dates and états. Use to see WHEN an
          article changed before picking a `date` for op="get".
        - **"search"**: full-text search across French consolidated codes (LEGI).
          Find the article when you know the concept but not the number ("période
          d'essai CDD", "clause de non-concurrence").

        Args:
            op: get (default) | versions | search.
            code: op="get"/"versions" (required) — short alias: CT (travail), CC
                (civil), CP (pénal), CSS (sécurité sociale), CCOM, CGI, CPI…
                (loi_codes lists all 22) — or a raw LEGITEXT id. op="search"
                (optional) — restrict to one code (alias CT/CC/… or LEGITEXT).
            num: op="get"/"versions" — article number, ex "L1242-2", "1128",
                "R4228-20".
            date: op="get" — YYYY-MM-DD, version in force AT THAT DATE (default:
                today). Use the date of the document citing the article: a 1992
                ruling cites the 1992 wording, not today's.
            query: op="search" — full-text query (websearch syntax, french
                stemming).
            en_vigueur: op="search" — only versions in force today (default True).
            limit: op="search" — max results (default 20, max 50).
        """
        from .. import fod_loi

        if op == "get":
            return fod_loi.article(_need(code, "code", op),
                                   _need(num, "num", op), date)
        if op == "versions":
            return fod_loi.versions(_need(code, "code", op),
                                    _need(num, "num", op))
        if op == "search":
            return fod_loi.search(_need(query, "query", op), code=code,
                                  en_vigueur=en_vigueur, limit=limit)
        raise _bad("op doit être 'get', 'versions' ou 'search'")

    @mcp.tool()
    def loi_codes() -> dict:
        """List the 22 French consolidated codes covered (alias → LEGITEXT +
        label). Discovery helper for loi_article — both its `code` argument and
        its op="search" filter."""
        from .. import fod_loi
        return fod_loi.codes()

    # --- Jurisprudence (fonds DILA + CEDH/CJUE/live, via service FOD) ---
    # Cass (publiés + inédits), cours d'appel, CE/CAA/TA (bulk + live), Conseil
    # constit, CNIL, CEDH, CJUE, Judilibre. Tri pertinence × autorité
    # (constit/CEDH/CJUE > Cass/CE > CAA/CA > TA/TJ/CNIL).

    @mcp.tool()
    def juris_decision(
        op: Literal["search", "get"] = "search",
        query: Optional[str] = None,
        fond: Optional[str] = None,
        juridiction: Optional[str] = None,
        date_min: Optional[str] = None,
        date_max: Optional[str] = None,
        limit: int = 20,
        expand: bool = True,
        decision_id: Optional[str] = None,
    ) -> dict:
        """A French or European court decision (jurisprudence) — search the
        collections full text, or read one decision in full.

        `op`:
        - **"search"** (default): search French & European case law
          (jurisprudence) full text — how courts actually ruled. Unified
          collections, ranked by FTS relevance × court authority, with
          legal-thesaurus query expansion. Returns {count, decisions: [{id,
          titre, juridiction, date_dec, solution, extrait, source_url, …}]}.
          Full text via op="get".
        - **"get"**: full text of a French court decision, with metadata
          (juridiction, formation, solution, ECLI) and a verifiable Légifrance
          source_url.

        Args:
            op: search (default) | get.
            query: op="search" — full-text query (websearch syntax, french
                stemming), ex "requalification CDD d'usage intermittent".
            fond: op="search" — restrict to one collection — "cass" (Cour de
                cassation, published) | "inca" (cassation, unpublished) | "capp"
                (cours d'appel) | "jade" (administrative DILA: CE/CAA/TA) |
                "jade_live" (administrative, portail live) | "constit"
                (Conseil constitutionnel) | "cnil" | "cedh" (Cour EDH) |
                "cjue" (CJUE/Tribunal UE) | "judilibre" (Cass/CA/TJ live).
            juridiction: op="search" — court name filter (ILIKE), ex "cassation",
                "appel de Paris", "Conseil d'État".
            date_min / date_max: op="search" — decision date bounds (YYYY-MM-DD).
            limit: op="search" — max results (default 20, max 50).
            expand: op="search" — legal-thesaurus synonym expansion (default True
                — set False for strict literal matching).
            decision_id: op="get" — id returned by op="search" (JURITEXT…,
                CETATEXT…, CONSTEXT…, CNILTEXT…).
        """
        from .. import fod_juris

        if op == "search":
            return fod_juris.search(_need(query, "query", op), fond=fond,
                                    juridiction=juridiction, date_min=date_min,
                                    date_max=date_max, limit=limit, expand=expand)
        if op == "get":
            return fod_juris.decision(_need(decision_id, "decision_id", op))
        raise _bad("op doit être 'search' ou 'get'")
