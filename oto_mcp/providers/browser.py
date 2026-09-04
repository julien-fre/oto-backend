"""Déclaration de registre du connecteur `browser`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# browser : connecteur GÉNÉRIQUE de lecture derrière login (oto-private#79). Les
# trois précédents sont écrits en dur pour UNE API privée qu'on exploite en
# profondeur ; celui-ci sert le besoin inverse — lire N sites (média payant,
# intranet, back-office sans API) sans un cycle de dev par site. **Multi-compte** :
# un compte du coffre = un site (host), donc un Context Browserbase par site
# (sessions isolées, cf. `cardinality`). byo_user : une session loguée
# est physiologiquement personnelle. Hors socle, installable depuis la library ;
# `browser_eval` (JS arbitraire) reste masqué par défaut (DEFAULT_HIDDEN_TOOLS).
CONNECTOR = _c(
    "browser", ["browser"], auth_modes={"byo_user"}, personal_session=True,
    # Session cookie ⟹ la dérivation dirait mono ; or ici un compte est un SITE
    # (un Context Browserbase par host), et il y en a par définition plusieurs.
    cardinality="multi", account_axis_static=True,
    secret_kind="cookie", label="Navigateur connecté", account_noun="site",
    help="lire un site qui exige d'être connecté — un compte par site, la session "
         "tourne chez Browserbase",
)

# Éditeur : le connecteur est le NÔTRE — on l'a écrit, et c'est nous qui recevons
# l'appel (le cookie du site vit dans notre coffre ; la session tourne sur notre compte
# Browserbase, une infra, pas une passerelle qui détiendrait le compte de la personne).
# DÉCLARÉ, et pas dérivé d'un défaut : depuis le 2026-09-02 il n'y a plus de défaut, et
# une omission ne doit pas pouvoir se lire comme un choix (`Connector.publisher_name`).
PUBLISHER = "Otomata"
SANS_LOGO_DE_MARQUE = True

DESCRIPTION = (
    "Lire un site qui exige d'être connecté — un intranet, un média payant, un "
    "back-office sans API — sans écrire de code dédié pour ce site. Un compte "
    "du coffre = un site ; la session se connecte une fois par navigateur "
    "hébergé et persiste ensuite."
)
