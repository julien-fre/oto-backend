"""Dispatch `op=` du tool `infosec_domain` (ADR 0047 §Amendement, appliqué au
connecteur infosec le 2026-08-11 : 6 tools → 1).

Ce module n'avait AUCUN test de surface : ses 6 tools étaient 6 fonctions imbriquées
dans `register()`, inatteignables autrement qu'en montant le serveur. Une consolidation
par `op=` déplace précisément le risque là — une op mal câblée part chercher la mauvaise
facette (les 6 réponses sont des dicts plausibles), et rien ne casse au boot. D'où, pour
chaque op : l'implémentation réellement appelée et les arguments qu'elle reçoit, le refus
explicite d'une op inconnue, et le refus d'un domaine invalide AVANT tout appel réseau.

Aucun test ne sort sur le réseau : les 6 facettes sont stubbées pour le dispatch, et le
transport (`_doh`, `httpx.AsyncClient`) l'est pour les tests de comportement.
"""
import asyncio

import pytest
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import infosec as I

_FACETS = ("_whois", "_dns", "_email_security", "_subdomains", "_tls", "_headers",
           "_blocklist", "_deliverability")
_REAL_SLEEP = asyncio.sleep   # capturé AVANT tout monkeypatch d'`asyncio.sleep`


def _tool():
    from fastmcp import FastMCP

    m = FastMCP("t")
    I.register(m)
    return asyncio.run(m.get_tool("oto_domain_check")).fn


def _call(**kwargs):
    return asyncio.run(_tool()(**kwargs))


@pytest.fixture
def facets(monkeypatch):
    """Remplace les 6 implémentations par des mouchards — on teste le ROUTAGE, pas le réseau."""
    seen: dict[str, tuple] = {}

    def _stub(name):
        async def fn(*args, **kw):
            seen[name] = (args, kw)
            return {"facet": name}
        return fn

    for name in _FACETS:
        monkeypatch.setattr(I, name, _stub(name))
    return seen


# --- routage : une op = une facette -------------------------------------------

@pytest.mark.parametrize("op,facet", [
    ("whois", "_whois"),
    ("dns", "_dns"),
    ("email_security", "_email_security"),
    ("subdomains", "_subdomains"),
    ("tls", "_tls"),
    ("headers", "_headers"),
    ("blocklist", "_blocklist"),
    ("deliverability", "_deliverability"),
])
def test_each_op_routes_to_its_own_facet(facets, op, facet):
    assert _call(op=op, domain="example.com") == {"facet": facet}
    assert set(facets) == {facet}, "une op ne doit déclencher QUE sa facette"


def test_every_declared_op_is_dispatched(facets):
    """`_OPS` sert de gate ET de message d'erreur : une op qui y serait déclarée sans
    branche de dispatch retomberait sur le `raise` final (jamais sur un autre appel)."""
    for op in I._OPS:
        facets.clear()
        out = _call(op=op, domain="example.com")
        assert len(facets) == 1, f"op={op} n'appelle aucune facette"
        assert out == {"facet": next(iter(facets))}


# --- arguments : ce que la facette reçoit --------------------------------------

def test_domain_is_normalised_before_the_facet(facets):
    """URL, e-mail, port, chemin, casse : la facette ne voit qu'un hostname nu."""
    for raw in ("https://Example.com/path?q=1", "contact@example.com",
                "example.com:8443", "EXAMPLE.COM."):
        facets.clear()
        _call(op="dns", domain=raw)
        assert facets["_dns"][0] == ("example.com",), raw


def test_subdomains_receives_limit_and_tls_receives_port(facets):
    _call(op="subdomains", domain="example.com", limit=7)
    assert facets["_subdomains"][0] == ("example.com", 7)
    facets.clear()
    _call(op="tls", domain="example.com", port=8443)
    assert facets["_tls"][0] == ("example.com", 8443)


def test_optional_args_keep_their_historical_defaults(facets):
    """Défauts d'avant la fusion : `limit=100` (subdomains), `port=443` (tls)."""
    _call(op="subdomains", domain="example.com")
    assert facets["_subdomains"][0] == ("example.com", 100)
    facets.clear()
    _call(op="tls", domain="example.com")
    assert facets["_tls"][0] == ("example.com", 443)


# --- refus ---------------------------------------------------------------------

def test_unknown_op_is_refused_with_the_allowed_list(facets):
    """Une op inconnue doit lever en NOMMANT les ops valides — jamais retomber
    silencieusement sur une facette (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être") as e:
        _call(op="portscan", domain="example.com")
    for op in I._OPS:
        assert op in str(e.value)
    assert facets == {}


def test_op_is_mandatory():
    """Pas de défaut : demander « l'infosec d'un domaine » sans dire QUOI est une
    erreur d'appel, pas une invitation à choisir à la place de l'appelant."""
    with pytest.raises(TypeError):
        _call(domain="example.com")


@pytest.mark.parametrize("op", I._OPS)
@pytest.mark.parametrize("bad", ["", "   ", "@", "://"])
def test_invalid_domain_is_refused_before_any_network_call(facets, op, bad):
    assert _call(op=op, domain=bad) == {"error": "domaine invalide"}
    assert facets == {}, "aucune facette ne doit être appelée sur un domaine invalide"


# --- comportement des facettes (transport stubbé, zéro réseau) -----------------

class _Resp:
    def __init__(self, payload=None, *, status=200, headers=None, url=""):
        self._payload, self.status_code = payload, status
        self.headers, self.url = headers or {}, url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _fake_httpx(monkeypatch, resp):
    """Remplace `httpx.AsyncClient` par un client qui rend toujours `resp`."""
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return resp

    monkeypatch.setattr(I.httpx, "AsyncClient", _Client)


def _fake_doh(monkeypatch, table: dict):
    async def fn(name, rtype):
        return list(table.get((name, rtype), []))
    monkeypatch.setattr(I, "_doh", fn)


def test_dns_reports_records_and_stack_hints(monkeypatch):
    _fake_doh(monkeypatch, {
        ("example.com", "A"): ["1.2.3.4"],
        ("example.com", "MX"): ["10 aspmx.l.GOOGLE.com."],
        ("example.com", "TXT"): ["v=spf1 include:_spf.google.com ~all",
                                 "hubspot-domain-verification=xyz"],
    })
    out = _call(op="dns", domain="https://example.com/")
    assert out["A"] == ["1.2.3.4"]
    assert out["stack_hints"] == ["Google Workspace", "HubSpot"]


def test_email_security_grades_the_posture(monkeypatch):
    _fake_doh(monkeypatch, {
        ("example.com", "TXT"): ["v=spf1 -all"],
        ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=reject; rua=mailto:x@example.com"],
        ("_mta-sts.example.com", "TXT"): ["v=STSv1; id=1"],
        ("google._domainkey.example.com", "TXT"): ["v=DKIM1; k=rsa; p=MIIB"],
    })
    out = _call(op="email_security", domain="example.com")
    assert out["dmarc_policy"] == "reject"
    assert out["mta_sts"] is True
    assert out["dkim_selectors_found"] == ["google"]
    assert out["posture"] == "forte"
    assert "sélecteurs courants" in out["note"], "l'avertissement DKIM reste dans la réponse"


def test_email_security_weak_when_dmarc_is_none(monkeypatch):
    _fake_doh(monkeypatch, {("_dmarc.example.com", "TXT"): ["v=DMARC1; p=none"]})
    out = _call(op="email_security", domain="example.com")
    assert (out["spf"], out["dmarc_policy"], out["posture"]) == (None, "none", "faible")


def test_subdomains_counts_all_but_returns_at_most_limit(monkeypatch):
    _fake_httpx(monkeypatch, _Resp([
        {"name_value": "api.example.com\n*.vpn.example.com"},
        {"name_value": "example.com"},              # l'apex lui-même est exclu
        {"name_value": "mail.other.com"},           # hors domaine
        {"name_value": "staging.example.com"},
    ]))
    out = _call(op="subdomains", domain="example.com", limit=2)
    assert out["count"] == 3, "`count` porte le total trouvé…"
    assert out["subdomains"] == ["api.example.com", "staging.example.com"], "…la liste est tronquée"


def test_subdomains_reports_crtsh_outage_instead_of_raising(monkeypatch):
    monkeypatch.setattr(I.asyncio, "sleep", lambda *_: _REAL_SLEEP(0))  # 3 backoffs, sans attendre
    _fake_httpx(monkeypatch, _Resp(None, status=502))
    out = _call(op="subdomains", domain="example.com")
    assert out["subdomains"] == [] and "crt.sh indisponible" in out["error"]


def test_headers_scores_the_security_headers(monkeypatch):
    _fake_httpx(monkeypatch, _Resp(headers={"Strict-Transport-Security": "max-age=1",
                                            "Content-Security-Policy": "default-src 'self'",
                                            "Server": "nginx"},
                                   url="https://example.com/"))
    out = _call(op="headers", domain="example.com")
    assert out["security_headers"]["hsts"] is True
    assert out["security_headers"]["csp"] is True
    assert out["security_headers"]["x_frame_options"] is False
    assert out["security_headers_score"] == "2/6"
    assert out["server"] == "nginx"


def test_whois_flags_an_unregistered_domain(monkeypatch):
    _fake_httpx(monkeypatch, _Resp(None, status=404))
    out = _call(op="whois", domain="nope.example")
    assert out["found"] is False and "RDAP" in out["note"]


def test_whois_extracts_registrar_and_dates(monkeypatch):
    _fake_httpx(monkeypatch, _Resp({
        "ldhName": "example.com",
        "status": ["client transfer prohibited"],
        "events": [{"eventAction": "registration", "eventDate": "1995-08-14"},
                   {"eventAction": "expiration", "eventDate": "2027-08-13"}],
        "entities": [{"roles": ["registrar"],
                      "vcardArray": ["vcard", [["fn", {}, "text", "RESERVED-IANA"]]]}],
        "nameservers": [{"ldhName": "a.iana-servers.net"}],
    }))
    out = _call(op="whois", domain="example.com")
    assert out["registrar"] == "RESERVED-IANA"
    assert (out["created"], out["expires"]) == ("1995-08-14", "2027-08-13")
    assert out["nameservers"] == ["a.iana-servers.net"]


def test_tls_never_raises_on_an_unreachable_host(monkeypatch):
    """Une erreur réseau doit revenir en champ `error`, pas en exception MCP."""
    def boom(*a, **k):
        raise OSError("unreachable")
    monkeypatch.setattr(I.socket, "create_connection", boom)
    out = _call(op="tls", domain="example.com", port=8443)
    assert out["host"] == "example.com" and out["port"] == 8443
    assert "OSError" in out["error"]


# --- délivrabilité : listes noires et bilan --------------------------------------

def _fake_doh_raw(monkeypatch, table: dict):
    """`_doh_raw` stubbé : clé (nom, type) → (statut, réponses) ; absent = NXDOMAIN.
    `_doh` en dérive, comme en vrai."""
    async def raw(name, rtype):
        return table.get((name, rtype), (3, []))

    async def doh(name, rtype):
        return list((await raw(name, rtype))[1])
    monkeypatch.setattr(I, "_doh_raw", raw)
    monkeypatch.setattr(I, "_doh", doh)


def test_blocklist_reports_listed_clean_and_error_distinctly(monkeypatch):
    """Inscrit, absent, injoignable : trois verdicts — une liste injoignable n'est
    jamais comptée propre."""
    _fake_doh_raw(monkeypatch, {
        ("4.3.2.1.bl.spamcop.net", "A"): (0, ["127.0.0.2"]),
        ("4.3.2.1.psbl.surriel.com", "A"): (2, []),
        ("4.3.2.1.in-addr.arpa", "PTR"): (0, ["mail.example.com."]),
        ("mail.example.com", "A"): (0, ["1.2.3.4"]),
    })
    out = _call(op="blocklist", ip="1.2.3.4")
    by_list = {r["list"]: r["status"] for r in out["ips"][0]["results"]}
    assert by_list["SpamCop"] == "listed"
    assert by_list["PSBL"] == "error"
    assert by_list["Barracuda"] == "clean"
    assert out["listed"] is True and out["listed_on"] == ["SpamCop"]
    assert out["errors"] == 1
    assert (out["ips"][0]["ptr"], out["ips"][0]["fcrdns"]) == ("mail.example.com", True)


def test_blocklist_never_reads_a_refusal_code_as_clean(monkeypatch):
    """Un code de refus (`127.255.255.254`, Spamhaus via résolveur public) n'est ni
    inscrit ni propre."""
    _fake_doh_raw(monkeypatch, {("4.3.2.1.bl.mailspike.net", "A"): (0, ["127.255.255.254"])})
    out = _call(op="blocklist", ip="1.2.3.4")
    row = next(r for r in out["ips"][0]["results"] if r["list"] == "Mailspike")
    assert row["status"] == "error" and "unexpected_answer" in row["detail"]


def test_blocklist_names_the_lists_it_cannot_check(monkeypatch):
    _fake_doh_raw(monkeypatch, {})
    out = _call(op="blocklist", domain="example.com")
    assert {"Spamhaus (ZEN / DBL)", "SURBL", "URIBL"} <= {x["name"] for x in out["not_checked"]}


def test_blocklist_takes_sending_ips_from_spf_and_says_when_there_are_none(monkeypatch):
    _fake_doh_raw(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 ip4:1.2.3.4 ip4:10.0.0.0/24 ~all"]),
    })
    out = _call(op="blocklist", domain="example.com")
    assert [x["ip"] for x in out["ips"]] == ["1.2.3.4"]
    assert any("10.0.0.0/24" in n for n in out["notes"])
    assert len(out["notes"]) == 1, "les plages se résument en UNE note"
    _fake_doh_raw(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 include:_spf.google.com ~all"]),
    })
    out = _call(op="blocklist", domain="example.com")
    assert out["ips"] == [] and any("infrastructure partagée" in n for n in out["notes"])


def test_blocklist_checks_the_domain_on_domain_lists(monkeypatch):
    _fake_doh_raw(monkeypatch, {("example.com.dbl.nordspam.com", "A"): (0, ["127.0.0.2"])})
    out = _call(op="blocklist", domain="example.com")
    assert out["domain_results"][0]["status"] == "listed"
    assert out["listed_on"] == ["NordSpam DBL"]


def test_blocklist_accepts_an_ip_without_domain_but_refuses_ipv6_and_garbage():
    with pytest.raises(McpError, match="IPv4"):
        _call(op="blocklist", ip="2001:db8::1")
    with pytest.raises(McpError, match="ip invalide"):
        _call(op="blocklist", ip="not-an-ip")
    assert _call(op="dns", ip="1.2.3.4") == {"error": "domaine invalide"}


def test_spf_lookup_count_follows_includes(monkeypatch):
    """La limite RFC 7208 des 10 requêtes se compte en suivant les include."""
    many = " ".join(f"include:s{i}.example.net" for i in range(6))
    table = {("example.com", "TXT"): (0, [f"v=spf1 {many} mx a ~all"])}
    for i in range(6):
        table[(f"s{i}.example.net", "TXT")] = (0, ["v=spf1 include:deep.example.net ~all"])
    table[("deep.example.net", "TXT")] = (0, ["v=spf1 ip4:1.2.3.4 -all"])
    _fake_doh_raw(monkeypatch, table)
    out = _call(op="email_security", domain="example.com")
    # 6 include + mx + a au premier niveau, puis 1 include dans chacun des 6 —
    # `deep` n'est parcouru qu'une fois mais chaque include qui le vise coûte.
    assert out["spf_dns_lookups"] == 6 + 2 + 6
    assert out["spf_lookup_limit_exceeded"] is True
    assert out["spf_all"] == "~all"


def test_email_security_tests_the_requested_dkim_selector(monkeypatch):
    _fake_doh_raw(monkeypatch, {("acme2026._domainkey.example.com", "TXT"): (0, ["v=DKIM1; p=MIIB"])})
    out = _call(op="email_security", domain="example.com", dkim_selector="acme2026")
    assert out["dkim_selectors_found"] == ["acme2026"]


def test_deliverability_scores_and_ranks_recommendations(monkeypatch):
    _fake_doh_raw(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 include:_spf.google.com ~all"]),
        ("_dmarc.example.com", "TXT"): (0, ["v=DMARC1; p=none"]),
        ("example.com", "MX"): (0, ["1 smtp.google.com."]),
        ("google._domainkey.example.com", "TXT"): (0, ["v=DKIM1; p=MIIB"]),
    })
    out = _call(op="deliverability", domain="example.com")
    assert 0 < out["score"] < 100
    prios = [r["priority"] for r in out["recommendations"]]
    assert prios == sorted(prios, key={"high": 0, "medium": 1, "low": 2}.get)
    issues = " ".join(r["issue"] for r in out["recommendations"])
    assert "p=none" in issues
    assert out["blocklists"]["not_checked"], "les listes non vérifiées restent visibles"


def test_deliverability_flags_a_missing_dmarc_as_high(monkeypatch):
    _fake_doh_raw(monkeypatch, {("example.com", "TXT"): (0, ["v=spf1 -all"])})
    out = _call(op="deliverability", domain="example.com")
    top = out["recommendations"][0]
    assert top["priority"] == "high" and "DMARC" in top["issue"]


def test_deliverability_does_not_ask_for_all_when_spf_redirects(monkeypatch):
    """`redirect=` délègue tout le SPF, `all` compris : pas de recommandation fausse."""
    _fake_doh_raw(monkeypatch, {("example.com", "TXT"): (0, ["v=spf1 redirect=_spf.example.net"])})
    out = _call(op="deliverability", domain="example.com")
    assert not any("qualifier" in r["issue"] for r in out["recommendations"])


def test_blocklist_does_not_check_ips_of_included_providers(monkeypatch):
    """Les `ip4:` d'un include sont l'infrastructure du FOURNISSEUR : ni vérifiées
    comme IP du domaine, ni sources de recommandations — seulement signalées."""
    _fake_doh_raw(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 ip4:1.2.3.4 include:spf.provider.net ~all"]),
        ("spf.provider.net", "TXT"): (0, ["v=spf1 ip4:5.6.7.8 -all"]),
    })
    out = _call(op="blocklist", domain="example.com")
    assert [x["ip"] for x in out["ips"]] == ["1.2.3.4"]
    assert any("fournisseurs inclus" in n for n in out["notes"])


def test_spf_redirect_keeps_the_ips_as_the_domain_s_own(monkeypatch):
    _fake_doh_raw(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 redirect=_spf.example.com"]),
        ("_spf.example.com", "TXT"): (0, ["v=spf1 ip4:1.2.3.4 -all"]),
    })
    out = _call(op="blocklist", domain="example.com")
    assert [x["ip"] for x in out["ips"]] == ["1.2.3.4"]


def test_deliverability_carries_its_coverage_next_to_the_score(monkeypatch):
    _fake_doh_raw(monkeypatch, {("example.com", "TXT"): (0, ["v=spf1 include:_spf.google.com ~all"])})
    out = _call(op="deliverability", domain="example.com")
    cov = out["coverage"]
    assert cov["own_ips_checked"] == 0
    assert cov["blocklists_checked"] == len(I._DOMAIN_LISTS)
    assert "SURBL" in cov["not_checked"]


def test_a_repeated_include_is_a_duplicate_not_a_loop(monkeypatch):
    _fake_doh_raw(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 include:_spf.p.net include:_spf.p.net ~all"]),
        ("_spf.p.net", "TXT"): (0, ["v=spf1 ip4:1.2.3.4 -all"]),
    })
    out = _call(op="email_security", domain="example.com")
    assert out["spf_duplicate_includes"] == ["_spf.p.net"]
    assert out["spf_errors"] == []
    assert out["spf_dns_lookups"] == 2, "chaque include répété coûte encore sa requête"


def test_a_real_include_loop_is_still_reported(monkeypatch):
    _fake_doh_raw(monkeypatch, {
        ("example.com", "TXT"): (0, ["v=spf1 include:a.example.net ~all"]),
        ("a.example.net", "TXT"): (0, ["v=spf1 include:example.com -all"]),
    })
    out = _call(op="email_security", domain="example.com")
    assert any("boucle" in e for e in out["spf_errors"])
