"""Envoi d'email transactionnel (invitations d'org) via **otomata-mailer**.

Standard Otomata : on n'utilise plus Resend per-app — l'endpoint générique
`POST mailer.oto.zone/api/send` (Scaleway TEM, brand Otomata, domaines from
vérifiés DKIM/SPF) sert les emails métier de toutes les apps. Bearer
`OTO_MAILER_SEND_BEARER`. **Best-effort** : sans bearer configuré ou en cas
d'échec, on ne lève pas — on renvoie False et l'appelant expose l'`invite_url`
pour un partage manuel.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("oto_mcp.email")

_MAILER_URL = os.environ.get("OTO_MAILER_URL", "https://mailer.oto.zone/api/send")
_MAIL_FROM = os.environ.get("OTO_MAIL_FROM", "Oto <oto@otomata.tech>")


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


_BTN = ('display:inline-block;background:#2c2112;color:#fefcf5;text-decoration:none;'
        'padding:10px 20px;border-radius:999px;font-weight:600')
_WRAP = 'font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;color:#2c2112'
_FAINT = 'color:#7a6c50;font-size:13px'


def _bouton(app_url: str | None, libelle: str) -> str:
    """Le bouton d'ouverture, ou RIEN.

    Le lien d'un projet dépend d'un patron déclaré par le tenant : le produit du
    partenaire n'a pas forcément cette vue, et coller NOTRE chemin sous SON domaine
    fabriquerait un lien mort — pire qu'une absence, parce qu'un lien mort ne se
    diagnostique pas, il se subit (cf. `links.py`). Un email est un lien AFFICHÉ, pas
    une redirection : on n'écrit rien plutôt que d'envoyer quelque part."""
    if not app_url:
        return ""
    return (f'<p><a href="{_esc(app_url)}" style="{_BTN}">{_esc(libelle)}</a></p>'
            f'<p style="{_FAINT}">{_esc(app_url)}</p>')


def send_invite_email(to: str, target_name: str | None, invite_url: str,
                      inviter: str | None = None, *, brand: str = "oto") -> bool:
    """Email d'invitation à rejoindre `brand`. True si envoyé, False sinon.

    `target_name` = ce qu'on rejoint (nom d'org OU d'équipe) ; None = invitation
    plateforme (onboarding pur → « rejoindre {brand} »). `brand` = le produit sous
    lequel l'org vit (`orgs.front_brand`, défaut oto) — la marque du TEXTE seulement :
    l'expéditeur reste le nôtre, un domaine d'envoi tiers supposerait sa vérification
    chez Scaleway TEM. Voix funnel : FR, vouvoiement + minuscules (alignée sur le
    dashboard)."""
    lead = f"{_esc(inviter)} vous invite" if inviter else "vous êtes invité·e"
    # `brand` échappé pour le corps HTML, brut pour le sujet (qui n'est pas du HTML —
    # même traitement que `target_name` juste en dessous ; `_send` neutralise les CRLF).
    where = (f"<strong>{_esc(target_name)}</strong> sur {_esc(brand)}" if target_name
             else _esc(brand))
    subject = (f"invitation à rejoindre {target_name} sur {brand}" if target_name
               else f"invitation à rejoindre {brand}")
    html = (
        f'<div style="{_WRAP}">'
        f'<p>{lead} à rejoindre {where}.</p>'
        f'<p><a href="{_esc(invite_url)}" style="{_BTN}">rejoindre</a></p>'
        f'<p style="{_FAINT}">ou collez ce lien : {_esc(invite_url)}</p>'
        f'</div>'
    )
    return _send(to, subject, html)


def send_resource_shared_email(to: str, *, type_label: str, name: str | None,
                               permission: str, app_url: str,
                               sharer: str | None = None, brand: str = "oto") -> bool:
    """Email à un utilisateur avec qui on vient de PARTAGER une ressource (projet,
    datastore, doctrine). Best-effort (False si non envoyé) — un échec ne casse
    jamais le partage. Voix funnel : FR, vouvoiement + minuscules."""
    droit = "en lecture" if permission == "read" else "en écriture"
    titre = f"{type_label} « {name} »" if name else f"un {type_label}"
    who = f"{_esc(sharer)} a partagé" if sharer else "on a partagé"
    subject = (f"{name} — {type_label} partagé avec vous sur {brand}" if name
               else f"un {type_label} partagé avec vous sur {brand}")
    html = (
        f'<div style="{_WRAP}">'
        f'<p>{who} avec vous {_esc(titre)} ({droit}) sur {_esc(brand)}.</p>'
        f'<p><a href="{_esc(app_url)}" style="{_BTN}">ouvrir dans {_esc(brand)}</a></p>'
        f'<p style="{_FAINT}">{_esc(app_url)}</p>'
        f'</div>'
    )
    return _send(to, subject, html)


def send_resource_transferred_email(to: str, *, type_label: str, name: str | None,
                                    app_url: str, sharer: str | None = None,
                                    brand: str = "oto") -> bool:
    """Email à un utilisateur à qui on vient de TRANSFÉRER la propriété d'une
    ressource (ADR 0030). Best-effort. Voix funnel : FR, vouvoiement + minuscules."""
    titre = f"{type_label} « {name} »" if name else f"un {type_label}"
    who = f"{_esc(sharer)} vous a transféré" if sharer else "on vous a transféré"
    subject = (f"{name} — {type_label} transféré à vous sur {brand}" if name
               else f"un {type_label} transféré à vous sur {brand}")
    html = (
        f'<div style="{_WRAP}">'
        f'<p>{who} la propriété de <strong>{_esc(titre)}</strong> sur {_esc(brand)} — '
        f'vous en êtes désormais propriétaire.</p>'
        f'<p><a href="{_esc(app_url)}" style="{_BTN}">ouvrir dans {_esc(brand)}</a></p>'
        f'<p style="{_FAINT}">{_esc(app_url)}</p>'
        f'</div>'
    )
    return _send(to, subject, html)


def send_change_request_email(to: str, *, project_name: str | None, doc_title: str | None,
                              proposer: str | None, is_create: bool,
                              app_url: str | None = None,
                              brand: str = "oto") -> bool:
    """Email à un VALIDATEUR : une proposition de modification attend sa décision
    (« les lecteurs proposent / les auteurs valident », oto/#6). Best-effort. Voix
    funnel : FR, vouvoiement + minuscules."""
    what = "une nouvelle page" if is_create else f"une modification de « {doc_title} »" if doc_title else "une modification"
    where = f" dans « {project_name} »" if project_name else ""
    who = f"{_esc(proposer)} propose" if proposer else "on propose"
    subject = f"proposition à valider sur {brand}{f' — {project_name}' if project_name else ''}"
    html = (
        f'<div style="{_WRAP}">'
        f'<p>{who} {_esc(what)}{_esc(where)} sur {_esc(brand)} — votre validation est attendue.</p>'
        f'{_bouton(app_url, "revoir et décider")}'
        f'</div>'
    )
    return _send(to, subject, html)


def send_change_request_resolved_email(to: str, *, project_name: str | None, doc_title: str | None,
                                       accepted: bool, app_url: str | None = None,
                                       brand: str = "oto") -> bool:
    """Email au PROPOSEUR : sa proposition a été acceptée ou refusée (oto/#6).
    Best-effort. Voix funnel : FR, vouvoiement + minuscules."""
    verdict = "acceptée" if accepted else "refusée"
    what = f"votre proposition sur « {doc_title} »" if doc_title else "votre proposition"
    where = f" dans « {project_name} »" if project_name else ""
    subject = f"proposition {verdict} sur {brand}{f' — {project_name}' if project_name else ''}"
    html = (
        f'<div style="{_WRAP}">'
        f'<p>{_esc(what)}{_esc(where)} a été <strong>{verdict}</strong> sur {_esc(brand)}.</p>'
        f'{_bouton(app_url, f"ouvrir dans {brand}")}'
        f'</div>'
    )
    return _send(to, subject, html)


# Ce qu'un état d'arbitrage DIT à celui qui a signalé — pas le mot interne.
# « declined » se traduit « non retenu » et jamais « refusé » : le rapporteur a rendu
# service en signalant, et le mot qui blesse est celui qu'on retient.
_VERDICT = {
    "resolved": ("traité", "✓"),
    "declined": ("non retenu", "—"),
}


def send_signal_digest_email(to: str, *, items: list, brand: str = "oto") -> bool:
    """UN email pour TOUS les retours arbitrés d'une personne (#451). Best-effort.

    **Groupé par construction, et c'est la raison d'être de ce gabarit.** Mesuré le
    27/08 : 3 personnes portaient 168 des 204 signaux en attente, dont deux externes à
    51 et 53. Un envoi par signal aurait donc expédié cinquante mails d'affilée à un
    partenaire le jour où l'on vide la pile. Arbitrer un signal ⟹ un mail d'une ligne ;
    en arbitrer cinquante ⟹ un mail de cinquante lignes. Un seul chemin, les deux
    régimes.

    ⚠️ **On dit « vos agents », jamais « vous ».** Ces retours sont émis par des agents
    en session, sous le compte de cette personne — qui n'a le plus souvent jamais su
    qu'ils existaient. Lui écrire « votre signalement » serait lui attribuer des mots
    qu'elle n'a pas écrits, et rendre le mail incompréhensible.

    `items` = dicts `{status, target, created_at, body, resolution}`. Voix funnel :
    FR, vouvoiement + minuscules."""
    if not items:
        return False
    n = len(items)
    subject = (f"{n} retour{'s' if n > 1 else ''} de vos agents sur {brand} : "
               f"ce qu'il en est")
    # ⚠️ Aucune f-string imbriquée portant un backslash ici : la box tourne en
    # **Python 3.10**, où « f-string expression part cannot include a backslash » est
    # une SyntaxError — alors qu'un venv local en 3.12+ la compile sans broncher. Le
    # boot preprod est mort dessus le 27/08, et ni les tests ni la CI ne l'ont vu :
    # les deux tournent sur un Python plus récent que le serveur. D'où des morceaux
    # assemblés en clair plutôt qu'une expression trop maligne.
    lignes = []
    for it in items:
        verdict, puce = _VERDICT.get(str(it.get("status")), ("traité", "·"))
        quand = str(it.get("created_at") or "")[:10]
        cible = _esc(str(it.get("target") or "")) or "(sans cible)"
        # Le corps est de la PROSE LIBRE écrite par un agent : on en donne assez pour
        # que la personne reconnaisse de quoi on parle, jamais tout — c'est un rappel,
        # pas une archive.
        brut = str(it.get("body") or "").strip().replace("\n", " ")
        extrait = _esc(brut[:180])
        note = _esc(str(it.get("resolution") or "").strip())

        date_html = f' <span style="{_FAINT}">({_esc(quand)})</span>' if quand else ""
        extrait_html = f'<br><span style="{_FAINT}">« {extrait}… »</span>' if extrait else ""
        note_html = f"<br>{note}" if note else ""
        lignes.append(
            f'<p style="margin:0 0 14px"><strong>{puce} {_esc(verdict)}</strong> — '
            f'{cible}{date_html}{extrait_html}{note_html}</p>')

    intro = (f'vos agents ont remonté {n} retour{"s" if n > 1 else ""} sur '
             f'{_esc(brand)}. voici ce qu\'il en est advenu.')
    pied = ("ces retours sont émis automatiquement par vos agents quand un outil se "
            "comporte mal ou qu\'une capacité leur manque. répondez à ce mail si "
            "l\'un d\'eux mérite d\'être rouvert.")
    html = (f'<div style="{_WRAP}">'
            f'<p>{intro}</p>'
            + "".join(lignes)
            + f'<p style="{_FAINT}">{pied}</p>'
            f'</div>')
    return _send(to, subject, html)


def render_composed_email(
    body: str,
    *,
    cta_text: str | None = None,
    cta_url: str | None = None,
    footer: bool = True,
) -> str:
    """Rend le HTML à la charte « manuscrit chaud » d'un email dont le **contenu
    est fourni par l'agent** (prose brute + CTA optionnel).

    `body` = texte brut : les lignes vides séparent des paragraphes, les sauts de
    ligne simples deviennent des `<br>`. Échappé (jamais de HTML injecté par
    l'agent). `footer` ajoute la signature de marque + l'opt-out par réponse."""
    paras = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    body_html = "".join(
        f'<p style="font-size:16px;line-height:1.6;margin:0 0 16px">'
        f'{_esc(p).replace(chr(10), "<br>")}</p>'
        for p in paras
    )
    cta_html = ""
    if cta_text and cta_url:
        cta_html = (
            f'<p style="padding:8px 0"><a href="{_esc(cta_url)}" style="{_BTN}">'
            f'{_esc(cta_text)}</a></p>'
        )
    footer_html = ""
    if footer:
        footer_html = (
            '<hr style="border:none;border-top:1px solid #ece4d0;margin:24px 0 16px">'
            f'<p style="{_FAINT}">oto, par otomata · oto.cx<br>'
            'vous recevez ce message car vous avez un compte oto — '
            'répondez à cet email pour nous parler, ou pour ne plus en recevoir.</p>'
        )
    return f'<div style="{_WRAP}">{body_html}{cta_html}{footer_html}</div>'


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
) -> bool:
    """Envoie un email à contenu libre (fourni par l'agent), rendu à la charte, via
    le mailer Otomata (Scaleway TEM).

    `from_email`/`from_name` = adresse expéditrice (défaut = marque `_MAIL_FROM`) ;
    le domaine doit être dans l'allowlist du service. `reply_to` défaut = la boîte
    du studio (`OTO_CONTACT_TO`). True si envoyé, False sinon (best-effort)."""
    html = render_composed_email(body, cta_text=cta_text, cta_url=cta_url, footer=footer)
    rt = reply_to or os.environ.get("OTO_CONTACT_TO", "alexis@otomata.tech")
    return _send(to, subject, html, reply_to=rt, from_email=format_from(from_email, from_name))


def send_contact_email(name: str, email: str, message: str) -> bool:
    """Message du formulaire de contact d'otomata.tech → boîte du studio.

    `reply_to` = l'email du visiteur pour répondre en un clic. Destinataire
    configurable via `OTO_CONTACT_TO` (défaut alexis@otomata.tech)."""
    to = os.environ.get("OTO_CONTACT_TO", "alexis@otomata.tech")
    subject = f"otomata.tech — message de {name}"
    body = _esc(message).replace("\n", "<br>")
    html = (
        f'<div style="{_WRAP}">'
        f'<p style="{_FAINT}">nouveau message via otomata.tech</p>'
        f'<p><strong>{_esc(name)}</strong> &lt;{_esc(email)}&gt;</p>'
        f'<hr style="border:none;border-top:1px solid #ece4d0;margin:16px 0">'
        f'<p>{body}</p>'
        f'</div>'
    )
    return _send(to, subject, html, reply_to=email)
