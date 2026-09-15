"""L'état PKCE porte sa DESTINATION (oto-backend#572 point 8) — même mécanique
que `flow.sign_state`/`read_state` (audience liée au state, rejet inter-flux par
construction, `flow.py:80-112`).

Avant ce lot, `pkce.make_state`/`verify_state` signaient et vérifiaient un état
SANS discriminant de destination : la signature et le TTL étaient contrôlés, pas
la CIBLE — un état émis pour un fournisseur A validait pour un fournisseur B tant
que le secret matchait (`OTO_MCP_OAUTH_STATE_SECRET`, mutualisé entre familles de
jetons, #572 point 7). Sans conséquence pratique aujourd'hui — aucun connecteur
n'appelle plus ces deux fonctions directement, chacun a réimplémenté sa propre
variante avec plus de champs (`salesforce.py:90-112`, `zoho.py:200`,
`google.py:119`) — mais un manque de défense en profondeur sur un helper partagé,
que ce lot ferme."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from oto_mcp.auth import pkce

_SECRET = b"test-secret"


def test_state_verifies_for_its_own_audience():
    state = pkce.make_state(_SECRET, "salesforce", "sub-1", "verifier-xyz")
    assert pkce.verify_state(_SECRET, "salesforce", state, ttl=600) == ("sub-1", "verifier-xyz")


def test_state_rejected_for_a_different_audience():
    """Le manque exact du #572 point 8 : un état émis pour un fournisseur ne doit
    PAS se vérifier pour un autre, même avec le même secret."""
    state = pkce.make_state(_SECRET, "salesforce", "sub-1", "verifier-xyz")
    assert pkce.verify_state(_SECRET, "zoho", state, ttl=600) is None


def test_legacy_state_without_audience_field_still_verifies():
    """Rétrocompatibilité (même patron que `flow.py`, pris à la main ici car aucun
    ancien état n'a de champ `aud` à relire) : un état signé AVANT ce champ reste
    accepté — l'absence ne peut jamais valoir « pour un autre fournisseur »,
    seulement « émis avant l'audience ». Fenêtre bornée par le TTL du state
    (minutes), pas par une date : à l'expiration, plus aucun ancien état ne peut
    se présenter — pas besoin d'un préavis calendaire comme `origine_override`."""
    payload = json.dumps({"sub": "sub-1", "v": "verifier-xyz", "ts": int(time.time())},
                         separators=(",", ":")).encode()
    sig = hmac.new(_SECRET, payload, hashlib.sha256).digest()
    legacy_state = f"{pkce.b64url(payload)}.{pkce.b64url(sig)}"
    assert pkce.verify_state(_SECRET, "salesforce", legacy_state, ttl=600) == ("sub-1", "verifier-xyz")


def test_expired_state_still_rejected():
    """Le TTL reste vérifié — ce lot ajoute l'audience, ne relâche rien d'autre."""
    old_payload = json.dumps({"sub": "sub-1", "v": "verifier-xyz", "ts": int(time.time()) - 1000,
                              "aud": "salesforce"}, separators=(",", ":")).encode()
    sig = hmac.new(_SECRET, old_payload, hashlib.sha256).digest()
    state = f"{pkce.b64url(old_payload)}.{pkce.b64url(sig)}"
    assert pkce.verify_state(_SECRET, "salesforce", state, ttl=600) is None


def test_tampered_signature_still_rejected():
    state = pkce.make_state(_SECRET, "salesforce", "sub-1", "verifier-xyz")
    body, _, sig = state.rpartition(".")
    assert pkce.verify_state(_SECRET, "salesforce", f"{body}.{sig[::-1]}", ttl=600) is None
