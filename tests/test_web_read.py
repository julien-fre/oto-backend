"""web_read (#348) — les transitions d'escalade se PROUVENT, et la garde SSRF mord.

Ce qui se teste ici : chaque cran tenté est TRACÉ (`tentatives`), le chemin
gagnant est DIT (`chemin`), un cran indisponible est sauté-et-dit, le ③ exige
l'opt-in, le cap mord PENDANT la lecture — et le fetch ne peut JAMAIS
atteindre le réseau privé (IP directe, domaine qui résout privé, redirection
publique→privée : les trois voies du SSRF, chacune son test).
"""
from __future__ import annotations

import asyncio
import ipaddress

import pytest
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import web as W


class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        if a and callable(a[0]):
            return deco(a[0])
        return deco


def _web_read(monkeypatch, *, fetch=None, serper="absent", browserbase_page=None):
    """Le tool monté avec les crans scriptés. `serper` : 'absent' (pas de clé),
    dict (réponse), Exception (panne)."""
    reg = _Reg()
    if fetch is not None:
        monkeypatch.setattr(W, "_fetch_http", lambda url: fetch)
    if serper == "absent":
        monkeypatch.setattr(W.access, "resolve_api_key",
                            lambda p: (_ for _ in ()).throw(RuntimeError("no key")))
    else:
        monkeypatch.setattr(W.access, "resolve_api_key", lambda p: ("k", False))

        class _FauxSerper:
            def __init__(self, api_key):
                pass

            def scrape_page(self, url, include_markdown=True, timeout_s=None):
                if isinstance(serper, Exception):
                    raise serper
                return serper
        import oto.tools.serper as _s
        monkeypatch.setattr(_s, "SerperClient", _FauxSerper)
    if browserbase_page is not None:
        async def _faux(url, as_html=False):
            return browserbase_page
        monkeypatch.setattr(W.browserbase, "fetch_page_ephemeral", _faux)
        monkeypatch.setattr(W.browserbase, "is_configured", lambda: True)
    W.register(reg)
    fn = reg.tools["web_read"]
    return lambda **kw: asyncio.run(fn(**kw))


_HTML_OK = ("<html><head><title>ACME</title><style>x{}</style></head><body>"
            + "<p>Contact : contact@acme.fr — 01 02 03 04 05.</p>" * 20
            + "<script>alert(1)</script></body></html>")


# ── ① suffit : pas d'escalade ────────────────────────────────────────────────

def test_le_fetch_qui_lit_ne_paie_rien(monkeypatch):
    lire = _web_read(monkeypatch,
                     fetch={"ok": True, "status": 200, "html": _HTML_OK,
                            "final_url": "https://acme.fr", "verdict": "lu"})
    out = lire(url="https://acme.fr")
    assert out["chemin"] == "http"
    assert "contact@acme.fr" in out["content"]
    assert "alert(1)" not in out["content"], "script/style ne sont pas du texte"
    assert out["title"] == "ACME"
    assert out["cout"] == {"serper_credits": 0, "browser_session": False}
    assert out["tentatives"] == [{"cran": "http", "verdict": "lu"}]


# ── ① échoue → ② ────────────────────────────────────────────────────────────

def test_un_403_escalade_vers_serper(monkeypatch):
    lire = _web_read(monkeypatch,
                     fetch={"ok": False, "verdict": "HTTP 403", "status": 403},
                     serper={"markdown": "# ACME\n\nContact : x@acme.fr\n" * 20})
    out = lire(url="https://acme.fr")
    assert out["chemin"] == "serper"
    assert out["cout"]["serper_credits"] == 1
    assert out["tentatives"][0] == {"cran": "http", "verdict": "HTTP 403"}
    assert out["tentatives"][1]["cran"] == "serper"


def test_une_coquille_vide_escalade_aussi(monkeypatch):
    lire = _web_read(monkeypatch,
                     fetch={"ok": True, "status": 200, "final_url": "https://x.fr",
                            "html": "<html><body><div id=root></div></body></html>",
                            "verdict": "lu"},
                     serper={"markdown": "du vrai contenu rendu par le JS " * 20})
    out = lire(url="https://x.fr")
    assert out["chemin"] == "serper"
    assert "coquille vide" in out["tentatives"][0]["verdict"]


def test_sans_cle_serper_le_cran_est_saute_et_dit(monkeypatch):
    lire = _web_read(monkeypatch,
                     fetch={"ok": False, "verdict": "timeout"}, serper="absent")
    with pytest.raises(McpError) as e:
        lire(url="https://lent.fr")
    assert "browser=true" in str(e.value), "le refus dit le geste suivant"


# ── ② échoue → ③ opt-in STRICT ──────────────────────────────────────────────

def test_sans_opt_in_jamais_de_navigateur(monkeypatch):
    lire = _web_read(monkeypatch,
                     fetch={"ok": False, "verdict": "HTTP 403", "status": 403},
                     serper={"markdown": ""},
                     browserbase_page={"content": "JAMAIS SERVI"})
    with pytest.raises(McpError) as e:
        lire(url="https://acme.fr")
    assert "browser=true" in str(e.value)
    assert "JAMAIS SERVI" not in str(e.value)


def test_avec_opt_in_le_navigateur_lit_et_le_cout_est_dit(monkeypatch):
    lire = _web_read(monkeypatch,
                     fetch={"ok": False, "verdict": "HTTP 403", "status": 403},
                     serper={"markdown": ""},
                     browserbase_page={"content": "Contact : x@acme.fr",
                                       "title": "ACME", "final_url": "https://acme.fr/c",
                                       "status": 200})
    out = lire(url="https://acme.fr", browser=True)
    assert out["chemin"] == "browser"
    assert out["cout"]["browser_session"] is True
    assert [t["cran"] for t in out["tentatives"]] == ["http", "serper", "browser"]


# ── la garde SSRF : les trois voies, chacune son test ────────────────────────

def test_une_ip_privee_directe_est_refusee():
    with pytest.raises(McpError) as e:
        W.check_url_public("http://172.16.16.4:8000/api/sirene/info")
    assert "non publique" in str(e.value)


def test_un_domaine_qui_resout_prive_est_refuse(monkeypatch):
    monkeypatch.setattr(W, "_resolved_ips",
                        lambda h: [ipaddress.ip_address("10.0.0.7")])
    with pytest.raises(McpError) as e:
        W.check_url_public("https://piege.example.com/")
    assert "non publique" in str(e.value)


def test_une_redirection_vers_le_prive_est_refusee(monkeypatch):
    """Le 302 public→privé : la garde repasse à CHAQUE saut."""
    sauts = {"n": 0}

    class _Resp:
        def __init__(self, redirect):
            self.is_redirect = redirect
            self.is_permanent_redirect = False
            self.status_code = 302 if redirect else 200
            self.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}

        def close(self):
            pass

    def _faux_get(url, **kw):
        sauts["n"] += 1
        return _Resp(redirect=True)

    monkeypatch.setattr(W, "_resolved_ips",
                        lambda h: [ipaddress.ip_address("93.184.216.34")]
                        if "example.com" in h else
                        [ipaddress.ip_address("169.254.169.254")])
    monkeypatch.setattr(W.requests, "get", _faux_get)
    with pytest.raises(McpError) as e:
        W._fetch_http("https://example.com/page")
    assert "non publique" in str(e.value)
    assert sauts["n"] == 1, "le saut privé est refusé AVANT toute connexion"


def test_un_schema_non_http_est_refuse():
    with pytest.raises(McpError):
        W.check_url_public("file:///etc/passwd")


def test_un_host_mixte_public_prive_est_refuse(monkeypatch):
    """Fail-closed sur l'ENSEMBLE : public + privé dans la même résolution =
    le montage type du contournement."""
    monkeypatch.setattr(W, "_resolved_ips",
                        lambda h: [ipaddress.ip_address("93.184.216.34"),
                                   ipaddress.ip_address("192.168.1.10")])
    with pytest.raises(McpError):
        W.check_url_public("https://mixte.example.com/")


# ── le cap mord PENDANT la lecture ───────────────────────────────────────────

def test_le_cap_arrete_la_lecture_en_cours(monkeypatch):
    servis = {"n": 0}

    class _Resp:
        is_redirect = False
        is_permanent_redirect = False
        status_code = 200
        encoding = "utf-8"
        headers: dict = {}

        def iter_content(self, chunk_size, decode_unicode=False):
            while True:
                servis["n"] += 1
                yield b"x" * chunk_size

        def close(self):
            pass

    monkeypatch.setattr(W, "check_url_public", lambda u: None)
    monkeypatch.setattr(W.requests, "get", lambda *a, **k: _Resp())
    out = W._fetch_http("https://enorme.fr/")
    assert out["ok"] and len(out["html"]) <= W._MAX_FETCH_BYTES + 65536
    assert servis["n"] <= (W._MAX_FETCH_BYTES // 65536) + 1, \
        "la lecture s'arrête PENDANT — jamais accumuler-puis-tronquer"


# ── #491 : ce que la réponse DIT de l'hôte réellement servi ──────────────────
#
# Signal #491 (17/08, `wrong_result`) : « web_read rend par intermittence le
# contenu d'un AUTRE domaine que celui demandé (cross-contamination between
# concurrent calls) ». L'enquête du 28/08 a réfuté la contamination et daté ce
# qui reste :
#
#   1. le journal de prod du 17/08 porte 371 `web_read`, **tous du MÊME `sub`**
#      (aucune minute avec deux appelants distincts) — il n'y a jamais eu deux
#      utilisateurs à contaminer l'un par l'autre ;
#   2. aucun état n'est partagé entre deux appels : `requests.get` ouvre sa
#      propre connexion, `SerperClient` est instancié à chaque appel, une
#      session Browserbase est ouverte puis relâchée par appel ;
#   3. le cas cité (`www.calitex.fr` → `boutique.nydel-france.fr`) est une
#      **redirection 301 légitime**, revérifiée en direct le 28/08 — elle n'est
#      d'ailleurs visible qu'avec notre User-Agent, curl nu reçoit un 403 ;
#   4. le second cas cité (`solidarmonde.fr` → `artisansdumonde.org`) n'existe
#      pas au journal : le seul appel (`#357616`) a ÉCHOUÉ sur
#      `Serper scrape 500`, et aucun appel de la plateforme n'a jamais porté
#      `artisansdumonde` dans ses arguments.
#
# Reste un vrai défaut, et c'est celui qui a rendu l'accusation crédible : le
# tool AFFIRMAIT une `final_url` qu'il ne connaissait pas. Sur le cran ②, serper
# suit les redirections en silence et ne rend AUCUNE URL finale — `_sortie`
# recopiait alors l'URL DEMANDÉE. La parade du rapporteur (« comparer
# `final_url` à l'hôte demandé ») était donc structurellement aveugle sur ce
# cran, et l'écart d'hôte du cran ① restait à sa charge.
#
# D'où la règle que ces tests figent : **`final_url` est OBSERVÉE ou elle est
# `None`**, et un écart d'hôte est ANNONCÉ par le tool.

def test_le_cran_serper_n_invente_pas_l_url_finale(monkeypatch):
    """Serper ne dit pas où il a atterri ⟹ on ne le sait pas, et on le DIT."""
    lire = _web_read(monkeypatch,
                     fetch={"ok": False, "verdict": "HTTP 403", "status": 403},
                     serper={"markdown": "du contenu venu d'on ne sait où. " * 20})
    out = lire(url="https://acme.fr")
    assert out["chemin"] == "serper"
    assert out["final_url"] is None, \
        "recopier l'URL demandée serait AFFIRMER une chose inconnue (#491)"
    assert out["hote"] == {"demande": "acme.fr", "servi": None, "conforme": None}
    assert "avertissement" in out


def test_une_redirection_hors_domaine_est_annoncee(monkeypatch):
    """Le cas `calitex.fr` → `boutique.nydel-france.fr` : redirection légitime,
    mais l'appelant doit l'apprendre DU TOOL, pas d'une comparaison qu'il pense
    à faire lui-même (#491)."""
    lire = _web_read(monkeypatch,
                     fetch={"ok": True, "status": 200, "html": _HTML_OK,
                            "final_url": "https://boutique.nydel-france.fr/fr/",
                            "verdict": "lu"})
    out = lire(url="https://www.calitex.fr/")
    assert out["final_url"] == "https://boutique.nydel-france.fr/fr/"
    assert out["hote"] == {"demande": "www.calitex.fr",
                           "servi": "boutique.nydel-france.fr",
                           "conforme": False}
    assert "boutique.nydel-france.fr" in out["avertissement"]
    assert "calitex.fr" in out["avertissement"]


def test_un_saut_vers_www_ou_un_sous_domaine_n_est_pas_un_ecart(monkeypatch):
    """`acme.fr` → `www.acme.fr` (ou `shop.acme.fr`) est le MÊME site : crier au
    loup à chaque redirection canonique rendrait l'avertissement inaudible."""
    for servie in ("https://www.acme.fr/", "https://shop.acme.fr/x"):
        lire = _web_read(monkeypatch,
                         fetch={"ok": True, "status": 200, "html": _HTML_OK,
                                "final_url": servie, "verdict": "lu"})
        out = lire(url="https://acme.fr/")
        assert out["hote"]["conforme"] is True, servie
        assert "avertissement" not in out, servie


def test_deux_lectures_successives_ne_se_contaminent_pas(monkeypatch):
    """L'accusation de #491 prise au mot : deux lectures d'affilée rendent
    CHACUNE son domaine. Aucun état partagé entre appels — ce test le fige pour
    que l'absence de cache reste un choix, pas un hasard."""
    pages = {
        "https://un.fr": "<html><head><title>UN</title></head><body>"
                         + "<p>le contenu de UN.</p>" * 30 + "</body></html>",
        "https://deux.fr": "<html><head><title>DEUX</title></head><body>"
                           + "<p>le contenu de DEUX.</p>" * 30 + "</body></html>",
    }
    monkeypatch.setattr(W, "_fetch_http",
                        lambda url: {"ok": True, "status": 200, "html": pages[url],
                                     "final_url": url, "verdict": "lu"})
    lire = _web_read(monkeypatch, serper="absent")
    a = lire(url="https://un.fr")
    b = lire(url="https://deux.fr")
    c = lire(url="https://un.fr")
    assert (a["title"], b["title"], c["title"]) == ("UN", "DEUX", "UN")
    assert "DEUX" not in a["content"] and "UN." not in b["content"]
    assert [o["final_url"] for o in (a, b, c)] == \
        ["https://un.fr", "https://deux.fr", "https://un.fr"]


# ── #491 (2ᵉ défaut) : un appel qui pend gèle TOUT le monde ──────────────────
#
# Le même journal du 17/08 donne 11 lectures **au-delà de 30 s**, une à **57,5 s**.
# `web_read` est `async def` (il `await` le cran ③), donc FastMCP l'exécute DANS
# la boucle — mais ses crans ① et ② sont du I/O SYNCHRONE (`requests`,
# `SerperClient`). 57 s de boucle tenue, pour tous les utilisateurs à la fois :
# c'est le mode de gel n°1 de `docs/event-loop-perf.md`.
#
# Le garde-fou AST `test_no_blocking_async_handlers` ne peut PAS le voir : son
# critère est « ce handler `await`-t-il quelque chose dans son propre scope ? »,
# et celui-ci `await` bien — au cran ③, tout en bas. D'où un test qui n'analyse
# pas le source mais OBSERVE le thread, comme le garde des middlewares.

def test_les_crans_bloquants_ne_tournent_pas_dans_la_boucle(monkeypatch):
    """Le fetch et le scraper sont synchrones : ils doivent sortir de la boucle."""
    import threading

    vus: list = []

    def _faux_fetch(url):
        vus.append(threading.current_thread())
        return {"ok": True, "status": 200, "html": _HTML_OK,
                "final_url": url, "verdict": "lu"}

    monkeypatch.setattr(W, "_fetch_http", _faux_fetch)
    monkeypatch.setattr(W.access, "resolve_api_key",
                        lambda p: (_ for _ in ()).throw(RuntimeError("no key")))
    reg = _Reg()
    W.register(reg)
    fn = reg.tools["web_read"]

    boucle: list = []

    async def _scenario():
        boucle.append(threading.current_thread())
        return await fn(url="https://acme.fr")

    asyncio.run(_scenario())
    assert vus and boucle, "la garde serait inerte si le fetch n'était pas atteint"
    assert vus[0] is not boucle[0], \
        "le fetch synchrone doit sortir de la boucle (asyncio.to_thread)"


def test_la_lecture_a_un_budget_et_dit_ce_qu_elle_a_tente(monkeypatch):
    """`_TIMEOUT` borne CHAQUE socket, jamais la lecture entière : six sauts de
    redirection valent six fois le budget, et le streaming n'est borné par rien.
    Il faut un délai GLOBAL — et son verdict doit dire ce qu'il a tenté (#491)."""
    horloge = {"t": 0.0}
    monkeypatch.setattr(W, "_now", lambda: horloge["t"])

    class _FauxRedirect:
        is_redirect = True
        is_permanent_redirect = False
        status_code = 302
        headers = {"Location": "https://acme.fr/encore"}

        def close(self):
            pass

    def _get(url, **kw):
        horloge["t"] += 20.0        # chaque saut brûle 20 s
        return _FauxRedirect()

    monkeypatch.setattr(W.requests, "get", _get)
    monkeypatch.setattr(W, "check_url_public", lambda u: None)

    res = W._fetch_http("https://acme.fr/")
    assert res["ok"] is False
    assert "délai" in res["verdict"], f"verdict opaque : {res['verdict']!r}"
    assert "redirection" in res["verdict"], \
        "le verdict doit dire ce qu'il a tenté, pas seulement qu'il a renoncé"
    assert horloge["t"] <= W._DEADLINE_S + 20.0, \
        "le budget doit couper AVANT d'épuiser les 5 redirections"
