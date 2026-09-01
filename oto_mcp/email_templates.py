"""Les 6 gabarits transactionnels de `email.py` — extraits d'ici pour deux
raisons (oto-backend#700).

**Place.** `email.py` frôlait déjà 500 lignes ; ajouter une deuxième langue par
gabarit l'aurait fait déborder. Le TRANSPORT (`_send`, l'anti-injection d'en-tête,
les envois BYO Resend/Scaleway TEM) reste dans `email.py` ; le TEXTE des 6
gabarits vit ici.

⚠️ **`import email as _email`, jamais `from .email import _send`.** On appelle
`_email._send(...)`, `_email._esc(...)`, `_email._bouton(...)`, `_email._BTN`
etc. — TOUJOURS qualifiés par le module, jamais des noms importés à plat.
`from .email import _send` capturerait la RÉFÉRENCE au moment de l'import, une
copie figée que `monkeypatch.setattr(email, "_send", ...)` (ou l'assignation
directe `mailer._send = ...` que fait `test_signal_reporter_notice.py`) ne
toucherait jamais — le test patcherait `email._send`, ce module continuerait
d'appeler l'ancien. Un import de MODULE reste une référence partagée : patcher
un attribut sur `email` est vu par `email_templates` immédiatement. C'est aussi
ce qui rend l'import circulaire inoffensif dans les deux sens : `email.py`
réexpose ces six fonctions (`from .email_templates import ...`) pour que
`email.send_invite_email` etc. restent des attributs valides du module `email`
— c'est ce que les tests monkeypatchent (`monkeypatch.setattr(email,
"send_invite_email", ...)`, `D.email.send_...`, `R.email.send_...`) — et
`import email as _email` ici ne dépend d'AUCUN attribut déjà défini, juste de
l'existence du module dans `sys.modules`.

**Locale, un seul paramètre.** Chaque gabarit prend `locale: str | None = None`.
`'en'` sert la version anglaise ; toute autre valeur (dont `None`, le cas
`users.locale IS NULL`) sert le FR — à l'octet près, comportement inchangé pour
un compte sans préférence. La validation de l'énum (`'en'|'fr'` strict) vit dans
la capacité `me.locale.set` (Input pydantic) : pas de normalisation de casse
ici, une comparaison directe suffit.

⚠️ **Aucune f-string imbriquée ne porte de backslash dans sa partie `{...}`** :
la box tourne en Python 3.10, où c'est une SyntaxError — un venv local plus
récent la compile sans broncher (cf. `send_signal_digest_email`, hérité tel
quel de `email.py`)."""
from __future__ import annotations

from . import email as _email


def send_invite_email(to: str, target_name: str | None, invite_url: str,
                      inviter: str | None = None, *, brand: str = "oto",
                      locale: str | None = None) -> bool:
    """Email d'invitation à rejoindre `brand`. True si envoyé, False sinon.

    `target_name` = ce qu'on rejoint (nom d'org OU d'équipe) ; None = invitation
    plateforme (onboarding pur → « rejoindre {brand} »). `brand` = le produit sous
    lequel l'org vit (`orgs.front_brand`, défaut oto) — la marque du TEXTE seulement :
    l'expéditeur reste le nôtre, un domaine d'envoi tiers supposerait sa vérification
    chez Scaleway TEM. `locale` = préférence du DESTINATAIRE (`users.locale`) ;
    voix funnel dans les deux langues : vouvoiement/« you » + minuscules."""
    if locale == "en":
        lead = f"{_email._esc(inviter)} invites you" if inviter else "you're invited"
        where = (f"<strong>{_email._esc(target_name)}</strong> on {_email._esc(brand)}" if target_name
                 else _email._esc(brand))
        subject = (f"invitation to join {target_name} on {brand}" if target_name
                   else f"invitation to join {brand}")
        html = (
            f'<div style="{_email._WRAP}">'
            f'<p>{lead} to join {where}.</p>'
            f'<p><a href="{_email._esc(invite_url)}" style="{_email._BTN}">join</a></p>'
            f'<p style="{_email._FAINT}">or paste this link: {_email._esc(invite_url)}</p>'
            f'</div>'
        )
        return _email._send(to, subject, html)
    lead = f"{_email._esc(inviter)} vous invite" if inviter else "vous êtes invité·e"
    # `brand` échappé pour le corps HTML, brut pour le sujet (qui n'est pas du HTML —
    # même traitement que `target_name` juste en dessous ; `_send` neutralise les CRLF).
    where = (f"<strong>{_email._esc(target_name)}</strong> sur {_email._esc(brand)}" if target_name
             else _email._esc(brand))
    subject = (f"invitation à rejoindre {target_name} sur {brand}" if target_name
               else f"invitation à rejoindre {brand}")
    html = (
        f'<div style="{_email._WRAP}">'
        f'<p>{lead} à rejoindre {where}.</p>'
        f'<p><a href="{_email._esc(invite_url)}" style="{_email._BTN}">rejoindre</a></p>'
        f'<p style="{_email._FAINT}">ou collez ce lien : {_email._esc(invite_url)}</p>'
        f'</div>'
    )
    return _email._send(to, subject, html)


def send_resource_shared_email(to: str, *, type_label: str, name: str | None,
                               permission: str, app_url: str,
                               sharer: str | None = None, brand: str = "oto",
                               locale: str | None = None) -> bool:
    """Email à un utilisateur avec qui on vient de PARTAGER une ressource (projet,
    datastore, guide). Best-effort (False si non envoyé) — un échec ne casse
    jamais le partage. `type_label` DOIT déjà être dans la langue de `locale` —
    ce gabarit ne traduit pas un mot qu'on lui donne. Voix funnel dans les deux
    langues : vouvoiement/« you » + minuscules."""
    if locale == "en":
        droit = "read access" if permission == "read" else "write access"
        titre = f"{type_label} “{name}”" if name else f"a {type_label}"
        who = f"{_email._esc(sharer)} shared" if sharer else "someone shared"
        subject = (f"{name} — {type_label} shared with you on {brand}" if name
                   else f"a {type_label} shared with you on {brand}")
        html = (
            f'<div style="{_email._WRAP}">'
            f'<p>{who} {_email._esc(titre)} with you ({droit}) on {_email._esc(brand)}.</p>'
            f'<p><a href="{_email._esc(app_url)}" style="{_email._BTN}">open in {_email._esc(brand)}</a></p>'
            f'<p style="{_email._FAINT}">{_email._esc(app_url)}</p>'
            f'</div>'
        )
        return _email._send(to, subject, html)
    droit = "en lecture" if permission == "read" else "en écriture"
    titre = f"{type_label} « {name} »" if name else f"un {type_label}"
    who = f"{_email._esc(sharer)} a partagé" if sharer else "on a partagé"
    subject = (f"{name} — {type_label} partagé avec vous sur {brand}" if name
               else f"un {type_label} partagé avec vous sur {brand}")
    html = (
        f'<div style="{_email._WRAP}">'
        f'<p>{who} avec vous {_email._esc(titre)} ({droit}) sur {_email._esc(brand)}.</p>'
        f'<p><a href="{_email._esc(app_url)}" style="{_email._BTN}">ouvrir dans {_email._esc(brand)}</a></p>'
        f'<p style="{_email._FAINT}">{_email._esc(app_url)}</p>'
        f'</div>'
    )
    return _email._send(to, subject, html)


def send_resource_transferred_email(to: str, *, type_label: str, name: str | None,
                                    app_url: str, sharer: str | None = None,
                                    brand: str = "oto",
                                    locale: str | None = None) -> bool:
    """Email à un utilisateur à qui on vient de TRANSFÉRER la propriété d'une
    ressource (ADR 0030). Best-effort. `type_label` déjà dans la langue de
    `locale`, cf. `send_resource_shared_email`. Voix funnel dans les deux
    langues : vouvoiement/« you » + minuscules."""
    if locale == "en":
        titre = f"{type_label} “{name}”" if name else f"a {type_label}"
        who = f"{_email._esc(sharer)} transferred" if sharer else "someone transferred"
        subject = (f"{name} — {type_label} transferred to you on {brand}" if name
                   else f"a {type_label} transferred to you on {brand}")
        html = (
            f'<div style="{_email._WRAP}">'
            f'<p>{who} ownership of <strong>{_email._esc(titre)}</strong> on {_email._esc(brand)} to '
            f'you — you are now the owner.</p>'
            f'<p><a href="{_email._esc(app_url)}" style="{_email._BTN}">open in {_email._esc(brand)}</a></p>'
            f'<p style="{_email._FAINT}">{_email._esc(app_url)}</p>'
            f'</div>'
        )
        return _email._send(to, subject, html)
    titre = f"{type_label} « {name} »" if name else f"un {type_label}"
    who = f"{_email._esc(sharer)} vous a transféré" if sharer else "on vous a transféré"
    subject = (f"{name} — {type_label} transféré à vous sur {brand}" if name
               else f"un {type_label} transféré à vous sur {brand}")
    html = (
        f'<div style="{_email._WRAP}">'
        f'<p>{who} la propriété de <strong>{_email._esc(titre)}</strong> sur {_email._esc(brand)} — '
        f'vous en êtes désormais propriétaire.</p>'
        f'<p><a href="{_email._esc(app_url)}" style="{_email._BTN}">ouvrir dans {_email._esc(brand)}</a></p>'
        f'<p style="{_email._FAINT}">{_email._esc(app_url)}</p>'
        f'</div>'
    )
    return _email._send(to, subject, html)


def send_change_request_email(to: str, *, project_name: str | None, doc_title: str | None,
                              proposer: str | None, is_create: bool,
                              app_url: str | None = None, brand: str = "oto",
                              locale: str | None = None) -> bool:
    """Email à un VALIDATEUR : une proposition de modification attend sa décision
    (« les lecteurs proposent / les auteurs valident », oto/#6). Best-effort. Voix
    funnel dans les deux langues : vouvoiement/« you » + minuscules."""
    if locale == "en":
        what = ("a new page" if is_create else
                f"a change to “{doc_title}”" if doc_title else "a change")
        where = f" in “{project_name}”" if project_name else ""
        who = f"{_email._esc(proposer)} is proposing" if proposer else "someone is proposing"
        subject = f"proposal to review on {brand}{f' — {project_name}' if project_name else ''}"
        html = (
            f'<div style="{_email._WRAP}">'
            f'<p>{who} {_email._esc(what)}{_email._esc(where)} on {_email._esc(brand)} — your review is '
            f'needed.</p>'
            f'{_email._bouton(app_url, "review and decide")}'
            f'</div>'
        )
        return _email._send(to, subject, html)
    what = "une nouvelle page" if is_create else f"une modification de « {doc_title} »" if doc_title else "une modification"
    where = f" dans « {project_name} »" if project_name else ""
    who = f"{_email._esc(proposer)} propose" if proposer else "on propose"
    subject = f"proposition à valider sur {brand}{f' — {project_name}' if project_name else ''}"
    html = (
        f'<div style="{_email._WRAP}">'
        f'<p>{who} {_email._esc(what)}{_email._esc(where)} sur {_email._esc(brand)} — votre validation est attendue.</p>'
        f'{_email._bouton(app_url, "revoir et décider")}'
        f'</div>'
    )
    return _email._send(to, subject, html)


def send_change_request_resolved_email(to: str, *, project_name: str | None, doc_title: str | None,
                                       accepted: bool, app_url: str | None = None,
                                       brand: str = "oto",
                                       locale: str | None = None) -> bool:
    """Email au PROPOSEUR : sa proposition a été acceptée ou refusée (oto/#6).
    Best-effort. Voix funnel dans les deux langues : vouvoiement/« you » + minuscules."""
    if locale == "en":
        verdict = "accepted" if accepted else "declined"
        what = f"your proposal on “{doc_title}”" if doc_title else "your proposal"
        where = f" in “{project_name}”" if project_name else ""
        subject = f"proposal {verdict} on {brand}{f' — {project_name}' if project_name else ''}"
        html = (
            f'<div style="{_email._WRAP}">'
            f'<p>{_email._esc(what)}{_email._esc(where)} was <strong>{verdict}</strong> on {_email._esc(brand)}.</p>'
            f'{_email._bouton(app_url, f"open in {brand}")}'
            f'</div>'
        )
        return _email._send(to, subject, html)
    verdict = "acceptée" if accepted else "refusée"
    what = f"votre proposition sur « {doc_title} »" if doc_title else "votre proposition"
    where = f" dans « {project_name} »" if project_name else ""
    subject = f"proposition {verdict} sur {brand}{f' — {project_name}' if project_name else ''}"
    html = (
        f'<div style="{_email._WRAP}">'
        f'<p>{_email._esc(what)}{_email._esc(where)} a été <strong>{verdict}</strong> sur {_email._esc(brand)}.</p>'
        f'{_email._bouton(app_url, f"ouvrir dans {brand}")}'
        f'</div>'
    )
    return _email._send(to, subject, html)


# Ce qu'un état d'arbitrage DIT à celui qui a signalé — pas le mot interne, dans
# les deux langues. « declined » se traduit « non retenu »/« not pursued » et
# jamais « refusé »/« rejected » : le rapporteur a rendu service en signalant,
# et le mot qui blesse est celui qu'on retient.
_VERDICT = {
    "resolved": ("traité", "✓"),
    "declined": ("non retenu", "—"),
}
_VERDICT_EN = {
    "resolved": ("done", "✓"),
    "declined": ("not pursued", "—"),
}


def send_signal_digest_email(to: str, *, items: list, brand: str = "oto",
                             locale: str | None = None) -> bool:
    """UN email pour TOUS les retours arbitrés d'une personne (#451). Best-effort.

    **Groupé par construction, et c'est la raison d'être de ce gabarit.** Mesuré le
    27/08 : 3 personnes portaient 168 des 204 signaux en attente, dont deux externes à
    51 et 53. Un envoi par signal aurait donc expédié cinquante mails d'affilée à un
    partenaire le jour où l'on vide la pile. Arbitrer un signal ⟹ un mail d'une ligne ;
    en arbitrer cinquante ⟹ un mail de cinquante lignes. Un seul chemin, les deux
    régimes — et ça vaut pour les deux langues : seul le TEXTE de chrome (sujet,
    intro, pied, verdicts) change avec `locale`, le regroupement est partagé.

    ⚠️ **On dit « vos agents »/« your agents », jamais « vous »/« you » seul.** Ces
    retours sont émis par des agents en session, sous le compte de cette personne —
    qui n'a le plus souvent jamais su qu'ils existaient. Lui écrire « votre
    signalement » serait lui attribuer des mots qu'elle n'a pas écrits.

    `items` = dicts `{status, target, created_at, body, resolution}` — prose libre
    écrite par un agent, jamais traduite (ni FR ni EN)."""
    if not items:
        return False
    en = locale == "en"
    n = len(items)
    if en:
        subject = (f"{n} update{'s' if n > 1 else ''} from your agents on {brand}: "
                   f"what happened")
    else:
        subject = (f"{n} retour{'s' if n > 1 else ''} de vos agents sur {brand} : "
                   f"ce qu'il en est")
    # ⚠️ Aucune f-string imbriquée portant un backslash ici : la box tourne en
    # **Python 3.10**, où « f-string expression part cannot include a backslash » est
    # une SyntaxError — alors qu'un venv local en 3.12+ la compile sans broncher. Le
    # boot preprod est mort dessus le 27/08, et ni les tests ni la CI ne l'ont vu :
    # les deux tournent sur un Python plus récent que le serveur. D'où des morceaux
    # assemblés en clair plutôt qu'une expression trop maligne.
    #
    # **On REGROUPE les arbitrages identiques.** 26 signalements du même défaut par la
    # même personne, c'est UN fait répété, pas 26 nouvelles : les lister un par un avec
    # la même phrase 26 fois transforme le retour en mur illisible — donc en spam, donc
    # en canal qu'on n'ouvre plus. La répétition d'un signal EST une information (elle
    # dit l'insistance), et elle se rend par un COMPTE et une période, pas par 26
    # paragraphes.
    groupes = []
    index = {}
    for it in items:
        cle = (str(it.get("status")), str(it.get("target")), str(it.get("resolution")))
        if cle not in index:
            index[cle] = {"it": it, "n": 0, "dates": []}
            groupes.append(index[cle])
        index[cle]["n"] += 1
        quand = str(it.get("created_at") or "")[:10]
        if quand:
            index[cle]["dates"].append(quand)

    verdicts = _VERDICT_EN if en else _VERDICT
    defaut = ("done", "·") if en else ("traité", "·")
    lignes = []
    for g in groupes:
        it, combien, dates = g["it"], g["n"], sorted(g["dates"])
        verdict, puce = verdicts.get(str(it.get("status")), defaut)
        cible = _email._esc(str(it.get("target") or "")) or ("(no target)" if en else "(sans cible)")
        # Le corps est de la PROSE LIBRE écrite par un agent : on en donne assez pour
        # que la personne reconnaisse de quoi on parle, jamais tout — c'est un rappel,
        # pas une archive.
        brut = str(it.get("body") or "").strip().replace("\n", " ")
        extrait = _email._esc(brut[:180])
        note = _email._esc(str(it.get("resolution") or "").strip())

        if combien > 1:
            periode = _email._esc(dates[0])
            if dates and dates[-1] != dates[0]:
                jonction = " to " if en else " au "
                periode = _email._esc(dates[0]) + jonction + _email._esc(dates[-1])
            gabarit_compte = " · %d reports, %s" if en else " · %d signalements, %s"
            compte = gabarit_compte % (combien, periode)
        else:
            compte = " (%s)" % _email._esc(dates[0]) if dates else ""

        date_html = f'<span style="{_email._FAINT}">{compte}</span>' if compte else ""
        guillemet = "“%s…”" if en else "« %s… »"
        extrait_html = (f'<br><span style="{_email._FAINT}">{guillemet % extrait}</span>'
                        if extrait else "")
        note_html = f"<br>{note}" if note else ""
        lignes.append(
            f'<p style="margin:0 0 14px"><strong>{puce} {_email._esc(verdict)}</strong> — '
            f'{cible}{date_html}{extrait_html}{note_html}</p>')

    # Ce qu'on annonce en tête est le nombre de RETOURS reçus, pas le nombre de
    # paragraphes : la personne compte ce qu'elle a envoyé, pas ce qu'on a su ranger.
    if en:
        intro = (f"your agents flagged {n} item{'s' if n > 1 else ''} on "
                 f"{_email._esc(brand)}. here's what happened.")
        pied = ("these updates are sent automatically by your agents when a tool "
                "misbehaves or a capability is missing. reply to this email if one "
                "of them deserves another look.")
    else:
        intro = (f'vos agents ont remonté {n} retour{"s" if n > 1 else ""} sur '
                 f'{_email._esc(brand)}. voici ce qu\'il en est advenu.')
        pied = ("ces retours sont émis automatiquement par vos agents quand un outil se "
                "comporte mal ou qu\'une capacité leur manque. répondez à ce mail si "
                "l\'un d\'eux mérite d\'être rouvert.")
    html = (f'<div style="{_email._WRAP}">'
            f'<p>{intro}</p>'
            + "".join(lignes)
            + f'<p style="{_email._FAINT}">{pied}</p>'
            f'</div>')
    return _email._send(to, subject, html)
