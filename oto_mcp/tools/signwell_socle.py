"""Socle partagé des modules du connecteur `signwell` (documents et modèles ;
envois groupés, webhooks et compte).

Le connecteur tient sur deux modules (`Connector.modules` au registre) :
`tools/signwell.py` (documents, modèles) et `tools/signwell_envois.py` (envois
groupés, webhooks, compte). Ce fichier porte ce qu'ils ont en commun — la
résolution de la clé, la traduction d'un refus de SignWell, la vue resserrée
d'un document, la validation des arguments — pour qu'un correctif ne couvre
jamais la moitié du connecteur. Il n'a pas de `register()` : c'est un helper.

**Ce que la vue d'un document corrige.** SignWell rend le lien de signature de
chaque destinataire sous DEUX clés selon le mode : `signing_url` quand il envoie
lui-même l'invitation, `embedded_signing_url` en signature embarquée (l'autre
vaut alors null). Et rien, dans la charge, ne dit si un destinataire recevra
vraiment un courriel : un document en `test_mode` détourne TOUTES les
invitations vers le titulaire du compte (vérifié en live), un document embarqué
n'écrit qu'aux destinataires marqués `send_email`. La vue rend donc un lien
unique (`signing_link`) et un `emailed` explicite par destinataire.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from ..mcp_errors import McpError
from .. import access

if TYPE_CHECKING:  # l'annotation de `_client()` seulement — jamais évaluée
    from oto.tools.signwell import SignWellClient

#: Où l'utilisateur crée sa clé, dans SON compte SignWell.
OU_CREER_LA_CLE = "SignWell → Settings → API → « Create API key »"


def _client() -> SignWellClient:
    """Le client SignWell pour la clé de CET appelant (byo : la clé agit au nom du
    compte qui l'a créée — c'est ce nom qui figure sur les invitations).

    L'import réel est fait dans le corps : les tests remplacent le client, et la
    sonde de version-skew lit l'annotation de retour pour vérifier que les
    méthodes appelées existent dans l'oto-core épinglé.
    """
    from oto.tools.signwell import SignWellClient

    key, _is_platform = access.resolve_api_key("signwell")
    return SignWellClient(api_key=key)


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _detail(body: Any) -> str:
    """Le motif lisible d'un refus SignWell. Forme relevée en live :
    `{message, meta: {error, message, messages[]}}` — `meta.messages` porte le
    détail (champ en faute), `message` le résumé."""
    if not isinstance(body, dict):
        return str(body or "")
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    messages = meta.get("messages") or ([meta["message"]] if meta.get("message") else [])
    if isinstance(messages, list) and messages:
        return " ; ".join(str(m) for m in messages)
    return str(body.get("message") or body.get("errors") or body)


def refus(e: Any) -> str:
    """La consigne qui correspond à un refus de SignWell (`UpstreamHTTPError`)."""
    status, detail = e.status_code, _detail(e.body)
    if status == 401:
        return ("SignWell refuse cette clé (401) : elle est inconnue ou a été supprimée. "
                f"Crée une clé sur {OU_CREER_LA_CLE}, puis remplace celle de la carte "
                "SignWell de ton compte.")
    if status == 404:
        return (f"SignWell : introuvable (404){' — ' + detail if detail else ''}. "
                "L'API n'a PAS de liste des documents : un identifiant de document ne "
                "se retrouve pas, il se garde depuis la réponse qui l'a créé.")
    if status == 409:
        return f"SignWell refuse l'opération dans l'état actuel du document (409) : {detail}"
    return f"SignWell a refusé la requête (HTTP {status}) : {detail}"


def traduire(e: Any) -> Exception:
    """4xx → refus nommé (l'appel est à changer, ou la clé). 429 et 5xx restent ce
    qu'ils sont : la taxonomie d'erreurs les classe réessayables, à raison."""
    if 400 <= e.status_code < 500 and e.status_code != 429:
        return _bad(refus(e))
    return e


def _run(fn: Callable[[], Any]) -> Any:
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        return fn()
    except ValueError as e:
        raise _bad(str(e)) from None
    except UpstreamHTTPError as e:
        raise traduire(e) from None


def _hors_op(op: str, **donnes: Any) -> None:
    """Refuse un argument qui ne s'applique pas à l'`op` choisie, plutôt que de
    l'ignorer : `signwell_document(op="get", subject=…)` laisserait croire que le
    sujet a été posé.

    `None` = non passé, et c'est le SEUL « absent » : un `False` explicite est un
    argument donné, donc refusé comme les autres (`full=False` sur un `op` sans vue
    resserrée n'est pas « rien »). D'où les booléens des tools en `Optional[bool] =
    None` — un `bool = False` nu rendrait l'omission et le `False` indiscernables."""
    en_trop = sorted(k for k, v in donnes.items() if v is not None)
    if en_trop:
        raise _bad(f"op='{op}' ne prend pas {en_trop}.")


def _need(op: str, **requis: Any) -> None:
    manquants = [k for k, v in requis.items() if v is None]
    if manquants:
        raise _bad(f"op='{op}' exige {', '.join('`' + m + '`' for m in manquants)}.")


def options_valides(op: str, options: Optional[Dict[str, Any]],
                    permises: Iterable[str]) -> Dict[str, Any]:
    """`options` porte les réglages rares du spec. Une clé inconnue est REFUSÉE en
    nommant les clés permises : SignWell ignorerait une faute de frappe sans rien
    dire, et le réglage voulu ne serait jamais posé."""
    options = dict(options or {})
    permises = tuple(permises)
    inconnues = sorted(set(options) - set(permises))
    if inconnues:
        raise _bad(f"op='{op}' : options inconnues {inconnues}. Permises : {sorted(permises)}.")
    return {k: v for k, v in options.items() if v is not None}


def fichiers_valides(files: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Chaque fichier : `name` + exactement UNE source (`file_url` ou `file_base64`)."""
    if not files:
        raise _bad("`files` est requis : [{name, file_url | file_base64}].")
    for i, f in enumerate(files):
        if not isinstance(f, dict) or not f.get("name"):
            raise _bad(f"files[{i}] : `name` requis (avec son extension, ex. « nda.pdf »).")
        if bool(f.get("file_url")) == bool(f.get("file_base64")):
            raise _bad(f"files[{i}] : exactement UNE source, `file_url` OU `file_base64`.")
    return files


def destinataires_valides(recipients: Optional[List[Dict[str, Any]]], *,
                          email_requis: bool = True) -> List[Dict[str, Any]]:
    """`id` unique par destinataire (c'est lui que visent `fields` et les text tags),
    et une adresse — sauf un destinataire joint par SMS seul."""
    if not recipients:
        raise _bad("`recipients` est requis : [{id, name, email}].")
    vus = set()
    for i, r in enumerate(recipients):
        if not isinstance(r, dict) or r.get("id") in (None, ""):
            raise _bad(f"recipients[{i}] : `id` requis (« 1 », « 2 »… — c'est le numéro "
                       "de signataire des text tags).")
        rid = str(r["id"])
        if rid in vus:
            raise _bad(f"recipients : l'id « {rid} » est répété.")
        vus.add(rid)
        if email_requis and not r.get("email") and r.get("delivery_method") != "sms":
            raise _bad(f"recipients[{i}] : `email` requis (ou `delivery_method=\"sms\"`).")
    return recipients


def sans_base64(files: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Ce qu'on peut échoer d'un fichier : jamais son contenu base64."""
    return [{"name": f.get("name"),
             "source": "file_url" if f.get("file_url") else "file_base64"}
            for f in (files or []) if isinstance(f, dict)]


def _emailed(doc: Dict[str, Any], r: Dict[str, Any]) -> Optional[bool]:
    """Ce destinataire reçoit-il l'invitation par courriel ? `None` = pas encore
    décidable (brouillon non envoyé)."""
    if doc.get("test_mode"):
        return False  # détournée vers le titulaire du compte
    if doc.get("embedded_signing"):
        return bool(r.get("send_email"))
    if r.get("delivery_method") == "sms":
        return False
    if str(doc.get("status") or "").lower() == "draft":
        return None
    return True


def vue_document(doc: Any) -> Any:
    """La vue resserrée d'un document : état, mode, et pour chaque destinataire UN
    lien et un `emailed` explicite. Les champs, fichiers et règles restent dans la
    charge brute (`full=True`)."""
    if not isinstance(doc, dict):
        return doc
    recipients = []
    for r in doc.get("recipients") or []:
        if not isinstance(r, dict):
            continue
        recipients.append({
            "id": r.get("id"), "name": r.get("name"), "email": r.get("email"),
            "status": r.get("status"), "signing_order": r.get("signing_order"),
            "signing_link": r.get("embedded_signing_url") or r.get("signing_url"),
            "emailed": _emailed(doc, r),
            "bounced": r.get("bounced"),
        })
    fields = doc.get("fields") or []
    vue = {
        "id": doc.get("id"), "name": doc.get("name"), "status": doc.get("status"),
        "test_mode": doc.get("test_mode"), "embedded_signing": doc.get("embedded_signing"),
        "apply_signing_order": doc.get("apply_signing_order"),
        "created_at": doc.get("created_at"), "updated_at": doc.get("updated_at"),
        "error_message": doc.get("error_message"),
        "fields_count": sum(len(p) if isinstance(p, list) else 1 for p in fields),
        "files": [f.get("name") for f in doc.get("files") or [] if isinstance(f, dict)],
        "recipients": recipients,
    }
    notes = []
    if doc.get("test_mode"):
        notes.append("test_mode : aucune invitation ne part aux destinataires — SignWell "
                     "les envoie au TITULAIRE du compte, sujet préfixé [TEST]. Document "
                     "sans valeur juridique.")
    if str(doc.get("status") or "") in ("Created", "Sending"):
        notes.append(f"statut « {doc.get('status')} » : SignWell traite encore le document "
                     "— les champs issus des text tags arrivent en différé, et rien n'est "
                     "envoyé tant que le statut n'est pas « Sent » (ou « Draft » pour un "
                     "brouillon). Relis-le dans quelques secondes.")
    if doc.get("embedded_signing"):
        notes.append("signature embarquée : SignWell ne vérifie pas l'adresse du "
                     "signataire — quiconque détient `signing_link` peut signer. Ne le "
                     "transmettre qu'à la personne visée.")
    if notes:
        vue["notes"] = notes
    return vue
