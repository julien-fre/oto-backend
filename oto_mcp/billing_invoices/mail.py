"""L'e-mail qui porte la facture au contact de facturation (#488).

## ⚠️ Le PDF n'est pas en pièce jointe, et ce n'est pas un choix

Le relais transactionnel d'Otomata (`otomata-mailer`, `POST mailer.oto.zone/api/send`)
n'accepte que `{from, to, cc, replyTo, subject, html}` — son `sendMail` ne passe
aucun `attachments` à nodemailer (`otomata-tech/otomata-auth-mailer`, `src/send.ts`
et `src/index.ts`). **Joindre le PDF demande une évolution de CE service-là**, qui
vit dans un autre dépôt. L'e-mail porte donc le numéro, les montants, la période, et
un lien vers l'espace facturation où le document se télécharge.

Deux voies existent pour la pièce jointe, aucune ne se prend en silence :
ouvrir `attachments` sur le mailer (le PDF partirait alors d'ici), ou laisser
**Pennylane** l'envoyer lui-même (`POST customer_invoices/{id}/send_by_email`,
exposé par oto-core), au prix d'un expéditeur et d'un gabarit qui ne sont pas les
nôtres. C'est une décision d'Alexis, pas une correction à faire au passage.

Voix funnel du dépôt : FR, vouvoiement + minuscules.
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import email_brand as _charte
from ..email import _bouton, _esc, _send

logger = logging.getLogger(__name__)


def _euros(cents: Optional[int]) -> str:
    """Centimes → « 22,80 € ». `None` (ligne d'avant la règle de TVA) → « — » :
    afficher « 0,00 € » affirmerait une exonération qui n'a pas eu lieu."""
    if cents is None:
        return "—"
    return f"{cents / 100:.2f}".replace(".", ",") + " €"


def _jour(valeur) -> str:
    """La date seule d'un horodatage normalisé « YYYY-MM-DD HH:MM:SS »."""
    return str(valeur or "")[:10]


def send_invoice_email(to: str, invoice: dict, *, app_url: Optional[str] = None,
                       brand: str = "oto") -> bool:
    """Prévient le contact de facturation qu'un document est disponible.

    Best-effort, comme tout l'envoi transactionnel du dépôt : un e-mail non parti
    ne remet pas en cause la facture, qui est émise, numérotée et téléchargeable.
    L'échec se lit à `emailed_at IS NULL` sur la ligne."""
    m = _charte.marque(brand)
    avoir = invoice.get("kind") == "credit_note"
    quoi = "avoir" if avoir else "facture"
    numero = invoice.get("number") or ""
    titre = f"{quoi} {numero}".strip()
    subject = f"votre {quoi} {numero} — {m.nom}".replace("  ", " ").strip()

    periode = ""
    debut, fin = _jour(invoice.get("period_start")), _jour(invoice.get("period_end"))
    if debut and fin:
        periode = (f'<p style="{_charte.PARA}">période du {_esc(debut)} '
                   f'au {_esc(fin)}.</p>')

    mention = _esc(invoice.get("vat_mention") or "")
    bloc_mention = (f'<p style="{_charte.PARA_FIN};{_charte.discret(m)}">{mention}</p>'
                    if mention else "")

    contenu = (
        f'<p style="{_charte.PARA}">votre {_esc(quoi)} '
        f'<strong>{_esc(numero)}</strong> est disponible.</p>'
        f'<p style="{_charte.PARA}">{_esc(_euros(invoice.get("amount_ht")))} ht'
        f' · tva {_esc(_euros(invoice.get("vat_amount")))}'
        f' · <strong>{_esc(_euros(invoice.get("amount_ttc")))} ttc</strong></p>'
        f'{periode}'
        f'{_bouton(app_url, "télécharger le pdf", brand)}'
        f'{bloc_mention}'
    )
    html = _charte.page(
        m, contenu,
        preheader=f"{_euros(invoice.get('amount_ttc'))} ttc — {titre}",
        # Une facture ne se désabonne pas : la mention de pied dit d'où elle vient,
        # pas comment ne plus la recevoir.
        mention=f"document émis par {m.nom} pour votre abonnement.",
        locale=None)
    envoye = _send(to, subject, html)
    if not envoye:
        logger.warning("facturation: e-mail de la %s %s non parti (destinataire %s)",
                       quoi, titre or invoice.get("id"), to)
    return envoye
