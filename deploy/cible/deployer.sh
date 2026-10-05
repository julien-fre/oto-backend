#!/bin/bash
# ============================================================================
# Déploiement d'un rôle d'instance cible (#967) — exécuté depuis l'arbre DU TAG, que la
# porte (deploy/cible/porte.sh) vient d'extraire : l'amorce, la bibliothèque bleu/vert et
# le lanceur sont ceux de la version qu'on monte, jamais une copie posée à la main.
#
#   1. la déclaration dit tout ce qui distingue ce rôle (declaration.py `variables`) ;
#   2. l'amorce remet le rôle à sa forme déclarée — ou le fait naître ;
#   3. la bibliothèque bleu/vert commune installe le tag dans la couleur
#      inactive, puis — AVANT de la démarrer — la garde des migrations (#1163) : la base
#      du rôle doit être à la tête des révisions DU TAG, sinon refus, en nommant l'écart et
#      la commande qui migre. Le déploiement ne migre JAMAIS lui-même : c'est un geste
#      explicite (docs/instance-cible.md, § Les migrations, un geste explicite) ;
#   4. elle démarre la couleur, bascule l'amont Caddy, vérifie le trafic public, vidange ;
#   5. la maintenance du rôle suit l'arbre qui sert désormais.
#
# usage : deployer.sh <déclaration.json> deployer <rôle> <tag>
#         deployer.sh <déclaration.json> retour   <rôle> <tag>   rebascule sur la couleur
#                                                                 inactive (version d'avant)
# Env   : OTO_CIBLE_DEPOT (posée par la porte)
# ============================================================================
# Pas de `set -e` : la bibliothèque bleu/vert gère elle-même ses échecs (rebascule,
# arrêt de la couleur neuve) et ne doit pas être interrompue au premier code non nul.
set -uo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "$#" -eq 4 ] || { echo "usage : deployer.sh <déclaration> deployer|retour <rôle> <tag>" >&2; exit 2; }
DECLARATION=$1 ACTION=$2 ROLE=$3 TAG=$4

VARIABLES=$(python3 "$ICI/declaration.py" variables "$DECLARATION" "$ROLE") || exit 1
eval "$VARIABLES"

bash "$ICI/amorcer.sh" "$DECLARATION" "$ROLE" || { echo "déploiement : amorce en échec, rien n'a basculé" >&2; exit 1; }

# --- les commandes du lanceur d'un arbre, hors service : mêmes fichiers d'environnement et
# --- même clé d'API que l'unité du rôle, sans le fichier de port (rien n'est servi).
lanceur_de() {
  local arbre=$1
  echo "systemd-run --pipe --wait --quiet --collect -p WorkingDirectory=$arbre" \
       "-p EnvironmentFile=$ETC_ROLE/app.env -p EnvironmentFile=$ETC_ROLE/lanceur.env" \
       "-p LoadCredential=scw:$CLE_SCW $arbre/.venv/bin/python $arbre/deploy/lanceur_secrets.py"
}

# --- la garde des migrations (#1163), appelée par la bibliothèque bleu/vert dans l'arbre
# --- de la couleur inactive, où le tag vient d'être installé, AVANT son démarrage : la
# --- tête attendue est celle du registre DE CE TAG, jamais du code qui sert. Toute issue
# --- autre que « à jour » refuse ; aucune ne migre.
bg_garde_avant_demarrage() {
  local arbre=$1 lanceur code
  lanceur=$(lanceur_de "$arbre")
  $lanceur --script deploy/cible/migrations_a_jour.py
  code=$?
  case "$code" in
    0) return 0 ;;
    3) echo "déploiement : REFUS — la base du rôle ${ROLE} n'est pas à la tête des migrations de ${TAG} (écart ci-dessus)." >&2
       echo "  Migrer est un geste explicite, à jouer en root sur la machine, depuis l'arbre où ${TAG} est installé :" >&2
       echo "    $lanceur migrer upgrade head" >&2
       echo "  puis relancer la montée de ${TAG} sur ${ROLE}." >&2 ;;
    *) echo "déploiement : REFUS — l'état des migrations de la base du rôle ${ROLE} ne se conclut pas (code ${code}, motif ci-dessus) : on ne monte pas ${TAG} à l'aveugle." >&2
       echo "  Lire l'état, en root sur la machine :" >&2
       echo "    $lanceur migrer current" >&2
       echo "    $lanceur migrer heads" >&2 ;;
  esac
  return 1
}

# shellcheck source=deploy/oto-mcp-bluegreen.sh
. "$ICI/../oto-mcp-bluegreen.sh"

case "$ACTION" in
  deployer) bg_run "$TAG" ;;
  retour)   bg_run --rollback ;;
  *) echo "action inconnue « $ACTION »" >&2; exit 2 ;;
esac

# La maintenance est posée APRÈS la bascule, et n'est jamais un motif d'échec
# du déploiement — une maintenance manquée se rattrape, un service arrêté non. Mais son
# échec se DIT, en tête du journal du run.
if ! bash "$ICI/amorcer.sh" "$DECLARATION" "$ROLE" maintenance; then
  echo "déploiement : ATTENTION — maintenance de ${UNITE} non posée (le service, lui, a basculé)" >&2
fi
