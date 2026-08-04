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


# Ce qu'il faut d'un champ pour le LIRE ou l'ÉCRIRE — le reste des 57 clés que
# Salesforce renvoie par champ (aggregatable, byteLength, compoundFieldName, mask…)
# ne sert à personne côté agent.
_DESCRIBE_FIELD_KEYS = ("name", "label", "type", "length", "nillable",
                        "createable", "updateable", "referenceTo", "defaultValue")
_DESCRIBE_OBJECT_KEYS = ("name", "label", "labelPlural", "custom", "createable",
                         "updateable", "deletable", "queryable", "searchable", "keyPrefix")


def _project_describe(raw: dict) -> dict:
    """Projection resserrée d'un describe sObject (signal #339).

    Le payload brut d'un Account standard fait ~220 Ko / 45 clés (127
    childRelationships, actionOverrides, recordTypeInfos…) : trop gros pour le
    contexte d'un agent, donc tronqué et déporté en fichier par le client — donc
    inchaînable, alors que seuls 51 champs comptent. On garde l'objet + ses champs,
    `verbose=True` rend le brut à qui en a besoin."""
    fields = []
    for f in (raw.get("fields") or []):
        out = {k: f.get(k) for k in _DESCRIBE_FIELD_KEYS if f.get(k) not in (None, [], "")}
        out["name"] = f.get("name")          # toujours présent, même vide
        # Un picklist n'est utile qu'en VALEURS d'API actives (l'objet complet porte
        # label/validFor/defaultValue par entrée = 4× le poids pour rien).
        picks = [p.get("value") for p in (f.get("picklistValues") or []) if p.get("active")]
        if picks:
            out["picklistValues"] = picks
        fields.append(out)
    obj = {k: raw.get(k) for k in _DESCRIBE_OBJECT_KEYS if raw.get(k) is not None}
    obj["fields"] = fields
    obj["field_count"] = len(fields)
    obj["_note"] = ("Projection (name/label/type/length/nillable/createable/updateable/"
                    "referenceTo/picklistValues). verbose=true pour le payload Salesforce brut.")
    return obj


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
    `me.salesforce_connect`, pour qu'il n'existe qu'une façon de démarrer.

    `app` (comme `scope`) est une clé cachée, pas un `FlowParam` déclaré : le
    dashboard/front la passe hors formulaire (le client sait qui il est), elle ne
    doit jamais devenir un champ visible à l'utilisateur."""
    from ..capabilities import salesforce_connect
    return salesforce_connect.start_for(
        ctx, (values.get("scope") or "member"), values.get("app"))


# Le flux de consentement, déclaré comme celui de Zoho — c'est ce qui fait apparaître le
# bouton sur la fiche, SANS que le dashboard ait à connaître le nom « salesforce ».
# ⚠️ PAS de paramètre « Pour qui ? ». Il a existé, et c'était un pansement : la surface
# ORG n'avait pas de bouton de connexion, donc consentir pour l'org ne pouvait se faire
# que depuis la fiche PERSONNELLE, en le déclarant dans un menu. Le levier manquant a été
# posé (02/08) — le sélecteur est alors devenu une question absurde : on est sur sa fiche,
# on autorise pour soi ; on est sur la fiche de l'org, on autorise pour l'org. Le scope se
# DÉDUIT de la surface, l'appelant le passe (`values["scope"]`), on ne le demande plus.
connector_flow.declare(
    "salesforce",
    start=_start_flow,
    label="Autoriser oto chez Salesforce",
    callback_path="/api/salesforce/oauth/callback",
)


def _sf_error_hint(exc: Exception) -> str:
    """Traduit l'erreur OAuth Salesforce brute en message actionnable. Utilisée
    par la sonde `_verify` (credential déjà posé) ET par le flow OAuth live
    (`salesforce_oauth.exchange_code`, échec de l'échange authorization_code) —
    les deux surfaces d'erreur Salesforce partagent le même vocabulaire brut,
    donc les mêmes branches de correspondance s'appliquent.

    ⚠️ Une traduction AJOUTE, elle ne REMPLACE jamais. La version précédente
    substituait sa supposition au dire du fournisseur : un `invalid_grant` était
    systématiquement rendu « refresh token périmé, ou login_url incorrect », alors
    que Salesforce disait autre chose (code d'autorisation expiré, appel depuis
    une IP non autorisée…). Le message accusait la mauvaise pièce et envoyait
    corriger ce qui marchait — une heure perdue le 31/07."""
    raw = " ".join(str(exc).split())[:220]
    low = raw.lower()
    hint = _sf_hint_for(low)
    return f"{hint} (Salesforce dit : {raw})" if hint else (
        f"échec de connexion Salesforce : {raw}")


def _sf_hint_for(low: str) -> str:
    """La correspondance seule — sans le dire du fournisseur, que l'appelant joint."""
    if "invalid_client" in low or "invalid_client_id" in low:
        return ("client_id / client_secret incorrect — vérifie la Connected App "
                "Salesforce (Consumer Key / Consumer Secret).")
    if "invalid_grant" in low:
        return ("le grant a été refusé — jeton révoqué ou expiré, code d'autorisation "
                "déjà consommé, ou appel bloqué par les restrictions IP de l'app "
                "(le rafraîchissement part de NOTRE serveur, pas de ton navigateur). "
                "Le motif exact est entre parenthèses ci-dessous.")
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
    return ""


def _verify(fields: dict, config: dict | None = None,
            instance: tuple | None = None) -> None:  # noqa: ARG001 (config: contrat de sonde, non utilisé ici)
    """Sonde en deux temps (auth PUIS accès réel) :

    1. **refresh du token OAuth** : valide client_id + client_secret + refresh_token +
       login_url d'un coup (échec → message actionnable via `_sf_error_hint`) ;
    2. **lecture réelle** (`SELECT Id FROM Contact LIMIT 1`) : un token peut
       authentifier mais le profil/permission set de la Connected App peut ne pas
       donner accès à l'objet Contact — capté ici plutôt qu'au premier appel agent.

    ⚠️ **Elle n'est plus « sans effet de bord », et ne peut pas l'être.** Sous rotation
    (RTR, imposée par Salesforce), l'étape 1 consomme le refresh token et en reçoit un
    neuf : une sonde qui ne persiste pas ce remplaçant DÉTRUIT la connexion qu'elle
    prétend vérifier. C'est ce qui s'est produit le 31/07 — la sonde post-écriture de
    `persist_token` tuait le jeton 500 ms après sa pose. Elle branche donc la même
    persistance que le chemin des outils, quand elle porte sur un credential déjà
    stocké.
    """
    from oto.tools.salesforce.client import SalesforceClient

    client = SalesforceClient(
        client_id=fields.get("client_id"),
        client_secret=fields.get("client_secret"),
        refresh_token=fields.get("refresh_token"),
        login_url=_login_url(fields.get("login_url")),
        # `instance` = la clé RÉELLEMENT sondée, fournie par l'appelant. Sans elle on
        # ne peut que deviner via la cascade — qui désigne la plus proche, pas celle
        # qu'on teste : un `verify level=org` chez quelqu'un qui a AUSSI une clé perso
        # comparait le jeton d'org au jeton perso, ne reconnaissait pas, ne persistait
        # rien, et tuait donc le jeton d'org en le rafraîchissant. Vécu 03/08.
        on_refresh=_rotation_writer_for(fields.get("refresh_token") or "", instance),
    )
    try:
        client.query("SELECT Id FROM Contact LIMIT 1")
    except Exception as e:  # noqa: BLE001 — l'erreur provider EST le retour de la sonde
        raise ValueError(_sf_error_hint(e)) from e


class _Cible:
    """L'entité sondée, sous la forme attendue par `_rotation_writer`."""

    def __init__(self, entity_type, entity_id, account=""):
        self.entity_type, self.entity_id, self.account = entity_type, entity_id, account


def _rotation_writer_for(jeton_lu: str, instance: tuple | None = None):
    """Le writer de rotation pour la SONDE.

    Quand l'appelant DIT quelle entité il teste (`instance`), on écrit là — sans
    deviner. C'est le cas nominal, et le seul correct dès qu'il existe plusieurs clés
    pour un même connecteur : la cascade désigne la plus PROCHE, pas celle qu'on sonde.

    Sinon (appelants qui ne le fournissent pas encore) on retombe sur la cascade, et
    on ne branche l'écriture que si le credential résolu porte bien le jeton qu'on
    s'apprête à consommer. Deux cas où l'on ne persiste rien, volontairement :

    - **sonde avant persistance** (`api_key_save`) : les champs testés sont des
      candidats, aucune ligne ne les porte encore — il n'y a rien à mettre à jour ;
    - **hors contexte de requête** (CLI, test) : pas d'org, donc pas de cascade.

    Dans les deux cas on retombe sur l'ancien comportement (aucune écriture), ce qui
    est correct : on ne peut pas corrompre ce qu'on n'a pas identifié.
    """
    from .. import access

    if instance is not None:
        etype, eid, *reste = instance
        return _rotation_writer(_Cible(etype, eid, reste[0] if reste else ""), jeton_lu)
    try:
        rc = access.resolve_credential("salesforce", emit_on_failure=False)
    except Exception:  # noqa: BLE001 — pas de credential résolu = rien à persister
        return None
    if rc.entity_type is None or (rc.fields or {}).get("refresh_token") != jeton_lu:
        return None
    return _rotation_writer(rc, jeton_lu)


def _rotation_writer(rc, jeton_lu: str):
    """Persiste le refresh token RENOUVELÉ, là où l'ancien a été lu.

    Salesforce impose la rotation (RTR) sur les External Client Apps : chaque
    rafraîchissement invalide le jeton utilisé et en renvoie un neuf. Ne pas
    l'écrire revient à révoquer la connexion au premier appel — et à la faire
    révoquer *complètement* au second, Salesforce traitant la réutilisation d'un
    jeton consommé comme une compromission (révocation du jeton courant ET des
    access tokens associés).

    ⚠️ **Écriture conditionnelle**, pas un écrasement : on ne réécrit que si le
    jeton stocké est toujours celui qu'on a lu. Deux appels concurrents (ou la
    preprod, qui partage cette base avec la prod) peuvent avoir tourné entre-temps ;
    écraser aveuglément remettrait en place un jeton déjà consommé, c'est-à-dire
    exactement le geste que Salesforce interprète comme une attaque.
    """
    from .. import credentials_store

    def _write(token_data: dict) -> None:
        nouveau = token_data.get("refresh_token")
        # Pas de rotation, ou grant plateforme (pas de ligne de coffre à réécrire).
        if not nouveau or nouveau == jeton_lu or rc.entity_type is None:
            return
        row = credentials_store.get_credential_with_meta(
            rc.entity_type, rc.entity_id, "salesforce", account=rc.account)
        if not row or not row.get("secret"):
            return
        champs = credentials_store.unpack_secret("salesforce", row["secret"])
        if champs.get("refresh_token") != jeton_lu:
            return  # quelqu'un d'autre a déjà tourné : sa valeur est plus récente
        # ⚠️ `meta` DOIT être repassé. L'upsert fait `meta = EXCLUDED.meta` avec
        # `json.dumps(meta or {})` : omettre l'argument n'est pas « ne pas toucher au
        # meta », c'est l'ÉCRASER par {}. Comme la rotation réécrit à chaque appel
        # d'outil, la version précédente effaçait `instance_url`/`identity_url`/
        # `connected_at` dès le premier usage — on ne savait alors plus sur quelle org
        # Salesforce la clé pointait. Repéré le 03/08, sur une clé qui avait tourné
        # depuis la veille pendant qu'une clé fraîche avait encore son meta intact.
        credentials_store.set_credential(
            rc.entity_type, rc.entity_id, "salesforce",
            credentials_store.pack_secret("salesforce",
                                          {**champs, "refresh_token": nouveau}),
            account=rc.account, meta=row.get("meta") or {})

    return _write


def register(mcp: FastMCP) -> None:
    connector_verify.register("salesforce", _verify)
    from oto.tools.salesforce.client import SalesforceClient

    def _client() -> SalesforceClient:
        # On passe par `resolve_credential` (et non `resolve_credential_fields`) parce
        # qu'on a besoin de l'ENTITÉ gagnante de la cascade : sous rotation, il faut
        # réécrire le jeton renouvelé exactement là où il a été lu — clé membre, clé
        # d'équipe ou clé d'org — sinon on le range au mauvais niveau.
        rc = access.resolve_credential("salesforce")
        creds = rc.fields
        return SalesforceClient(
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
            refresh_token=creds.get("refresh_token"),
            login_url=_login_url(creds.get("login_url")),
            on_refresh=_rotation_writer(rc, creds.get("refresh_token") or ""),
        )

    @mcp.tool()
    def salesforce_describe(sobject: str, verbose: bool = False) -> dict:
        """Field metadata for an sObject type (e.g. "Account", "Contact", or custom).

        Returns a TIGHT projection: the object's own flags + one entry per field with
        what you need to read or write it (name, label, type, length, nillable,
        createable, updateable, referenceTo, picklist values). Salesforce's raw
        describe is ~220 KB for a standard Account (127 childRelationships,
        actionOverrides, recordTypeInfos, 57 keys per field) — too big to chain on.

        Args:
            sobject: e.g. "Account", "Contact", or a custom "Foo__c".
            verbose: True → the RAW Salesforce payload, unprojected. Only when you
                need something the projection drops (child relationships, layouts);
                expect it to be truncated by the client.
        """
        raw = _client().describe(sobject)
        return raw if verbose else _project_describe(raw)

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
