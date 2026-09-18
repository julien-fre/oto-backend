"""Déclaration de registre du connecteur `payfit`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# payfit : paie et RH, TOUT ce que l'API Partner documente — lecture et écriture.
# keyed api_key (Bearer), BYO seulement : une clé API PayFit est créée par un
# admin de l'entreprise et n'ouvre que cette entreprise — une clé plateforme
# n'aurait aucun sens, et c'est aussi pourquoi un GROUPE de sociétés pose une
# instance de connecteur par société (cf. `connectors/docs/payfit.md`).
#
# ⚠️ Logiciel de PAIE : NIR, IBAN, rémunérations, motifs d'absence liés à la
# santé. Depuis le 17/09/2026 (signal d'usage #1063) le connecteur ne RETIRE
# plus rien en dur : tout est servi, et la protection passe par le **défaut
# serveur de filtres de champs** (`field_filter_defaults.SERVER_DEFAULTS`), que
# l'org_admin peut lever — cf. `tools/payfit.py`.
#
# Trois modules, une seule clé : les outils de `payfit.py` et ses frères, montés
# ensemble par `modules`.
CONNECTOR = _c(
    "payfit", ["payfit"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="PayFit",
    help="paie et RH : entreprise, annuaire, contrats, absences, bulletins, "
         "comptabilité de paie, temps de travail, mutuelle — et l'écriture "
         "(collaborateur, contrat, absence) en dry-run par défaut",
    href="https://payfit.com",
    modules=("payfit", "payfit_paie", "payfit_social"),
    # Le mot de PayFit pour un « compte » : une clé = une ENTREPRISE, et un groupe en
    # pose autant qu'il a de sociétés. Sans ce mot, l'agent bloqué sur une ambiguïté
    # lit « plusieurs comptes payfit » et doit traduire ; avec, il lit « plusieurs
    # sociétés » et sait ce qu'il cherche. Le multi-compte lui-même n'est pas déclaré
    # ici : il vaut déjà pour tout connecteur dont le credential se pose
    # (`Connector.auth_multi_account`).
    account_noun="société",
)

CATEGORY = "RH"
PUBLISHER = "PayFit"
LOGO_DOMAIN = "payfit.com"

DESCRIPTION = (
    "Le pilotage RH et financier d'une entreprise gérée avec PayFit : l'annuaire "
    "des collaborateurs, leurs contrats (nature, convention collective, forfait "
    "jours, essai, rupture), leurs absences, les bulletins (métadonnées et PDF), "
    "les écritures comptables de paie et leur export, le fichier de virement, "
    "l'état du cycle de paie, le temps de travail réalisé, les titres-restaurant, "
    "la mutuelle et la prévoyance. Écriture possible — créer un collaborateur, un "
    "contrat, une absence, l'annuler, affilier à une mutuelle — toujours en "
    "dry-run par défaut. NIR, coordonnées bancaires et motif d'absence sont "
    "masqués par un défaut serveur qu'un administrateur d'org peut lever."
)
