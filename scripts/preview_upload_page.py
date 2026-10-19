"""Aperçu local de la page d'upload signé (`api/uploads`), dans tous ses états.

    uv run python scripts/preview_upload_page.py      # → http://localhost:8790
    (anglais par défaut ; `?lang=fr` ou un navigateur en français pour la version FR)

Sert le VRAI gabarit (`_upload_page_html`) sous la VRAIE CSP (`csp_upload`) — un
script bloqué par son empreinte se voit donc ici comme en prod. Aucune base, aucun
jeton : le POST du formulaire rend l'accusé ou le refus choisi par l'adresse
(`/u/<scénario>`), sans rien écrire.
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from oto_mcp import upload_tokens
from oto_mcp.api.base import _locale_from_accept_language
from oto_mcp.api.uploads import _SCRIPT_UPLOAD, _upload_page_html
from oto_mcp.entetes_securite import csp_upload

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8790

# scénario → (cible scellée, statut du POST, corps du POST, délai en secondes)
SCENARIOS = {
    "fichier-ok": ({"kind": "project_file", "filename": "rapport.pdf"}, 200,
                   {"ok": True, "kind": "project_file", "bytes": 128170}, 1.2),
    "image-url": ({"kind": "image"}, 200,
                  {"ok": True, "kind": "image", "bytes": 48211,
                   "url": "https://media.example.com/images/u1/3f9a0c2e7b1d4e8f9a0c2e7b1d4e8f9a.png"}, 1.2),
    "tableau-ok": ({"kind": "datastore", "namespace": "leads", "format": "csv"}, 200,
                   {"ok": True, "kind": "datastore", "bytes": 9120, "inserted": 42}, 1.2),
    "page-ok": ({"kind": "doc", "op": "create", "title": "Compte rendu du 19/10"}, 200,
                {"ok": True, "kind": "doc", "bytes": 2048}, 1.2),
    "deja-utilise": ({"kind": "project_file", "filename": "contrat.pdf"}, 409,
                     {"error": "token_already_used"}, .6),
    "trop-gros": ({"kind": "project_file", "filename": "video.mp4"}, 413,
                  {"error": "content_too_large"}, .6),
    "image-format": ({"kind": "image"}, 400, {"error": "unsupported_type"}, .6),
    "colonne-inconnue": ({"kind": "datastore", "namespace": "leads", "format": "csv"}, 400,
                         {"error": "unknown_column", "detail": "colonne « telephone2 » non déclarée"}, .6),
    "interdit": ({"kind": "project_file", "filename": "budget.xlsx"}, 403,
                 {"error": "forbidden"}, .6),
    "reseau": ({"kind": "project_file", "filename": "photo.jpg"}, None, None, .6),
}

_INDEX = (
    '<!doctype html><meta charset="utf-8"><title>Aperçu — pages d’upload</title>'
    '<style>body{font:14px/1.6 -apple-system,system-ui,sans-serif;max-width:40rem;'
    'margin:8vh auto;padding:0 16px;color:#1c2024}a{color:#2d69d1}code{color:#60646c}</style>'
    '<h1>Pages d’upload : tous les états</h1><ul>'
    '<li><a href="/u/expire">Lien invalide ou expiré</a> <code>401</code></li>'
    + "".join(f'<li><a href="/u/{k}">{k}</a> <code>POST → {v[1] or "coupure réseau"}</code></li>'
              for k, v in SCENARIOS.items())
    + '</ul>')


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, ctype: str, body: str, headers: dict | None = None):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        nom = self.path.split("?")[0].removeprefix("/u/")
        entetes = {"Content-Security-Policy": csp_upload(_SCRIPT_UPLOAD)}
        # `?lang=fr` force la langue ; sinon celle du navigateur, l'anglais par défaut
        lang = ("fr" if "lang=fr" in self.path else
                _locale_from_accept_language(self.headers.get("accept-language")) or "en")
        if self.path == "/":
            self._send(200, "text/html; charset=utf-8", _INDEX)
        elif nom == "expire":
            self._send(401, "text/html; charset=utf-8", _upload_page_html(None, lang), entetes)
        elif nom in SCENARIOS:
            label = upload_tokens.target_label(SCENARIOS[nom][0], lang)
            self._send(200, "text/html; charset=utf-8", _upload_page_html(label, lang), entetes)
        else:
            self._send(404, "text/plain", "inconnu")

    def do_POST(self):
        nom = self.path.split("?")[0].removeprefix("/u/")
        _cible, statut, corps, delai = SCENARIOS.get(nom, (None, 404, {"error": "?"}, 0))
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        time.sleep(delai)
        if statut is None:  # connexion coupée sans réponse : le fetch échoue
            self.close_connection = True
            return
        self._send(statut, "application/json", json.dumps(corps))


if __name__ == "__main__":
    print(f"Aperçu : http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
