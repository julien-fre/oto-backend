"""Microsoft 365 directory — an organization's Microsoft Entra ID via Microsoft Graph
(`DirectoryClient`): accounts and licences, subscriptions, groups and members, sites
read; licences, enabled accounts and group members written.

Credential = the PERSON's Microsoft 365 account (carrier `microsoft`, OAuth, delegated
permissions) that has authorized THIS service (scopes `DIRECTORY`, approved by a tenant
administrator — never by the default approval, `auth/microsoft.ADMIN_ONLY_SERVICES`);
her own Microsoft role bounds what Graph returns.

**Surface** — the pair of every connector: `entra` reads (`lecture.LECTURE`), `entra_act`
acts (`lecture.ACTE`) and does only three things — assign or remove a licence, enable or
disable an account, add or remove a group member. No account is created or deleted, no
password touched: that stays in the Microsoft 365 admin centre (decision of 21/10/2026).

Protocol facts the tool carries (`oto.tools.microsoft.directory`): a licence is a
`skuId` named by the tenant's subscriptions; the last sign-in needs Entra ID P1 and a
reporting role, or Graph refuses the whole listing; a team site's members are its
group's, SharePoint's own permissions are not listed; a licence needs the user's usage
location; a distribution list's members are Exchange's, Graph does not change them.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .. import output_projection
from ._microsoft_graph import (bad, client_for, need, raise_refusal, raw, refuse_ignored,
                               run)
from .lecture import ACTE, LECTURE

#: The Microsoft service this tool calls: its scopes, its card (`auth/microsoft`).
_SERVICE = "entra"
_LABEL = "Microsoft 365 directory"

_OPS = ("licenses", "users", "user", "groups", "group", "sites")
_ACT_OPS = ("add_license", "remove_license", "enable_account", "disable_account",
            "add_member", "remove_member")
#: The groups whose members Graph cannot change (Exchange's).
_EXCHANGE_KINDS = ("distribution", "mail_security")
#: How many accounts or groups are read before filtering — far above an SMB's directory.
_READ_CEILING = 5000

_SIGN_IN_REFUSED = (
    "Microsoft refuses the last sign-in (HTTP 403): it needs an Entra ID P1 licence on "
    "the organization (Business Premium, E3/E5 — not Business Basic or Standard) and a "
    "reporting role for the connected account. Retry without `sign_in`.")


def _ops_error(ops: tuple = _OPS) -> str:
    return "op must be " + ", ".join(f"'{o}'" for o in ops[:-1]) + f" or '{ops[-1]}'"


def _contient(valeur: Optional[str], cherche: str) -> bool:
    return cherche in (valeur or "").lower()


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.microsoft import DirectoryClient
    from oto.tools.microsoft.directory import SKU_NAMES, group_kind

    def _client() -> DirectoryClient:
        """The Graph client of THIS caller, with their current token."""
        return client_for(DirectoryClient, _SERVICE)

    def _run(fn):
        return run(fn, _LABEL)

    def _nom(s: dict) -> str:
        """A subscription's commercial name, its part number when unknown."""
        return SKU_NAMES.get(s.get("skuPartNumber"), s.get("skuPartNumber")) or ""

    def _noms(abonnements: list) -> dict:
        """`skuId` → the licence's name, from the tenant's subscriptions."""
        return {s.get("skuId"): _nom(s) for s in abonnements}

    def _licences(u: dict, noms: dict) -> list:
        return [noms.get(l.get("skuId"), l.get("skuId")) for l in u.get("assignedLicenses") or []]

    def _compte(u: dict, noms: dict) -> dict:
        vue = {"id": u.get("id"), "name": u.get("displayName"),
               "email": u.get("userPrincipalName"), "mail": u.get("mail"),
               "enabled": u.get("accountEnabled"), "type": u.get("userType"),
               "jobTitle": u.get("jobTitle"), "department": u.get("department"),
               "city": u.get("city"), "office": u.get("officeLocation"),
               "created": u.get("createdDateTime"), "usageLocation": u.get("usageLocation"),
               "licenses": _licences(u, noms)}
        if "signInActivity" in u:
            a = u.get("signInActivity") or {}
            vue.update(lastSignIn=a.get("lastSignInDateTime"),
                       lastNonInteractiveSignIn=a.get("lastNonInteractiveSignInDateTime"))
        return vue

    def _groupe(g: dict) -> dict:
        return {"id": g.get("id"), "name": g.get("displayName"), "kind": group_kind(g),
                "mail": g.get("mail"),
                "team": "Team" in (g.get("resourceProvisioningOptions") or []),
                "visibility": g.get("visibility"), "description": g.get("description")}

    def _membre(m: dict) -> dict:
        return {"id": m.get("id"), "name": m.get("displayName"),
                "email": m.get("userPrincipalName") or m.get("mail"),
                "enabled": m.get("accountEnabled"),
                "type": (m.get("@odata.type") or "").rsplit(".", 1)[-1] or None}

    @mcp.tool(name="entra", annotations=LECTURE)
    def entra(
        op: Literal["licenses", "users", "user", "groups", "group", "sites"] = "licenses",
        user: Optional[str] = None,
        group_id: Optional[str] = None,
        query: Optional[str] = None,
        enabled: Optional[bool] = None,
        licensed: Optional[bool] = None,
        license: Optional[str] = None,
        type: Optional[Literal["Member", "Guest"]] = None,
        kind: Optional[Literal["microsoft365", "security", "distribution",
                               "mail_security"]] = None,
        sign_in: bool = False,
        limit: int = 500,
        full: bool = False,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """READ the organization's Microsoft 365 directory — for its administrators. Nothing
        here changes anything: a licence, an account or a group is changed in the
        Microsoft 365 admin centre. What comes back is bounded by the connected person's
        Microsoft role.

        `op`:
        - **"licenses"** (default): the subscriptions — {licenses: [{sku, name, bought,
          assigned, free, status}], count}.
        - **"users"**: the accounts, members and guests — {users: [{id, name, email,
          mail, enabled, type, jobTitle, department, city, office, created, licenses}],
          count}. Filters: `enabled`, `licensed`, `license` (part of a licence name, e.g.
          "Business Standard"), `type`, `query` (part of the name, address or
          department). `sign_in=True` adds `lastSignIn` and `lastNonInteractiveSignIn`
          — Microsoft needs an Entra ID P1 licence and a reporting role for it.
        - **"user"**: one account by `user` (id or address), with its licences, its
          manager and the groups it is a direct member of.
        - **"groups"**: the groups — {groups: [{id, name, kind, mail, team, visibility,
          description}], count}; `kind`: microsoft365 (a team, a team site),
          security, distribution (a mailing list), mail_security. `query` filters on
          the name or address.
        - **"group"**: one group by `group_id`, with its members, its owners and, for a
          Microsoft 365 group, its team site. Access granted inside SharePoint itself
          is not listed.
        - **"sites"**: the SharePoint sites the connected person can see; `query`
          searches them.

        Args:
            op: licenses (default) | users | user | groups | group | sites.
            user: op="user" — the account's id or address.
            group_id: op="group" — the group's id.
            query: op="users"/"groups"/"sites" — part of a name or address (sites: a
                search).
            enabled: op="users" — True: active accounts only, False: disabled ones.
            licensed: op="users" — True: with at least one licence, False: none.
            license: op="users" — accounts holding a licence whose name contains it.
            type: op="users" — Member or Guest.
            kind: op="groups" — microsoft365 | security | distribution | mail_security.
            sign_in: op="users" — add the last sign-in dates.
            limit: op="users"/"groups"/"sites" — max items returned (default 500).
            full: return Graph's raw objects instead of the trimmed view.
            fields: op="users"/"groups"/"sites"/"licenses" — keep only these keys.
        """
        # Refusal BEFORE any credential resolution: an unknown op never reaches the client.
        if op not in _OPS:
            raise bad(_ops_error())
        if fields is not None and op in ("user", "group"):
            raise bad(f"`fields` only applies to the listing ops, not op='{op}'.")
        if op != "users" and (enabled is not None or licensed is not None
                              or license is not None or type is not None or sign_in):
            raise bad(f"`enabled`/`licensed`/`license`/`type`/`sign_in` only apply to "
                      f"op='users' (got op='{op}').")
        if op != "groups" and kind is not None:
            raise bad(f"`kind` only applies to op='groups' (got op='{op}').")
        if query is not None and op not in ("users", "groups", "sites"):
            raise bad(f"op='{op}' does not use `query`.")

        def _liste(cle: str, items: list) -> dict:
            out = {cle: items[:limit], "count": len(items[:limit])}
            if len(items) > limit:
                out["truncated"] = True
            return output_projection.project(out, items_path=cle, fields=fields)

        if op == "licenses":
            refuse_ignored(op, user=user, group_id=group_id)
            abonnements = _run(_client().list_subscriptions)
            if full:
                return _liste("licenses", abonnements)
            vues = []
            for s in abonnements:
                achete = (s.get("prepaidUnits") or {}).get("enabled")
                utilise = s.get("consumedUnits")
                vues.append({"sku": s.get("skuPartNumber"),
                             "name": _nom(s),
                             "bought": achete, "assigned": utilise,
                             "free": (achete - utilise) if isinstance(achete, int)
                             and isinstance(utilise, int) else None,
                             "status": s.get("capabilityStatus")})
            return _liste("licenses", vues)

        if op == "users":
            refuse_ignored(op, user=user, group_id=group_id)
            c = _client()
            try:
                comptes = c.list_users(sign_in=sign_in, limit=_READ_CEILING)
            except UpstreamHTTPError as e:
                if sign_in and e.status_code == 403:
                    raise bad(_SIGN_IN_REFUSED) from e
                raise_refusal(e, _LABEL)
            noms = _noms(_run(c.list_subscriptions))
            cherche, licence = (query or "").lower(), (license or "").lower()
            gardes = []
            for u in comptes:
                portees = _licences(u, noms)
                if enabled is not None and bool(u.get("accountEnabled")) != enabled:
                    continue
                if licensed is not None and bool(portees) != licensed:
                    continue
                if licence and not any(licence in (n or "").lower() for n in portees):
                    continue
                if type is not None and u.get("userType") != type:
                    continue
                if cherche and not any(_contient(u.get(k), cherche) for k in (
                        "displayName", "userPrincipalName", "mail", "department")):
                    continue
                gardes.append(raw(u) if full else _compte(u, noms))
            return _liste("users", gardes)

        if op == "user":
            refuse_ignored(op, group_id=group_id)
            qui = need(user, "user", op)
            c = _client()
            u = _run(lambda: c.get_user(qui))
            groupes = _run(lambda: c.list_user_groups(qui))
            if full:
                return {**u, "groups": groupes}
            m = u.get("manager") or None
            return {**_compte(u, _noms(_run(c.list_subscriptions))),
                    "manager": {"id": m.get("id"), "name": m.get("displayName"),
                                "mail": m.get("mail")} if m else None,
                    "groups": [_groupe(g) for g in groupes]}

        if op == "groups":
            refuse_ignored(op, user=user, group_id=group_id)
            cherche = (query or "").lower()
            groupes = _run(lambda: _client().list_groups(limit=_READ_CEILING))
            gardes = [raw(g) if full else _groupe(g) for g in groupes
                      if (kind is None or group_kind(g) == kind)
                      and (not cherche or _contient(g.get("displayName"), cherche)
                           or _contient(g.get("mail"), cherche))]
            return _liste("groups", gardes)

        if op == "group":
            refuse_ignored(op, user=user)
            gid = need(group_id, "group_id", op)
            c = _client()
            g = _run(lambda: c.get_group(gid))
            membres = _run(lambda: c.list_group_members(gid))
            proprietaires = _run(lambda: c.list_group_members(gid, owners=True))
            site = (_run(lambda: c.get_group_site(gid))
                    if group_kind(g) == "microsoft365" else None)
            if full:
                return {**g, "members": membres, "owners": proprietaires, "site": site}
            return {**_groupe(g), "members": [_membre(m) for m in membres],
                    "owners": [_membre(m) for m in proprietaires],
                    "site": {"id": site.get("id"), "name": site.get("displayName"),
                             "webUrl": site.get("webUrl")} if site else None}

        refuse_ignored(op, user=user, group_id=group_id)
        sites = _run(lambda: _client().list_sites(query=query or "*", limit=limit))
        return _liste("sites", [raw(s) if full else {
            "id": s.get("id"), "name": s.get("displayName"), "webUrl": s.get("webUrl"),
            "created": s.get("createdDateTime")} for s in sites])

    def _abonnement(abonnements: list, license: str) -> dict:
        """The subscription `license` names: its `skuId`, its part number or its name —
        exactly, or by a part of the name when only one matches."""
        cherche = license.strip().lower()
        for s in abonnements:
            if cherche in ((s.get("skuId") or "").lower(),
                           (s.get("skuPartNumber") or "").lower(), _nom(s).lower()):
                return s
        proches = [s for s in abonnements if cherche in _nom(s).lower()]
        if len(proches) == 1:
            return proches[0]
        noms = sorted(_nom(s) for s in (proches or abonnements))
        raise bad(f"`license` {license!r} names {'several' if proches else 'none'} of the "
                  f"organization's subscriptions: {', '.join(noms) or 'none'}. Nothing was "
                  f"changed.")

    @mcp.tool(name="entra_act", annotations=ACTE)
    def entra_act(
        op: Literal["add_license", "remove_license", "enable_account", "disable_account",
                    "add_member", "remove_member"],
        user: Optional[str] = None,
        license: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> dict:
        """CHANGE the organization's Microsoft 365 directory: ⚠️ every op takes effect at
        once for the person concerned. Only three things: a licence, an account enabled
        or not, a group's members. Creating or deleting an account and passwords stay in
        the Microsoft 365 admin centre. Reading is `entra`. Microsoft checks the
        connected person's role on every call: License Administrator for licences, User
        Administrator for accounts, Groups Administrator (or the group's owner) for
        members.

        `op` (required):
        - **"add_license"** / **"remove_license"**: gives `user` the subscription
          `license`, or takes it back — named as `entra(op="licenses")` shows it (its
          name, e.g. "Business Standard", or its `sku`). Adding needs a free licence
          and the user's usage location (set in the admin centre). Removing a licence
          cuts the services it brings (mailbox, OneDrive, Teams…) once Microsoft's
          grace period ends. Returns {user, license, licenses}.
        - **"enable_account"** / **"disable_account"**: `user` can sign in again, or no
          longer can; data, licences and groups stay. Returns {user, enabled}.
        - **"add_member"** / **"remove_member"**: `user` joins or leaves the group
          `group_id` — for a Microsoft 365 group, its team and its SharePoint team site.
          Only security and Microsoft 365 groups: a distribution list's members are
          changed in Exchange. Returns {user, group, member}.

        Args:
            op: add_license | remove_license | enable_account | disable_account |
                add_member | remove_member.
            user: the account — its id or address (`entra(op="users")`).
            license: op="add_license"/"remove_license" — the subscription.
            group_id: op="add_member"/"remove_member" — the group's id
                (`entra(op="groups")`).
        """
        if op not in _ACT_OPS:
            raise bad(_ops_error(_ACT_OPS))
        qui = need(user, "user", op)

        if op in ("add_license", "remove_license"):
            refuse_ignored(op, group_id=group_id)
            nom = need(license, "license", op)
            c = _client()
            abonnements = _run(c.list_subscriptions)
            abonnement = _abonnement(abonnements, nom)
            sku = abonnement.get("skuId")
            libelle = _nom(abonnement)
            if op == "add_license":
                achete = (abonnement.get("prepaidUnits") or {}).get("enabled")
                if isinstance(achete, int) and abonnement.get("consumedUnits", 0) >= achete:
                    raise bad(f"No free {libelle} licence: {achete} bought, all assigned. "
                              f"Buy one, or remove it from another account. Nothing was "
                              f"changed.")
                u = _run(lambda: c.assign_licenses(qui, add=[sku]))
            else:
                u = _run(lambda: c.assign_licenses(qui, remove=[sku]))
            return {"user": qui, "license": libelle,
                    "licenses": _licences(u or {}, _noms(abonnements))}

        if op in ("enable_account", "disable_account"):
            refuse_ignored(op, license=license, group_id=group_id)
            actif = op == "enable_account"
            c = _client()
            _run(lambda: c.set_account_enabled(qui, actif))
            return {"user": qui, "enabled": actif}

        refuse_ignored(op, license=license)
        gid = need(group_id, "group_id", op)
        c = _client()
        g = _run(lambda: c.get_group(gid))
        if group_kind(g) in _EXCHANGE_KINDS:
            raise bad(f"{g.get('displayName') or gid} is a {group_kind(g).replace('_', ' ')} "
                      f"group: its members are changed in Exchange (admin centre), not "
                      f"through Microsoft Graph. Nothing was changed.")
        membre = _run(lambda: c.get_user(qui)).get("id")
        if op == "add_member":
            _run(lambda: c.add_group_member(gid, membre))
        else:
            _run(lambda: c.remove_group_member(gid, membre))
        return {"user": qui, "group": g.get("displayName") or gid,
                "member": op == "add_member"}
