"""Garde-fou : aucun secret ne doit partir en QUERY STRING (#284).

Un `requests.*(..., params={... "client_secret": ...})` place le secret dans
l'URL. L'URL se retrouve alors dans le message de toute exception `requests`
(remonté à l'agent, donc au transcript de conversation), dans les breadcrumbs
Sentry, dans les logs de proxy et dans les access logs du serveur distant.

Vécu le 2026-07-28 : le refresh OAuth Zoho échouait et renvoyait à l'agent
`400 Client Error: for url: …/oauth/v2/token?client_id=…&client_secret=…&
refresh_token=…` — les trois secrets en clair. Les credentials concernés ont dû
être considérés comme compromis.

La forme correcte est `data=` (corps form-encodé), qui est aussi celle que
prescrit RFC 6749 §2.3.1 pour les credentials client.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"

# Noms de clés considérés comme secrets (minuscules, comparaison exacte).
SECRET_KEYS = {
    "client_secret", "refresh_token", "access_token", "api_key", "apikey",
    "password", "secret", "token", "authorization", "private_key",
}


def _params_kwarg(call: ast.Call) -> ast.keyword | None:
    for kw in call.keywords:
        if kw.arg == "params":
            return kw
    return None


def _secret_keys_in(node: ast.AST) -> list[str]:
    """Clés secrètes littérales d'un dict passé en `params=`."""
    if not isinstance(node, ast.Dict):
        return []
    found = []
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            if k.value.strip().lower() in SECRET_KEYS:
                found.append(k.value)
    return found


def _is_http_call(call: ast.Call) -> bool:
    """`requests.post(...)`, `requests.request(...)`, `session.get(...)`…"""
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr in {"get", "post", "put", "patch", "delete", "request", "head"}
    return False


def _offenders() -> list[str]:
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_http_call(node):
                continue
            kw = _params_kwarg(node)
            if kw is None:
                continue
            for key in _secret_keys_in(kw.value):
                out.append(f"{path.relative_to(ROOT.parent)}:{node.lineno} "
                           f"→ params={{…'{key}'…}}")
    return out


def test_no_secret_passed_as_query_parameter():
    bad = _offenders()
    assert not bad, (
        "Secret(s) passé(s) en query string — utiliser `data=` (corps) :\n  "
        + "\n  ".join(bad))


@pytest.mark.parametrize("src,expected", [
    ('requests.post(u, params={"client_secret": s})', 1),
    ('requests.post(u, data={"client_secret": s})', 0),
    ('requests.get(u, params={"page": 1})', 0),
    ('foo.params({"client_secret": s})', 0),
])
def test_detector_itself(tmp_path, src, expected, monkeypatch):
    """La sonde attrape bien le motif fautif et n'a pas de faux positif."""
    f = tmp_path / "oto_mcp" / "m.py"
    f.parent.mkdir(parents=True)
    f.write_text(src, encoding="utf-8")
    monkeypatch.setattr(pathlib.Path, "rglob", lambda self, pat: [f])
    monkeypatch.setitem(globals(), "ROOT", f.parent)
    assert len(_offenders()) == expected
