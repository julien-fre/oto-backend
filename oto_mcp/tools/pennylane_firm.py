"""Pennylane (cabinet) — l'API « Firm » de Pennylane, côté cabinet comptable.

Adaptateur MINCE sur `oto.tools.pennylane_firm.PennylaneFirmClient` (oto-core) : un
jeton de cabinet, posé par un admin d'org sur la carte du connecteur (palier org),
atteint toutes les sociétés du portefeuille. Quatre outils :

- `pennylane_firm_companies` — le portefeuille (une page), ou la fiche d'une société ;
- `pennylane_firm_tree` — l'arbre de la GED d'une société : dossiers, ou fichiers
  (filtre `parent_folder_id`), au curseur ;
- `pennylane_firm_create_folder` — ÉCRITURE ;
- `pennylane_firm_upload_url` — frappe le lien du RELAIS d'upload
  (`tools/pennylane_firm_relais.py`, route `POST /api/relay/{token}`), gardé par le
  contrôle de doublon.

⚠️ `company_id` est l'id CÔTÉ CABINET : rien à voir avec les ids du connecteur
`pennylane` (API Company), et les mêmes que la GED vue par `pennylaneged`. L'API n'a
ni suppression, ni déplacement, ni renommage : un mauvais dépôt ne se corrige pas par
elle — d'où le refus du doublon AVANT d'écrire.

Les erreurs de la lib deviennent des refus nommés (`pennylane_firm_socle.traduire`) ;
un 403 nomme le scope qui manque au jeton. Tous les outils sont synchrones (`def`) :
FastMCP les joue dans un thread, coffre et base compris.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .. import access, output_projection
from ..auth.hooks import current_user_sub_from_token
from ..connectors import verify as connector_verify
from . import pennylane_firm_relais as relais
from . import pennylane_firm_socle as socle
from .pennylane_firm_socle import Refus, _client

# Ce que la vue par défaut d'une liste retire (`full=True` rend le brut). Denylist :
# un champ neuf de Pennylane reste visible tant qu'on ne l'a pas jugé.
_SOCIETE_RETIREE = ("billing_company_name", "address", "city", "postal_code",
                    "activity_nomenclature", "activity_code")
_GED_RETIREE = ("url", "files", "created_at", "updated_at")


def _verify(fields: dict, config: dict | None = None) -> None:
    """Sonde « tester la connexion » : une page d'un élément du portefeuille. Prouve
    le jeton ET le scope `companies:readonly` ; un 403 nomme le scope manquant."""
    from oto.tools.pennylane_firm import PennylaneFirmClient

    PennylaneFirmClient(fields["key"]).list_companies(per_page=1)


def _sub() -> str:
    sub = current_user_sub_from_token()
    if not sub:
        raise socle.vers_mcp(Refus(401, "unauthenticated",
                                   "Pennylane (firm) tools need an authenticated caller."))
    return sub


def _appel(appel, geste: str):
    """Un appel à la lib, ses refus traduits en `McpError` nommée."""
    try:
        return socle.traduire(appel, geste)
    except Refus as e:
        raise socle.vers_mcp(e) from e


def _slim(page: dict, retire: tuple, full: bool) -> dict:
    """La vue de tri d'une page (`items`), ou le brut si `full`."""
    if full or not isinstance(page, dict):
        return page
    out = output_projection.project(page, items_path="items", item_drop=retire)
    out["projection"] = {"omitted": list(retire), "full": "pass full=true for every field"}
    return out


def _appelant() -> tuple[str, int]:
    """`(sub, org)` de l'APPEL : le lien de relais scelle les deux, et l'org porte le
    jeton de cabinet que le relais résoudra."""
    sub = _sub()
    org = access.current_org(sub)
    if org is None:
        raise socle.vers_mcp(Refus(
            403, "no_organization",
            "An upload link is minted for an organization: call within one (`_org`)."))
    return sub, org


def register(mcp: FastMCP) -> None:
    connector_verify.register(socle.CONNECTOR, _verify)

    @mcp.tool()
    def pennylane_firm_companies(company_id: Optional[int] = None,
                                 page: Optional[int] = None,
                                 per_page: Optional[int] = None,
                                 client_code: Optional[str] = None,
                                 full: bool = False) -> dict:
        """The accounting firm's portfolio in Pennylane (firm API token): one page of
        companies, or the record of one company with `company_id`. The `id` of a
        company is the firm-side `company_id` every other `pennylane_firm_*` tool
        takes — unrelated to the ids of the `pennylane` connector, the same as
        `pennylaneged`'s. Paged by number: ask page + 1 while it is below
        `total_pages`. 5 requests/s per token; on `pennylane_rate_limited`, wait 60 s.

        Args:
            company_id: firm-side id — returns that one company's record.
            page: page number, from 1.
            per_page: up to 1000.
            client_code: only the company with this client code.
            full: every field (default: id, name, siren, client_code, external_id).
        """
        c = _client()
        if company_id is not None:
            return _appel(lambda: c.get_company(company_id), "the company record")
        filtre = ([{"field": "client_code", "operator": "eq", "value": client_code}]
                  if client_code else None)
        res = _appel(lambda: c.list_companies(page=page, per_page=per_page,
                                              filter=filtre), "the list of companies")
        return _slim(res, _SOCIETE_RETIREE, full)

    @mcp.tool()
    def pennylane_firm_tree(company_id: int,
                            kind: Literal["folders", "files"] = "files",
                            parent_folder_id: Optional[int] = None,
                            cursor: Optional[str] = None,
                            limit: Optional[int] = None,
                            full: bool = False) -> dict:
        """One company's document store (GED) in Pennylane: its folders, or its files
        (one folder's with `parent_folder_id`). Items carry `id`, `name`, `path` and
        `parent_folder`; the tree is rebuilt from them. Paged by cursor: while
        `has_more`, pass `next_cursor` back as `cursor`. 5 requests/s per token.

        Args:
            company_id: firm-side id, from `pennylane_firm_companies`.
            kind: "files" (default) or "folders".
            parent_folder_id: files only — the files of this folder.
            cursor: `next_cursor` of the previous page.
            limit: items per page, up to 100.
            full: every field, download urls and dates included.
        """
        c = _client()
        if kind == "folders":
            if parent_folder_id is not None:
                raise socle.vers_mcp(Refus(
                    400, "unsupported_filter",
                    "Pennylane filters folders by id only: list them all and read "
                    "each one's `parent_folder`."))
            res = _appel(lambda: c.list_dms_folders(company_id, limit=limit,
                                                    cursor=cursor), "the folder list")
        else:
            res = _appel(lambda: c.list_dms_files(company_id, limit=limit, cursor=cursor,
                                                  parent_folder_id=parent_folder_id),
                         "the file list")
        return _slim(res, _GED_RETIREE, full)

    @mcp.tool()
    def pennylane_firm_create_folder(company_id: int, name: str,
                                     parent_folder_id: Optional[int] = None) -> dict:
        """Create a folder in one company's GED, at the root or under
        `parent_folder_id`. Pennylane cannot rename, move or delete a folder through
        the API: list the tree first and reuse an existing folder. Token scope
        dms_files:all.

        Args:
            company_id: firm-side id, from `pennylane_firm_companies`.
            name: the folder's name.
            parent_folder_id: the parent folder; omitted = the root.
        """
        c = _client()
        return _appel(lambda: c.create_dms_folder(company_id, name, parent_folder_id),
                      "the folder creation")

    @mcp.tool()
    def pennylane_firm_upload_url(company_id: int, parent_folder_id: int,
                                  name: str) -> dict:
        """Get a single-use link (15 min) to upload ONE local file into a folder of a
        company's GED: run the returned `command` with the file's path. The file
        goes from your machine to oto, which relays it to Pennylane with the firm
        token — no token on your side. Pennylane cannot delete, move or rename a
        file through the API: an upload cannot be undone. Refused when `name`
        already exists in the folder. Token scope dms_files:all.

        Args:
            company_id: firm-side id, from `pennylane_firm_companies`.
            parent_folder_id: the target folder (never the root), from
                `pennylane_firm_tree`.
            name: the file's name in the GED, extension included.
        """
        sub, org = _appelant()
        try:
            nom = relais.valider_nom(name)
            c = _client()
            socle.verifier_absent(c, org, company_id, parent_folder_id, nom)
            lien = relais.frapper(sub, org, company_id, parent_folder_id, nom)
        except Refus as e:
            raise socle.vers_mcp(e) from e
        return {
            **lien,
            "method": "POST",
            # Chemin entre guillemets DOUBLES : sans eux, curl lit un `;` ou une `,`
            # du chemin comme un séparateur d'options de `-F`.
            "command": f"curl -sS -F 'file=@\"<local path>\"' '{lien['url']}'",
            "target": {"company_id": company_id, "parent_folder_id": parent_folder_id,
                       "name": nom},
            "hint": ("Replace <local path> and run the command: the answer is the file "
                     "Pennylane created. Single use; on `pennylane_rate_limited` wait "
                     "60 s and mint a new link."),
        }
