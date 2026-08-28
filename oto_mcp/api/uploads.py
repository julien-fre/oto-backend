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
            '<script>'
            'const f=document.getElementById("f"),m=document.getElementById("msg");'
            'f.addEventListener("submit",async e=>{e.preventDefault();'
            'const fi=document.getElementById("file");'
            'if(!fi.files.length){return}'
            'const fd=new FormData();fd.append("file",fi.files[0]);'
            'm.textContent="Envoi…";m.className="msg";'
            'try{const r=await fetch(location.href,{method:"POST",body:fd});'
            'const j=await r.json().catch(()=>({}));'
            'if(r.ok){f.style.display="none";m.textContent="✓ Reçu. Tu peux fermer cette page.";m.className="msg ok"}'
            'else{m.textContent="Échec : "+(j.error||r.status)+(j.detail?" — "+j.detail:"");m.className="msg err"}'
            '}catch(err){m.textContent="Erreur réseau.";m.className="msg err"}});'
            '</script>')
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
    if request.method == "POST":
        try:
            form = await request.form()
        except Exception:
            return _json_error(request, 400, "invalid_multipart")
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return _json_error(request, 400, "missing_file")
        data = await upload.read()
        ct = getattr(upload, "content_type", None)
    else:  # PUT — corps brut
        data = await request.body()
        ct = request.headers.get("content-type")
    return await _do_signed_upload(request, payload, data, ct)


async def upload_form(request: Request) -> Response:
    """Page HTML d'upload d'un lien signé (GET) — **fallback humain** quand l'agent
    n'a pas de shell (claude.ai lui transmet ce lien). Le jeton n'est PAS consommé
    au GET (seulement au POST du fichier). Autoportée (aucun asset externe)."""
    from .. import upload_tokens
    payload = upload_tokens.verify(request.path_params.get("token", ""))
    if payload is None:
        return HTMLResponse(_upload_page_html(None), status_code=401)
    return HTMLResponse(_upload_page_html(upload_tokens.target_label(payload["target"])))
