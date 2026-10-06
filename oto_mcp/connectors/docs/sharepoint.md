## prerequisite — connect with your Microsoft 365 account

on the "SharePoint & OneDrive" card, click **Connect with Microsoft** and choose your work account. Nothing else to install or register.
- the agent acts **with your rights**: it sees your OneDrive, the SharePoint sites and the files shared with you, no more, no less. Each person in the org connects their own account
- work or school accounts only: a personal Microsoft account (outlook.com, hotmail) does not have SharePoint
- ⚠️ **many organizations require a Microsoft 365 administrator to authorize oto a first time.** If Microsoft shows "admin approval required", this is that case: your admin connects once in the same way and ticks "consent on behalf of your organization", then everyone can connect
- the connection lasts over time; it drops if the password changes, if the organization revokes it or after a long inactivity: the card then says which account is "to reconnect", the others keep working
- **several Microsoft accounts** (yours, the one a client gives you): connect them one after the other, Microsoft lets you choose the account each time. Each one is added to the others, named by its address; reconnecting with the same account only replaces its own. To remove one, remove its row on the card: the others stay

## usage — find, read, drop a document

- "my files" → `sharepoint_file()`: the root of your OneDrive; `path="Folder/Subfolder"` to go down
- "in the client's Microsoft 365" → the same call with `_account="me@fabrikam.com"` (the linked account's address); without `_account`, it is the default account. Each response recalls in `_account` the account that was used
- "the Marketing site" → `sharepoint_site(query="Marketing")`, or by its address: `sharepoint_site(op="get", url="https://contoso.sharepoint.com/sites/Marketing")`
- "its document libraries" → `sharepoint_site(op="drives", site_id="…")`; each returned `id` is a `drive_id`
- "the content of this folder" → `sharepoint_file(drive_id="…", path="Contracts/2026")`
- "Marie's OneDrive" → `sharepoint_file(user="marie@contoso.com")`, if she shared it with you
- "find the Dupont contract" → `sharepoint_file(op="search", query="Dupont contract")` in your OneDrive, or with `drive_id=` in a library
- "read this document" → `sharepoint_file(op="download", drive_id="…", item_id="…")`: a Word or PowerPoint comes back as text (converted to PDF by Microsoft), an Excel as CSV, a PDF as text
- each response is a trimmed view (name, size, type, folder, link, last modified); `full=true` returns the complete Microsoft Graph object
- "drop this meeting report" → `sharepoint_file(op="upload", drive_id="…", path="Meeting reports", name="cr-2026-10-01.md", content_text="…")`; a binary file in `content_base64`

## note — what is misleading

- ⚠️ **a 403 does not mean the file doesn't exist**: your account has no access to it, or your organization blocks oto
- ⚠️ **search goes through SharePoint's index**: a file that was just dropped may not be there yet. To find it right away, list it by its folder
- ⚠️ **a name already taken is refused** on upload (`conflict="fail"`, the default): `conflict="rename"` keeps both, `conflict="replace"` overwrites
- site search (`op="search"`) does not find personal OneDrives: for a colleague's, use `user=`

## note — scope

files only: sites, libraries, OneDrive; list, search, read (up to 50 MB), upload (up to 25 MB), create a folder. Nothing deletes, moves or shares a file. Outlook mail, the calendar and Teams do not go through this connector.
