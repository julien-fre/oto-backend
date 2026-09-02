"""Déclaration de registre du connecteur `finkare`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# finkare : recouvrement de créances piloté par IA (relances, agents vocaux,
# mise en demeure). API v1 REST documentée sur docs.finkare.io — factures,
# débiteurs, paiements, et le workflow de relance.
#
# ⚠️ **La clé porte son environnement** : `fk_test_…` vise la sandbox,
# `fk_live_…` la production. Le client en DÉRIVE son URL de base plutôt que de
# la prendre en paramètre — une clé de test ne peut donc pas atteindre la prod
# par mégarde, et personne ne peut apparier une clé live à une URL de sandbox.
#
# ⚠️ **Pas de clé plateforme** : chaque org pose la sienne (BYO). Une clé
# Finkare donne accès aux créances d'UNE entreprise — la mutualiser n'aurait
# aucun sens, et la cascade doit s'arrêter au palier org.
# ⚠️ **Leur documentation annonce un serveur MCP hébergé qui N'EXISTE PAS.**
# `docs.finkare.io/mcp-server.md` décrit `https://mcp.finkare.io/mcp` (OAuth 2.1 +
# PKCE + enregistrement dynamique, 21 outils) — vérifié le 2026-09-02 : **aucun
# enregistrement DNS**, l'hôte ne résout pas. Si ce serveur existait, ce connecteur
# n'aurait pas lieu d'être : on le monterait (`kind="mount"`, cf. docs/federation.md)
# et leurs outils natifs remplaceraient les nôtres.
#
# La note est ici, et pas dans une tête : un service qui annonce une porte inexistante
# le fera croire à chaque agent qui lira sa doc. **Revérifier le DNS avant de
# réécrire ce connecteur en mount** — c'est un lot de trente lignes le jour où il
# répond, et le connecteur classique reste utile à côté (précédent `folk`/`folkmcp`).
#
# ⚠️ Leur `openapi.json` public n'est PAS non plus exploitable : c'est le gabarit
# d'exemple de Mintlify (« OpenAPI Plant Store », deux chemins sur `/plants`). Les
# méthodes du client viennent des pages de référence, lues une par une.

# ⚠️ **JAMAIS EXERCÉ CONTRE LE SERVICE RÉEL** — état au 2026-09-02, et personne ne
# l'a demandé autrement (la relance pour obtenir une clé d'essai a été explicitement
# écartée). Ce qui EST vérifié : les quatre outils se montent, chaque méthode
# correspond à la documentation de référence lue endpoint par endpoint, et une clé
# `fk_test_` ne peut pas atteindre les vraies créances. Ce qui NE l'est PAS : la forme
# réelle des réponses, les codes d'erreur, la pagination.
#
# La note est ici pour qu'un premier utilisateur sache ce qu'il essuie — un connecteur
# livré se lit comme un connecteur éprouvé, et rien ne distingue les deux à l'usage
# tant que le premier appel n'a pas eu lieu.

CONNECTOR = _c(
    "finkare", ["finkare"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key",
    label="Finkare",
    help="recouvrement de créances : factures, débiteurs, paiements, relances",
    href="https://app.finkare.io",
)

CATEGORY = "Finance"
# Éditeur TIERS, nommé comme l'exige le régime des connecteurs à tiers : ce
# service n'est pas exploité par Otomata, et le catalogue ne doit pas laisser
# croire l'inverse.
PUBLISHER = "Finkare"
DESCRIPTION = ("Recouvrement de créances automatisé par IA — importer des factures, "
               "suivre les paiements, piloter les relances et lire le score de "
               "paiement d'un débiteur.")
LOGO_DOMAIN = "finkare.io"
