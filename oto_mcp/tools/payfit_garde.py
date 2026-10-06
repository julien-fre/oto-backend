"""PayFit — le client, les gardes d'arguments, la traduction des erreurs amont, le
rendu d'un fichier et la sonde, partagés par tous les modules d'outils du connecteur.

Séparés des modules d'outils pour tenir sous 500 lignes. Cinq règles vivent ici :

- **aucun argument n'est retenu au silence** : « fourni » se lit `is not None`,
  jamais la vérité — `fr=False` et `limit=0` sont des valeurs fournies ;
- **une erreur amont se classe sur `status_code`**, jamais sur le texte du message ;
- **une clé vide est refusée AVANT le client** : passée vide, `PayfitClient`
  résoudrait `PAYFIT_API_KEY` dans l'environnement du SERVEUR et travaillerait sur
  une autre entreprise que celle dont la clé est posée ;
- **aucune écriture n'est câblée** : toute op d'écriture rend le refus nommé
  `payfit_write_not_wired`, sans résoudre la clé ni appeler PayFit (24/09/2026) ;
- **un document ne sort que si la politique de l'org ne masque rien** : un PDF ou un
  fichier ne se filtre pas, donc il est verrouillé tant que les masques PayFit ne
  sont pas levés (`serve_document`). Un EXTRAIT verbatim d'un document (la ligne
  brute d'un bulletin) suit le même verrou (`documents_unlocked`).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from . import ecriture_non_cablee

if TYPE_CHECKING:
    from oto.tools.payfit import PayfitClient

_NAME = "payfit"
logger = logging.getLogger(__name__)
DEFAULT_LIMIT = 50


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _client() -> PayfitClient:
    """Le client pour la clé de CET appelant. L'import réel est dans le corps : les
    tests remplacent la classe du package."""
    from oto.tools.payfit import PayfitClient

    key, _ = access.resolve_api_key(_NAME)
    if not isinstance(key, str) or not key.strip():
        # Vide, le client irait chercher une clé dans l'environnement du serveur.
        raise _bad("PayFit : aucune clé API posée pour ce connecteur.")
    return PayfitClient(api_key=key.strip())


def run(fn: Callable[[], Any]) -> Any:
    """Exécute un appel au client ; une erreur de validation ou amont devient une
    consigne `INVALID_PARAMS`."""
    from oto.tools.common.errors import UpstreamHTTPError

    try:
        return fn()
    except ValueError as e:
        raise _bad(str(e)) from None
    except UpstreamHTTPError as e:
        raise _bad(upstream_message(e)) from None


def need(op: str, **required: Any) -> None:
    missing = [n for n, v in required.items() if v is None or v == ""]
    if missing:
        raise _bad(f"op={op!r} exige {', '.join('`' + m + '`' for m in missing)}.")


def refuse_ignored(op: str, **provided: Any) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention.
    `is not None`, jamais la vérité : `False` et `0` sont des valeurs fournies."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} n'utilise pas `{name}`.")


def limit_or_default(limit: Optional[int]) -> int:
    return DEFAULT_LIMIT if limit is None else limit


def refuse_unknown_op(op: str, *allowed: str) -> McpError:
    return _bad(f"op={op!r} inconnu — attendu {', '.join(repr(a) for a in allowed)}.")


# ---------------------------------------------------------------------------
# Écriture : NON CÂBLÉE, jamais (décision du 24/09/2026)
# ---------------------------------------------------------------------------

def not_wired(op: str, action: str, **what: Any) -> McpError:
    """Le refus de TOUTE écriture PayFit : rien n'est envoyé, quel que soit l'argument.
    La capacité d'écrire n'existe pas dans le connecteur tant qu'on ne la décide pas —
    pas d'interrupteur d'org, pas d'activation par un org_admin. Ni la clé ni le
    client ne sont touchés : le refus ne dépend de rien. Forme commune aux connecteurs
    en lecture seule : `ecriture_non_cablee.refus`.

    `what` décrit l'action à l'agent. L'appelant n'y met que des identifiants, des
    dates et des libellés — jamais une valeur masquée par défaut (NIR, IBAN, motif
    d'absence), qui finirait dans le journal des appels."""
    return ecriture_non_cablee.refus(_NAME, "PayFit", op, action, **what)


# ---------------------------------------------------------------------------
# La mention de rédaction : ce que la politique EFFECTIVE masque, pas ce qu'elle
# masquerait par défaut
# ---------------------------------------------------------------------------

def redaction_notice() -> str:
    """La mention servie avec chaque réponse qui peut porter un champ sensible.

    Elle dit ce que la politique EFFECTIVE de l'appelant masque — la même cascade
    que la sortie (`access.resolve_field_filter` : politique de l'org active, sinon
    le défaut serveur). Une mention constante annonçait « NIR, IBAN/BIC masqués »
    même quand l'org avait levé la règle (signal oto #1269) : l'agent croyait servir
    une donnée protégée, et le disait à l'utilisateur.

    Les champs sensibles sont ceux du défaut serveur (`SERVER_DEFAULTS["payfit"]`),
    nommés comme ils sortent : c'est sous ces noms que l'agent les lit. Une politique
    illisible LÈVE : la sortie est de toute façon retenue (`redaction.redact_payload`)."""
    from .. import field_filter_defaults

    ff = access.resolve_field_filter(_NAME)
    clairs = field_filter_defaults.champs_du_plancher_en_clair(_NAME, ff)
    masques = [c for c in field_filter_defaults.champs_du_plancher(_NAME) if c not in clairs]
    lever = ("un org_admin règle la politique de ce connecteur (dashboard, ou "
             "`oto_org_settings domain=field_filters service=payfit`)")
    if not clairs:
        return (f"Rédaction effective pour PayFit (défaut serveur, ou politique de "
                f"l'org) : {', '.join(masques)} sont masqués. Ce n'est pas une absence "
                f"de donnée — {lever}. `absence_category` reste lisible dans tous les "
                f"cas.")
    reste = (f"Restent masqués : {', '.join(masques)}." if masques
             else "Aucun champ sensible n'est masqué.")
    return (f"Rédaction effective pour PayFit : la politique de l'org laisse EN CLAIR "
            f"{', '.join(clairs)} — servis tels que PayFit les rend. {reste} Pour "
            f"changer cela, {lever}.")


# ---------------------------------------------------------------------------
# Documents : bulletin PDF, export comptable, fichier de virement, document
# ---------------------------------------------------------------------------

# Refus servis à l'agent : ils disent POURQUOI et QUI peut ouvrir — jamais un
# détour. Un agent à qui l'on suggère une autre voie la prend.
DOCUMENTS_LOCKED = (
    "PayFit : document non servi. Il contient des données par salarié (NIR, IBAN, "
    "noms et montants) que la politique de filtres de champs de ton org masque pour "
    "PayFit — et un filtre ne peut pas masquer l'intérieur d'un fichier. Pour ouvrir "
    "les documents PayFit, un org_admin de l'org doit lever les masques du "
    "connecteur `payfit` (politique sans aucune règle). ⚠️ Ce geste rend AUSSI le "
    "NIR, l'IBAN/BIC et le motif d'absence EN CLAIR dans toutes les réponses JSON "
    "PayFit, pour tous les membres de l'org : ce n'est pas une ouverture des seuls "
    "documents.")
DOCUMENTS_POLICY_UNREADABLE = (
    "PayFit : document non servi. La politique de filtres de champs de ton org n'a "
    "pas pu être lue, et un document qui porte NIR ou IBAN ne sort pas sans elle. "
    "Réessaie dans un instant ; si ça persiste, c'est un incident côté oto.")
OVERTIME_LINE_LOCKED = (
    "PayFit : la ligne brute du bulletin (`line`) n'est pas servie. C'est du texte "
    "du document, qu'un filtre de champs ne voit pas, et la politique de filtres de "
    "champs de ton org masque des champs PayFit. `kind`, `label`, `numbers` et "
    "`rates` restent servis, filtrés par cette politique. Pour obtenir la ligne "
    "brute, un org_admin de l'org doit lever les masques du connecteur `payfit` "
    "(politique sans aucune règle) — ce qui rend AUSSI le NIR, les coordonnées "
    "bancaires et le motif d'absence EN CLAIR dans toutes les réponses JSON PayFit.")


def documents_open() -> bool:
    """Le VERROU des documents : vrai seulement si la politique EFFECTIVE de
    l'appelant pour `payfit` ne masque RIEN.

    La politique se lit par le mécanisme existant, `access.resolve_field_filter` —
    la même cascade que la sortie JSON (politique de l'org active, sinon le plancher
    serveur). Sans politique d'org, c'est le plancher qui s'applique : il masque NIR,
    IBAN, BIC et `absence_type`, donc le verrou est fermé. Il ne s'ouvre que sur une
    politique d'org VIDE (`rules: []`, autoritaire).

    ⚠️ Pourquoi « ne masque rien » et pas « ne masque pas le NIR » : un fichier ne se
    filtre pas du tout. Une org qui a posé N'IMPORTE QUELLE règle sur `payfit` a dit
    qu'un champ ne doit pas sortir ; le PDF qui le contient le ferait sortir quand
    même. Le seul état où un document respecte la politique est celui où elle ne
    retire rien."""
    return access.resolve_field_filter(_NAME).is_empty


def documents_unlocked() -> bool:
    """`documents_open`, **fail-closed** : une politique illisible (base
    indisponible…) est un refus nommé, jamais un verrou ouvert — exactement comme
    `redaction.redact_payload` retient la sortie JSON d'un service à défaut serveur.
    À lire AVANT tout appel amont : ce qu'on ne servira pas ne se télécharge pas."""
    try:
        return documents_open()
    except Exception as e:  # noqa: BLE001 — refus nommé, fail-closed
        logger.warning("payfit : politique de filtres illisible, document refusé",
                       exc_info=True)
        raise _bad(DOCUMENTS_POLICY_UNREADABLE) from e


def serve_document(fetch: Callable[[], dict]) -> dict:
    """Le SEUL chemin d'un document PayFit vers l'agent : verrou, PUIS appel amont,
    PUIS rendu. Le document n'est pas même téléchargé quand le verrou est fermé, ni
    quand la politique est illisible (`documents_unlocked`).

    Le rendu passe par `file_content.render_for_agent`, domicile unique de la règle
    inline-vs-URL signée."""
    from .. import file_content

    if not documents_unlocked():
        raise _bad(DOCUMENTS_LOCKED)
    blob = run(fetch)
    sub = access.current_user_sub_or_raise()
    try:
        return file_content.render_for_agent(
            blob.get("data") or b"", blob.get("filename") or "payfit",
            blob.get("mimetype") or "application/octet-stream",
            sub=sub, prefix="payfit-files")
    except file_content.MediaUnavailable as e:
        raise _bad(str(e)) from None


# ---------------------------------------------------------------------------
# Erreurs amont & sonde
# ---------------------------------------------------------------------------

def upstream_message(e: Any) -> str:
    status = e.status_code
    if status == 401:
        return "PayFit : clé API refusée (HTTP 401) — invalide, révoquée ou inactive."
    if status == 403:
        return ("PayFit : accès refusé (HTTP 403) — la clé ne porte pas le scope "
                "requis par cette ressource (collaborators:read, contracts:read, "
                "time:read, contracts:payslips:read, accounting:read, "
                "payment-files:read, health-insurance:read/write, "
                "collaborators:meal-vouchers:read, ou un scope d'écriture).")
    if status == 404:
        return "PayFit : ressource introuvable (HTTP 404)."
    if status == 429:
        return "PayFit : trop de requêtes (HTTP 429) — réessaie dans un instant."
    if status >= 500:
        return f"PayFit est momentanément indisponible (HTTP {status})."
    return f"PayFit a refusé la requête (HTTP {status}) : {e.body}"


def verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : introspection de la clé puis
    `GET /companies/{id}` (aucun scope requis), sans effet."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.payfit import PayfitClient

    key = (fields or {}).get("key")
    if not isinstance(key, str) or not key.strip():
        raise connector_verify.NonAutorise("PayFit : clé API vide.")
    try:
        PayfitClient(api_key=key.strip()).get_company()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(upstream_message(e)) from e
        raise


def register_probe() -> None:
    connector_verify.register(_NAME, verify)
