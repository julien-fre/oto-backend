"""Déclaration de registre du connecteur `pennylaneged`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# pennylaneged : GED (bac documentaire) Pennylane via l'API PRIVÉE de la SPA
# (`app.pennylane.com/companies/{cid}/dms`, cookie + CSRF tournant). DISTINCT du
# connecteur keyé `pennylane` (API publique) : credential = session navigateur,
# pas une clé API → l'API publique ne porte aucun scope DMS. Exécution =
# **Browserbase** : l'user se logue 1× via Live View (`pennylaneged_connect_start`),
# sa session persiste dans un Context = le credential (coffre). Upload =
# control plane ici (URL S3 présignée) + PUT des octets EN LOCAL (RGPD, issue #31).
# Expérimental (API interne RE) : hors socle, installable depuis la library.
# **byo_org** : la session peut être configurée au niveau USER, ÉQUIPE ou ORG
# (cas cabinet : une seule connexion Pennylane partagée par la team pour pousser
# dans les GED clients — cascade user > groupe > org). `personal_session=True`
# reste = catégorie « session navigateur » côté UI (orthogonal au partage).
# Deux modules d'outils : la GED (`pennylaneged`) et la SESSION (login Live View +
# sonde de vérification). Séparés le 2026-09-03 — la sonde de login n'a rien à voir
# avec le bac documentaire, et les mêler avait fini par mettre 600 lignes dans un
# fichier. L'ordre compte : `pennylaneged` est importé d'abord, `pennylaneged_session`
# en dérive (origine + seams d'erreur).
CONNECTOR = _c(
    "pennylaneged", ["pennylaneged"], auth_modes={"byo_user", "byo_org"},
    modules=("pennylaneged", "pennylaneged_session"),
    personal_session=True, secret_kind="cookie",
    label="Pennylane GED",
    help="bac documentaire Pennylane (session Browserbase)",
    publisher="Pennylane", href="https://app.pennylane.com",
)

CATEGORY = "Finance"
LOGO_DOMAIN = "pennylane.com"

DESCRIPTION = (
    "Le bac documentaire (GED) de Pennylane, via ta session Pennylane connectée "
    "par navigateur hébergé — pas la clé API publique du connecteur "
    "`pennylane`, qui n'a aucun accès à ces documents. Configurable au niveau "
    "d'un utilisateur, d'une équipe ou de toute l'organisation."
)
