"""Email — envoi d'un message à contenu libre (rédigé par l'agent), per-org.

L'adresse expéditrice appartient à un **connecteur email** de l'org (config keyée
par connecteur dans `orgs.email_settings`) ; le **transport en dérive**
(`providers.EMAIL_CONNECTOR_TRANSPORT`) :
- connecteur **`scaleway`** → transport `mailer` : service Otomata `mailer.oto.zone`
  (Scaleway TEM). Domaine vérifié côté TEM **et** dans l'allowlist `MAILER_FROM_DOMAINS`.
  La clé d'envoi reste celle d'Otomata (pas de clé d'org).
- connecteur **`resend`** → transport `resend` : BYOK, appel direct de l'API Resend
  avec la **clé Resend de l'org** (coffre, `access.resolve_api_key("resend")`). Domaine
  vérifié côté Resend par l'org.

Autorisation **dynamique** selon le `from` résolu :
- envoi depuis une adresse déclarée de l'org → **membre de l'org** suffit ;
- repli **marque** = l'expéditeur de l'instance (`OTO_MAIL_FROM` ; org sans adresse configurée, `from` omis) →
  réservé **super_admin** (c'est l'identité de marque de la plateforme).

À distinguer de `gmail_compose`, qui écrit depuis la boîte Gmail de l'utilisateur
(et qui, lui, rédige un BROUILLON par défaut — envoyer y est explicite).

Spine : chargé explicitement dans `register_all`, hors gate d'activation, masqué
par défaut (`PROTECTED_TOOLS`/`DEFAULT_HIDDEN_TOOLS` côté visibilité).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastmcp import Context, FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INTERNAL_ERROR, INVALID_PARAMS

from .. import access, config, db, email as mailer, org_store, providers, roles, scheduler, session_org
from ..auth.hooks import current_user_sub_from_token

logger = logging.getLogger(__name__)


def _cle_d_org_absente(sub: str, org_id, connecteur: str, libelle: str) -> str:
    """Le refus d'un envoi différé sans la clé d'org de son transport — il dit QUI la pose
    et OÙ (oto#108). Il renvoyait vers `oto_set_org_secret`, un outil retiré le
    25/06/2026 : la destination nommée n'existait plus."""
    from .. import detenteurs, links
    return (f"Transport {libelle} sans clé d'org : un administrateur de l'org la pose"
            f"{links.ou_poser_la_cle(sub, org=org_id, connecteur=connecteur)} avant de "
            "programmer."
            + detenteurs.phrase("Administrateurs de cette org",
                                detenteurs.admins_de_l_org(sub, org_id)))


_CC_MAX = 10

# Une adresse en copie : `local@domaine.tld`, ASCII, rien d'autre. Un nom affiché, une
# virgule ou un saut de ligne dans `cc` deviendraient des destinataires de plus ou un
# en-tête injecté chez le relais ; une adresse que ce motif refuse se signale, elle ne
# se corrige pas.
_ADRESSE_RE = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")

# Plafond quotidien de DESTINATAIRES (`to` + `cc`) par org sur le transport COMMUN — le
# relais de l'instance, sous SA clé et SON domaine (`OTO_MAILER_URL`). Décision d'Alexis
# du 04/10/2026 : la copie est permise, mais un envoi sur la clé commune engage la
# réputation de TOUS les envois de l'instance (activation, relances, résumés) ; une
# org qui envoie avec SA clé (Resend, Scaleway TEM) n'a aucun plafond de plateforme.
# 200 : vingt envois à dix copies, ou deux cents envois simples, par jour — au-dessus
# d'une séquence d'onboarding pilotée à la main, en dessous d'un publipostage. Réglable
# par instance (`OTO_EMAIL_PLATFORM_DAILY_RECIPIENTS`), lu à chaque envoi.
_PLAFOND_COMMUN_DEFAUT = 200


def _plafond_commun() -> int:
    """Le plafond du jour sur le transport commun. Une valeur illisible LÈVE : un
    plafond deviné serait un plafond que personne n'a posé."""
    raw = os.environ.get("OTO_EMAIL_PLATFORM_DAILY_RECIPIENTS")
    if raw is None:
        return _PLAFOND_COMMUN_DEFAUT
    try:
        valeur = int(raw)
    except ValueError:
        raise RuntimeError(f"OTO_EMAIL_PLATFORM_DAILY_RECIPIENTS illisible : {raw!r} "
                           "(attendu un entier >= 0).")
    if valeur < 0:
        raise RuntimeError(f"OTO_EMAIL_PLATFORM_DAILY_RECIPIENTS négatif : {valeur}.")
    return valeur


def _garder_plafond_commun(sub: str, destinataires: int) -> None:
    """Refuse un envoi sur le transport commun qui dépasserait le plafond du jour de
    l'org de l'appel. Compté dans le journal des appels (`tool_calls.quantity` des
    `email_send` passés sous `key_mode='platform'`, cf. `_metrer_commun`), remis à
    zéro à minuit UTC : aucune table de plus. Le journal s'écrit à la fin de l'appel —
    deux envois simultanés peuvent se croiser, le plafond est une borne de réputation,
    pas un compte facturé."""
    plafond = _plafond_commun()
    org = access.current_org(sub)
    deja = db.destinataires_communs_du_jour(org_id=org, sub=sub)
    if deja + destinataires <= plafond:
        return
    raise McpError(ErrorData(
        code=INVALID_PARAMS,
        message=(f"Plafond quotidien du transport commun atteint : {deja}/{plafond} "
                 f"destinataire(s) aujourd'hui pour cette org, cet envoi en compte "
                 f"{destinataires} (`to` + `cc`). Réessaie après minuit UTC, réduis les "
                 "copies, ou envoie depuis une adresse d'un connecteur email de l'org "
                 "(sa propre clé Resend ou Scaleway TEM : aucun plafond de plateforme)."),
        data={"code": "platform_email_daily_cap", "retryable": True,
              "limit": plafond, "used": deja, "units": destinataires}))


def _metrer_commun(destinataires: int) -> None:
    """Consigne au journal de l'appel ce qu'un envoi sur le transport commun a
    consommé : c'est ce que `_garder_plafond_commun` relit."""
    session_org.note_call_trace(quantity=destinataires, key_mode="platform")


def _err(msg: str, code: int = INVALID_PARAMS) -> McpError:
    return McpError(ErrorData(code=code, message=msg))


def _sub_or_raise() -> str:
    # Un échec d'identité MONTE (le seam le journalise avec sa raison, #464) : seul
    # un appel réellement sans jeton est « non authentifié ».
    sub = current_user_sub_from_token()
    if not sub:
        raise _err("Auth requise — ce tool ne marche que sur le transport HTTP authentifié.")
    return sub


def _resolve_route(from_email: Optional[str]) -> tuple[str, dict]:
    """Résout (sub, route) et APPLIQUE l'autorisation. `route` = {org_id, connector,
    from_email, from_name, transport, reply_to, quiet_hours} ; from_email=None +
    org sans expéditeur ⇒ marque par défaut. Lève McpError actionnable sinon.

    Le TRANSPORT dérive du CONNECTEUR de l'expéditeur (scaleway→mailer, resend→resend)."""
    sub = _sub_or_raise()
    org = access.current_org(sub)

    # Chemin org : une adresse déclarée d'un connecteur email de l'org active
    if org is not None:
        match = org_store.resolve_sender(org, from_email)
        if match is not None:
            sender, connector = match
            if not roles.is_org_member(sub, org):
                raise _err("Tu n'es pas membre de l'org active — passe `org=<id>` sur cet appel.")
            transport = providers.EMAIL_CONNECTOR_TRANSPORT.get(connector)
            if transport is None:
                raise _err(f"Connecteur email inconnu pour « {sender.get('email')} » : {connector!r}.")
            return sub, {
                "org_id": org,
                "connector": connector,
                "from_email": sender.get("email"),
                "from_name": sender.get("name"),
                "transport": transport,
                "reply_to": sender.get("reply_to"),
                "quiet_hours": org_store.org_email_quiet_hours(org, connector),
                "footer": org_store.org_email_footer(org, connector),
            }
        if from_email is not None:
            raise _err(f"« {from_email} » n'est pas une adresse déclarée d'un connecteur email de "
                       "l'org active. Ajoute-la via `oto_org_settings(domain='email', op='set')`, ou omets `from_email`.")

    # Chemin marque (l'expéditeur de l'instance, `OTO_MAIL_FROM`) — super_admin uniquement
    if from_email is not None:
        raise _err("Aucune org active avec une adresse d'envoi configurée. Configure-la "
                   "(`oto_org_settings(domain='email', op='set')`) ou passe la bonne org (`org=<id>`).")
    if not access.is_super_admin(sub):
        raise _err("Ton org n'a pas d'adresse d'envoi configurée — demande à un org_admin "
                   "de l'ajouter via `oto_org_settings(domain='email', op='set')`. L'envoi sous "
                   f"l'adresse de la plateforme ({mailer._mail_from()}) est réservé au "
                   "super_admin de la plateforme.")
    return sub, {"org_id": None, "connector": None, "from_email": None, "from_name": None,
                 "transport": "mailer", "reply_to": None, "quiet_hours": None, "footer": None}


def _cle_de_l_org(connector: Optional[str]) -> bool:
    """L'envoi part-il avec la clé de l'ORG ? Dérivé du registre, jamais d'une liste
    recopiée : un connecteur email sans palier `platform` ne peut envoyer qu'avec une
    clé apportée (cascade byo). Le repli marque (`connector=None`, clé commune du
    mailer) rend False — notre pied y reste toujours.

    Si un connecteur email gagnait un jour un palier plateforme, ceci rendrait False
    pour TOUS ses envois : notre pied reviendrait, faute de savoir au rendu (avant la
    mise en file) quelle clé partira. C'est le côté sûr ; le séparer demanderait de
    rendre le pied APRÈS la résolution de la clé."""
    c = providers.connector_for_provider(connector) if connector else None
    return c is not None and bool(c.auth_modes) and "platform" not in c.auth_modes


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def email_send(
        ctx: Context,
        to: str,
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        from_email: Optional[str] = None,
        cta_text: Optional[str] = None,
        cta_url: Optional[str] = None,
        image_url: Optional[str] = None,
        image_alt: Optional[str] = None,
        reply_to: Optional[str] = None,
        send_at: Optional[str] = None,
        force_now: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Envoie un email à contenu libre depuis une adresse de TON org active,
        rendu à la charte, avec au choix un bouton et UNE image de tête. Peut être
        DIFFÉRÉ.

        L'org déclare ses adresses expéditrices (`oto_org_settings domain=email`) ;
        chacune envoie soit via le mailer Otomata (domaine vérifié côté TEM), soit
        via la clé Resend de l'org. Usage type — séquences d'onboarding pilotées
        par l'agent : lis l'état du compte cible, rédige un message ADAPTÉ, envoie,
        puis trace dans le datastore pour ne pas relancer en double. Pour envoyer
        depuis la boîte Gmail de l'utilisateur, c'est `gmail_compose` — attention, lui
        rédige un BROUILLON par défaut, il faut `mode="send"` pour qu'il parte.

        Envoi différé : par défaut l'org a une fenêtre « quiet hours » (ex. 20h–8h) ;
        si tu composes dedans, l'envoi est AUTO-décalé au prochain créneau ouvert —
        tu n'as rien à calculer. Laisse `send_at` vide dans ce cas. Pour une heure
        précise, passe `send_at`. Pour forcer un envoi immédiat malgré les quiet
        hours, `force_now=True`. Gère/annule la file : `oto_scheduled_emails(op='list'|'cancel')`.

        Pied de page : un envoi avec la clé de ton org (Resend, Scaleway TEM) porte le
        pied de la PLATEFORME, sauf si l'org a déclaré SON désabonnement sur ce
        connecteur — `oto_org_settings(domain='email', op='set', connector=…,
        footer={"unsubscribe_url": "https://…"} ou {"unsubscribe_email": "…"})`,
        org_admin : son pied remplace alors le nôtre. Rien ne le retire depuis cet
        outil. L'envoi sous l'adresse de la plateforme garde toujours le nôtre. Le champ
        `footer` de la réponse dit lequel part (`org` | `platform`).

        Image de tête : `image_url` (https) + `image_alt` REQUIS ; l'URL publique
        vient de `oto_upload_url(target="image")` (un upload, réutilisable).

        Renvoie {sent, to, cc, subject, from, transport, footer} en envoi immédiat ;
        {scheduled, id, scheduled_at, ...} si différé ; +`html` si dry_run.

        Args:
            to: adresse email du destinataire.
            cc: adresses en copie visible (liste, max 10), `nom@domaine.tld` sans nom
                affiché. Pas de copie cachée. Sous l'adresse de la plateforme, `to` +
                `cc` comptent dans un plafond quotidien de destinataires par org.
            subject: objet (voix funnel oto : minuscules, vouvoiement).
            body: corps en texte brut. Les lignes vides séparent les paragraphes ;
                les sauts de ligne simples sont conservés. Le HTML est échappé
                (n'injecte pas de balises). Écris du contenu réel, personnalisé —
                jamais d'invention sur le compte du destinataire.
            from_email: adresse expéditrice. DOIT être une adresse déclarée de l'org
                active. Omise = l'adresse par défaut de l'org (ou l'adresse de la
                plateforme si l'org n'en a aucune — super_admin uniquement).
            cta_text: libellé d'un bouton d'action optionnel (ex. « ouvrir oto »).
            cta_url: URL du bouton (requis si `cta_text` est fourni).
            image_url: URL `https://` publique d'UNE image en tête du mail (480 px,
                réduite sur mobile). Pour la publier : `oto_upload_url(target="image")`
                → PUT le fichier → l'accusé rend `url` (permanente, réutilisable).
            image_alt: texte de remplacement, REQUIS avec `image_url` — beaucoup de
                clients bloquent les images, le mail doit garder son sens sans elle.
            reply_to: adresse de réponse (défaut = celle du sender, sinon la boîte
                du studio).
            send_at: heure d'envoi souhaitée (ISO 8601, ex. "2026-06-24T08:00").
                Sans fuseau = fuseau de l'org. Passée = programme à cette heure.
            force_now: envoie tout de suite même dans la fenêtre quiet hours.
            dry_run: si vrai, REND le HTML sans envoyer — pour relire avant l'envoi.
        """
        to = (to or "").strip()
        subject = (subject or "").strip()
        if not to or "@" not in to:
            raise _err("`to` doit être une adresse email valide.")
        if not subject:
            raise _err("`subject` est requis.")
        if not (body or "").strip():
            raise _err("`body` est requis.")
        if cta_text and not cta_url:
            raise _err("`cta_url` est requis avec `cta_text`.")
        # Borné AVANT tout traitement : une liste de mille entrées identiques ne doit
        # pas coûter mille validations pour finir dédoublonnée sous la borne.
        if len(cc or []) > _CC_MAX:
            raise _err(f"`cc` : {_CC_MAX} adresses au plus.")
        copies: list[str] = []
        for a in cc or []:
            a = (a or "").strip()
            if len(a) > 254 or not _ADRESSE_RE.fullmatch(a):
                raise _err(f"`cc` : adresse invalide {a!r} (attendu `nom@domaine.tld`, "
                           "une adresse par élément, sans nom affiché).")
            if a.lower() != to.lower() and a.lower() not in {c.lower() for c in copies}:
                copies.append(a)
        # Le gabarit porte les refus de l'image (alt manquant, `http://`) : on les
        # déclenche AVANT de résoudre la route, pour que le refus d'un paramètre
        # précède celui d'une autorisation — comme les vérifications juste au-dessus.
        # La fonction est PURE : la rappeler au rendu ne coûte rien, et c'est ce qui
        # permet de garder cet ordre tout en rendant, plus bas, à la bonne marque.
        try:
            mailer._image_html(image_url, image_alt)
        except ValueError as e:
            raise _err(str(e))

        sub, route = _resolve_route((from_email or "").strip() or None)
        # La marque de CELUI QUI ENVOIE : un client du partenaire dont l'agent écrit à un
        # prospect signait « oto, par otomata · oto.cx » en pied — le pied d'un
        # produit qu'il n'a jamais vu, sous son propre nom de domaine d'envoi.
        #
        # ⚠️ Dérivée du `sub` que la route vient d'authentifier, JAMAIS d'un appel
        # d'auth de plus : celui-ci lèverait avant les refus de paramètre ci-dessus et
        # inverserait l'ordre des erreurs de cet outil.
        marque_expediteur = config.front_for(sub)[1] or "oto"
        # Le pied de l'ORG remplace le nôtre sur un envoi fait avec SA clé, et là
        # seulement (décision d'Alexis du 12/09/2026, conformité) : apporter sa clé
        # changeait le transport et pas le gabarit, si bien qu'un prospect froid lisait
        # « vous avez un compte oto » et se voyait proposer de se désabonner auprès de
        # nous. Sur la clé commune, jamais — la condition est ici, pas dans la route.
        pied_org = route.get("footer") if _cle_de_l_org(route["connector"]) else None
        pied = "org" if pied_org else "platform"
        html = mailer.render_composed_email(body, cta_text=cta_text, cta_url=cta_url,
                                            image_url=image_url, image_alt=image_alt,
                                            brand=marque_expediteur, org_footer=pied_org)
        org_id = route["org_id"]
        from_hdr = mailer.format_from(route["from_email"], route["from_name"]) or mailer._mail_from()
        transport = route["transport"]
        rt = reply_to or route["reply_to"]

        if dry_run:
            return {"sent": False, "dry_run": True, "to": to, "cc": copies, "subject": subject,
                    "from": from_hdr, "transport": transport, "footer": pied, "html": html}

        # Quiet hours du CONNECTEUR de l'expéditeur (résolues dans la route). Repli
        # marque (org=None / pas de connecteur) → désactivé (seul send_at diffère).
        quiet = route.get("quiet_hours") or {"start": 0, "end": 0}
        try:
            when = scheduler.compute_scheduled_at(
                datetime.now(timezone.utc), quiet, send_at, force_now)
        except ValueError:
            raise _err(f"`send_at` invalide : {send_at!r} (attendu ISO 8601, ex. "
                       "2026-06-24T08:00).")

        destinataires = 1 + len(copies)
        if transport == "mailer":
            _garder_plafond_commun(sub, destinataires)

        if when is not None:
            # Envoi différé → mise en file (HTML rendu + autz déjà figés).
            for nom, libelle in (("resend", "Resend"), ("scaleway", "Scaleway TEM")):
                if transport == nom and not (org_id and org_store.has_org_secret(org_id, nom)):
                    raise _err(_cle_d_org_absente(sub, org_id, nom, libelle))
            sched_id = db.enqueue_scheduled_email(
                org_id=org_id, created_by=sub, to_email=to, subject=subject, body_html=html,
                from_email=route["from_email"], from_name=route["from_name"],
                reply_to=rt, transport=transport, scheduled_at=when, cc=copies)
            if transport == "mailer":
                _metrer_commun(destinataires)   # compté le jour où il est programmé
            logger.info("email_send différé #%d → %s à %s (transport=%s)",
                        sched_id, to, when.isoformat(), transport)
            return {"sent": False, "scheduled": True, "id": sched_id,
                    "scheduled_at": when.isoformat(), "to": to, "cc": copies, "subject": subject,
                    "from": from_hdr, "transport": transport, "footer": pied}

        # Envoi immédiat.
        if transport == "resend":
            api_key, _key_is_platform = access.resolve_api_key("resend")  # cascade user > org ; lève si absente
            ok = mailer.send_via_resend(to, subject, html, api_key=api_key,
                                        from_email=from_hdr, reply_to=rt, cc=copies)
        elif transport == "scaleway":
            f = access.resolve_credential_fields("scaleway")  # cascade → clé de l'org
            if not f.get("secret_key") or not f.get("project_id"):
                raise _err("Connecteur Scaleway TEM non configuré pour ton org : pose "
                           "`secret_key` + `project_id` (clé de TON compte Scaleway TEM).")
            ok = mailer.send_via_scaleway_tem(
                to, subject, html, secret_key=f["secret_key"], project_id=f["project_id"],
                region=f.get("region") or "fr-par",
                from_email=route["from_email"], from_name=route["from_name"], reply_to=rt,
                cc=copies)
        else:
            ok = mailer.send_composed_email(
                to, subject, body, cta_text=cta_text, cta_url=cta_url, reply_to=rt,
                from_email=route["from_email"], from_name=route["from_name"],
                image_url=image_url, image_alt=image_alt, brand=marque_expediteur, cc=copies)

        if not ok:
            hint = ("clé Resend invalide/absente" if transport == "resend"
                    else "clé/projet Scaleway TEM absent, ou domaine du `from` non vérifié "
                         "dans ton compte Scaleway" if transport == "scaleway"
                    else "mailer indisponible, ou domaine du `from` hors allowlist "
                         "`MAILER_FROM_DOMAINS` (demande l'ajout à un super_admin)")
            raise _err(f"Envoi échoué ({hint}). Rien n'a été envoyé.", code=INTERNAL_ERROR)
        if transport == "mailer":
            _metrer_commun(destinataires)
        logger.info("email_send → %s (cc=%d, from=%r, transport=%s)", to, len(copies),
                    from_hdr, transport)
        return {"sent": True, "dry_run": False, "to": to, "cc": copies, "subject": subject,
                "from": from_hdr, "transport": transport, "footer": pied}
