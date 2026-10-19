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
`api.routes.make_routes` ; ce module ne porte que les handlers — et leur CONTRAT
(`ContratDeRoute`, oto#106) : la route ne sera jamais une capacité (corps brut, pas de
JWT), mais un client REST doit pouvoir l'ouvrir en ne lisant que `/openapi.json`. Il y
voyait une souche « écrite à la main », sans corps ni accusé ni refus.
"""
from __future__ import annotations

import html as _html
from typing import Literal, Optional

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from .. import db, geste, upload_tokens
from ..capabilities._types import ContratDeRoute, DeclaredError
from .base import _json, _json_error, _locale_from_accept_language


# Le seul script de la page, en constante : la CSP l'autorise par son EMPREINTE
# (`entetes_securite.csp_upload`), calculée sur cette chaîne même — le modifier sans
# que la politique suive est impossible. Tout ce qu'il affiche passe par
# `textContent` : ni le nom du fichier choisi ni le `detail` d'un refus n'entrent
# jamais dans le HTML. Ses textes suivent le `lang` de la page : un seul script pour
# les deux langues, donc une seule empreinte.
_SCRIPT_UPLOAD = """
const f=document.getElementById("f"),fi=document.getElementById("file"),
z=document.getElementById("zone"),nm=document.getElementById("nom"),
b=document.getElementById("go"),m=document.getElementById("msg"),
ok=document.getElementById("ok"),u=document.getElementById("url");
const FR=document.documentElement.lang==="fr";
const T=FR?{retry:"Réessayer",sending:"Envoi…",copied:"Copié",
fail:c=>"L’envoi a échoué ("+c+").",net:"Erreur réseau : vérifie ta connexion puis réessaie.",
units:[" o"," Ko"," Mo"],comma:true,
msg:{token_already_used:"Ce lien a déjà servi. Demande à l’assistant d’en générer un nouveau.",
invalid_or_expired_token:"Ce lien a expiré. Demande à l’assistant d’en générer un nouveau.",
content_too_large:"Le fichier est trop volumineux pour ce lien.",
image_too_large:"L’image dépasse 2 Mo.",
unsupported_type:"Format non accepté : png, jpeg, gif ou webp seulement.",
empty_body:"Le fichier est vide.",missing_file:"Le fichier est vide.",
forbidden:"Ce compte n’a plus accès à la destination.",
unknown_project:"Le projet de destination n’existe plus.",
unknown_doc:"La page de destination n’existe plus.",
not_utf8:"Le fichier n’est pas un texte UTF-8."}}
:{retry:"Try again",sending:"Uploading…",copied:"Copied",
fail:c=>"Upload failed ("+c+").",net:"Network error: check your connection and try again.",
units:[" B"," KB"," MB"],comma:false,
msg:{token_already_used:"This link has already been used. Ask the assistant for a new one.",
invalid_or_expired_token:"This link has expired. Ask the assistant for a new one.",
content_too_large:"The file is too large for this link.",
image_too_large:"The image is larger than 2 MB.",
unsupported_type:"Unsupported format: png, jpeg, gif or webp only.",
empty_body:"The file is empty.",missing_file:"The file is empty.",
forbidden:"This account no longer has access to the destination.",
unknown_project:"The destination project no longer exists.",
unknown_doc:"The destination page no longer exists.",
not_utf8:"The file is not UTF-8 text."}};
const taille=n=>{if(n<1024){return n+T.units[0]}if(n<1048576){return (n/1024).toFixed(0)+T.units[1]}
const v=(n/1048576).toFixed(1);return (T.comma?v.replace(".",","):v)+T.units[2]};
const montre=()=>{const x=fi.files[0];z.classList.toggle("has",!!x);b.disabled=!x;
if(x){nm.textContent=x.name+" · "+taille(x.size)}m.hidden=true};
fi.addEventListener("change",montre);
["dragenter","dragover"].forEach(t=>z.addEventListener(t,e=>{e.preventDefault();z.classList.add("drag")}));
["dragleave","drop"].forEach(t=>z.addEventListener(t,()=>z.classList.remove("drag")));
z.addEventListener("drop",e=>{e.preventDefault();if(e.dataTransfer.files.length){fi.files=e.dataTransfer.files;montre()}});
const err=t=>{m.textContent=t;m.hidden=false;b.disabled=false;b.classList.remove("busy");b.textContent=T.retry};
f.addEventListener("submit",async e=>{e.preventDefault();if(!fi.files.length){return}
const fd=new FormData();fd.append("file",fi.files[0]);
m.hidden=true;b.disabled=true;b.classList.add("busy");b.textContent=T.sending;
try{const r=await fetch(location.href,{method:"POST",body:fd});
const j=await r.json().catch(()=>({}));
if(r.ok){f.hidden=true;ok.hidden=false;
if(j.url){u.hidden=false;u.querySelector("code").textContent=j.url;
u.querySelector("button").addEventListener("click",ev=>{navigator.clipboard.writeText(j.url);ev.target.textContent=T.copied})}}
else{const c=j.error||r.status;err((T.msg[c]||T.fail(c))+(j.detail?" "+j.detail:""))}
}catch(x){err(T.net)}});
"""

# Les jetons du design du tableau de bord (gris Radix Slate, le bleu d'Oto, rayons
# 6/8/12 px, corps 13 px). Pas de police distante : la page est autoportée, la CSP n'ouvre
# aucune origine — Inter s'il est installé, sinon la police du système.
_STYLE_UPLOAD = (
    ':root{color-scheme:light;--border:#d9d9e0;--muted:#60646c;--accent:#2d69d1;'
    '--g1:#fcfcfd;--g2:#f9f9fb;--g3:#f0f0f3;--g4:#e8e8ec;--g9:#8b8d98;--g12:#1c2024;'
    '--red:#ce2c31;--green:#218358}'
    '*{box-sizing:border-box}[hidden]{display:none!important}'
    'body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;'
    'justify-content:center;padding:24px 16px;background:var(--g2);color:var(--g12);'
    'font:13px/18px Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;'
    'letter-spacing:-.002em;-webkit-font-smoothing:antialiased}'
    '.card{width:100%;max-width:440px;background:#fff;border:1px solid var(--border);'
    'border-radius:12px;padding:24px;box-shadow:0 1px 2px #1c20240a,0 8px 24px #1c20240a}'
    '.ic{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;'
    'justify-content:center;background:var(--g3);color:var(--muted);margin-bottom:14px}'
    '.ic.ok{background:#eaf6ef;color:var(--green)}.ic.ko{background:#fdeeee;color:var(--red)}'
    'h1{font-size:20px;line-height:28px;font-weight:500;letter-spacing:-.019em;margin:0 0 4px}'
    'p{margin:0;color:var(--muted)}p b{color:var(--g12);font-weight:500}'
    '.zone{position:relative;margin-top:20px;display:flex;flex-direction:column;align-items:center;gap:6px;'
    'padding:28px 16px;border:1px dashed #cdced6;border-radius:8px;background:var(--g1);'
    'text-align:center;cursor:pointer;transition:border-color .15s,background .15s}'
    '.zone:hover,.zone.drag{border-color:var(--accent);background:#f4f7fd}'
    '.zone:focus-within{outline:2px solid var(--accent);outline-offset:2px}'
    '.zone input{position:absolute;inset:0;opacity:0;cursor:pointer}'
    '.zone svg{color:var(--g9)}.zone .t{font-weight:500}.zone .s{color:var(--muted);font-size:12px}'
    '.zone .s u{color:var(--accent);text-decoration:none}'
    '.zone.has{border-style:solid;background:#fff}.zone.has svg{color:var(--accent)}'
    '.nom{display:none;font-weight:500;word-break:break-all}.zone.has .nom{display:block}'
    '.zone.has .t,.zone.has .s{display:none}'
    'button{font:inherit;font-weight:500;cursor:pointer;border-radius:6px}'
    '#go{width:100%;margin-top:12px;height:34px;border:0;background:var(--accent);color:#fff}'
    '#go:hover:not(:disabled){background:#2559b5}'
    '#go:disabled{background:var(--g4);color:var(--g9);cursor:default}'
    '#go.busy{background:var(--accent);color:#fff;opacity:.8}'
    '#go:focus-visible{outline:2px solid var(--accent);outline-offset:2px}'
    '.msg{margin-top:12px;padding:9px 12px;border-radius:6px;background:#fdeeee;'
    'border:1px solid #f6cfd0;color:var(--red)}'
    '.url{margin-top:16px;display:flex;gap:8px;align-items:center;padding:6px 6px 6px 10px;'
    'border:1px solid var(--border);border-radius:6px;background:var(--g1)}'
    '.url code{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'
    'font:12px ui-monospace,SFMono-Regular,Menlo,monospace}'
    '.url button{border:1px solid var(--border);background:#fff;padding:3px 10px;color:var(--g12)}'
    '.url button:hover{background:var(--g3)}'
    '.foot{margin-top:16px;font-size:12px;color:var(--g9)}'
)

_ICONE_DEPOT = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/>'
    '<path d="M12 3v12"/></svg>')
_ICONE_OK = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M20 6 9 17l-5-5"/></svg>')
_ICONE_EXPIRE = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>')


# Les textes de la page, par langue. L'anglais par défaut ; le français quand le
# navigateur le demande (`Accept-Language`, même règle que le tableau de bord).
_TEXTES_UPLOAD = {
    "en": {
        "title": "Upload a file", "dest": "Destination",
        "drop": "Drop your file here", "or": "or", "browse": "browse your computer",
        "file": "File", "send": "Upload",
        "foot": "Single-use link: it expires after the upload.",
        "ok": "File received", "ok_sub": "You can close this page and go back to the conversation.",
        "copy": "Copy",
        "expired": "Invalid or expired link",
        "expired_sub": "This upload link is no longer valid: it works once and expires after "
                       "a few minutes. Ask the assistant for a new one.",
    },
    "fr": {
        "title": "Déposer un fichier", "dest": "Destination",
        "drop": "Glisse ton fichier ici", "or": "ou", "browse": "parcours ton ordinateur",
        "file": "Fichier", "send": "Envoyer",
        "foot": "Lien à usage unique : il expire après le dépôt.",
        "ok": "Fichier reçu", "ok_sub": "Tu peux fermer cette page et revenir à la conversation.",
        "copy": "Copier",
        "expired": "Lien invalide ou expiré",
        "expired_sub": "Ce lien de dépôt n’est plus valable : il ne sert qu’une fois et expire "
                       "au bout de quelques minutes. Demande à l’assistant d’en générer un nouveau.",
    },
}


def _upload_page_html(label: str | None, lang: str = "en") -> str:
    """Page d'upload autoportée d'un lien signé (#105, fallback humain). `label` None
    = lien invalide/expiré (message, sans formulaire). Le POST du fichier se fait vers
    la MÊME URL (multipart `file`), en fetch, avec accusé/erreur affiché. `lang` :
    `en` (défaut) ou `fr`."""
    lang = "fr" if lang == "fr" else "en"
    t = _TEXTES_UPLOAD[lang]
    sep = "\u00a0:" if lang == "fr" else ":"
    if label is None:
        titre = t["expired"]
        body = (
            f'<main class="card"><div class="ic ko">{_ICONE_EXPIRE}</div>'
            f'<h1>{t["expired"]}</h1><p>{t["expired_sub"]}</p></main>')
    else:
        titre = t["title"]
        safe = _html.escape(label)
        body = (
            f'<main class="card"><form id="f"><div class="ic">{_ICONE_DEPOT}</div>'
            f'<h1>{t["title"]}</h1>'
            f'<p>{t["dest"]}{sep} <b>{safe}</b></p>'
            '<label class="zone" id="zone">'
            f'{_ICONE_DEPOT}<span class="t">{t["drop"]}</span>'
            f'<span class="s">{t["or"]} <u>{t["browse"]}</u></span>'
            '<span class="nom" id="nom"></span>'
            f'<input type="file" name="file" id="file" required aria-label="{t["file"]}"></label>'
            f'<button type="submit" id="go" disabled>{t["send"]}</button>'
            '<p id="msg" class="msg" role="alert" hidden></p>'
            f'<p class="foot">{t["foot"]}</p></form>'
            f'<div id="ok" hidden><div class="ic ok">{_ICONE_OK}</div>'
            f'<h1>{t["ok"]}</h1><p>{t["ok_sub"]}</p>'
            '<div class="url" id="url" hidden><code></code>'
            f'<button type="button">{t["copy"]}</button></div></div></main>'
            f'<script>{_SCRIPT_UPLOAD}</script>')
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{titre}</title><style>{_STYLE_UPLOAD}</style></head>'
        f'<body>{body}</body></html>')


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
    from .. import garde_identite, upload_tokens
    sub, target = payload["sub"], payload["target"]
    # Le lien porte le sub SCELLÉ au mint, et sert sans aucun jeton : c'est un porteur
    # d'identité à part entière, 15 minutes durant. Il passe donc par la garde d'identité
    # comme les autres portes — compte en pause, tenant désactivé (#1165).
    if (coupe := await run_in_threadpool(garde_identite.refus, sub)):
        return _json_error(request, 403, *coupe)
    try:
        await run_in_threadpool(upload_tokens.check_target_access, sub, target)
    except upload_tokens.UploadError as e:
        return _json_error(request, e.status, e.code, e.message or None)
    if not data:
        return _json_error(request, 400, "empty_body")
    if len(data) > upload_tokens.max_bytes():
        return _json_error(request, 413, "content_too_large")
    # Consommer AVANT de matérialiser (anti-rejeu / double-écriture).
    if not await run_in_threadpool(db.consume_upload_token, payload["jti"]):
        return _json_error(request, 409, "token_already_used")
    # Le GESTE (oto#273) : `upload`. Il ne devient `import` que si le jeton a scellé
    # `donnees_d_origine=true` au mint — c'est le store qui requalifie, comme pour toute
    # face : un fichier n'est pas une origine parce qu'il est un fichier. L'acteur est
    # le compte scellé au jeton (celui qui l'a frappé, pas le porteur anonyme du lien),
    # et le geste est le jeton lui-même : son `jti`, à usage unique, désigne ce dépôt.
    try:
        with geste.portee(geste.UPLOAD, sub, payload["jti"]):
            result = await run_in_threadpool(upload_tokens.materialize, sub, target,
                                             data, ct)
    except upload_tokens.UploadError as e:
        # La phrase part avec le code : « ligne NDJSON 4 312 invalide » dit où reprendre,
        # `bad_ndjson` seul ne le dit pas (oto#106).
        return _json_error(request, e.status, e.code, e.message or None)
    return _json(request, result)


class CorpsRefuse(Exception):
    """Un corps refusé à la lecture : `status` et `code` de la réponse d'erreur."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


async def lire_corps_borne(request: Request, plafond: int) -> bytes:
    """Le corps brut, refusé au-delà de `plafond` : sur l'annonce, sinon en cours de
    lecture (#562)."""
    if _annonce_trop_gros(request, plafond):
        raise CorpsRefuse(413, "content_too_large")
    try:
        return await _lire_borne(request, plafond)
    except _CorpsTropGros:
        raise CorpsRefuse(413, "content_too_large") from None


async def lire_multipart_borne(request: Request, plafond: int):
    """Un formulaire multipart dont la partie `file` pèse au plus `plafond` : rend
    `(form, upload, octets)`. Le parseur lit le corps DÉJÀ BORNÉ (plafond + l'enveloppe)
    et n'accepte qu'une partie fichier. Quelques champs texte restent tolérés
    (`max_fields`) : un formulaire sans fichier, ou un `file` envoyé en texte, doit
    rendre `missing_file`, pas `invalid_multipart` — le corps borné rend leur coût
    négligeable. Lève `CorpsRefuse`."""
    corps = await lire_corps_borne(request, plafond + _MARGE_MULTIPART)
    try:
        form = await Request(request.scope, _rejouer(corps)).form(max_files=1, max_fields=16)
    except Exception:  # noqa: SILENT — tout échec du parseur EST le refus rendu
        raise CorpsRefuse(400, "invalid_multipart") from None
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise CorpsRefuse(400, "missing_file")
    data = await upload.read()
    if len(data) > plafond:
        raise CorpsRefuse(413, "content_too_large")
    return form, upload, data


async def upload_receive(request: Request) -> JSONResponse:
    """Réception d'un upload signé (issue #105) : PAS de JWT — le jeton signé DANS
    l'URL fait foi (scellé sub/org/cible, TTL, usage unique). Deux voies :
    **PUT** = un agent avec shell y pousse le corps brut (`curl --data-binary`) ;
    **POST** multipart `file` = le formulaire humain (fallback claude.ai). On
    matérialise en RÉAPPLIQUANT l'autz de la cible. Accusé léger, jamais le body."""
    payload = upload_tokens.verify(request.path_params.get("token", ""))
    if payload is None:
        return _json_error(request, 401, "invalid_or_expired_token")
    plafond = upload_tokens.max_bytes()
    try:
        if request.method == "POST":
            _form, upload, data = await lire_multipart_borne(request, plafond)
            ct = getattr(upload, "content_type", None)
        else:  # PUT — corps brut
            data = await lire_corps_borne(request, plafond)
            ct = request.headers.get("content-type")
    except CorpsRefuse as e:
        return _json_error(request, e.status, e.code)
    return await _do_signed_upload(request, payload, data, ct)


async def upload_form(request: Request) -> Response:
    """Page HTML d'upload d'un lien signé (GET) — **fallback humain** quand l'agent
    n'a pas de shell (claude.ai lui transmet ce lien). Le jeton n'est PAS consommé
    au GET (seulement au POST du fichier). Autoportée (aucun asset externe)."""
    payload = upload_tokens.verify(request.path_params.get("token", ""))
    from ..entetes_securite import csp_upload
    headers = {"Cache-Control": "private", "Referrer-Policy": "no-referrer",
               "Content-Security-Policy": csp_upload(_SCRIPT_UPLOAD)}
    lang = _locale_from_accept_language(request.headers.get("accept-language")) or "en"
    headers["Vary"] = "Accept-Language"
    if payload is None:
        return HTMLResponse(_upload_page_html(None, lang), status_code=401, headers=headers)
    return HTMLResponse(
        _upload_page_html(upload_tokens.target_label(payload["target"], lang), lang),
        headers=headers
    )


# ── Le contrat publié (oto#106) ───────────────────────────────────────────────────

class AccuseDeDepot(BaseModel):
    """L'accusé LÉGER d'un dépôt : jamais le corps reçu, jamais un identifiant interne
    (oto#86). Les champs présents dépendent de la cible scellée au jeton (`kind`)."""
    ok: bool
    kind: Literal["doc", "project_file", "datastore", "image"]
    bytes: int = Field(description="taille reçue, en octets")
    op: Optional[Literal["create", "update"]] = Field(None, description="`doc`")
    chars: Optional[int] = Field(None, description="`doc` : caractères de la page")
    filename: Optional[str] = Field(None, description="`project_file`")
    url: Optional[str] = Field(None, description="`image` : l'adresse publique, permanente")
    datastore: Optional[str] = Field(None, description="`datastore` : le tableau chargé")
    inserted: Optional[int] = Field(None, description="`datastore` : lignes créées")
    updated: Optional[int] = Field(None, description="`datastore` : lignes mises à jour "
                                                     "(même clé d'upsert)")
    count: Optional[int] = Field(None, description="`datastore` : lignes du fichier")
    fusions: Optional[list[dict]] = Field(None, description=(
        "`datastore` : les lignes du fichier qui ont FUSIONNÉ sur la clé (oto#141) — "
        "`{rang, dans_rang, id, cle}`, `dans_rang` = la ligne du fichier qui a posé la "
        "ligne visée, `null` pour une ligne déjà en base"))
    entetes_traduits: Optional[dict[str, str]] = Field(None, description=(
        "`datastore` CSV : en-têtes renommés (un point ne peut pas figurer dans un nom "
        "de colonne) — ancien → nouveau"))
    # Les relevés d'écriture (`hors_schema`, `hors_options`…) du
    # datastore s'ajoutent tels quels : ce sont ceux de toute écriture de lignes.
    model_config = {"extra": "allow"}


_REFUS_DE_LA_RECEPTION = (
    DeclaredError(401, "invalid_or_expired_token",
                  "le jeton de l'adresse est illisible, falsifié ou expiré (TTL court) : "
                  "en frapper un autre (`POST /api/me/upload-url`)"),
    DeclaredError(409, "token_already_used",
                  "le jeton est à usage unique et a déjà servi : en frapper un autre"),
    DeclaredError(413, "content_too_large",
                  "le corps dépasse `max_bytes` (rendu à la frappe) — un `Content-Length` "
                  "annoncé au-delà est refusé sans rien lire"),
    DeclaredError(400, "empty_body", "le corps est vide"),
    DeclaredError(400, "invalid_multipart", "`POST` : le corps n'est pas un multipart lisible"),
    DeclaredError(400, "missing_file",
                  "`POST` : pas de partie `file` ; ou un fichier vide (image, fichier "
                  "de projet)"),
    DeclaredError(403, "forbidden",
                  "la cible est RE-jugée à la réception : le compte qui a frappé le jeton "
                  "n'y a plus l'écriture"),
    DeclaredError(404, "unknown_doc", "`doc` : la page visée n'existe plus"),
    DeclaredError(404, "unknown_project", "le projet visé n'existe plus"),
    DeclaredError(400, "bad_parent",
                  "`doc` (`op=create`) : la page parente n'existe plus, ou n'est pas "
                  "dans le projet de la cible"),
    DeclaredError(400, "not_utf8", "`doc` ou `datastore` : le corps n'est pas de l'UTF-8"),
    DeclaredError(400, "bad_ndjson",
                  "`datastore` NDJSON : une ligne n'est pas un objet JSON — `detail` "
                  "dit laquelle"),
    DeclaredError(400, "empty_dataset",
                  "`datastore` : aucune ligne (un CSV exige son en-tête)"),
    DeclaredError(400, "entete_en_collision",
                  "`datastore` CSV : un en-tête traduit tombe sur une colonne déjà "
                  "déclarée"),
    DeclaredError(400, "bad_row",
                  "`datastore` : une ligne est refusée par le stockage — `detail` dit "
                  "laquelle et pourquoi"),
    DeclaredError(409, "business_key_exists",
                  "`datastore`, jeton frappé sans `upsert` (oto#141, à partir de sa "
                  "date) : des lignes du fichier partagent une valeur de clé, ou — "
                  "frappé sans `key`, le fichier AJOUTE — en portent une que le "
                  "tableau a déjà. `detail` les nomme, rien n'est écrit ; frapper un "
                  "jeton avec `key` (désigner) ou `upsert=true` (fusionner)"),
    DeclaredError(400, "unknown_column",
                  "`datastore` (oto#124, à partir de sa date) : le fichier pose une "
                  "colonne que le schéma du tableau ne déclare pas. `detail` la nomme, "
                  "rien n'est écrit ; la déclarer (`PATCH …/schema`) puis renvoyer"),
    DeclaredError(413, "image_too_large", "`image` : au-delà de 2 Mo"),
    DeclaredError(400, "unsupported_type",
                  "`image` : ni png, ni jpeg, ni gif, ni webp (jugé sur les octets)"),
)

_FICHIER_BRUT = {"type": "string", "format": "binary"}

upload_receive.contrat = ContratDeRoute(
    description=(
        "Dépose le contenu d'un lien signé frappé par `POST /api/me/upload-url` (ou "
        "l'outil agent `oto_upload_url`). Aucun en-tête `Authorization` : le jeton de "
        "l'adresse fait foi — il scelle le compte, l'org et la cible, vit 15 minutes et "
        "ne sert qu'une fois. `PUT` : le corps BRUT, sous le `Content-Type` rendu à la "
        "frappe — pour un tableau, NDJSON (`application/x-ndjson`, un objet JSON par "
        "ligne) ou CSV (`text/csv`, en-tête requis), upserté en lot sur la clé choisie à "
        "la frappe (sinon la clé métier du tableau) ; pour une page, du Markdown ; pour "
        "un fichier de projet ou une image, les octets. `POST` : le même dépôt en "
        "multipart, une seule partie `file` (le formulaire de la page `GET`). La cible "
        "est RE-jugée à la réception, et la réponse est un accusé léger, jamais le "
        "corps."),
    Output=AccuseDeDepot,
    errors=_REFUS_DE_LA_RECEPTION,
    corps={
        "PUT": {"application/x-ndjson": {"type": "string"},
                "text/csv": {"type": "string"},
                "text/markdown": {"type": "string"},
                "application/octet-stream": _FICHIER_BRUT},
        "POST": {"multipart/form-data": {
            "type": "object", "required": ["file"],
            "properties": {"file": _FICHIER_BRUT}}},
    },
    authentifiee=False,
)

upload_form.contrat = ContratDeRoute(
    description=(
        "La page HTML de dépôt d'un lien signé — la voie d'un humain, quand l'agent n'a "
        "pas de shell. Aucun en-tête `Authorization`, et le jeton n'est PAS consommé : "
        "il ne l'est qu'au dépôt (`POST` du formulaire). Lien invalide ou expiré : la "
        "même page, en 401, sans formulaire."),
    media="text/html",
    authentifiee=False,
)
