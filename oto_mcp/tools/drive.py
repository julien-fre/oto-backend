"""Google Drive — surface oto-core (DriveClient) exposée par-utilisateur, multi-compte.

Gestion des fichiers/dossiers du Drive du user : lister, organiser (déplacer,
renommer, dossiers), supprimer, partager. Scope `/auth/drive` **complet**
(restricted) — pour voir/gérer TOUS les fichiers, pas seulement ceux créés par
oto. Compte par défaut ou ciblé par `account`. Per-user via OAuth.

L'**upload** local→Drive reste côté CLI (pas de FS serveur). La LECTURE, elle, est
exposée en entier et sans disque : `drive_download` pour les fichiers binaires/
uploadés, `drive_export` pour les Google natifs (Docs/Sheets/Slides — leur contenu
ne se télécharge pas, il se convertit). Les deux rendent le contenu à l'agent
(inline texte, ou URL signée pour un binaire). L'argument « pas de FS » ne valait
pas pour l'export : convertir en mémoire n'écrit rien (signal #329 — sans lui,
impossible d'ingérer les notes de réunion Gemini autrement qu'au copier-coller).
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, file_content, google_oauth


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


# Formats d'export offerts à l'agent (un mot, pas un mime à recopier).
_EXPORT_MIME = {
    "markdown": "text/markdown",
    "md": "text/markdown",
    "text": "text/plain",
    "txt": "text/plain",
    "html": "text/html",
    "pdf": "application/pdf",
    "csv": "text/csv",
}

# Défaut par type SOURCE : un tableur n'a pas de markdown, une présentation non plus.
# Sert aussi de test « est-ce un natif Google ? » — sinon c'est drive_download.
_DEFAULT_EXPORT_BY_SOURCE = {
    "application/vnd.google-apps.document": "text/markdown",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


def _client_for_user(account: Optional[str] = None):
    sub = access.current_user_sub_or_raise()
    try:
        creds = google_oauth.credentials_for(sub, account=account)
    except RuntimeError as e:
        raise _bad(str(e))
    from oto.tools.google.drive.lib.drive_client import DriveClient
    return DriveClient(credentials=creds)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def drive_list(
        folder_id: Optional[str] = None,
        query: Optional[str] = None,
        page_size: int = 100,
        account: Optional[str] = None,
    ) -> dict:
        """List Drive files.

        Args:
            folder_id: restrict to a parent folder id.
            query: raw Drive query (e.g. "name contains 'report'", "mimeType='application/pdf'").
            page_size: max results (paginates).
            account: email of the Google account to use (default if omitted).

        Returns {files: [{id, name, mimeType, modifiedTime, size, webViewLink}], count}.
        """
        client = _client_for_user(account)
        files = await asyncio.to_thread(client.list_files, folder_id, query, page_size)
        return {"files": files, "count": len(files)}

    @mcp.tool()
    async def drive_download(file_id: str, account: Optional[str] = None) -> dict:
        """Fetch the CONTENT (bytes) of a Drive file, by file_id.

        Get `file_id` from `drive_list`/`drive_metadata`. The response depends on
        the file:
        - **small text** (txt/csv/json/markdown, ≤256 KB) → returned INLINE:
          `{encoding: "text", content}` — read it directly.
        - **binary or large** (PDF, image, big file) → uploaded to temporary
          storage and returned as a short-lived signed URL: `{encoding: "url",
          url, expires_in}` (seconds). Fetch the URL to get the bytes.

        For a Google-native doc (Docs/Sheets/Slides), this fails — read those with
        `drive_export` instead (they are converted, not downloaded). Returns
        {filename, mimeType, size, encoding, content|url, expires_in?}.
        """
        client = _client_for_user(account)
        try:
            f = await asyncio.to_thread(client.get_file_bytes, file_id)
        except Exception as e:
            raise _bad(str(e))
        data, filename, mime = f["data"], f["filename"], f["mimeType"]
        sub = access.current_user_sub_or_raise()
        try:
            return await asyncio.to_thread(
                file_content.render_for_agent, data, filename, mime,
                sub=sub, prefix="drive-files")
        except file_content.MediaUnavailable as e:
            raise _bad(str(e))

    @mcp.tool()
    async def drive_export(
        file_id: str, format: Optional[str] = None, account: Optional[str] = None,
    ) -> dict:
        """Read the CONTENT of a GOOGLE-NATIVE doc (Docs/Sheets/Slides), by file_id.

        The counterpart of `drive_download`, which only works on uploaded/binary
        files: a Google-native doc has no binary content to download (403 "Only
        files with binary content can be downloaded"), its content comes out of an
        EXPORT — that's this tool. Use it to actually READ meeting notes ("… - Notes
        par Gemini"), specs, or any Doc you found with `drive_list`.

        Args:
            file_id: from `drive_list` / `drive_metadata` (mimeType
                application/vnd.google-apps.{document,spreadsheet,presentation}).
            format: markdown | text | html | pdf | csv. Omit for a sensible default
                per type (Doc→markdown, Sheet→csv (first sheet), Slides→text).
            account: which Google account (default: the primary one).

        Returns {filename, mimeType, encoding, content|url, …}: text formats come
        back INLINE, `pdf` as a short-lived signed URL.
        """
        client = _client_for_user(account)
        mime = _EXPORT_MIME.get((format or "").strip().lower()) if format else None
        if format and not mime:
            raise _bad(f"format « {format} » inconnu — attendu : "
                       f"{', '.join(sorted(_EXPORT_MIME))}.")
        if mime is None:
            meta = await asyncio.to_thread(client.get_file_metadata, file_id)
            src = (meta.get("mimeType") or "")
            mime = _DEFAULT_EXPORT_BY_SOURCE.get(src)
            if mime is None:
                # Pas un natif Google : l'export ne s'applique pas, mais le download si.
                raise _bad(f"« {meta.get('name') or file_id} » n'est pas un document "
                           f"Google natif (mimeType {src or 'inconnu'}) : son contenu se "
                           f"lit avec drive_download, pas drive_export.")
        try:
            f = await asyncio.to_thread(client.export_file_bytes, file_id, mime)
        except Exception as e:
            raise _bad(str(e))
        sub = access.current_user_sub_or_raise()
        try:
            return await asyncio.to_thread(
                file_content.render_for_agent, f["data"], f["filename"], mime,
                sub=sub, prefix="drive-exports")
        except file_content.MediaUnavailable as e:
            raise _bad(str(e))

    @mcp.tool()
    async def drive_metadata(file_id: str, account: Optional[str] = None) -> dict:
        """Get a Drive file's metadata by id."""
        client = _client_for_user(account)
        return await asyncio.to_thread(client.get_file_metadata, file_id)

    @mcp.tool()
    async def drive_create_folder(
        name: str, parent_folder_id: Optional[str] = None, account: Optional[str] = None,
    ) -> dict:
        """Create a folder, optionally inside a parent folder. Returns the folder metadata."""
        client = _client_for_user(account)
        return await asyncio.to_thread(client.create_folder, name, parent_folder_id)

    @mcp.tool()
    async def drive_update(
        file_id: str,
        new_name: Optional[str] = None,
        move_to_folder: Optional[str] = None,
        account: Optional[str] = None,
    ) -> dict:
        """Rename and/or move a file/folder.

        Args:
            new_name: new name (rename).
            move_to_folder: destination folder id (move). You can do both at once.
            account: email of the Google account to use (default if omitted).
        """
        if not new_name and not move_to_folder:
            raise _bad("Fournis `new_name` (renommer) et/ou `move_to_folder` (déplacer).")
        client = _client_for_user(account)
        out: dict = {}
        if new_name:
            out["renamed"] = await asyncio.to_thread(client.rename_file, file_id, new_name)
        if move_to_folder:
            out["moved"] = await asyncio.to_thread(client.move_file, file_id, move_to_folder)
        return out

    @mcp.tool()
    async def drive_delete(file_id: str, account: Optional[str] = None) -> dict:
        """Delete a file/folder (moves it to trash). Irreversible from the API's point of view."""
        client = _client_for_user(account)
        return await asyncio.to_thread(client.delete_file, file_id)

    @mcp.tool()
    async def drive_access(
        file_id: str,
        email: Optional[str] = None,
        role: str = "reader",
        remove: bool = False,
        notify: bool = True,
        account: Optional[str] = None,
    ) -> dict:
        """Inspect or change who can access a file/folder.

        Args:
            email: the person to share with / revoke. OMIT to just LIST current access.
            role: "reader", "commenter" or "writer" (when granting).
            remove: True + `email` → revoke that person's access.
            notify: send Google's notification email (when granting).
            account: email of the Google account to use (default if omitted).

        No `email` → {permissions: [...], count}. With `email` → grants (or
        revokes if `remove`) and returns the operation result.
        """
        client = _client_for_user(account)
        if not email:
            perms = await asyncio.to_thread(client.list_permissions, file_id)
            return {"permissions": perms, "count": len(perms)}
        if remove:
            return await asyncio.to_thread(client.unshare, file_id, email)
        return await asyncio.to_thread(client.share, file_id, email, role, notify)
