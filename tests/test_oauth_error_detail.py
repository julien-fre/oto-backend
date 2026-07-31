"""Le motif d'un refus OAuth doit ARRIVER jusqu'à l'utilisateur.

Ce que ces tests empêchent de revenir : `invalid_grant` est le code fourre-tout d'OAuth 2
— code d'autorisation expiré, déjà consommé, verifier PKCE qui ne correspond pas, appel
depuis une IP non autorisée, jeton révoqué. Le fournisseur dit LEQUEL dans
`error_description`. On le jetait (`oauth_flow.py`), et la traduction Salesforce
SUBSTITUAIT en plus sa propre supposition au dire du fournisseur.

Résultat vécu le 31/07 : un refus dû aux restrictions IP de la Connected App était rendu
« refresh token périmé, ou login_url incorrect ». Le message accusait la mauvaise pièce
et envoyait corriger ce qui marchait — quatre hypothèses explorées à la main sur une
information que le serveur avait déjà reçue.
"""
from __future__ import annotations

import pytest

from oto_mcp import oauth_flow
from oto_mcp.tools.salesforce import _sf_error_hint


class _Resp:
    def __init__(self, payload: dict, status: int = 400):
        self._payload, self.status_code = payload, status

    def json(self) -> dict:
        return self._payload


# --- la fabrique partagée : ne plus jeter error_description --------------------

def _exchange(monkeypatch, payload: dict, status: int = 400) -> str:
    monkeypatch.setattr(oauth_flow.requests, "post",
                        lambda *a, **k: _Resp(payload, status))
    with pytest.raises(oauth_flow.OAuthFlowError) as e:
        oauth_flow.exchange_code("https://ex.test/token", code="c", client_id="i",
                                 client_secret="s", redirect="https://r.test/cb")
    return str(e.value)


def test_le_motif_du_fournisseur_survit(monkeypatch):
    msg = _exchange(monkeypatch, {"error": "invalid_grant",
                                  "error_description": "ip restricted"})
    assert "invalid_grant" in msg
    assert "ip restricted" in msg, "le motif est jeté — on rediagnostique à la main"


def test_sans_description_le_message_reste_lisible(monkeypatch):
    """Beaucoup de fournisseurs n'en renvoient pas : pas de « : » orphelin ni de None."""
    msg = _exchange(monkeypatch, {"error": "invalid_client"})
    assert "invalid_client" in msg and "None" not in msg
    assert not msg.rstrip(".").endswith(":")


def test_un_corps_sans_error_du_tout(monkeypatch):
    msg = _exchange(monkeypatch, {}, status=503)
    assert "503" in msg and "None" not in msg


# --- la traduction AJOUTE, elle ne REMPLACE pas -------------------------------

def test_la_traduction_conserve_le_dire_du_fournisseur():
    msg = _sf_error_hint(RuntimeError(
        "Échec de l'échange OAuth (x.my.salesforce.com) : invalid_grant : ip restricted."))
    assert "ip restricted" in msg, (
        "la traduction a écrasé le motif réel par sa supposition — c'est le bug de fond")


def test_la_traduction_reste_actionnable():
    """On garde l'indice métier : le brut seul (« invalid_grant ») n'aide personne."""
    msg = _sf_error_hint(RuntimeError("invalid_grant : ip restricted"))
    assert "IP" in msg or "restrictions" in msg


def test_aucune_branche_ne_promet_une_cause_unique():
    """TRIPWIRE. Une traduction qui AFFIRME la cause d'un code fourre-tout ment tôt ou
    tard. Le mot « périmé » suivi de rien d'autre était exactement ça."""
    from oto_mcp.tools import salesforce as sf
    msg = sf._sf_hint_for("invalid_grant")
    assert "ou" in msg.lower() or "…" in msg, (
        "la branche invalid_grant énonce UNE cause : elle redeviendra trompeuse")


def test_un_message_inconnu_passe_tel_quel():
    assert "boom" in _sf_error_hint(RuntimeError("boom"))
