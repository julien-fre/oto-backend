"""Handlers de la RÉCEPTION d'un upload signé (issue #105) — `/api/upload/{token}`.

**Pas de JWT** : le jeton scellé DANS l'URL fait foi (sub / org / cible, TTL court,
usage unique, `upload_tokens.py`). Trois voies pour le même jeton :

- `PUT`  → un agent qui a un shell y pousse le corps brut (`curl --data-binary`)
- `POST` → multipart `file`, le formulaire humain (fallback quand l'agent n'a pas
           de shell : claude.ai transmet le lien à la personne)
- `GET`  → la page HTML d'upload, autoportée (aucun asset externe) ; le jeton n'y
           est PAS consommé, seulement au POST du fichier

La matérialisation RÉAPPLIQUE l'autz de la cible (`check_target_access`), borne la
taille, puis consomme le jeton AVANT d'écrire — anti-rejeu et anti-double-écriture.
L'accusé est léger : jamais le corps reçu.

⚠️ **Le plafond de taille se tient PENDANT la lecture, pas après** (#562). Jusqu'au
23/09/2026, le PUT faisait `await request.body()` et le POST `await request.form()`
sans borne, puis comparait la longueur au plafond : un porteur du lien — un tiers, par
construction — faisait tenir en mémoire (ou spooler sur disque) un corps de taille
arbitraire, sur un serveur mono-boucle. Le corps se lit désormais en flux, et la
lecture s'arrête au premier octet au-delà du plafond ; un `Content-Length` déclaré
au-delà est refusé sans rien lire.

La table de routes (chemins, méthodes, ORDRE) reste assemblée dans
`api.routes.make_routes` ; ce module ne porte que les handlers.
"""
from __future__ import annotations

import html as _html

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from .. import db
from .base import _json, _json_error


# Le seul script de la page, en constante : la CSP l'autorise par son EMPREINTE
# (`entetes_securite.csp_upload`), calculée sur cette chaîne même — le modifier sans
# que la politique suive est impossible.
_SCRIPT_UPLOAD = (
    'const f=document.getElementById("f"),m=document.getElementById("msg");'
    'f.addEventListener("submit",async e=>{e.preventDefault();'
    'const fi=document.getElementById("file");'
    'if(!fi.files.length){return}'
    'const fd=new FormData();fd.append("file",fi.files[0]);'
    'm.textContent="Envoi…";m.className="msg";'
    'try{const r=await fetch(location.href,{method:"POST",body:fd});'
    'const j=await r.json().catch(()=>({}));'
    'if(r.ok){f.style.display="none";m.textContent="✓ Reçu. Tu peux fermer cette page."+(j.url?" URL publique : "+j.url:"");m.className="msg ok"}'
    'else{m.textContent="Échec : "+(j.error||r.status)+(j.detail?" — "+j.detail:"");m.className="msg err"}'
    '}catch(err){m.textContent="Erreur réseau.";m.className="msg err"}});'
)


def _upload_page_html(label: str | None) -> str:
    """Page d'upload autoportée d'un lien signé (#105, fallback humain). `label` None
    = lien invalide/expiré (message, sans formulaire). Le POST du fichier se fait vers
    la MÊME URL (multipart `file`), en fetch, avec accusé/erreur affiché."""
    if label is None:
        body = ('<h1>Lien d’upload invalide ou expiré</h1>'
                '<p>Demande à l’assistant de régénérer un lien.</p>')
    else:
        safe = _html.escape(label)
        body = (
            f'<h1>Déposer un fichier</h1><p class="tgt">Destination : <b>{safe}</b></p>'
            '<form id="f"><input type="file" name="file" id="file" required>'
            '<button type="submit">Envoyer</button></form>'
            '<p id="msg" class="msg"></p>'
            f'<script>{_SCRIPT_UPLOAD}</script>')
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Upload — oto</title><style>'
        ':root{color-scheme:light dark}'
        'body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:12vh auto;padding:0 1.2rem}'
        'h1{font-size:1.5rem;margin:0 0 .6rem}.tgt{color:#666}'
        'form{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin:1.4rem 0}'
        'button{padding:.55rem 1.1rem;border:0;border-radius:.5rem;background:#4f46e5;color:#fff;font:inherit;cursor:pointer}'
        'button:hover{background:#4338ca}.msg{min-height:1.5rem}.ok{color:#16a34a}.err{color:#dc2626}'
        '</style></head><body>' + body + '</body></html>')


# Ce que l'enveloppe multipart ajoute autour du fichier (frontières, en-têtes de
# partie, nom de fichier) : le formulaire humain n'envoie qu'une partie `file`. Le
# plafond du FICHIER reste `max_bytes()`, revérifié sur la partie extraite.
_MARGE_MULTIPART = 64 * 1024


class _CorpsTropGros(Exception):
    """Le corps dépasse le plafond : la lecture s'est arrêtée là."""


def _annonce_trop_gros(request: Request, plafond: int) -> bool:
    """Un `Content-Length` déclaré au-delà du plafond se refuse sans rien lire. Absent
    (envoi `chunked`), c'est la lecture en flux qui tient la borne."""
    annonce = request.headers.get("content-length", "")
    return annonce.isdigit() and int(annonce) > plafond


async def _lire_borne(request: Request, plafond: int) -> bytes:
    """Lit le corps en flux et s'ARRÊTE dès que `plafond` est dépassé (#562) — jamais
    un `request.body()` qui prendrait tout avant de compter."""
    recu = bytearray()
    async for morceau in request.stream():
        recu += morceau
        if len(recu) > plafond:
            raise _CorpsTropGros
    return bytes(recu)


def _rejouer(corps: bytes):
    """Un `receive` ASGI qui rend le corps déjà lu — pour confier au parseur multipart
    un corps borné plutôt que le flux brut de la requête."""
    async def _receive() -> dict:
        return {"type": "http.request", "body": corps, "more_body": False}
    return _receive


async def _do_signed_upload(request: Request, payload: dict, data: bytes,
                            ct: str | None) -> JSONResponse:
    """Cœur commun des réceptions d'upload signé : autz réappliquée → borne de
    taille → consommation à usage unique → matérialisation. DB sync → threadpool."""
    from .. import upload_tokens
    sub, target = payload["sub"], payload["target"]
    try:
        await run_in_threadpool(upload_tokens.check_target_access, sub, target)
    except upload_tokens.UploadError as e:
        return _json_error(request, e.status, e.code)
    if not data:
        return _json_error(request, 400, "empty_body")
    if len(data) > upload_tokens.max_bytes():
        return _json_error(request, 413, "content_too_large")
    # Consommer AVANT de matérialiser (anti-rejeu / double-écriture).
    if not await run_in_threadpool(db.consume_upload_token, payload["jti"]):
        return _json_error(request, 409, "token_already_used")
    try:
        result = await run_in_threadpool(upload_tokens.materialize, sub, target, data, ct)
    except upload_tokens.UploadError as e:
        return _json_error(request, e.status, e.code)
    return _json(request, result)


async def upload_receive(request: Request) -> JSONResponse:
    """Réception d'un upload signé (issue #105) : PAS de JWT — le jeton signé DANS
    l'URL fait foi (scellé sub/org/cible, TTL, usage unique). Deux voies :
    **PUT** = un agent avec shell y pousse le corps brut (`curl --data-binary`) ;
    **POST** multipart `file` = le formulaire humain (fallback claude.ai). On
    matérialise en RÉAPPLIQUANT l'autz de la cible. Accusé léger, jamais le body."""
    from .. import upload_tokens
    payload = upload_tokens.verify(request.path_params.get("token", ""))
    if payload is None:
        return _json_error(request, 401, "invalid_or_expired_token")
    plafond = upload_tokens.max_bytes()
    if request.method == "POST":
        plafond += _MARGE_MULTIPART
    if _annonce_trop_gros(request, plafond):
        return _json_error(request, 413, "content_too_large")
    try:
        corps = await _lire_borne(request, plafond)
    except _CorpsTropGros:
        return _json_error(request, 413, "content_too_large")
    if request.method == "POST":
        # Le parseur lit le corps DÉJÀ BORNÉ et n'accepte qu'une partie fichier.
        # Quelques champs texte restent tolérés (`max_fields`) : un formulaire sans
        # fichier, ou un `file` envoyé en texte, doit rendre `missing_file`, pas
        # `invalid_multipart` — le corps borné rend leur coût négligeable.
        try:
            form = await Request(request.scope, _rejouer(corps)).form(
                max_files=1, max_fields=16)
        except Exception:
            return _json_error(request, 400, "invalid_multipart")
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return _json_error(request, 400, "missing_file")
        data = await upload.read()
        ct = getattr(upload, "content_type", None)
    else:  # PUT — corps brut
        data = corps
        ct = request.headers.get("content-type")
    return await _do_signed_upload(request, payload, data, ct)


async def upload_form(request: Request) -> Response:
    """Page HTML d'upload d'un lien signé (GET) — **fallback humain** quand l'agent
    n'a pas de shell (claude.ai lui transmet ce lien). Le jeton n'est PAS consommé
    au GET (seulement au POST du fichier). Autoportée (aucun asset externe)."""
    from .. import upload_tokens
    payload = upload_tokens.verify(request.path_params.get("token", ""))
    from ..entetes_securite import csp_upload
    headers = {"Cache-Control": "private", "Referrer-Policy": "no-referrer",
               "Content-Security-Policy": csp_upload(_SCRIPT_UPLOAD)}
    if payload is None:
        return HTMLResponse(_upload_page_html(None), status_code=401, headers=headers)
    return HTMLResponse(
        _upload_page_html(upload_tokens.target_label(payload["target"])),
        headers=headers
    )
