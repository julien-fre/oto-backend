"""Le lien de désinscription d'une relance : signer, vérifier, adresser.

Un jeton signé (HMAC-SHA256) qui scelle un `sub`, et rien d'autre. Même patron que
`upload_tokens` — même secret d'instance (`OTO_MCP_OAUTH_STATE_SECRET`), même
encodage — avec **une différence assumée : pas d'expiration**.

Un lien de désinscription qui périme n'est pas un lien de désinscription. Le mail
qu'on relit six mois plus tard est précisément celui dont on ne veut plus, et un
« ce lien a expiré » à ce moment-là transforme un refus en corvée. Ce que le jeton
autorise borne le risque : cesser de recevoir NOS relances, pour un compte que
l'appelant devait déjà connaître. Il n'ouvre aucune lecture, aucune écriture d'org,
et ne se rejoue pas en autre chose.

⚠️ **Le secret d'instance est ce qui le tient.** Sans `OTO_MCP_OAUTH_STATE_SECRET`,
on ne fabrique pas de lien : `lien()` lève, et l'envoi refuse plutôt que de partir
avec un pied de page qui ne mène nulle part.

⚠️ **`verify()` lève aussi dans ce cas — délibérément, et c'est le seul endroit où ce
module ne se tait pas.** Rendre `None` ferait afficher « lien invalide » à quelqu'un
dont le lien est parfaitement valide : son refus serait perdu, et la faute lui serait
attribuée. Un secret absent est une panne de serveur ; elle doit se voir comme telle
(500 bruyant, Sentry) et pas comme une erreur de l'utilisateur. Toutes les AUTRES
causes de rejet — signature fausse, forme illisible, mauvais `typ` — rendent bien
`None` : celles-là ne sont pas de notre fait.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Optional

_TYP = "optout"


class OptOutSecretManquant(RuntimeError):
    """Pas de secret d'instance : aucun lien signable, donc aucun envoi."""


def _secret() -> bytes:
    v = os.environ.get("OTO_MCP_OAUTH_STATE_SECRET")
    if not v:
        raise OptOutSecretManquant(
            "OTO_MCP_OAUTH_STATE_SECRET manquant : impossible de signer un lien de "
            "désinscription, donc impossible d'envoyer une relance.")
    return v.encode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(sub: str) -> str:
    """Le jeton de désinscription d'un compte. **Stable** : le même sub rend toujours
    le même jeton — un renvoi ne fabrique pas un second lien à révoquer."""
    payload = json.dumps({"typ": _TYP, "sub": sub},
                         separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(sig)}"


def verify(token: str) -> Optional[str]:
    """Le `sub` scellé si la signature est valide, `None` sinon. Fail-closed sur
    toute erreur de forme : un jeton qu'on ne sait pas lire n'est pas un jeton."""
    if not token or "." not in token:
        return None
    p_b64, sig_b64 = token.split(".", 1)
    try:
        payload = _b64url_decode(p_b64)
        sig = _b64url_decode(sig_b64)
    # noqa: SILENT — fail-closed : toute erreur de vérification ⇒ jeton refusé
    except Exception:
        return None
    if not hmac.compare_digest(sig, hmac.new(_secret(), payload, hashlib.sha256).digest()):
        return None
    try:
        data = json.loads(payload)
    # noqa: SILENT — fail-closed : toute erreur de vérification ⇒ jeton refusé
    except Exception:
        return None
    if data.get("typ") != _TYP:
        return None
    sub = data.get("sub")
    return str(sub) if sub else None


def lien(sub: str) -> str:
    """L'adresse complète servie dans le pied du mail.

    Sur le BACKEND (`OTO_MCP_PUBLIC_URL`), pas sur le dashboard : la désinscription
    doit fonctionner sans session, sans JavaScript et sans que le front soit déployé —
    c'est le même argument que la page publique d'un doc partagé (`/p/d/<token>`).
    """
    base = os.environ.get("OTO_MCP_PUBLIC_URL", "https://mcp.oto.ninja").rstrip("/")
    return f"{base}/o/u/{sign(sub)}"


# La page rendue au destinataire. Server-rendered, sans JS, sans marque tierce : elle
# confirme, elle ne propose rien d'autre. Idempotente — la recharger n'est pas une
# erreur, et le dire évite qu'on la reclique en pensant que ça n'a pas marché.
_PAGE = """<!DOCTYPE html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{titre}</title></head>
<body style="margin:0;background:#faf9f7;font-family:-apple-system,BlinkMacSystemFont,\
'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1c1917">
<div style="max-width:520px;margin:12vh auto;padding:28px 32px;background:#fff;\
border:1px solid #e7e5e4;border-radius:12px">
<p style="margin:0 0 12px;font-size:15px;font-weight:600">{titre}</p>
<p style="margin:0;font-size:15px;line-height:1.6">{corps}</p>
</div></body></html>"""

_TEXTES = {
    "fr": ("C'est noté",
           "Vous ne recevrez plus nos messages de relance. Les emails liés à votre "
           "compte (invitations, partages) continuent d'arriver : ils ne sont pas "
           "de la relance."),
    "en": ("Done",
           "You will not receive our follow-up messages any more. Emails tied to your "
           "account (invitations, shares) still come through: those are not "
           "follow-ups."),
    "refus": ("Lien invalide",
              "Ce lien de désinscription n'est pas valide. Répondez simplement à "
              "l'email que vous avez reçu, on s'en occupe."),
}


def page_confirmation(locale: Optional[str] = None) -> str:
    titre, corps = _TEXTES["en" if locale == "en" else "fr"]
    return _PAGE.format(lang="en" if locale == "en" else "fr", titre=titre, corps=corps)


def page_refus() -> str:
    titre, corps = _TEXTES["refus"]
    return _PAGE.format(lang="fr", titre=titre, corps=corps)
