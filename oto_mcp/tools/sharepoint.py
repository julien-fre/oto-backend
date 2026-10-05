"""SharePoint & OneDrive — les fichiers Microsoft 365 d'une personne, via Microsoft Graph.

Credential = la connexion Microsoft de la PERSONNE (OAuth, permissions déléguées),
acquise et renouvelée par `auth/microsoft.py` : l'agent voit exactement ce
qu'elle voit dans Microsoft 365. Un 403 dit « pas d'accès pour toi », jamais
« n'existe pas ». Plusieurs comptes liés : l'appel choisit le sien par l'axe
générique `_account=` (aucun paramètre propre aux outils), résolu par
`access.resolve_credential` comme pour tout connecteur multi-compte.

**Surface** (un tool par objet, le verbe en `op`) :
- `sharepoint_site` (search/get/drives) — trouver un site, lire ses bibliothèques
  de documents (une bibliothèque = un drive) ;
- `sharepoint_file` (list/get/search/download/upload/create_folder) — les éléments
  d'un drive : le OneDrive de la personne par défaut, une bibliothèque par
  `drive_id`, ou le OneDrive d'un collaborateur (`user`) ; un élément par `item_id` ou par `path`. La lecture passe par
  `file_content.render_for_agent` (texte inline, CSV d'un tableur, URL signée
  sinon) ; un document Word ou PowerPoint est converti en PDF par Graph pour que
  son texte se lise.

⚠️ `upload` et `create_folder` ÉCRIVENT dans SharePoint ou OneDrive ; par défaut
un nom déjà pris est refusé (`conflict="fail"`), jamais écrasé en silence. Aucune
suppression, aucun déplacement, aucun partage : hors de cette surface.
"""
from __future__ import annotations

import base64
import binascii
import mimetypes
from typing import Literal, Optional, Union
from urllib.parse import urlsplit

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..mcp_errors import McpError

# Lus en PDF par défaut : Graph les convertit, et c'est le texte du PDF que l'agent
# lit (le binaire Office brut ne se lit pas). Un tableur reste brut : il se rend
# en CSV. `as_pdf` force l'un ou l'autre.
_CONVERTIS = {"doc", "docx", "dot", "dotx", "odt", "rtf", "ppt", "pptx", "pps",
              "ppsx", "odp"}
_DOWNLOAD_MAX = 50 * 1024 * 1024
_UPLOAD_MAX = 25 * 1024 * 1024


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    body = e.body if isinstance(e.body, dict) else {}
    detail = str((body.get("error") or {}).get("message") or e.body or "")[:400]
    if status == 401:
        return (f"Microsoft Graph refuse le jeton (HTTP 401) : reconnecte-toi depuis "
                f"tes connecteurs, « SharePoint & OneDrive ». {detail}").strip()
    if status == 403:
        return (f"Microsoft Graph refuse l'accès (HTTP 403) : ce compte Microsoft n'a "
                f"pas les droits sur cet élément, ou son organisation bloque oto. "
                f"{detail}").strip()
    if status == 404:
        return f"Microsoft Graph : introuvable (HTTP 404). {detail}".strip()
    if status == 409:
        return (f"Microsoft Graph : un élément porte déjà ce nom (HTTP 409) — "
                f"`conflict=\"rename\"` ou `\"replace\"` pour passer outre. {detail}").strip()
    return f"Microsoft Graph a refusé la requête (HTTP {status}) : {detail}"


def _brut(objet: dict) -> dict:
    """`full=True` : l'objet Graph tel que l'amont le livre."""
    return objet


def _site(s: dict) -> dict:
    return {k: s.get(k) for k in ("id", "displayName", "name", "webUrl", "description")}


def _drive(d: dict) -> dict:
    return {k: d.get(k) for k in ("id", "name", "driveType", "webUrl", "description")}


def _item(i: dict) -> dict:
    """La vue d'un driveItem : de quoi le reconnaître et le rouvrir."""
    parent = i.get("parentReference") or {}
    dossier = (parent.get("path") or "").split("root:", 1)[-1] if parent.get("path") else None
    return {
        "id": i.get("id"), "name": i.get("name"),
        "kind": "folder" if "folder" in i else "file",
        "size": i.get("size"),
        "mimeType": (i.get("file") or {}).get("mimeType"),
        "childCount": (i.get("folder") or {}).get("childCount"),
        "folder_path": (dossier or "/") if dossier is not None else None,
        "drive_id": parent.get("driveId"),
        "webUrl": i.get("webUrl"),
        "lastModifiedDateTime": i.get("lastModifiedDateTime"),
        "lastModifiedBy": ((i.get("lastModifiedBy") or {}).get("user") or {}).get("displayName"),
    }


def _refuse_ignored(op: str, **provided) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op='{op}' n'utilise pas `{name}`.")


def _need(value, name: str, op: str):
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _bad(f"op='{op}' requiert `{name}`.")
    return value


def _contenu(content_base64: Optional[str], content_text: Optional[str]) -> bytes:
    if (content_base64 is None) == (content_text is None):
        raise _bad("op='upload' requiert `content_base64` OU `content_text` (un seul).")
    if content_text is not None:
        data = content_text.encode("utf-8")
    else:
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise _bad("`content_base64` n'est pas du base64 valide — encode le fichier "
                       "entier, sans en-tête `data:` ni retour à la ligne.") from None
    if not data:
        raise _bad("le contenu à déposer est vide.")
    if len(data) > _UPLOAD_MAX:
        raise _bad(f"fichier de {len(data) // (1024 * 1024)} Mo : ce tool dépose "
                   f"jusqu'à {_UPLOAD_MAX // (1024 * 1024)} Mo.")
    return data


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.microsoft import GraphClient, MicrosoftAuthError

    from .. import file_content
    from ..auth import microsoft as ms_auth

    def _client() -> GraphClient:
        """Le client Graph de CET appelant, avec son jeton du moment (renouvelé
        par `auth/microsoft.py` s'il expire)."""
        try:
            jeton = ms_auth.access_token_for(access.current_user_sub_or_raise())
        except (RuntimeError, MicrosoftAuthError) as e:
            raise _bad(str(e))
        return GraphClient(jeton)

    def _run(fn):
        """4xx de Graph → refus nommé. 429 et 5xx restent ce qu'ils sont : la
        taxonomie d'erreurs les classe réessayables."""
        try:
            return fn()
        except UpstreamHTTPError as e:
            if 400 <= e.status_code < 500 and e.status_code != 429:
                raise _bad(_upstream_message(e))
            raise
        except ValueError as e:
            raise _bad(str(e))

    def _drive_id(client: GraphClient, drive_id: Optional[str], user: Optional[str]) -> str:
        """Le drive visé : une bibliothèque (`drive_id`), le OneDrive d'un
        collaborateur (`user`), sinon le OneDrive de la personne connectée."""
        if drive_id and user:
            raise _bad("désigne le drive par `drive_id` (une bibliothèque, depuis "
                       "sharepoint_site op='drives') OU par `user` (le OneDrive d'un "
                       "collaborateur, par son adresse) — pas les deux.")
        if drive_id:
            return drive_id
        if user:
            return _run(lambda: client.get_user_drive(user))["id"]
        return _run(client.get_my_drive)["id"]

    @mcp.tool()
    def sharepoint_site(
        op: Literal["search", "get", "drives"] = "search",
        query: Optional[str] = None,
        site_id: Optional[str] = None,
        url: Optional[str] = None,
        limit: int = 50,
        full: bool = False,
    ) -> dict:
        """A SharePoint site of the organization, and its document libraries.

        `op`:
        - **"search"** (default): sites whose name or description match `query`.
          Only the sites you can open in Microsoft 365.
        - **"get"**: one site, by `site_id` or by its `url` as read in the
          browser (e.g. https://contoso.sharepoint.com/sites/Marketing).
        - **"drives"**: the site's document libraries (`site_id`). A library is a
          drive: pass its `id` as `drive_id` to `sharepoint_file`.

        Args:
            op: search (default) | get | drives.
            query: op="search" — words to look for.
            site_id: op="get"/"drives" — the site id returned by search/get.
            url: op="get" — the site's address, instead of `site_id`.
            limit: op="search"/"drives" — max results (default 50).
            full: return Graph's raw objects instead of the trimmed view.
        """
        site_vue, drive_vue = (_brut, _brut) if full else (_site, _drive)
        c = _client()
        if op == "search":
            _refuse_ignored(op, site_id=site_id, url=url)
            sites = _run(lambda: c.search_sites(_need(query, "query", op), limit=limit))
            return {"sites": [site_vue(s) for s in sites], "count": len(sites)}
        if op == "get":
            _refuse_ignored(op, query=query)
            if bool(site_id) == bool(url):
                raise _bad("op='get' requiert `site_id` OU `url` (un seul).")
            if site_id:
                return site_vue(_run(lambda: c.get_site(site_id)))
            parts = urlsplit(url.strip())
            if parts.scheme != "https" or not parts.hostname:
                raise _bad("`url` est l'adresse https du site, ex. "
                           "https://contoso.sharepoint.com/sites/Marketing")
            chemin = parts.path.strip("/")
            return site_vue(_run(lambda: c.get_site_by_path(parts.hostname, chemin) if chemin
                              else c.get_site(parts.hostname)))
        if op == "drives":
            _refuse_ignored(op, query=query, url=url)
            drives = _run(lambda: c.list_site_drives(_need(site_id, "site_id", op),
                                                     limit=limit))
            return {"drives": [drive_vue(d) for d in drives], "count": len(drives)}
        raise _bad("op doit être 'search', 'get' ou 'drives'.")

    @mcp.tool()
    def sharepoint_file(
        op: Literal["list", "get", "search", "download", "upload",
                    "create_folder"] = "list",
        drive_id: Optional[str] = None,
        user: Optional[str] = None,
        item_id: Optional[str] = None,
        path: Optional[str] = None,
        query: Optional[str] = None,
        name: Optional[str] = None,
        content_base64: Optional[str] = None,
        content_text: Optional[str] = None,
        conflict: Literal["fail", "rename", "replace"] = "fail",
        as_pdf: Optional[bool] = None,
        sheet: Optional[Union[int, str]] = None,
        max_rows: Optional[int] = None,
        limit: int = 200,
        full: bool = False,
    ) -> dict:
        """A file or folder in a SharePoint document library or a OneDrive.

        The drive is YOUR OneDrive by default; `drive_id` targets a library
        (from `sharepoint_site` op="drives"), `user` a colleague's OneDrive (by
        email, if they shared it with you) — at most one of the two. Inside it, an
        item is `item_id` OR `path` relative to the drive root
        ("Contrats/2026/nda.docx"); neither = the drive root.

        `op`:
        - **"list"** (default): the content of a folder (`item_id`/`path`, root
          when omitted). Returns {items: [{id, name, kind, size, mimeType,
          childCount, folder_path, webUrl, lastModifiedDateTime, lastModifiedBy}]}.
        - **"get"**: one item's metadata.
        - **"search"**: files of the whole drive matching `query` (name, metadata
          and content, from SharePoint's index — a file added seconds ago may not
          show yet).
        - **"download"**: READ a file's content. Word and PowerPoint documents are
          converted to PDF by Microsoft and their TEXT returned inline (plus
          `raw_url`); small text files inline; spreadsheets (.xlsx) inline as CSV
          (`sheet`, `max_rows`); anything else as a short-lived signed URL.
          `as_pdf=true` converts another Office type, `as_pdf=false` returns the
          original bytes. Up to 50 MB.
        - **"upload"**: ⚠️ WRITES — drop a file `name` into the folder
          `item_id`/`path` (root when omitted), from `content_base64` (any file)
          or `content_text` (UTF-8 text). Up to 25 MB. An existing name is
          refused by default (`conflict="fail"`); "rename" keeps both,
          "replace" overwrites.
        - **"create_folder"**: ⚠️ WRITES — a folder `name` inside `item_id`/
          `path` (root when omitted), same `conflict` rule.

        Nothing here deletes, moves or shares a file.

        Args:
            op: list (default) | get | search | download | upload | create_folder.
            drive_id: a library's drive id. Omit (with `user`) for your OneDrive.
            user: email of a colleague whose OneDrive to use, instead.
            item_id: the item (folder for list/upload/create_folder) by id.
            path: the item by path from the drive root, instead of `item_id`.
            query: op="search" — words to look for.
            name: op="upload"/"create_folder" — the new file or folder name.
            content_base64: op="upload" — the file, base64-encoded.
            content_text: op="upload" — the file as plain text, instead.
            conflict: op="upload"/"create_folder" — fail (default) | rename |
                replace.
            as_pdf: op="download" — force (true) or skip (false) the PDF
                conversion; omit for the default per type.
            sheet: op="download" of an .xlsx — sheet name or 0-based index.
            max_rows: op="download" of an .xlsx — rows per sheet (default 200).
            limit: op="list"/"search" — max items (default 200).
            full: return Graph's raw driveItems instead of the trimmed view.
        """
        vue = _brut if full else _item
        if (as_pdf is not None or sheet is not None or max_rows is not None) \
                and op != "download":
            raise _bad(f"`as_pdf`/`sheet`/`max_rows` ne valent que pour op='download' "
                       f"(reçu op='{op}').")
        if (content_base64 is not None or content_text is not None) and op != "upload":
            raise _bad(f"`content_base64`/`content_text` ne valent que pour op='upload' "
                       f"(reçu op='{op}').")
        c = _client()
        drive = _drive_id(c, drive_id, user)

        if op == "list":
            _refuse_ignored(op, query=query, name=name)
            items = _run(lambda: c.list_children(drive, item_id=item_id, path=path,
                                                 limit=limit))
            return {"drive_id": drive, "items": [vue(i) for i in items],
                    "count": len(items)}

        if op == "get":
            _refuse_ignored(op, query=query, name=name)
            return vue(_run(lambda: c.get_item(drive, item_id=item_id, path=path)))

        if op == "search":
            _refuse_ignored(op, item_id=item_id, path=path, name=name)
            items = _run(lambda: c.search_items(drive, _need(query, "query", op),
                                                limit=limit))
            return {"drive_id": drive, "items": [vue(i) for i in items],
                    "count": len(items)}

        if op == "download":
            _refuse_ignored(op, query=query, name=name)
            if not item_id and not path:
                raise _bad("op='download' requiert `item_id` ou `path`.")
            meta = _run(lambda: c.get_item(drive, item_id=item_id, path=path))
            if "folder" in meta:
                raise _bad(f"« {meta.get('name')} » est un dossier : op='list' pour "
                           "voir son contenu.")
            if (meta.get("size") or 0) > _DOWNLOAD_MAX:
                raise _bad(f"« {meta.get('name')} » pèse {meta['size'] // (1024 * 1024)} "
                           f"Mo : ce tool lit jusqu'à {_DOWNLOAD_MAX // (1024 * 1024)} Mo "
                           f"(ouvre-le par son webUrl : {meta.get('webUrl')}).")
            nom = meta.get("name") or meta["id"]
            ext = nom.rsplit(".", 1)[-1].lower() if "." in nom else ""
            pdf = as_pdf if as_pdf is not None else ext in _CONVERTIS
            data = _run(lambda: c.download(drive, item_id=meta["id"],
                                           format="pdf" if pdf else None))
            if pdf:
                nom, mime = f"{nom.rsplit('.', 1)[0]}.pdf", "application/pdf"
            else:
                mime = ((meta.get("file") or {}).get("mimeType")
                        or mimetypes.guess_type(nom)[0] or "application/octet-stream")
            sub = access.current_user_sub_or_raise()
            try:
                out = file_content.render_for_agent(
                    data, nom, mime, sub=sub, prefix="sharepoint-files",
                    sheet=sheet, max_rows=max_rows)
            except (file_content.MediaUnavailable, file_content.SpreadsheetError) as e:
                raise _bad(str(e)) from None
            return {**out, "item": vue(meta), **({"converted_from": ext} if pdf else {})}

        if op == "upload":
            _refuse_ignored(op, query=query)
            data = _contenu(content_base64, content_text)
            fichier = _need(name, "name", op)
            mime = mimetypes.guess_type(fichier)[0] or (
                "text/plain; charset=utf-8" if content_text is not None
                else "application/octet-stream")
            return vue(_run(lambda: c.upload(drive, fichier, data, parent_id=item_id,
                                               parent_path=path, conflict=conflict,
                                               content_type=mime)))

        if op == "create_folder":
            _refuse_ignored(op, query=query)
            return vue(_run(lambda: c.create_folder(drive, _need(name, "name", op),
                                                      parent_id=item_id, parent_path=path,
                                                      conflict=conflict)))

        raise _bad("op doit être 'list', 'get', 'search', 'download', 'upload' ou "
                   "'create_folder'.")
