"""Salesforce — generic CRUD over sObjects (Contact, Account…) via REST + SOQL.

Credential = OAuth2 Connected App à 3 secrets (client_id/client_secret/refresh_token)
+ `login_url` non-secret (login.salesforce.com prod, test.salesforce.com sandbox, ou
My Domain) → modèle générique multi-champs (ADR 0011), résolu par appel via
`access.resolve_credential_fields("salesforce")`. byo_user OU byo_org (pas de quota
plateforme : le credential EST le grant). Contrairement à Zoho, pas de table de
région fixe : le refresh Salesforce renvoie l'`instance_url`, mis en cache en mémoire
côté client avec l'access token.

"Companies" = l'sObject standard **Account** ; contacts = **Contact**. Surface
générique par `sobject` (comme hubspot/zoho) plutôt que des tools contact/account
dédiés — couvre aussi Lead/Opportunity/objets custom sans code supplémentaire.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .. import access, connector_flow, connector_verify, status_hints


def _login_url(login_url: Optional[str]) -> str:
    return (login_url or "").strip().rstrip("/") or "https://login.salesforce.com"


def _salesforce_credential_state(fields: dict) -> status_hints.CredentialState:
    """SOURCE UNIQUE de « ce credential Salesforce est-il utilisable ? ».

    Connexion en DEUX temps, comme Zoho : on pose la Connected App (Consumer Key +
    Secret + Login URL), puis on consent — et c'est le consentement qui produit le
    refresh_token. L'état intermédiaire est NORMAL, pas une panne : sans cette
    déclaration, `api_key_save` sonderait un credential incomplet par construction,
    refuserait la pose, et le bouton Connecter deviendrait injoignable (le blocage
    circulaire vécu sur Zoho le 28/07). Un seul libellé, rendu tel quel par toutes
    les surfaces."""
    if (fields.get("client_id") and fields.get("client_secret")
            and not fields.get("refresh_token")):
        return status_hints.CredentialState(
            complete=False, missing=("refresh_token",),
            next_action=("Connected App enregistrée, mais l'autorisation n'a pas "
                         "encore été donnée — clique « Connecter » sur la fiche du "
                         "connecteur pour ouvrir le consentement Salesforce."))
    return status_hints.CredentialState(complete=True)


def _salesforce_pending_action(sub: str, org, group, entry: dict):  # noqa: ARG001
    """Étape qui manque, pour le verdict de la fiche — le PENDANT d'affichage de
    `_salesforce_credential_state`.

    Les deux hooks sont nécessaires et ne servent pas au même moment : `register_state`
    dit à la POSE si l'incomplétude est attendue (sinon la sonde refuse d'écrire),
    celui-ci dit à la LECTURE ce qu'il reste à faire. Sans lui, la carte paraît
    configurée — l'app est bien posée — et échoue au premier appel d'outil. Calqué sur
    `zoho._pending_action_for` : même seam, même fail-open, même libellé unique rendu
    tel quel par toutes les surfaces."""
    if entry.get("mode") == "forbidden":
        return None   # rien de posé → le verdict « à connecter » suffit
    try:
        # `resolve_credential(sub=…)` : le hook tourne depuis /api/me (REST), hors
        # contexte MCP → le sub doit être EXPLICITE. `emit_on_failure=False` : sonde
        # d'affichage, elle ne doit pas fausser le signal d'usage.
        fields = access.resolve_credential(
            "salesforce", want="byo", sub=sub, emit_on_failure=False).fields
    except Exception:  # noqa: BLE001 — fail-open, jamais /api/me en erreur
        return None
    st = _salesforce_credential_state(fields)
    return None if st.complete else "Autorise oto chez Salesforce"


status_hints.register_state("salesforce", _salesforce_credential_state)
status_hints.register("salesforce", _salesforce_pending_action)


def _start_flow(ctx, values: dict) -> dict:
    """Point d'entrée du flux générique — délègue au MÊME handler que la capacité
    `me.salesforce_connect`, pour qu'il n'existe qu'une façon de démarrer."""
    from ..capabilities import salesforce_connect
    return salesforce_connect.start_for(ctx, (values.get("scope") or "member"))


# Le flux de consentement, déclaré comme celui de Zoho — c'est ce qui fait apparaître le
# bouton sur la fiche, SANS que le dashboard ait à connaître le nom « salesforce ».
connector_flow.declare(
    "salesforce",
    start=_start_flow,
    label="Autoriser oto chez Salesforce",
    callback_path="/api/salesforce/oauth/callback",
    params=(connector_flow.FlowParam(
        name="scope", label="Pour qui ?", default="member", required=False,
        help="au nom de qui ranger la connexion — org/équipe demandent d'en être admin",
        options=(("member", "moi"), ("org", "toute l'org"), ("group", "mon équipe"))),
    ),
)


def _sf_error_hint(exc: Exception) -> str:
    """Traduit l'erreur OAuth Salesforce brute en message actionnable. Utilisée
    par la sonde `_verify` (credential déjà posé) ET par le flow OAuth live
    (`salesforce_oauth.exchange_code`, échec de l'échange authorization_code) —
    les deux surfaces d'erreur Salesforce partagent le même vocabulaire brut,
    donc les mêmes branches de correspondance s'appliquent."""
    low = str(exc).lower()
    if "invalid_client" in low or "invalid_client_id" in low:
        return ("client_id / client_secret incorrect — vérifie la Connected App "
                "Salesforce (Consumer Key / Consumer Secret).")
    if "invalid_grant" in low:
        return ("refresh token périmé/révoqué, ou login_url incorrect (prod "
                "login.salesforce.com vs sandbox test.salesforce.com) — régénère-le.")
    if "invalid_scope" in low:
        return ("les OAuth Scopes de la Connected App n'incluent pas `api` et "
                "`refresh_token` (ou `offline_access`) — Setup → App Manager → "
                "ton app → Edit Policies → OAuth Scopes, puis réessaie.")
    if "redirect_uri_mismatch" in low:
        # DÉRIVÉE, jamais écrite : ce message est lu depuis la prod ET la preprod, et
        # chacune envoie sa propre redirect_uri. Une URL en dur y désignait toujours la
        # prod — donc un utilisateur de preprod lisait « doit être exactement <prod> »
        # alors que son backend envoyait autre chose. Le message accusait la victime.
        from .. import connector_flow
        attendue = connector_flow.callback_url("salesforce") or "l'URL affichée sur la fiche"
        return ("Callback URL de la Connected App incorrecte — doit être exactement "
                f"{attendue} (vérifie qu'il n'y a pas d'espace ni de slash final en trop).")
    return f"échec de connexion Salesforce : {exc}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (config: contrat de sonde, non utilisé ici)
    """Sonde SANS effet de bord, en deux temps (auth PUIS accès réel) :

    1. **refresh du token OAuth** : valide client_id + client_secret + refresh_token +
       login_url d'un coup (échec → message actionnable via `_sf_error_hint`) ;
    2. **lecture réelle** (`SELECT Id FROM Contact LIMIT 1`) : un token peut
       authentifier mais le profil/permission set de la Connected App peut ne pas
       donner accès à l'objet Contact — capté ici plutôt qu'au premier appel agent.
    """
    from oto.tools.salesforce.client import SalesforceClient

    client = SalesforceClient(
        client_id=fields.get("client_id"),
        client_secret=fields.get("client_secret"),
        refresh_token=fields.get("refresh_token"),
        login_url=_login_url(fields.get("login_url")),
    )
    try:
        client.query("SELECT Id FROM Contact LIMIT 1")
    except Exception as e:  # noqa: BLE001 — l'erreur provider EST le retour de la sonde
        raise ValueError(_sf_error_hint(e)) from e


def register(mcp: FastMCP) -> None:
    connector_verify.register("salesforce", _verify)
    from oto.tools.salesforce.client import SalesforceClient

    def _client() -> SalesforceClient:
        creds = access.resolve_credential_fields("salesforce")
        return SalesforceClient(
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
            refresh_token=creds.get("refresh_token"),
            login_url=_login_url(creds.get("login_url")),
        )

    @mcp.tool()
    def salesforce_describe(sobject: str) -> dict:
        """Field metadata for an sObject type (e.g. "Account", "Contact", or custom)."""
        return _client().describe(sobject)

    @mcp.tool()
    def salesforce_list(
        sobject: str,
        fields: Optional[str] = None,
        where: Optional[str] = None,
        limit: int = 200,
    ) -> dict:
        """List records of an sObject type (built as a SOQL SELECT).

        Args:
            sobject: e.g. "Contact", "Account" (companies), "Lead", "Opportunity".
            fields: comma-separated field names. Optional — a sensible default set
                is used per known sObject if omitted.
            where: SOQL WHERE clause without the "WHERE" keyword,
                e.g. "Industry = 'Technology'".
        """
        return _client().list_records(sobject, fields=fields, where=where, limit=limit)

    @mcp.tool()
    def salesforce_get(sobject: str, record_id: str, fields: Optional[str] = None) -> dict:
        """Get one record by id."""
        return _client().get_record(sobject, record_id, fields=fields)

    @mcp.tool()
    def salesforce_query(soql: str) -> dict:
        """Run a raw SOQL query,
        e.g. "SELECT Id, Name FROM Account WHERE Industry = 'Technology'"."""
        return _client().query(soql)

    @mcp.tool()
    def salesforce_search(sosl: str) -> dict:
        """Run a raw SOSL search,
        e.g. "FIND {Acme} IN ALL FIELDS RETURNING Account(Id, Name)"."""
        return _client().search(sosl)

    @mcp.tool()
    def salesforce_create(sobject: str, data: dict) -> dict:
        """Create a record (data = field → value).

        e.g. sobject="Contact", data={"FirstName": "Ada", "LastName": "Lovelace",
        "Email": "ada@example.com"}; sobject="Account", data={"Name": "Acme Corp"}.
        """
        return _client().create_record(sobject, data)

    @mcp.tool()
    def salesforce_update(sobject: str, record_id: str, data: dict) -> dict:
        """Update a record's fields."""
        return _client().update_record(sobject, record_id, data)

    @mcp.tool()
    def salesforce_delete(sobject: str, record_id: str) -> dict:
        """Delete a record. Irreversible."""
        return _client().delete_record(sobject, record_id)

    @mcp.tool()
    def salesforce_upsert(
        sobject: str, external_id_field: str, external_id: str, data: dict,
    ) -> dict:
        """Create-or-update a record keyed on an external id field (idempotent)."""
        return _client().upsert_record(sobject, external_id_field, external_id, data)

    @mcp.tool()
    def salesforce_notes(record_id: str) -> dict:
        """List the Enhanced Notes attached to a record (ContentNote, the
        Lightning default — not supported on orgs still on classic Notes)."""
        return {"notes": _client().list_notes(record_id)}

    @mcp.tool()
    def salesforce_create_note(record_id: str, title: str, body: str) -> dict:
        """Add an Enhanced Note (ContentNote) to a record."""
        return _client().create_note(record_id, title, body)
