"""Le DESSIN d'un email — la marque sous laquelle il part, et le gabarit qui l'habille.

`email.py` porte le TRANSPORT, `email_templates.py` le TEXTE ; ce module porte ce
que le destinataire VOIT. Trois choses le justifient, et aucune n'est cosmétique.

**1. La marque n'était qu'un mot.** Depuis 7d10a798 les six gabarits écrivent le
nom du produit du destinataire (`orgs.front_brand` / `config.front_for`) — mais la
couleur, elle, restait celle d'oto pour tout le monde. Un client de Tulina lisait
« sur tulina » en brun otomata, puis cliquait vers une application blanc-et-ardoise :
le mot suivait le tenant, le dessin non. Une marque est ici un **jeu de jetons**
(`Marque`), indexé par le MÊME slug que le texte, et il n'y a plus d'endroit où
l'un puisse suivre le tenant sans l'autre.

**2. Un slug inconnu ne doit rien casser.** `front_brand` est une colonne, pas une
énum : un tenant déclaré demain y écrira son slug avant que ce fichier le connaisse.
`marque()` rend alors le gabarit NEUTRE **portant son nom** — jamais une couleur
devinée, jamais le nom d'un autre produit. C'est la même règle que `links.py` : on
préfère l'absence d'affirmation à une affirmation fausse.

**3. Un email n'est pas une page web.** Le gabarit d'avant était un `<div>` avec un
`max-width` — Outlook ignore les deux, et l'email s'y étalait sur toute la fenêtre.
D'où ce qui suit, qui n'est pas un goût mais une contrainte de client :

- **tables imbriquées** pour la colonne et pour le bouton (Outlook/Word ne met en
  page que ça) ;
- **styles en ligne uniquement** — pas de `<style>` (Gmail le garde, mais pas dans
  l'aperçu ni chez tous les webmails), pas de variables CSS, pas de flex ni de grid ;
- **police système** : une webfont ne se charge pas dans la plupart des clients, donc
  `Inter` n'est qu'un premier choix devant une vraie pile de replis ;
- **preheader caché** : sans lui, la ligne d'aperçu de la boîte de réception affiche
  le premier texte venu (« ou collez ce lien »). C'est la deuxième chose qu'on lit
  d'un email, avant même de l'ouvrir ;
- **`color-scheme: light` déclaré** : les couleurs ci-dessous sont des valeurs claires
  littérales ; sans la déclaration, Outlook mobile et Apple Mail repeignent le fond en
  sombre et laissent le texte en place — c'est exactement l'accident qu'a connu la
  page de connexion Logto de Tulina (cf. `logto/custom.css` côté front).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ⚠️ Import de MODULE, jamais `from .email import _esc` — même règle, et pour la même
# raison, que l'en-tête d'`email_templates.py` : un nom importé à plat serait une
# copie figée que `monkeypatch.setattr(email, …)` ne toucherait jamais. C'est aussi
# ce qui rend le cycle inoffensif dans les deux sens : `email.py` importe ce module
# de la même façon (`from . import email_brand as _charte`) et personne ne dépend,
# au moment de l'import, d'un attribut de l'autre — seulement à l'appel.
from . import email as _email

# Une webfont ne se charge pas dans un client mail : `Inter` est le premier choix
# (il s'affiche chez qui l'a installée, et sur les clients web des deux produits),
# les suivantes sont ce que le reste du monde a réellement.
POLICE = ("Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
          "Helvetica,Arial,sans-serif")

# Largeur de la colonne. 600px est le plancher historique des clients de bureau ;
# le `max-width` la réduit sur mobile, l'attribut `width` sert Outlook qui l'ignore.
LARGEUR = 600
# Gouttière de 32px de chaque côté ⟹ ce qu'il reste pour une image pleine largeur.
LARGEUR_UTILE = LARGEUR - 64


@dataclass(frozen=True)
class Marque:
    """Ce qu'il faut savoir d'un produit pour lui écrire un email à SA tête.

    Les couleurs sont des littéraux clairs : aucun jeton, aucune variable — voir
    l'en-tête du module. `nom` est ce qui s'imprime (le slug est un identifiant, pas
    un mot de marque) ; `site` s'affiche en pied et n'est jamais un lien cliquable —
    un lien de pied vers le produit ferait doublon avec le bouton et donne un signal
    de spam de plus pour rien.
    """
    slug: str
    nom: str
    site: str
    fond: str        # derrière la carte
    surface: str     # la carte
    encre: str       # le texte
    discret: str     # le texte secondaire (dates, adresses, pied)
    filet: str       # bordure de carte et séparateur
    bouton_fond: str
    bouton_encre: str


# La charte des deux produits maison. Elles ne partagent PAS une palette : c'est le
# constat qui a motivé ce module. Tulina reprend le système de son application
# (échelle Radix Slate + `#1c2024`, cf. `src/app/globals.css` et `logto/custom.css`
# de tulina-app-front) ; oto garde sa charte chaude, à l'octet près — repeindre oto
# au passage aurait été une décision produit prise en douce.
MARQUES: dict[str, Marque] = {
    "oto": Marque(
        slug="oto", nom="oto", site="oto.cx",
        fond="#faf6ec", surface="#fffdf7", encre="#2c2112", discret="#7a6c50",
        filet="#ece4d0", bouton_fond="#2c2112", bouton_encre="#fefcf5",
    ),
    "tulina": Marque(
        slug="tulina", nom="Tulina", site="tulina.ai",
        fond="#f9f9fb", surface="#ffffff", encre="#1c2024", discret="#60646c",
        filet="#d9d9e0", bouton_fond="#1c2024", bouton_encre="#ffffff",
    ),
}

# Le gabarit d'un slug qu'on ne connaît pas : les gris neutres du système partagé
# par les deux fronts, et le NOM qu'on nous a donné. Pas de site (on ne l'invente
# pas), donc pas de ligne de pied à sa marque.
_NEUTRE = Marque(
    slug="", nom="", site="",
    fond="#f9f9fb", surface="#ffffff", encre="#1c2024", discret="#60646c",
    filet="#d9d9e0", bouton_fond="#1c2024", bouton_encre="#ffffff",
)


def marque(slug: Optional[str]) -> Marque:
    """La marque de ce slug. Inconnu ⟹ gabarit neutre **portant son nom**.

    On ne replie PAS sur oto : écrire « oto » en pied de l'email d'un partenaire est
    le faux que 7d10a798 a corrigé côté texte, et il reviendrait ici par la porte du
    dessin. Sans nom du tout (`None`, `""`), c'est bien oto — le défaut de
    `front_brand`, dont NULL veut dire « la plateforme »."""
    s = (slug or "").strip()
    if not s:
        return MARQUES["oto"]
    connue = MARQUES.get(s.lower())
    if connue is not None:
        return connue
    return Marque(**{**_NEUTRE.__dict__, "slug": s, "nom": s})


# --- Styles dérivés d'une marque -------------------------------------------
#
# Rendus par des fonctions plutôt que posés en constantes : une constante serait
# forcément celle d'UNE marque, et c'est précisément la faute qu'on répare.

PARA = "margin:0 0 16px"          # paragraphe courant (la cellule porte la typo)
PARA_FIN = "margin:0"             # dernier d'un bloc : pas de marge orpheline


def discret(m: Marque) -> str:
    """Texte secondaire : l'URL en clair sous un bouton, une date, le pied."""
    return f"color:{m.discret};font-size:13px;line-height:1.5"


def bouton(m: Marque, url: Optional[str], libelle: str) -> str:
    """Le bouton d'ouverture, ou RIEN — avec, dessous, l'adresse en clair.

    `url` vide rend la chaîne vide : le lien d'une vue dépend d'un patron déclaré par
    le tenant, et le produit du partenaire n'a pas forcément cette vue (`links.py`).
    Un email est un lien AFFICHÉ, pas une redirection : on n'écrit rien plutôt que
    d'envoyer quelque part.

    **Table, pas `<a>` seul** : Outlook (moteur Word) n'applique ni `padding` ni
    `border-radius` à un `<a>` en `inline-block` — le bouton s'y réduisait au texte.
    Le fond et l'arrondi vivent donc sur le `<td>`, le `<a>` ne porte que sa mise en
    page interne."""
    if not url:
        return ""
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:4px 0 12px"><tr>'
        f'<td align="center" style="border-radius:999px;background:{m.bouton_fond}">'
        f'<a href="{_email._esc_attr(url)}" style="display:inline-block;padding:12px 24px;'
        f'font-family:{POLICE};font-size:15px;font-weight:600;line-height:1;'
        f'color:{m.bouton_encre};text-decoration:none;border-radius:999px">'
        f'{_email._esc(libelle)}</a></td></tr></table>'
        # L'adresse en clair sous le bouton est échappée en ATTRIBUT alors qu'elle
        # est du texte : `_esc` laisse passer les guillemets, et un `"` venu d'une
        # URL d'agent traverserait alors le rendu intact. `&quot;` s'affiche `"` —
        # la propriété « aucune donnée d'entrée ne ressort avec un guillemet nu »
        # coûte donc zéro et se vérifie d'un seul coup d'œil.
        f'<p style="{PARA};{discret(m)};word-break:break-all">'
        f'{_email._esc_attr(url)}</p>'
    )


def _lien_desinscription(m: Marque, desinscription: Optional[tuple]) -> str:
    """Le lien « ne plus recevoir », ou RIEN.

    Il ne passe PAS par `mention` : cette phrase-là est échappée (aucun appelant n'a
    le droit d'y glisser du HTML), donc un lien y arriverait en toutes lettres. Il a
    son propre paramètre, une paire `(url, libellé)`, et l'URL est échappée en
    ATTRIBUT — un guillemet refermerait le `href` et la balise suivante serait celle
    de l'auteur du texte.

    Un `https://` est exigé : un lien de désinscription en clair est bloqué ou marqué
    « non sécurisé », et un désabonnement qu'on ne peut pas cliquer n'en est pas un.
    """
    if not desinscription:
        return ""
    url, libelle = desinscription
    url, libelle = (url or "").strip(), (libelle or "").strip()
    if not url or not libelle:
        return ""
    if not url.startswith("https://"):
        raise ValueError(f"lien de désinscription : https:// attendu (reçu {url[:24]!r}).")
    return (f'<br><a href="{_email._esc_attr(url)}" style="color:inherit">'
            f'{_email._esc(libelle)}</a>')


def _pied(m: Marque, mention: Optional[str],
          desinscription: Optional[tuple] = None) -> str:
    """Le pied : la signature de marque, puis la raison de l'envoi.

    Trois régimes, et la distinction porte du sens :
    `None` = **pas de pied du tout** (la carte s'arrête au contenu — ce que demande
    `footer=False`) ; `""` = la signature seule ; un texte = signature + raison.

    Sans `site` (marque inconnue), la ligne de signature disparaît plutôt que de
    porter un nom sans adresse — on n'invente pas le domaine d'un partenaire ; et un
    pied qui n'aurait alors NI signature NI mention ne se rend pas du tout.

    `desinscription` = `(url, libellé)` d'un lien de désabonnement, réservé au pied
    MARKETING (cf. `mention_transactionnelle`, qui n'en propose délibérément pas)."""
    if mention is None:
        return ""
    signature = f"{_email._esc(m.nom)} · {_email._esc(m.site)}<br>" if m.site else ""
    if not signature and not mention:
        return ""
    return (
        f'<tr><td style="padding:0 32px"><div style="border-top:1px solid {m.filet}">'
        f'</div></td></tr>'
        f'<tr><td style="padding:16px 32px 28px 32px;font-family:{POLICE};'
        f'{discret(m)}">{signature}{_email._esc(mention)}'
        f'{_lien_desinscription(m, desinscription)}</td></tr>'
    )


def mention_transactionnelle(m: Marque, locale: Optional[str]) -> str:
    """La phrase de pied d'un email TRANSACTIONNEL : pourquoi il arrive, et quoi
    faire s'il n'aurait pas dû.

    Elle ne propose pas de désabonnement, et c'est délibéré : on ne se désabonne pas
    d'une invitation ni d'un partage — le jour où l'on n'en veut plus, c'est le
    compte ou le partage qu'on retire, pas une case à décocher. Le pied MARKETING
    (`email.render_composed_email`) dit, lui, l'inverse, parce qu'il le doit."""
    if locale == "en":
        return (f"you're receiving this because something involving you happened on "
                f"{m.nom}. reply to this message if it looks wrong.")
    return (f"vous recevez ce message parce qu'une action vous concerne sur {m.nom}. "
            f"répondez-y si quelque chose cloche.")


def page(m: Marque, contenu: str, *, preheader: str, mention: Optional[str],
         locale: Optional[str] = None, desinscription: Optional[tuple] = None) -> str:
    """Le document complet : `<head>`, fond, carte, en-tête de marque, pied.

    `contenu` = les `<p>` déjà rendus par le gabarit (la cellule porte la typo, donc
    un paragraphe n'a que sa marge à déclarer — cf. `PARA`). `preheader` = la ligne
    d'aperçu de la boîte de réception ; `mention` = la phrase de pied qui dit
    pourquoi cet email arrive (`None` = aucun pied, cf. `_pied`). Les deux sont
    ÉCHAPPÉS ici, comme tout le reste : aucun appelant n'a le droit d'y passer du
    HTML.

    Le bourrage d'espaces de largeur nulle après le preheader existe parce que Gmail
    colle à la ligne d'aperçu le texte qui SUIT : sans lui, l'aperçu affiche le
    preheader puis le début du corps, recollés en une phrase qui n'a pas de sens.
    """
    lang = "en" if locale == "en" else "fr"
    bourrage = "&#8203;&#847;" * 60
    return (
        '<!DOCTYPE html>'
        f'<html lang="{lang}"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="x-apple-disable-message-reformatting">'
        '<meta name="color-scheme" content="light">'
        '<meta name="supported-color-schemes" content="light">'
        f'<title>{_email._esc(m.nom)}</title>'
        '</head>'
        f'<body style="margin:0;padding:0;background:{m.fond};'
        f'-webkit-text-size-adjust:100%">'
        f'<div style="display:none;max-height:0;max-width:0;opacity:0;overflow:hidden;'
        f'mso-hide:all;font-size:1px;line-height:1px;color:{m.fond}">'
        f'{_email._esc(preheader)}{bourrage}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="width:100%;background:{m.fond}">'
        f'<tr><td align="center" style="padding:32px 12px">'
        f'<table role="presentation" width="{LARGEUR}" cellpadding="0" cellspacing="0" '
        f'border="0" style="width:100%;max-width:{LARGEUR}px;background:{m.surface};'
        f'border:1px solid {m.filet};border-radius:12px">'
        f'<tr><td style="padding:28px 32px 0 32px;font-family:{POLICE};font-size:15px;'
        f'font-weight:600;letter-spacing:-0.01em;color:{m.encre}">{_email._esc(m.nom)}</td></tr>'
        f'<tr><td style="padding:20px 32px 4px 32px;font-family:{POLICE};'
        f'font-size:16px;line-height:1.6;color:{m.encre}">{contenu}</td></tr>'
        + _pied(m, mention, desinscription) +
        '</table></td></tr></table></body></html>'
    )
