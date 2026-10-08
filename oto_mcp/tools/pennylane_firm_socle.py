"""Socle commun du connecteur `pennylane_firm` — outils MCP ET relais d'upload.

Deux pièces que les deux faces partagent, et qui ne doivent exister qu'une fois :

- **le refus nommé** (`Refus`) et la **traduction** d'une erreur de la lib
  (`traduire`) : un refus de Pennylane devient un statut, un code et une phrase bornée,
  jamais le jeton (il ne vit que dans l'en-tête `Authorization` du client) ;
- **le cache anti-doublon** (`verifier_absent`, `reserver`…) : les NOMS présents dans
  un dossier de la GED, par (org, société, dossier), 15 min.
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

from mcp.types import INVALID_PARAMS, ErrorData

from ..mcp_errors import McpError

if TYPE_CHECKING:  # annotation seulement — la sonde de version-skew la lit
    from oto.tools.pennylane_firm import PennylaneFirmClient

CONNECTOR = "pennylane_firm"

#: Borne de l'extrait d'un refus de Pennylane rendu à l'appelant.
EXTRAIT = 400

#: Pennylane demande d'attendre une minute après un 429.
ATTENTE_429_S = 60


class Refus(Exception):
    """Un refus nommé du connecteur : `status` (HTTP, face REST), `code` (le contrat),
    `message` (la phrase, en anglais : elle est servie), `retryable`, et le délai
    conseillé quand il y en a un. Traduit en `McpError` par `vers_mcp`, en réponse JSON
    par la route du relais."""

    def __init__(self, status: int, code: str, message: str, *,
                 retryable: bool = False, retry_after: Optional[int] = None):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after

    @property
    def details(self) -> dict:
        d: dict = {"retryable": self.retryable}
        if self.retry_after is not None:
            d["retry_after_seconds"] = self.retry_after
        return d


def vers_mcp(e: Refus) -> McpError:
    """La face MCP d'un refus : le code dans `data`, comme `activation_gate`."""
    return McpError(ErrorData(code=INVALID_PARAMS,
                              message=f"Refusal `{e.code}`: {e.message}",
                              data={"code": e.code, **e.details}))


def _client(key: Optional[str] = None) -> PennylaneFirmClient:
    """Le client de la lib. Sans `key`, le jeton de l'org de l'APPEL (cascade, outil
    MCP) ; avec, celui que le relais a résolu sous l'org scellée de son jeton.

    Import différé : les tests remplacent `PennylaneFirmClient` sur le paquet de la
    lib. Le type de retour est la classe NUE : la sonde de version-skew le lit pour
    savoir contre quelle classe vérifier les méthodes appelées ici et chez les
    modules frères qui importent cette fabrique."""
    from oto.tools.pennylane_firm import PennylaneFirmClient

    if key is None:
        from .. import access
        key, _plateforme = access.resolve_api_key(CONNECTOR)
    return PennylaneFirmClient(key)


def _borne(texte: str) -> str:
    texte = " ".join(str(texte).split())
    return texte if len(texte) <= EXTRAIT else texte[:EXTRAIT] + "…"


def traduire(appel: Callable, geste: str):
    """EXÉCUTE un appel à la lib et rend son résultat, ou lève un `Refus` nommé.

    Prend une fonction, pas un résultat : l'exception doit naître sous cette garde.
    Le message de la lib porte déjà le statut et un extrait borné de la réponse de
    Pennylane ; jamais le jeton, qui ne sort pas de l'en-tête du client."""
    from oto.tools.pennylane_firm import (PennylaneFirmError, PennylaneFirmRateLimited,
                                          PennylaneFirmScopeMissing)
    try:
        return appel()
    except PennylaneFirmRateLimited as e:
        raise Refus(429, "pennylane_rate_limited",
                    f"Pennylane refused {geste}: request rate exceeded (5 requests per "
                    f"second per firm token). Wait {ATTENTE_429_S} s before trying again. "
                    f"{_borne(e)}", retryable=True, retry_after=ATTENTE_429_S) from e
    except PennylaneFirmScopeMissing as e:
        portee = e.scope or "the scope this action needs"
        raise Refus(403, "pennylane_scope_missing",
                    f"Pennylane refused {geste}: the firm token lacks {portee}. This is "
                    "not an argument to fix: an org admin must replace the token on the "
                    f"connector's card with one carrying that scope. {_borne(e)}") from e
    except PennylaneFirmError as e:
        st = e.status_code
        if st == 401:
            raise Refus(502, "pennylane_token_invalid",
                        f"Pennylane refused the firm token for {geste} (401): an org admin "
                        "must set a valid token on the connector's card.") from e
        if st == 404:
            raise Refus(404, "pennylane_not_found",
                        f"Pennylane found no target for {geste} (404): company_id is the "
                        "firm-side id, folder ids belong to that one company. "
                        f"{_borne(e)}") from e
        if st == 422:
            raise Refus(422, "pennylane_rejected",
                        f"Pennylane rejected the content of {geste} (422). {_borne(e)}") from e
        if st >= 500:
            raise Refus(502, "pennylane_unavailable",
                        f"Pennylane is unavailable for {geste} ({st}): retry later.",
                        retryable=True) from e
        raise Refus(502, "pennylane_error",
                    f"Pennylane refused {geste} ({st}). {_borne(e)}") from e
    except RuntimeError as e:
        # Réseau, réponse illisible : aucun statut amont.
        raise Refus(502, "pennylane_unreachable",
                    f"Pennylane did not answer {geste}: {_borne(e)}", retryable=True) from e


# ── Cache anti-doublon : les noms présents dans un dossier ─────────────────────────

#: Durée de vie d'une entrée, alignée sur celle d'un lien de relais.
TTL_DOUBLONS_S = 900
#: Pages lues au plus pour remplir une entrée (100 fichiers par page).
PAGES_MAX = 100

_VERROU = threading.Lock()
# (org, société, dossier) -> {"expire": monotonic, "noms": set, "en_vol": set}
_CACHE: dict[tuple, dict] = {}


def _cle(org_id, company_id, parent_folder_id) -> tuple:
    return (int(org_id), int(company_id), int(parent_folder_id))


def _entree_vivante(cle: tuple) -> Optional[dict]:
    e = _CACHE.get(cle)
    if e is not None and e["expire"] > time.monotonic():
        return e
    return None


def _phrase_doublon(name: str, company_id, parent_folder_id) -> str:
    return (f"A file named {name!r} already exists in folder {parent_folder_id} of "
            f"company {company_id} (or is being uploaded). The API cannot rename or "
            "delete a file: choose another name.")


def verifier_absent(c, org_id, company_id, parent_folder_id, name: str) -> None:
    """Refuse si `name` est présent (ou en cours de dépôt) dans le dossier.

    Une entrée périmée ou absente se remplit d'abord chez Pennylane — hors verrou,
    c'est un appel réseau. Un parcours incomplet (`has_more`) ne conclut JAMAIS
    « absent ». Remplissage écrit ICI et non dans une fonction à part : le parcours
    des refus déclarés de la route (`tests/_refus_atteignables.py`) s'arrête à quatre
    appels du handler, et ce refus doit rester à sa portée."""
    cle = _cle(org_id, company_id, parent_folder_id)
    with _VERROU:
        vivante = _entree_vivante(cle)
    if vivante is None:
        page = traduire(lambda: c.list_dms_files(
            company_id, parent_folder_id=parent_folder_id, limit=100, all_pages=True,
            max_pages=PAGES_MAX), "the listing of the target folder")
        if page.get("has_more"):
            raise Refus(409, "duplicate_check_incomplete",
                        f"The target folder holds more than {PAGES_MAX * 100} files: the "
                        "duplicate check cannot read it whole, so nothing is uploaded. "
                        "Pick a smaller folder.")
        noms = {str(it.get("name")) for it in page.get("items") or [] if it.get("name")}
        with _VERROU:
            ancienne = _CACHE.get(cle) or {}
            _CACHE[cle] = {"expire": time.monotonic() + TTL_DOUBLONS_S, "noms": noms,
                           "en_vol": set(ancienne.get("en_vol") or ())}
    with _VERROU:
        e = _CACHE.get(cle) or {}
        if name in (e.get("noms") or ()) or name in (e.get("en_vol") or ()):
            raise Refus(409, "name_already_exists",
                        _phrase_doublon(name, company_id, parent_folder_id))


def reserver(c, org_id, company_id, parent_folder_id, name: str) -> None:
    """Comme `verifier_absent`, puis marque le nom EN VOL, atomiquement : deux relais
    du même nom dans ce processus ne partent pas tous les deux."""
    verifier_absent(c, org_id, company_id, parent_folder_id, name)
    cle = _cle(org_id, company_id, parent_folder_id)
    with _VERROU:
        e = _CACHE.get(cle)
        if e is None or name in e["noms"] or name in e["en_vol"]:
            raise Refus(409, "name_already_exists",
                        _phrase_doublon(name, company_id, parent_folder_id))
        e["en_vol"].add(name)


def confirmer(org_id, company_id, parent_folder_id, name: str) -> None:
    """Le dépôt a réussi : le nom est désormais PRÉSENT."""
    with _VERROU:
        e = _CACHE.get(_cle(org_id, company_id, parent_folder_id))
        if e is not None:
            e["en_vol"].discard(name)
            e["noms"].add(name)


def liberer(org_id, company_id, parent_folder_id, name: str) -> None:
    """Le dépôt a échoué : le nom n'est plus en vol."""
    with _VERROU:
        e = _CACHE.get(_cle(org_id, company_id, parent_folder_id))
        if e is not None:
            e["en_vol"].discard(name)


def invalider(org_id, company_id, parent_folder_id) -> None:
    """Un 422 de Pennylane : ce que le cache croit savoir du dossier ne vaut plus. Les
    noms en vol (d'autres relais) sont gardés."""
    with _VERROU:
        e = _CACHE.get(_cle(org_id, company_id, parent_folder_id))
        if e is not None:
            e["expire"] = 0.0
