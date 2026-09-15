"""Marque des pages publiques du backend — favicon, et à QUI la page appartient.

La source de vérité du mark est `oto-studio/brand/` : le fichier recopié ici est
`brand/logos/oto/oto-dashboard-mark.svg`, l'« open O » saffran (anneau ouvert,
caps arrondis). On le duplique en inline pour que les pages et endpoints
auto-portés du backend (share_ui, page de doc publique, `/favicon.svg` et
`/favicon.ico` de l'endpoint MCP) affichent le mark sans requête réseau ni asset
à déployer. Toute évolution du mark part de `oto-studio/brand/` et se reporte
ici, à l'octet près.
"""
from __future__ import annotations

import base64

# Identique octet pour octet à `oto-studio/brand/logos/oto/oto-dashboard-mark.svg`.
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-64 -64 128 128">'
    '<circle r="44" fill="none" stroke="#f0b41e" stroke-width="28"'
    ' stroke-linecap="round" stroke-dasharray="230 46" transform="rotate(-8)">'
    '</circle>'
    '</svg>'
)

# Balise <link> auto-portée (data-URI) pour les pages HTML server-side.
FAVICON_LINK = (
    '<link rel=icon type="image/svg+xml" href="data:image/svg+xml;base64,'
    + base64.b64encode(FAVICON_SVG.encode("utf-8")).decode("ascii")
    + '">'
)


# ── À qui appartient une page publique ? ─────────────────────────────────────
#
# Deux surfaces posent la même question et n'ont ni l'une ni l'autre de compte sous la
# main : la page d'un projet partagé et celle d'un doc partagé. Leur lecteur n'est pas
# authentifié — ni session, ni org, ni `sub`. Le tenant s'y résout donc par la DONNÉE
# (contenu → projet → org → tenant), jamais par l'hôte, qui ne dit que par où le
# visiteur est arrivé.
#
# Ces fonctions vivent ici parce que les deux pages les partagent : les laisser dans
# l'une ferait dépendre la page de doc de l'UI de partage, ce qu'aucune des deux ne
# justifie.

# Les jetons CSS au-delà des sept d'une `Marque` : accents et papiers propres à NOTRE
# charte. On ne les DÉRIVE pas pour un partenaire — dériver des couleurs produit un
# rendu approximatif qu'on servirait à ses clients en son nom. Ils prennent les gris du
# système, comme le gabarit neutre des emails.
_JETONS_NEUTRES = {"paper2": "#f1f1f4", "paper3": "#f9f9fb", "ink_soft": "#2b3036",
                   "mute": "#60646c", "faint": "#8b8d98", "hair_soft": "#e8e8ec",
                   "primary_soft": "#e8e8ec", "primary_ink": "#1c2024",
                   "accent": "#3358d4", "ink_deep": "#111113",
                   "ombre": "rgba(28,32,36,.04)", "ombre2": "rgba(28,32,36,.14)"}

# NOTRE charte, à l'octet — les valeurs qui étaient écrites dans chaque page avant le
# 15/09/2026. Elles restent ici, et nulle part ailleurs : une page de partenaire ne les
# atteint plus.
_JETONS_OTO = {"paper2": "#f4ecd2", "paper3": "#faf5e6", "ink_soft": "#4a3a23",
               "mute": "#6c5e44", "faint": "#8a7b5c", "hair_soft": "#ede1bd",
               "primary_soft": "#fbe7a8", "primary_ink": "#5a3b03",
               "accent": "#2a87d8", "ink_deep": "#241a0e",
               "ombre": "rgba(44,33,18,.04)", "ombre2": "rgba(44,33,18,.14)"}


def nom_affiche(marque):
    """La même marque, avec le NOM que le registre déclare pour ce tenant.

    `email_brand.marque` est écrite pour `orgs.front_brand`, où l'argument est déjà un
    mot de marque ; ici on lui passe un identifiant de tenant. Sans ce cran, un
    partenaire qui s'appelle « Acme » s'affiche « acme » — en minuscules, à ses propres
    clients. Même résolution que le socle d'accueil (`instructions._socle_for`).
    """
    from . import tenancy
    if not marque.slug or marque.slug == tenancy.PRIMARY_SLUG:
        return marque
    nom = next((e.name for e in tenancy.current().entries()
                if e.slug == marque.slug and e.name), marque.nom)
    return marque if nom == marque.nom else type(marque)(**{**marque.__dict__, "nom": nom})


def marque_du_proprietaire(owner_type, owner_id):
    """La marque sous laquelle ce contenu est partagé — celle de son propriétaire.

    Un contenu SANS org relève de la plateforme, et c'est une déclaration : on rend la
    nôtre explicitement, par le même chemin que les autres, pas par une branche par
    défaut. Même contrat que `orgs.front_brand`, dont NULL veut dire « la plateforme ».
    """
    from . import db, email_brand
    slug = None
    if owner_type == "org" and owner_id is not None:
        slug = db.org_tenant_slug(int(owner_id))
    return nom_affiche(email_brand.marque(slug))


def jetons(marque) -> dict:
    """Les jetons CSS de cette marque. Les nôtres à l'octet si la page est à nous.

    ⚠️ **Ces valeurs sont injectées dans un bloc `<style>` complet** — le sink est le
    parseur CSS du navigateur, pas le HTML, donc `html.escape` n'y protégerait de rien.
    Ce qui protège est la validation de `email_brand._declaree`, qui refuse une palette
    déclarée dès qu'une teinte n'est pas `#rgb`/`#rrggbb` **et la refuse ENTIÈRE** : une
    valeur hostile ne ressort pas amputée, elle ne ressort pas du tout.

    On ne revalide donc PAS ici — deux vérités sur la même question valent moins qu'une
    seule, et la source unique est celle qui lit la base. Mais la dépendance est réelle
    et silencieuse : assouplir cette expression ailleurs ouvrirait ces pages sans que
    rien ne le dise. C'est pourquoi elle a une garde mécanique à elle, qui joue la charge
    utile de bout en bout (`tests/test_share_ui_tenant.py`).

    À savoir si on y touche : l'exigence d'ici est **plus stricte** que celle qui a
    motivé la validation. Les emails injectent dans un attribut `style=` ; une accolade
    fermante y est inerte, alors qu'ici elle terminerait la règle et ouvrirait un
    sélecteur arbitraire.
    """
    if not marque.slug or marque.slug == "oto":
        return {"bg": "#fefcf5", "surface": "#fff", "ink": "#2c2112", "hair": "#dccfa8",
                "primary": "#f0b41e", **_JETONS_OTO}
    return {"bg": marque.fond, "surface": marque.surface, "ink": marque.encre,
            "hair": marque.filet, "primary": marque.bouton_fond, **_JETONS_NEUTRES}
