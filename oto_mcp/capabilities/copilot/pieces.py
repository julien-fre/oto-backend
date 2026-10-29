"""Files the person attaches to a message of the in-app agent: images, PDFs and small
text files (`docs/copilot.md` §Pièces jointes).

**Deposited first, named after.** `POST /api/copilot/attachments` (multipart, `api/copilot.py`)
stores the bytes under `copilot-attachments/` (durable, private) and a row in
`copilot_attachments` that binds it to its `sub` AND its org; the turn then names it by
id (`TurnInput.attachments`). Nobody else can use an id: another account, or the same
one in another org, reads "unknown", exactly like an id that does not exist.

**The bytes decide the type**, never the declared one alone: an image or a PDF must
carry its format's signature, and the signature must be what the file claims to be (a
PNG renamed `.pdf` is refused, 415). A text file has no signature: it must be declared
as one of the text types (or carry their extension), decode as UTF-8 and carry no
binary signature. Each type has its own size bound (`TYPES`): the one the model accepts
for an image (5 MB), 20 MB for a PDF, 512 KB for text, inlined into the request.

**The thread stores a marker, never the bytes**: one small `oto_attachment` block per
file, before the person's text. Each model request resolves them on a COPY of the
history (`resoudre`): in the CURRENT turn (the latest message carrying the person's
words, and so every step of that turn) a marker becomes a label and the real content —
an image or a PDF by a fresh presigned URL that the provider fetches (valid 10 minutes,
`_URL_DUREE_S`), a text file inlined as a text document. In an EARLIER turn it becomes
one line saying a file was shown, so a file is not billed again on every later turn. A marker never reaches the API unresolved.

The URLs are presigned ONCE per leg (`memo`, the leg's own, never stored): a fresh URL
on each step would change the message, and the prompt cache that rereads the history
from one step to the next would miss at every step.
"""
from __future__ import annotations

import logging
import os
import time
import unicodedata
import uuid
from typing import Callable, Optional

from ... import media_store
from ...db import copilot as store

logger = logging.getLogger(__name__)

PREFIXE = "copilot-attachments"
MARQUE = "oto_attachment"
MAX_PAR_TOUR = 5

IMAGES = ("image/png", "image/jpeg", "image/gif", "image/webp")
PDF = "application/pdf"
TEXTES = ("text/plain", "text/markdown", "text/csv", "application/json")

MAX_IMAGE = 5 * 1024 * 1024
MAX_PDF = 20 * 1024 * 1024
MAX_TEXTE = 512 * 1024

#: What this backend accepts: media type → max bytes. The ONE source the upload
#: enforces and `GET /api/copilot/attachments` publishes.
TYPES: dict[str, int] = {
    **{t: MAX_IMAGE for t in IMAGES},
    PDF: MAX_PDF,
    **{t: MAX_TEXTE for t in TEXTES},
}
#: The largest file the upload reads before judging it.
MAX_DEPOT = max(TYPES.values())
#: What one person may deposit in all, over a sliding window of `FENETRE_QUOTA_H` hours,
#: whatever the org: the files are stored (30 days) and each one can be sent to the
#: model, so an unbounded upload is storage and spend paid by the instance. Summed on
#: the rows (`copilot_attachments.size`), not in memory: blue, green and preprod share
#: the base. Two uploads at the same instant may pass it by one file — a bound, not an
#: accounting.
QUOTA_DEPOT = 200 * 1024 * 1024
FENETRE_QUOTA_H = 24

_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".pdf": PDF,
               ".txt": "text/plain", ".text": "text/plain", ".log": "text/plain",
               ".md": "text/markdown", ".markdown": "text/markdown",
               ".csv": "text/csv", ".json": "application/json"}
_ALIAS = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg",
          "text/x-markdown": "text/markdown"}
_NOM_MAX = 200
#: How long a presigned URL sent to the model's provider stays valid. Shorter than the
#: instance's default (`media_store.presign_expiry()`, 1 h): the URL leaves our perimeter
#: (the provider fetches it, and may log the request that carries it), and whoever holds
#: it reads the file without any identity. The provider fetches it within the request;
#: ten minutes is ample for that and bounds a leaked URL's use.
_URL_DUREE_S = 600
#: A presigned URL is reused within a leg for this long, then signed again: well before
#: it expires (`_URL_DUREE_S`), so a URL sent is always valid for 5 more minutes.
_URL_FRAICHE_S = 300


class Refus(Exception):
    """A file refused at the upload: `status`, `code`, and a sentence for the person."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def types_acceptes() -> dict:
    """What the upload accepts, as the front reads it (`GET /api/copilot/attachments`)."""
    return {"types": dict(TYPES), "max_per_turn": MAX_PAR_TOUR}


def nom_affiche(filename: Optional[str]) -> str:
    """The original name, kept readable (accents included) but made harmless: last path
    segment, no control or format characters, bounded, extension kept."""
    brut = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    nom = "".join(" " if unicodedata.category(c)[0] in "CZ" and c != " " else c
                  for c in brut)
    nom = " ".join(nom.split()).strip(" .")
    if len(nom) > _NOM_MAX:
        racine, ext = os.path.splitext(nom)
        ext = ext[:16]
        nom = racine[:_NOM_MAX - len(ext)] + ext
    return nom or "file"


def _annonce(declare: Optional[str], nom: str) -> Optional[str]:
    """What the file CLAIMS to be: its declared type when it is one we accept, else
    what its extension says, else None."""
    base = (declare or "").split(";")[0].strip().lower()
    base = _ALIAS.get(base, base)
    if base in TYPES:
        return base
    return _EXTENSIONS.get(os.path.splitext(nom)[1].lower())


def _texte_utf8(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def juger(data: bytes, declare: Optional[str], nom: str) -> str:
    """The media type the file is stored and shown as, judged on its bytes. Raises
    `Refus` (415 `attachment_type`, 413 `attachment_too_large`, 400 `missing_file`)."""
    if not data:
        raise Refus(400, "missing_file", "The file is empty.")
    annonce = _annonce(declare, nom)
    # The format's signature, read on the bytes (`media_store`, the same reading as
    # the project files').
    signe = media_store._signature(data, "")
    if annonce in IMAGES or annonce == PDF:
        if signe != annonce:
            raise Refus(415, "attachment_type",
                        f"This file is not a valid {annonce.split('/')[1].upper()}: its "
                        "content does not match its type.")
        media_type = annonce
    elif annonce in TEXTES:
        # An image or a PDF declared as text is refused; a short ASCII prefix that
        # happens to look like an audio signature (`ID3`, `OggS`…) is not a reason:
        # a real binary fails the UTF-8 test anyway.
        if signe in IMAGES or signe == PDF or not _texte_utf8(data):
            raise Refus(415, "attachment_type",
                        "A text file must be UTF-8 text (plain text, Markdown, CSV or "
                        "JSON).")
        media_type = annonce
    elif signe in IMAGES or signe == PDF:
        # No usable claim (no type, no known extension): the signature alone decides.
        media_type = signe
    else:
        raise Refus(415, "attachment_type",
                    "Attach an image (PNG, JPEG, GIF, WebP), a PDF, or a text file "
                    "(plain text, Markdown, CSV, JSON).")
    if len(data) > TYPES[media_type]:
        raise Refus(413, "attachment_too_large",
                    f"This file is too large: {TYPES[media_type] // 1024} KB at most "
                    f"for {media_type}.")
    return media_type


def deposer(sub: str, org_id: Optional[int], data: bytes, filename: Optional[str],
            declare: Optional[str]) -> dict:
    """Judges, stores and records one file, within the person's quota (429
    `attachment_quota_exceeded`, `QUOTA_DEPOT`). Sync (storage and base)."""
    nom = nom_affiche(filename)
    media_type = juger(data, declare, nom)
    if store.volume_depose(sub, FENETRE_QUOTA_H) + len(data) > QUOTA_DEPOT:
        raise Refus(429, "attachment_quota_exceeded",
                    f"Attachment quota reached: {QUOTA_DEPOT // (1024 * 1024)} MB at most "
                    f"per person over {FENETRE_QUOTA_H} hours. Try again later.")
    # One object per upload (`propre_au_depot`): purging one person's file must never
    # remove another's identical one.
    cle = media_store.upload_object(PREFIXE, sub, data, media_type, nom,
                                    max_bytes=MAX_DEPOT, propre_au_depot=True)
    piece_id = f"att_{uuid.uuid4().hex}"
    store.creer_piece(piece_id, sub, org_id, cle, nom, media_type, len(data))
    return {"id": piece_id, "name": nom, "media_type": media_type, "size": len(data)}


def marques(ids: list[str], sub: str, org_id: Optional[int]) -> Optional[list[dict]]:
    """The thread's markers for `ids` (in order, duplicates folded), or None when one of
    them is not this person's in this org — or does not exist. Sync."""
    uniques = list(dict.fromkeys(ids))
    lignes = store.pieces(uniques, sub, org_id)
    if len(lignes) != len(uniques):
        return None
    return [{"type": MARQUE, "id": i, "name": lignes[i]["name"],
             "media_type": lignes[i]["media_type"]} for i in uniques]


# --- resolution, on each model request ---------------------------------------------------

def _tour_courant(messages: list[dict]) -> Optional[int]:
    """The index of the latest message carrying the person's words: a user message with
    anything other than tool results. Tool results and page answers are not a turn."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") != "user":
            continue
        contenu = m.get("content")
        if isinstance(contenu, str):
            return i
        if isinstance(contenu, list) and any(
                isinstance(b, dict) and b.get("type") != "tool_result" for b in contenu):
            return i
    return None


def _note_ancienne(m: dict) -> dict:
    return {"type": "text", "text": (
        f'[Attachment "{m.get("name") or "file"}" ({m.get("media_type") or "file"}) was '
        "shown with an earlier message. Ask the person to attach it again if you need to "
        "look at it.]")}


def _note_perdue(m: dict) -> dict:
    return {"type": "text", "text": (
        f'[Attachment "{m.get("name") or "file"}" could not be loaded. Ask the person to '
        "attach it again if you need it.]")}


def _contenu(ligne: dict, presigner: Callable[[str], str],
             lire: Callable[[str], bytes]) -> list[dict]:
    nom, media_type = ligne["name"], ligne["media_type"]
    etiquette = {"type": "text", "text": f"Attachment: {nom}"}
    if media_type in IMAGES:
        return [etiquette, {"type": "image",
                            "source": {"type": "url", "url": presigner(ligne["s3_key"])}}]
    if media_type == PDF:
        return [etiquette, {"type": "document",
                            "source": {"type": "url", "url": presigner(ligne["s3_key"])},
                            "title": nom}]
    texte = lire(ligne["s3_key"]).decode("utf-8")
    if texte.startswith("﻿"):
        texte = texte[1:]
    return [etiquette, {"type": "document",
                        "source": {"type": "text", "media_type": "text/plain",
                                   "data": texte},
                        "title": nom}]


def _presigner_court(cle: str) -> str:
    return media_store.presign_get(cle, expiry=_URL_DUREE_S)


def _lire_texte(cle: str) -> bytes:
    return media_store.fetch_object(cle, max_bytes=MAX_TEXTE)


def resoudre(messages: list[dict], sub: str, org_id: Optional[int], memo: dict, *,
             presigner: Optional[Callable[[str], str]] = None,
             lire: Optional[Callable[[str], bytes]] = None) -> list[dict]:
    """The messages as sent to the model: every marker resolved (module docstring).
    A COPY when there is any marker; the stored history is never touched. Sync."""
    presigner = presigner or _presigner_court
    lire = lire or _lire_texte
    if not any(isinstance(m.get("content"), list) and any(
            isinstance(b, dict) and b.get("type") == MARQUE for b in m["content"])
            for m in messages):
        return messages
    courant = _tour_courant(messages)
    a_resoudre = []
    if courant is not None and isinstance(messages[courant].get("content"), list):
        a_resoudre = [b.get("id") for b in messages[courant]["content"]
                      if isinstance(b, dict) and b.get("type") == MARQUE]
    maintenant = time.monotonic()
    manquants = [i for i in a_resoudre
                 if i not in memo or maintenant - memo[i][0] > _URL_FRAICHE_S]
    lignes = store.pieces(manquants, sub, org_id) if manquants else {}
    for i in manquants:
        ligne = lignes.get(i)
        blocs = None
        if ligne is not None:
            try:
                blocs = _contenu(ligne, presigner, lire)
            except (media_store.MediaError, UnicodeDecodeError) as e:
                logger.warning("copilot : pièce jointe %s illisible (%s)", i,
                               getattr(e, "code", type(e).__name__))
        memo[i] = (maintenant, blocs)
    sortie = []
    for idx, m in enumerate(messages):
        contenu = m.get("content")
        if not isinstance(contenu, list) or not any(
                isinstance(b, dict) and b.get("type") == MARQUE for b in contenu):
            sortie.append(m)
            continue
        neuf = []
        for b in contenu:
            if not (isinstance(b, dict) and b.get("type") == MARQUE):
                neuf.append(b)
            elif idx != courant:
                neuf.append(_note_ancienne(b))
            else:
                blocs = (memo.get(b.get("id")) or (0, None))[1]
                neuf.extend(blocs if blocs else [_note_perdue(b)])
        sortie.append({**m, "content": neuf})
    return sortie
