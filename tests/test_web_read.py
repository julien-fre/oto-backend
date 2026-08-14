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
from mcp.shared.exceptions import McpError

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

            def scrape_page(self, url, include_markdown=True):
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
