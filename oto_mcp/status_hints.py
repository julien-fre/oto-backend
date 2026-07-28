"""Registre de « pending actions » par connecteur (lot 2, seam générique).

Certains connecteurs ont une connexion en DEUX temps : la clé/l'autorisation
résout, mais il manque encore une étape côté user pour être opérationnel
(unipile : lier un canal ; session navigateur : ré-authentifier une session
morte). Plutôt que de faire remonter ces notions spécifiques dans le modèle
générique (`ProviderStatus`), chaque connecteur ENREGISTRE ici un hook qui
répond « quelle étape manque ? » — le front reste agnostique : il affiche le
libellé tel quel comme verdict + CTA.

Patron identique à `connector_verify.py` : registre passif, enregistrement à
l'import du module connecteur, fail-open (un hook qui casse ne casse jamais
/api/me).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# fn(sub, org, group, entry) -> libellé de l'étape manquante, ou None si rien.
# `entry` = l'entrée ProviderStatus déjà construite (mode, flags par niveau…).
_HOOKS: dict[str, Callable[[str, Optional[int], Optional[int], dict], Optional[str]]] = {}


def register(connector: str, fn: Callable[[str, Optional[int], Optional[int], dict], Optional[str]]) -> None:
    _HOOKS[connector] = fn


def has_hook(connector: str) -> bool:
    return connector in _HOOKS


# --- état d'un credential : UN calcul, plusieurs surfaces ---------------------
#
# Le hook ci-dessus a besoin de la DB (il part d'un `sub`) : seul `/api/me` peut
# l'appeler. Les autres surfaces qui doivent connaître le MÊME fait — la sonde
# « tester la connexion », l'erreur de résolution, demain le formulaire — se sont
# donc mises à le RE-DÉRIVER chacune à sa façon.
#
# Vécu le 2026-07-28 en introduisant la connexion Zoho en deux temps (app posée,
# consentement à venir) : cinq endroits décidaient séparément « ce credential
# est-il utilisable ? », et il a fallu quatre correctifs successifs pour les
# aligner — dont un qui envoyait l'utilisateur « régénérer un refresh token
# périmé » qui n'existait pas encore.
#
# D'où ce second registre, volontairement **PUR** : `fields -> CredentialState`.
# Sans DB, donc appelable de partout. Le hook `pending_action` s'y adosse quand un
# état est déclaré ; la sonde `verify` s'en sert pour court-circuiter avec le même
# libellé. Un seul texte, un seul critère.


@dataclass(frozen=True)
class CredentialState:
    """État d'un credential déduit de ses SEULS champs.

    `complete` = utilisable en l'état. Sinon `missing` nomme ce qui manque et
    `next_action` porte le geste, en clair, tel que les surfaces l'afficheront
    (front comme message d'erreur) — jamais reformulé en aval."""
    complete: bool
    next_action: str = ""
    missing: tuple[str, ...] = field(default_factory=tuple)


_STATE_HOOKS: dict[str, Callable[[dict], Optional[CredentialState]]] = {}


def register_state(connector: str, fn: Callable[[dict], Optional[CredentialState]]) -> None:
    """Déclare COMMENT lire l'état d'un credential de ce connecteur. La
    spécificité reste dans le module du connecteur (patron `connector_verify`)."""
    _STATE_HOOKS[connector] = fn


def credential_state(connector: str, fields: dict) -> Optional[CredentialState]:
    """État du credential, ou None si le connecteur n'en déclare pas. Fail-open :
    un hook qui casse ne doit jamais faire échouer la surface appelante."""
    fn = _STATE_HOOKS.get(connector)
    if fn is None:
        return None
    try:
        return fn(fields or {})
    except Exception:
        logger.warning("status_hints: état %s en échec (fail-open)", connector,
                       exc_info=True)
        return None


def require_complete(connector: str, fields: dict) -> None:
    """Lève `ValueError(next_action)` si le credential est incomplet — le raccourci
    des SONDES (`connector_verify`), pour qu'elles n'inventent pas leur propre
    diagnostic. No-op si le connecteur ne déclare pas d'état."""
    st = credential_state(connector, fields)
    if st is not None and not st.complete:
        raise ValueError(st.next_action)


def pending_action(connector: str, sub: str, org: Optional[int],
                   group: Optional[int], entry: dict) -> Optional[str]:
    """Étape manquante pour ce (sub, connecteur), ou None. Fail-open."""
    fn = _HOOKS.get(connector)
    if fn is None:
        return None
    try:
        return fn(sub, org, group, entry)
    except Exception:
        logger.warning("status_hints: hook %s en échec (fail-open)", connector,
                       exc_info=True)
        return None
