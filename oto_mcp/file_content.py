"""Décision de rendu d'un contenu de fichier pour un agent MCP (texte vs binaire).

Partagé par les tools qui ramènent le contenu d'un fichier (PJ Gmail, fichier
Drive…) : un petit contenu textuel est renvoyé INLINE (l'agent le lit), un binaire
ou un gros fichier passe par une URL signée (`media_store.upload_private`). Le seuil
inline évite d'injecter trop de tokens dans le contexte de l'agent.

Un TABLEUR `.xlsx` est un binaire qu'on sait lire : il est rendu INLINE en CSV par
feuille (`file_extract.render_xlsx_csv`, oto#181), borné au même seuil inline.
"""
from __future__ import annotations

from typing import Optional, Union

from .file_extract import (DEFAULT_SHEET_ROWS, NOT_A_SPREADSHEET, SpreadsheetError,
                           is_spreadsheet, render_xlsx_csv)

# Au-delà de cette taille, même un contenu textuel part en URL signée plutôt que
# d'être injecté dans le contexte de l'agent (texte = tokens).
INLINE_TEXT_CAP = 256 * 1024  # 256 Ko

_TEXTUAL_MIME = {
    "application/json", "application/ld+json", "application/xml",
    "application/csv", "application/x-ndjson", "application/markdown",
    "application/x-yaml", "application/yaml",
}


def as_text(data: bytes, mime: str) -> Optional[str]:
    """Renvoie le contenu décodé en UTF-8 si le fichier est textuel, sinon None.

    Un type `text/*` ou JSON/CSV/XML/YAML est traité comme texte ; pour un type
    inconnu, on décode quand même et on accepte si c'est de l'UTF-8 propre sans
    octet NUL (heuristique : un binaire — PDF, image — échoue le decode ou
    contient des NUL → part en URL signée)."""
    m = (mime or "").split(";")[0].strip().lower()
    looks_text = m.startswith("text/") or m in _TEXTUAL_MIME
    try:
        s = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if looks_text or "\x00" not in s:
        return s
    return None


class MediaUnavailable(RuntimeError):
    """Le stockage temporaire (S3) requis pour servir un binaire/gros fichier en
    URL signée est indisponible — l'appelant traduit en erreur de tool."""


def render_for_agent(data: bytes, filename: str, mime: str, *, sub: str, prefix: str,
                     sheet: Optional[Union[int, str]] = None,
                     max_rows: Optional[int] = None) -> dict:
    """Rendu d'un contenu de fichier pour un agent MCP — **home unique** de la
    règle inline-vs-URL (ex-duplication gmail/drive/slack).

    - petit contenu textuel (≤ `INLINE_TEXT_CAP`) → INLINE
      `{encoding: "text", content}` (l'agent le lit) ;
    - binaire ou volumineux → dépôt privé S3 + **URL signée temporaire**
      `{encoding: "url", url, expires_in}` (`media_store.upload_private`) ;
    - tableur `.xlsx` → INLINE en CSV par feuille : `{encoding: "text",
      format: "csv", content, sheets, sheet_names, truncated}`, restreint à la
      feuille `sheet` (nom ou index) et borné à `max_rows` lignes par feuille et à
      `INLINE_TEXT_CAP` caractères. `sheet`/`max_rows` sur un autre format lèvent
      `SpreadsheetError` (`not_a_spreadsheet`) : jamais ignorés en silence.
      **Tronqué** → le fichier BRUT complet est AUSSI déposé en privé : `raw_url`
      + `raw_expires_in` (signal #1152, 24/09/2026 : un tableur de 2 414 lignes rendu
      à 200 n'avait aucun chemin vers ses données — or on le veut entier pour le
      charger dans un tableau ou le ranger). Le CSV inline reste : c'est lui que
      l'agent lit. Stockage indisponible → `raw_unavailable` le DIT, le rendu inline
      n'échoue pas pour autant.

    `prefix` = préfixe de clé S3 (`gmail-attachments`, `drive-files`,
    `slack-files`…) ; `sub` = propriétaire du dépôt. **Appel BLOQUANT** (I/O S3) :
    invoquer depuis un handler sync (threadpool) ou via `asyncio.to_thread`.
    Lève `MediaUnavailable` si le stockage est absent (S3 non configuré), et
    `SpreadsheetError` (statut nommé) sur un tableur trop gros, forgé ou illisible.
    """
    out = {"filename": filename, "mimeType": mime, "size": len(data)}
    if is_spreadsheet(filename, mime):
        r = render_xlsx_csv(data, sheet=sheet,
                            max_rows=DEFAULT_SHEET_ROWS if max_rows is None else max_rows,
                            max_chars=INLINE_TEXT_CAP)
        out.update(encoding="text", format="csv", content=r.text,
                   sheets=[{"index": s.index, "name": s.name, "rows_total": s.rows_total,
                            "rows_rendered": s.rows_rendered, "truncated": s.truncated}
                           for s in r.sheets],
                   sheet_names=list(r.sheet_names), truncated=r.truncated)
        if r.truncated:
            _joindre_le_brut(out, data, filename, mime, sub=sub, prefix=prefix)
        return out
    if sheet is not None or max_rows is not None:
        raise SpreadsheetError(
            NOT_A_SPREADSHEET,
            f"`sheet`/`max_rows` ne valent que pour un tableur .xlsx — « {filename} » "
            f"({mime or 'type inconnu'}) n'en est pas un : relance sans ces paramètres.")
    text = as_text(data, mime)
    if text is not None and len(data) <= INLINE_TEXT_CAP:
        out.update(encoding="text", content=text)
        return out
    from . import media_store
    try:
        url = media_store.upload_private(prefix, sub, data, mime, filename)
    except media_store.MediaError as e:
        raise MediaUnavailable(
            f"Fichier binaire/volumineux ({len(data)} octets) : stockage temporaire "
            f"indisponible pour produire une URL ({e}). Configurer OTO_MCP_S3_*."
        )
    out.update(encoding="url", url=url, expires_in=media_store.presign_expiry())
    return out


def _joindre_le_brut(out: dict, data: bytes, filename: str, mime: str, *, sub: str,
                     prefix: str) -> None:
    """Dépose le fichier ORIGINAL (tableur tronqué) et en rend l'URL signée.

    Un stockage absent ne fait pas échouer le rendu : le CSV inline est juste et
    utile. Mais l'absence se DIT (`raw_unavailable`) — jamais un `raw_url` manquant
    en silence, qu'un agent lirait « pas de fichier complet »."""
    from . import media_store
    try:
        out["raw_url"] = media_store.upload_private(prefix, sub, data, mime, filename)
    except media_store.MediaError as e:
        out["raw_unavailable"] = (
            f"fichier brut non déposé : stockage temporaire indisponible ({e}). Le "
            "rendu CSV ci-dessus est tronqué ; relance avec `sheet` et un `max_rows` "
            "plus haut pour lire plus de lignes.")
        return
    out["raw_expires_in"] = media_store.presign_expiry()
