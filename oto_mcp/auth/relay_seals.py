"""Les sceaux du relais d'autorisation (`auth/relay.py`) : l'état et la marque de code.

Deux HMAC-SHA256 sous `OTO_MCP_OAUTH_STATE_SECRET`, chacun sous son DOMAINE — un `state`
d'un autre flux OAuth d'oto (même secret, autre format) ne se relit jamais comme l'un
d'eux :

- **l'état** scelle, dans le `state` envoyé à Logto, le rappel du client, SON `state`, le
  host émetteur et l'heure. Il est lisible (base64) : il n'y a rien de secret dedans, il ne
  doit juste pas pouvoir être forgé ni rejoué sur un autre host ;
- **la marque** (`oto1.<étiquette>.<code>`) lie un code relayé à ce rappel sur ce host.
  ⚠️ C'est une vérification de COHÉRENCE, pas une garde : quiconque passe par le relais
  obtient une marque valide pour son propre rappel. La garde réelle d'un code intercepté est
  PKCE, que le relais exige et transmet intact.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

CODE_MARQUE = "oto1"
ETAT_TTL = 3600     # connexion par code e-mail : plusieurs minutes, pas plusieurs heures


def secret() -> Optional[bytes]:
    v = os.environ.get("OTO_MCP_OAUTH_STATE_SECRET")
    return v.encode() if v else None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _signature(cle: bytes, domaine: str, charge: bytes) -> bytes:
    return hmac.new(cle, domaine.encode() + b"|" + charge, hashlib.sha256).digest()


def sceller_etat(cle: bytes, as_base: str, rappel_client: str,
                 etat_client: Optional[str], *, maintenant: Optional[int] = None) -> str:
    charge = json.dumps({"v": 1, "a": as_base, "r": rappel_client, "s": etat_client,
                         "t": int(time.time() if maintenant is None else maintenant)},
                        separators=(",", ":")).encode()
    return f"{_b64(charge)}.{_b64(_signature(cle, 'oto-relay-state-v1', charge))}"


def ouvrir_etat(cle: bytes, etat: str, as_base: str, *, ttl: int = ETAT_TTL
                ) -> tuple[Optional[tuple[str, Optional[str], int]], str]:
    """`((rappel, state du client, âge en s), "")` si le sceau est intact, récent et émis
    pour CE host ; `(None, raison)` sinon — `format`, `sig`, `host` ou `ttl`, pour le
    journal : un retour refusé après une connexion lente ne doit pas se lire comme un
    secret différent entre deux environnements."""
    if not etat or etat.count(".") != 1:
        return None, "format"
    b64_charge, b64_sig = etat.split(".")
    try:
        charge, sig = _unb64(b64_charge), _unb64(b64_sig)
    # noqa: SILENT — fail-closed : un sceau illisible est un sceau refusé, raison rendue
    except Exception:
        return None, "format"
    if not hmac.compare_digest(sig, _signature(cle, "oto-relay-state-v1", charge)):
        return None, "sig"
    donnees = json.loads(charge)      # signé par nous : c'est notre propre JSON
    if not isinstance(donnees, dict) or donnees.get("v") != 1:
        return None, "format"
    if donnees.get("a") != as_base:
        return None, "host"
    age = int(time.time()) - int(donnees.get("t") or 0)
    if age > ttl:
        return None, "ttl"
    rappel, etat_client = donnees.get("r"), donnees.get("s")
    if not isinstance(rappel, str) or not (etat_client is None or isinstance(etat_client, str)):
        return None, "format"
    return (rappel, etat_client, age), ""


def _etiquette(cle: bytes, as_base: str, rappel_client: str, code: str) -> str:
    charge = "\n".join((as_base, rappel_client, code)).encode()
    return _b64(_signature(cle, "oto-relay-code-v1", charge))[:32]


def marquer_code(cle: bytes, as_base: str, rappel_client: str, code: str) -> str:
    return f"{CODE_MARQUE}.{_etiquette(cle, as_base, rappel_client, code)}.{code}"


def lire_code(cle: bytes, as_base: str, rappel_client: Optional[str],
              code: str) -> Optional[str]:
    """Le code Logto d'un code MARQUÉ si son étiquette le lie à CE rappel sur CE host, None
    sinon — y compris sans rappel : on ne transmet pas un code dont on ne peut plus vérifier
    le destinataire."""
    parties = code.split(".", 2)
    if len(parties) != 3 or parties[0] != CODE_MARQUE or not parties[2] or not rappel_client:
        return None
    if not parties[1].isascii():   # `compare_digest` lève sur une chaîne non ASCII
        return None
    attendue = _etiquette(cle, as_base, rappel_client, parties[2])
    return parties[2] if hmac.compare_digest(parties[1], attendue) else None
