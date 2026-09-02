"""Envoi d'email transactionnel (invitations d'org) via **otomata-mailer**.

Standard Otomata : on n'utilise plus Resend per-app — l'endpoint générique
`POST mailer.oto.zone/api/send` (Scaleway TEM, brand Otomata, domaines from
vérifiés DKIM/SPF) sert les emails métier de toutes les apps. Bearer
`OTO_MAILER_SEND_BEARER`. **Best-effort** : sans bearer configuré ou en cas
d'échec, on ne lève pas — on renvoie False et l'appelant expose l'`invite_url`
pour un partage manuel.
"""
from __future__ import annotations

import html as _html
import logging
import os

log = logging.getLogger("oto_mcp.email")

_MAILER_URL = os.environ.get("OTO_MAILER_URL", "https://mailer.oto.zone/api/send")
_MAIL_FROM = os.environ.get("OTO_MAIL_FROM", "Oto <oto@otomata.tech>")


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    """Échappement pour une VALEUR D'ATTRIBUT : `_esc` laisse passer les guillemets,
    et un `"` dans un `alt=` refermerait l'attribut — la balise suivante serait celle
    de l'auteur du texte, pas la nôtre."""
    return _html.escape(s or "", quote=True)


def _no_crlf(s: str | None) -> str | None:
    """Neutralise une injection d'en-tête email : retire CR/LF (et NUL) d'une valeur
    destinée à un champ d'en-tête (sujet, to, from, reply_to). Des données
    user-controlled (nom de projet, titre de page) transitent par le sujet ; un
    \\r\\n y injecterait un en-tête arbitraire côté service d'envoi."""
    if s is None:
        return None
    return s.replace("\r", "").replace("\n", " ").replace("\x00", "")


def _send(to: str, subject: str, html: str, reply_to: str | None = None,
          from_email: str | None = None) -> bool:
    """Envoi via mailer.oto.zone (Scaleway TEM). `from_email` = adresse expéditrice
    (défaut marque `_MAIL_FROM`) — le service refuse (403) un domaine hors allowlist
    `MAILER_FROM_DOMAINS`. Best-effort (False si pas de bearer ou échec)."""
    bearer = os.environ.get("OTO_MAILER_SEND_BEARER")
    if not bearer:
        return False
    try:
        import httpx
        # Anti-injection d'en-tête : neutralise CR/LF sur TOUS les champs d'en-tête
        # (choke-point unique → couvre tous les templates). Le corps `html` n'est pas
        # un en-tête (et déjà échappé par les templates via _esc).
        payload = {"from": _no_crlf(from_email or _MAIL_FROM), "to": _no_crlf(to),
                   "subject": _no_crlf(subject), "html": html}
        if reply_to:
            payload["reply_to"] = _no_crlf(reply_to)
        r = httpx.post(
            _MAILER_URL,
            headers={"Authorization": f"Bearer {bearer}"},
            json=payload,
            timeout=10.0,
        )
        if r.status_code == 200:
            return True
        log.warning("mailer %s → %s %s", _MAILER_URL, r.status_code, r.text[:200])
        return False
    except Exception as e:  # réseau, import, etc. → best-effort
        log.warning("email to %s not sent (%s)", to, e)
        return False


def send_via_resend(to: str, subject: str, html: str, *, api_key: str,
                    from_email: str, reply_to: str | None = None) -> bool:
    """Envoi direct via l'API Resend, avec la clé BYOK de l'org. `from_email` =
    adresse sur un domaine vérifié côté Resend par l'org. Best-effort (False si
    échec), même contrat que `_send`. PAS d'usage du client oto-core (interdiction
    de résolution de secret côté serveur)."""
    if not api_key or not from_email:
        return False
    try:
        import httpx
        payload = {"from": from_email, "to": [to], "subject": subject, "html": html}
        if reply_to:
            payload["reply_to"] = reply_to
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=10.0,
        )
        if r.status_code in (200, 201):
            return True
        log.warning("resend → %s %s", r.status_code, r.text[:200])
        return False
    except Exception as e:  # réseau, import, etc. → best-effort
        log.warning("resend email to %s not sent (%s)", to, e)
        return False


def send_via_scaleway_tem(to: str, subject: str, html: str, *, secret_key: str,
                          project_id: str, from_email: str, from_name: str | None = None,
                          region: str = "fr-par", reply_to: str | None = None) -> bool:
    """Envoi direct via l'API Scaleway TEM, avec la clé BYO de l'org (secret_key +
    project_id). `from_email` = adresse sur un domaine VÉRIFIÉ dans le compte Scaleway
    de l'org — l'API TEM refuse les domaines non vérifiés (propriété du domaine garantie
    par Scaleway, zéro logique domaine côté oto). Best-effort (False si échec), même
    contrat que `send_via_resend`. PAS de résolution de secret côté serveur."""
    if not secret_key or not project_id or not from_email:
        return False
    region = region or "fr-par"
    try:
        import httpx
        frm: dict = {"email": from_email}
        if from_name:
            frm["name"] = from_name
        payload: dict = {
            "from": frm,
            "to": [{"email": to}],
            "subject": subject,
            "html": html,
            "project_id": project_id,
        }
        if reply_to:
            payload["additional_headers"] = [{"key": "Reply-To", "value": reply_to}]
        r = httpx.post(
            f"https://api.scaleway.com/transactional-email/v1alpha1/regions/{region}/emails",
            headers={"X-Auth-Token": secret_key},
            json=payload,
            timeout=10.0,
        )
        if r.status_code in (200, 201):
            return True
        log.warning("scaleway tem → %s %s", r.status_code, r.text[:200])
        return False
    except Exception as e:  # réseau, import, domaine non vérifié → best-effort
        log.warning("scaleway tem email to %s not sent (%s)", to, e)
        return False


# Le DESSIN (marques, palettes, gabarit de page, bouton) vit dans `email_brand.py`
# — ce module-ci garde le TRANSPORT. Import de MODULE, jamais `from .email_brand
# import page` : le cycle est mutuel (email_brand appelle `_esc`/`_esc_attr` d'ici)
# et seul un import de module le rend inoffensif dans les deux sens, aucun des deux
# ne touchant un attribut de l'autre au moment de l'import.
from . import email_brand as _charte  # noqa: E402


def _bouton(app_url: str | None, libelle: str, brand: str = "oto") -> str:
    """Le bouton d'ouverture à la marque du DESTINATAIRE, ou RIEN.

    Le lien d'un projet dépend d'un patron déclaré par le tenant : le produit du
    partenaire n'a pas forcément cette vue, et coller NOTRE chemin sous SON domaine
    fabriquerait un lien mort — pire qu'une absence, parce qu'un lien mort ne se
    diagnostique pas, il se subit (cf. `links.py`). Un email est un lien AFFICHÉ, pas
    une redirection : on n'écrit rien plutôt que d'envoyer quelque part."""
    return _charte.bouton(_charte.marque(brand), app_url, libelle)


# Les 6 gabarits transactionnels (texte + locale FR/EN) vivent dans
# `email_templates.py` — extraits pour tenir sous 500 lignes une fois la
# version anglaise ajoutée (oto-backend#700). Réexposés ICI pour que
# `email.send_invite_email` etc. restent des attributs du module `email`
# (c'est ce que les tests monkeypatchent) — import placé APRÈS `_send`,
# `_esc` et `_bouton` ci-dessus, dont `email_templates` dépend.
from .email_templates import (  # noqa: E402,F401 — réexport intentionnel
    send_change_request_email,
    send_change_request_resolved_email,
    send_invite_email,
    send_resource_shared_email,
    send_resource_transferred_email,
    send_signal_digest_email,
)


# L'image se contraint à la largeur utile de la colonne (`email_brand.LARGEUR_UTILE`)
# par l'attribut `width` (lu par les clients qui ignorent le CSS) ET par
# `max-width:100%` (affichage réduit : l'image suit la colonne au lieu de la déborder).
#
# ⚠️ Lu À L'APPEL, jamais au niveau module : `email_brand` importe `email` en retour,
# donc lire un attribut de l'un pendant l'exécution du corps de l'autre casse l'import
# dès qu'on entre par `email_brand` (vécu ici même). Le cycle n'est inoffensif QUE
# tant que les deux modules ne se touchent qu'au moment de l'appel.
_IMG_STYLE = "max-width:100%;height:auto;display:block;border:0"


def _image_html(image_url: str | None, image_alt: str | None) -> str:
    """L'image de tête, ou RIEN. Lève `ValueError` — jamais de repli :

    - **`alt` obligatoire** : beaucoup de clients bloquent les images, le mail doit
      garder son sens sans elle. Pas de valeur par défaut, qui ne dirait rien.
    - **`https://` seul** : un `http://` est bloqué ou marqué « non sécurisé » par les
      clients, et un `data:`/`cid:` n'est pas une URL publique stable.
    - URL et alt échappés en ATTRIBUT (guillemets compris)."""
    url = (image_url or "").strip()
    alt = (image_alt or "").strip()
    if not url and not alt:
        return ""
    if url and not alt:
        raise ValueError("`image_alt` est requis avec `image_url` : le texte de "
                         "remplacement porte le sens du visuel quand l'image est bloquée.")
    if alt and not url:
        raise ValueError("`image_alt` sans `image_url` : rien à décrire.")
    if not url.startswith("https://"):
        raise ValueError(f"`image_url` doit commencer par https:// (reçu : {url[:24]!r}).")
    return (f'<p style="{_charte.PARA}"><img src="{_esc_attr(url)}" alt="{_esc_attr(alt)}" '
            f'width="{_charte.LARGEUR_UTILE}" style="{_IMG_STYLE}"></p>')


def render_composed_email(
    body: str,
    *,
    cta_text: str | None = None,
    cta_url: str | None = None,
    footer: bool = True,
    image_url: str | None = None,
    image_alt: str | None = None,
    brand: str = "oto",
    locale: str | None = None,
    unsubscribe_url: str | None = None,
) -> str:
    """Rend le HTML, à la charte de `brand`, d'un email dont le **contenu est fourni
    par l'agent** (prose brute + CTA optionnel + UNE image de tête).

    `body` = texte brut : les lignes vides séparent des paragraphes, les sauts de
    ligne simples deviennent des `<br>`. Échappé (jamais de HTML injecté par
    l'agent). `footer` ajoute la signature de marque + l'opt-out par réponse.
    `image_url` + `image_alt` (les deux, ou aucun) placent une image AVANT le corps ;
    voir `_image_html` pour ce qui est refusé (`ValueError`).

    La ligne d'aperçu de la boîte de réception est le PREMIER PARAGRAPHE, pas le
    sujet : Gmail affiche « sujet — aperçu » côte à côte, et y répéter le sujet ne
    dit rien de plus. C'est aussi ce qui évite l'aperçu d'avant, où la boîte allait
    chercher le premier texte venu (« ou collez ce lien »)."""
    m = _charte.marque(brand)
    image_html = _image_html(image_url, image_alt)
    paras = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    body_html = "".join(
        f'<p style="{_charte.PARA}">{_esc(p).replace(chr(10), "<br>")}</p>'
        for p in paras
    )
    cta_html = _charte.bouton(m, cta_url, cta_text) if (cta_text and cta_url) else ""
    # Le pied MARKETING (pourquoi vous recevez ça, comment ne plus le recevoir) —
    # celui d'un transactionnel dit autre chose, cf. `email_templates`.
    #
    # ⚠️ La phrase CHANGE quand un lien de désinscription accompagne le pied : sans
    # lien, « répondez pour ne plus en recevoir » est le seul refus possible et il
    # faut le dire ; avec lien, le laisser proposerait deux chemins dont un seul est
    # enregistré quelque part (une réponse humaine ne persiste aucun refus).
    en = locale == "en"
    if not footer:
        mention = None
    elif unsubscribe_url:
        mention = (f"you're receiving this because you have a {m.nom} account — "
                   "reply to this email to talk to us."
                   if en else
                   f"vous recevez ce message car vous avez un compte {m.nom} — "
                   "répondez à cet email pour nous parler.")
    else:
        mention = (f"you're receiving this because you have a {m.nom} account — "
                   "reply to this email to talk to us, or to stop receiving them."
                   if en else
                   f"vous recevez ce message car vous avez un compte {m.nom} — "
                   "répondez à cet email pour nous parler, ou pour ne plus en recevoir.")
    desinscription = ((unsubscribe_url, "unsubscribe" if en else
                       "ne plus recevoir ces messages")
                      if (footer and unsubscribe_url) else None)
    apercu = paras[0] if paras else m.nom
    return _charte.page(m, image_html + body_html + cta_html,
                        preheader=apercu, mention=mention, locale=locale,
                        desinscription=desinscription)


def format_from(from_email: str | None, from_name: str | None = None) -> str | None:
    """En-tête `from` au format « Name <addr> » (ou l'adresse seule). None si pas
    d'adresse → l'appelant retombe sur la marque par défaut."""
    if not from_email:
        return None
    return f"{from_name} <{from_email}>" if from_name else from_email


def send_composed_email(
    to: str,
    subject: str,
    body: str,
    *,
    cta_text: str | None = None,
    cta_url: str | None = None,
    reply_to: str | None = None,
    footer: bool = True,
    from_email: str | None = None,
    from_name: str | None = None,
    image_url: str | None = None,
    image_alt: str | None = None,
    brand: str = "oto",
    locale: str | None = None,
    unsubscribe_url: str | None = None,
) -> bool:
    """Envoie un email à contenu libre (fourni par l'agent), rendu à la charte de
    `brand`, via le mailer Otomata (Scaleway TEM).

    `from_email`/`from_name` = adresse expéditrice (défaut = marque `_MAIL_FROM`) ;
    le domaine doit être dans l'allowlist du service. `reply_to` défaut = la boîte
    du studio (`OTO_CONTACT_TO`). `image_url`/`image_alt` = l'image de tête (cf.
    `render_composed_email`). True si envoyé, False sinon (best-effort)."""
    html = render_composed_email(body, cta_text=cta_text, cta_url=cta_url, footer=footer,
                                 image_url=image_url, image_alt=image_alt, brand=brand,
                                 locale=locale, unsubscribe_url=unsubscribe_url)
    rt = reply_to or os.environ.get("OTO_CONTACT_TO", "alexis@otomata.tech")
    return _send(to, subject, html, reply_to=rt, from_email=format_from(from_email, from_name))


def send_contact_email(name: str, email: str, message: str) -> bool:
    """Message du formulaire de contact d'otomata.tech → boîte du studio.

    `reply_to` = l'email du visiteur pour répondre en un clic. Destinataire
    configurable via `OTO_CONTACT_TO` (défaut alexis@otomata.tech). Pas de pied :
    ce mail-ci part chez NOUS, il n'a personne à qui expliquer pourquoi il arrive."""
    to = os.environ.get("OTO_CONTACT_TO", "alexis@otomata.tech")
    subject = f"otomata.tech — message de {name}"
    m = _charte.marque("oto")
    corps = _esc(message).replace("\n", "<br>")
    contenu = (
        f'<p style="{_charte.PARA};{_charte.discret(m)}">nouveau message via '
        f'otomata.tech</p>'
        f'<p style="{_charte.PARA}"><strong>{_esc(name)}</strong> '
        f'&lt;{_esc(email)}&gt;</p>'
        f'<div style="border-top:1px solid {m.filet};margin:0 0 16px"></div>'
        f'<p style="{_charte.PARA}">{corps}</p>'
    )
    html = _charte.page(m, contenu, preheader=f"message de {name}", mention=None)
    return _send(to, subject, html, reply_to=email)
