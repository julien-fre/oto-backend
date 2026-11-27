"""SharePoint & OneDrive — a person's Microsoft 365 files, via Microsoft Graph.

Credential = the PERSON's Microsoft 365 account (carrier `microsoft`, OAuth,
delegated permissions) that has authorized THIS service, acquired and renewed by
`auth/microsoft.py`: the agent sees exactly what they see in Microsoft 365. A 403 says "no access for you", never
"doesn't exist". Several linked accounts: the call picks its own through the
generic `_account=` axis (no parameter specific to the tools), resolved by
`access.resolve_credential` like for any multi-account connector.

**Surface** (one tool per object, the verb in `op`):
- `sharepoint_site` (search/get/drives) — find a site, read its document
  libraries (one library = one drive);
- `sharepoint_file` (list/get/search/download/upload/create_folder) — the items
  of a drive: the person's OneDrive by default, a library by
  `drive_id`, or a colleague's OneDrive (`user`); an item by `item_id` or by `path`. Reading goes through
  `file_content.render_for_agent` (inline text, CSV for a spreadsheet, signed URL
  otherwise); a Word or PowerPoint document is converted to PDF by Graph so that
  its text can be read.

- `sharepoint_excel` (sheets/range) — READ the cells of an Excel workbook in place;
  `sharepoint_excel_act` (update_range) WRITES a range of cells, nothing else of the
  file (formatting, other sheets, history stay) — instead of rebuilding and
  re-uploading the whole workbook.

⚠️ `upload` and `create_folder` WRITE to SharePoint or OneDrive; by default
a name already taken is refused (`conflict="fail"`), never silently overwritten. No
deletion, no move, no sharing: outside this surface.
"""
from __future__ import annotations

import base64
import binascii
import mimetypes
from typing import Literal, Optional, Union
from urllib.parse import urlsplit

from fastmcp import FastMCP

from .. import access, file_source
from ._microsoft_graph import bad as _bad, need as _need, raw as _brut
from ._microsoft_graph import refuse_ignored as _refuse_ignored
from ._microsoft_graph import raise_refusal, run, token
from .lecture import ACTE, LECTURE

# Read as PDF by default: Graph converts them, and it is the PDF's text that the agent
# reads (the raw Office binary can't be read). A spreadsheet stays raw: it renders
# as CSV. `as_pdf` forces one or the other.
_CONVERTIS = {"doc", "docx", "dot", "dotx", "odt", "rtf", "ppt", "pptx", "pps",
              "ppsx", "odp"}
#: The Microsoft service these tools call: its scopes, its card (`auth/microsoft`).
_SERVICE = "sharepoint"
_LABEL = "SharePoint & OneDrive"
_CONFLIT = ("an item already has this name — `conflict=\"rename\"` or "
            "`\"replace\"` to override")
_DOWNLOAD_MAX = 50 * 1024 * 1024
_UPLOAD_MAX = 25 * 1024 * 1024
#: Rows of a range returned inline by default — a sheet of a few thousand rows would
#: fill the agent's context.
_EXCEL_ROWS = 200
_WAC = ("Excel for the web could not open this workbook for this Microsoft account "
        "(Microsoft refused its WAC session — common on a workbook in a colleague's "
        "OneDrive, or for an account without Excel for the web). Your rights on the FILE "
        "are not in question. ")
_WAC_REFUS_LECTURE = _WAC + (
    "Read it with sharepoint_file(op=\"download\") on the same file (same `drive_id` or "
    "`user`, same `item_id` or `path`): a workbook comes back as CSV, sheet by sheet "
    "(`sheet`, `max_rows`).")
_WAC_REFUS_ECRITURE = _WAC + (
    "Its cells cannot be written in place: download it with sharepoint_file(op=\"download\"), "
    "change it, and upload it back with sharepoint_file(op=\"upload\", conflict=\"replace\") "
    "— or ask its owner for edit rights.")


def _site(s: dict) -> dict:
    return {k: s.get(k) for k in ("id", "displayName", "name", "webUrl", "description")}


def _drive(d: dict) -> dict:
    return {k: d.get(k) for k in ("id", "name", "driveType", "webUrl", "description")}


def _item(i: dict) -> dict:
    """A driveItem's view: enough to recognize it and reopen it."""
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


def _contenu(content_base64: Optional[str], content_text: Optional[str],
             source: Optional[dict] = None) -> tuple[bytes, Optional[str], Optional[str]]:
    """The bytes to upload, and the (filename, mime) a server-side `source` carries.

    `source` lets an agent drop a file it never held in its context (a PDF of
    several MB from a project, a signed URL from another tool): the server fetches
    it (`file_source.resolve`, same shapes as everywhere else) under the same
    `_UPLOAD_MAX` as the inline forms — never a bigger file by the back door."""
    donnes = [k for k, v in (("content_base64", content_base64),
                             ("content_text", content_text), ("source", source))
              if v is not None]
    if len(donnes) != 1:
        raise _bad("op='upload' requires exactly one of `content_base64`, `content_text` "
                   f"or `source` (got {', '.join(donnes) or 'none'}).")
    if source is not None:
        try:
            rf = file_source.resolve(source, max_bytes=_UPLOAD_MAX)
        except file_source.FileSourceError as e:
            raise _bad(f"`source`: {e}") from None
        if not rf.data:
            raise _bad("the content to upload is empty.")
        return rf.data, rf.filename, rf.mime
    if content_text is not None:
        data = content_text.encode("utf-8")
    else:
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise _bad("`content_base64` is not valid base64 — encode the whole file, "
                       "without a `data:` header or line breaks.") from None
    if not data:
        raise _bad("the content to upload is empty.")
    if len(data) > _UPLOAD_MAX:
        raise _bad(f"{len(data) // (1024 * 1024)} MB file: this tool uploads "
                   f"up to {_UPLOAD_MAX // (1024 * 1024)} MB.")
    return data, None, None


def register(mcp: FastMCP) -> None:
    from oto.tools.microsoft import FilesClient, WorkbookClient

    from .. import file_content

    def _client() -> FilesClient:
        """The Graph client of THIS caller, with their current token (renewed
        by `auth/microsoft.py` if it expires)."""
        return FilesClient(token(_SERVICE))

    def _run(fn):
        return run(fn, _LABEL, _CONFLIT)

    def _run_excel(fn, ecrire: bool):
        """`_run` for the workbook API, which opens the file in Excel for the web (WAC):
        Microsoft can refuse that session while the account reads the FILE fine (a
        workbook in a colleague's OneDrive, an account without Excel for the web). That
        403 is not a missing right: it is said as such, with the path that works."""
        from oto.tools.common.errors import UpstreamHTTPError

        try:
            return fn()
        except UpstreamHTTPError as e:
            if e.status_code == 403 and "WAC" in str(e.body):
                raise _bad(_WAC_REFUS_ECRITURE if ecrire else _WAC_REFUS_LECTURE) from e
            raise_refusal(e, _LABEL, _CONFLIT)
        except ValueError as e:
            raise _bad(str(e))

    def _drive_id(client: FilesClient, drive_id: Optional[str], user: Optional[str]) -> str:
        """The target drive: a library (`drive_id`), a colleague's OneDrive
        (`user`), otherwise the connected person's OneDrive."""
        if drive_id and user:
            raise _bad("designate the drive by `drive_id` (a library, from "
                       "sharepoint_site op='drives') OR by `user` (a colleague's "
                       "OneDrive, by their address) — not both.")
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
                raise _bad("op='get' requires `site_id` OR `url` (only one).")
            if site_id:
                return site_vue(_run(lambda: c.get_site(site_id)))
            parts = urlsplit(url.strip())
            if parts.scheme != "https" or not parts.hostname:
                raise _bad("`url` is the site's https address, e.g. "
                           "https://contoso.sharepoint.com/sites/Marketing")
            chemin = parts.path.strip("/")
            return site_vue(_run(lambda: c.get_site_by_path(parts.hostname, chemin) if chemin
                              else c.get_site(parts.hostname)))
        if op == "drives":
            _refuse_ignored(op, query=query, url=url)
            drives = _run(lambda: c.list_site_drives(_need(site_id, "site_id", op),
                                                     limit=limit))
            return {"drives": [drive_vue(d) for d in drives], "count": len(drives)}
        raise _bad("op must be 'search', 'get' or 'drives'.")

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
        source: Optional[dict] = None,
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
        ("Contracts/2026/nda.docx"); neither = the drive root.

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
          (`sheet`, `max_rows`) plus `raw_url`, a short-lived signed URL to the
          original workbook (formats kept); anything else as a short-lived signed
          URL. `as_pdf=true` converts another Office type, `as_pdf=false` skips the
          conversion (the document itself, rendered by the rules above). Up to 50 MB.
        - **"upload"**: ⚠️ WRITES — drop a file `name` into the folder
          `item_id`/`path` (root when omitted), from `content_base64` (any file),
          `content_text` (UTF-8 text) or `source` (a file the SERVER fetches, so
          it never transits through your context: `{"kind":"project_file",
          "project_id":…,"file_id":…}`, `{"kind":"url","url":"https://…"}` — a
          signed URL from another tool —, `{"kind":"drive",…}`, `{"kind":"gmail",
          …}`, an Outlook attachment `{"kind":"outlook","message_id":…,
          "attachment_id":…}` (or `"filename":…` instead of `attachment_id`; the same
          Microsoft account as this call, or `"account":…`); `name` may be omitted,
          the source's filename is used). Up to 25 MB whatever the form. An existing name is refused by default
          (`conflict="fail"`); "rename" keeps both, "replace" overwrites.
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
            name: op="upload"/"create_folder" — the new file or folder name
                (op="upload" with `source`: defaults to the source's filename).
            content_base64: op="upload" — the file, base64-encoded.
            content_text: op="upload" — the file as plain text, instead.
            source: op="upload" — a server-side file reference, instead
                ({kind: project_file|url|drive|gmail|outlook, …}, ≤ 25 MB).
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
            raise _bad(f"`as_pdf`/`sheet`/`max_rows` only apply to op='download' "
                       f"(got op='{op}').")
        if (content_base64 is not None or content_text is not None
                or source is not None) and op != "upload":
            raise _bad(f"`content_base64`/`content_text`/`source` only apply to "
                       f"op='upload' (got op='{op}').")
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
                raise _bad("op='download' requires `item_id` or `path`.")
            meta = _run(lambda: c.get_item(drive, item_id=item_id, path=path))
            if "folder" in meta:
                raise _bad(f"« {meta.get('name')} » is a folder: use op='list' to "
                           "see its content.")
            if (meta.get("size") or 0) > _DOWNLOAD_MAX:
                raise _bad(f"« {meta.get('name')} » weighs {meta['size'] // (1024 * 1024)} "
                           f"MB: this tool reads up to {_DOWNLOAD_MAX // (1024 * 1024)} MB "
                           f"(open it through its webUrl: {meta.get('webUrl')}).")
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
            data, nom_source, mime_source = _contenu(content_base64, content_text, source)
            fichier = name or nom_source
            if not fichier:
                fichier = _need(name, "name", op)
            mime = mimetypes.guess_type(fichier)[0] or mime_source or (
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

        raise _bad("op must be 'list', 'get', 'search', 'download', 'upload' or "
                   "'create_folder'.")

    def _plage(r: dict, max_rows: Optional[int], displayed: bool) -> dict:
        lignes = (r.get("text") if displayed else r.get("values")) or []
        borne = max_rows or _EXCEL_ROWS
        out = {"address": r.get("address"), "rowCount": r.get("rowCount"),
               "columnCount": r.get("columnCount"), "values": lignes[:borne]}
        if len(lignes) > borne:
            out["truncated"] = True
        return out

    def _classeur(drive_id, user, item_id, path):
        if not item_id and not path:
            raise _bad("the workbook is `item_id` or `path` (from sharepoint_file "
                       "op='list'/'search').")
        return WorkbookClient(token(_SERVICE)), _drive_id(_client(), drive_id, user)

    @mcp.tool(annotations=LECTURE)
    def sharepoint_excel(
        op: Literal["sheets", "range"] = "sheets",
        drive_id: Optional[str] = None,
        user: Optional[str] = None,
        item_id: Optional[str] = None,
        path: Optional[str] = None,
        sheet: Optional[str] = None,
        address: Optional[str] = None,
        displayed: bool = False,
        max_rows: Optional[int] = None,
    ) -> dict:
        """READ the cells of an Excel workbook (.xlsx) stored in SharePoint or OneDrive,
        in place — the workbook is a file found with `sharepoint_file` (`drive_id` and
        `item_id`, or `path`; your OneDrive by default, `user` for a colleague's).
        Writing cells is `sharepoint_excel_act`.

        `op`:
        - **"sheets"** (default): the worksheets — {sheets: [{id, name, position,
          visibility}]}.
        - **"range"**: the cells of `sheet` (its name) at `address` (A1 notation:
          "A1:H40", "D:D") — without `address`, the sheet's used range (from A1 to its
          last cell with a value: the way to find the header row and the columns).
          Returns {address, rowCount, columnCount, values: [[…], …]} — numbers as
          numbers; `displayed=true` returns the cells as Excel shows them instead
          (formatted dates, percentages). At most `max_rows` rows (default 200):
          read a longer sheet by address, block after block.

        Args:
            op: sheets (default) | range.
            drive_id: the library's drive id (`sharepoint_site` op="drives"). Omit
                for your OneDrive.
            user: a colleague's OneDrive, by address, instead.
            item_id: the workbook's id.
            path: the workbook by path from the drive root, instead of `item_id`.
            sheet: op="range" — the worksheet's name.
            address: op="range" — the range, A1 notation; omit for the used range.
            displayed: op="range" — the text Excel displays instead of the values.
            max_rows: op="range" — rows returned (default 200).
        """
        if op not in ("sheets", "range"):
            raise _bad("op must be 'sheets' or 'range'.")
        if op == "sheets":
            _refuse_ignored(op, sheet=sheet, address=address, max_rows=max_rows)
            if displayed:
                raise _bad("`displayed` only applies to op='range'.")
            w, drive = _classeur(drive_id, user, item_id, path)
            feuilles = _run_excel(lambda: w.list_worksheets(drive, item_id=item_id,
                                                           path=path), ecrire=False)
            return {"sheets": [{k: f.get(k) for k in ("id", "name", "position",
                                                      "visibility")} for f in feuilles],
                    "count": len(feuilles)}
        nom = _need(sheet, "sheet", op)
        w, drive = _classeur(drive_id, user, item_id, path)
        r = _run_excel(lambda: w.get_range(drive, nom, address, item_id=item_id, path=path),
                       ecrire=False)
        return _plage(r, max_rows, displayed)

    @mcp.tool(annotations=ACTE)
    def sharepoint_excel_act(
        op: Literal["update_range"],
        sheet: str,
        address: str,
        values: list[list[Union[str, float, int, bool, None]]],
        drive_id: Optional[str] = None,
        user: Optional[str] = None,
        item_id: Optional[str] = None,
        path: Optional[str] = None,
    ) -> dict:
        """⚠️ WRITE cells of an Excel workbook (.xlsx) in SharePoint or OneDrive, in
        place: only the cells of `address` change — formatting, the other cells and
        sheets, the file's version history stay, and it works while colleagues have it
        open. Never rebuild and re-upload a workbook to change some cells. Reading is
        `sharepoint_excel`.

        `op` **"update_range"**: writes `values` into `sheet` at `address` (A1
        notation). `values` is a list of rows, exactly the range's shape: "H2:I4" =
        3 rows of 2 cells. A number is written as a number (keep the column's format),
        `null` leaves a cell unchanged, "" clears it, a text starting with "=" is a
        formula. To fill a column from row 2, read the used range first
        (`sharepoint_excel(op="range")`) to find the column letters and the rows.
        Returns the range as written {address, rowCount, columnCount, values}.

        Args:
            op: update_range.
            sheet: the worksheet's name (`sharepoint_excel` op="sheets").
            address: the range to write, A1 notation ("H2:I17").
            values: the rows to write, the range's exact shape.
            drive_id: the library's drive id. Omit for your OneDrive.
            user: a colleague's OneDrive, by address, instead.
            item_id: the workbook's id.
            path: the workbook by path from the drive root, instead of `item_id`.
        """
        if op != "update_range":
            raise _bad("op must be 'update_range'.")
        w, drive = _classeur(drive_id, user, item_id, path)
        r = _run_excel(lambda: w.update_range(drive, _need(sheet, "sheet", op),
                                              _need(address, "address", op), values,
                                              item_id=item_id, path=path), ecrire=True)
        return _plage(r, len(values), False)
