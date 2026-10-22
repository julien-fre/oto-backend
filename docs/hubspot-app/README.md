# The instance's HubSpot app — "Connect HubSpot" in one click

`auth/hubspot.py` runs the consent; this folder is the HubSpot side of it: the
Developer Platform project an operator uploads once per instance. Nothing here is a
secret.

## What to fill before uploading

`src/app/app-hsmeta.json` carries placeholders (`<…>`): the app name the instance
shows, its support email and pages, and the **redirect URL**:
`https://<public base URL>/api/hubspot/oauth/callback`. HubSpot matches it byte for
byte — read it from the instance (`oauth_flow.redirect_uri`, i.e.
`config.public_base_url()`), never by hand. List the preprod host too.

⚠️ **`requiredScopes` and `optionalScopes` are those of `auth/hubspot.py`**
(`REQUIRED_SCOPES` / `OPTIONAL_SCOPES`, derived from `tools/hubspot_scopes.SCOPES`).
`tests/test_hubspot_oauth.py` keeps this file equal to them: change the scopes in the
code, regenerate here, upload. Changing a REQUIRED scope after the first install makes
every connected portal reconnect.

⚠️ **`isUserLevel` cannot be changed later**: HubSpot does not migrate an account-level
app to user level, a new app is needed and every portal reconnects. Its listing rules
ask "AI connector" apps for user-level access (or HubSpot's MCP server): settle it
before the first `hs project upload`.

## Steps

1. In a HubSpot developer account owned by the instance's organisation (not a
   person's): `hs project upload` from this folder (HubSpot CLI ≥ 8.15).
2. Auth tab of the app → copy the client id and secret into the instance:
   `oto_admin_connector_setting(op="set", connector="hubspot", key="client_id", value=…)`,
   then `key="client_secret"`.
3. The connector card shows **Connect HubSpot** (`connect.app_ready` true). Without
   the two settings it keeps the pasted-key form alone.
4. Until the app is listed on the Marketplace, HubSpot caps it at **25** installed
   portals (developer test accounts excepted). Listing needs 3 active installs outside
   the publishing organisation.
