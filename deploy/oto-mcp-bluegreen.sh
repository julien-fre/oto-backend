#!/bin/bash
# ============================================================================
# Bibliothèque bleu/vert du backend oto — SOURCÉE par chaque déploiement :
#   /opt/deploy/oto-backend.sh         (notre PROD,     oto-mcp@blue|green)
#   /opt/deploy/oto-backend-canari.sh  (notre PRÉPROD,  oto-mcp-canari@blue|green)
#   deploy/cible/deployer.sh           (une instance tierce, chargée depuis l'arbre
#                                       du tag qu'elle déploie — docs/instance-cible.md)
#
# Source versionnée : otomata-tech/oto-backend, deploy/oto-mcp-bluegreen.sh
#   — pour notre box : modifier là, puis recopier ici (/opt/deploy/ sur oto-platform).
#   Copie de référence des unités/amonts : otomata-tech/infra,
#   scripts/oto-backend-bluegreen/ (+ README).
#
# POURQUOI. Avant (28/08/2026) : `systemctl restart` sec. Le démarrage à froid du
# backend prend 36-39 s pendant lesquelles le port est FERMÉ ; Caddy retenait les
# requêtes (lb_try_duration 60s) donc ça se voyait en latence, pas en erreurs —
# mais une session MCP ouverte, elle, était coupée net. À >100 déploiements/mois
# sur la préprod, ce sont les agents qui utilisent le MCP qui paient.
#
# MÉCANISME (5 lignes) :
#   1. la nouvelle version s'installe dans l'arbre de la couleur INACTIVE
#      (checkout + venv à elle : la couleur en service n'est jamais modifiée) ;
#   2. elle démarre sur SON port, on attend son 200 en direct sur 127.0.0.1 ;
#   3. on réécrit le seul fichier d'amont importé par le Caddyfile, `caddy validate`,
#      puis `systemctl reload caddy` — gracieux : les connexions établies CONTINUENT
#      sur l'ancienne couleur, seules les nouvelles requêtes vont sur la nouvelle ;
#   4. on vérifie le trafic public (attente bornée tant qu'aucune réponse HTTP ne
#      revient : certificat en cours d'émission), puis on DRAINE l'ancienne couleur
#      (on attend que ses connexions établies tombent à 0, plafond BG_DRAIN_MAX) ;
#   5. seulement alors on l'arrête. Échec à n'importe quelle étape : on ne bascule
#      pas (ou on rebascule), l'ancienne couleur n'a jamais cessé de servir.
#
# Variables attendues AVANT le `source` — TOUTES déclarées par le wrapper, aucune n'a
# de défaut ici (#967) : une cible qui en oublie une est refusée au chargement, en
# nommant ce qui manque, plutôt que de déployer avec une valeur qui serait la nôtre.
#   BG_ENV            libellé de l'environnement (prod | canari | <rôle d'une cible>)
#   BG_UNIT           gabarit systemd, sans @ (oto-mcp | oto-mcp-canari | …)
#   BG_TREE           préfixe des arbres : l'arbre d'une couleur est <BG_TREE>-<couleur>
#   BG_PORT_blue      port de la couleur bleue
#   BG_PORT_green     port de la couleur verte
#   BG_UPSTREAM       fichier d'amont Caddy GÉNÉRÉ (ex. /etc/caddy/upstream-oto-prod.conf)
#   BG_SNIPPET        préfixe des snippets Caddy (oto_prod | oto_canari | …)
#   BG_DOCSHARE_HOST  Host forcé pour le snippet doc-share (/p/d/*)
#   BG_ASK            1 si l'env porte le `ask` de l'on-demand TLS, sinon VIDE (déclaré)
#   BG_ACTIVE         fichier pointeur de couleur (blue | green)
#   BG_PUBLIC         URL publique de vérification post-bascule
#   BG_DRAIN_MAX      plafond du drain, en secondes
#   BG_LOCK           verrou du déploiement (un par environnement)
#   BG_CADDYFILE      Caddyfile que `caddy validate` juge avant chaque reload
#   BG_DRAIN          script de vidange différée (chemin STABLE : il tourne après nous)
#   BG_DRAIN_UNIT     nom de l'unité transitoire de vidange (une par environnement)
#   BG_LANCEUR        versionne — le lanceur est celui DU TAG (deploy/lanceur_secrets.py),
#                                rien n'est propagé d'une couleur à l'autre. Notre prod et
#                                notre préprod y sont depuis le 30/09/2026 (#967, lot 5) ;
#                     propage  — mode HISTORIQUE, gardé jusqu'au lot 5b (retour arrière du
#                                lot 5) : le lanceur `start-encrypted.sh` vit hors git dans
#                                l'arbre et passe de la couleur en service à la nouvelle.
#
# Garde FACULTATIVE, une fonction et non une variable : `bg_garde_avant_demarrage <arbre>`.
# Si le wrapper la définit, `bg_run` l'appelle après l'installation du tag dans la couleur
# inactive et AVANT son démarrage ; un code non nul arrête tout — la couleur n'a pas
# démarré, rien n'a basculé. Nos wrappers n'en définissent pas (leurs gestes sont figés par
# tests/deploy/gestes_bleu_vert/) ; deploy/cible/deployer.sh y vérifie que la base du rôle
# est à la tête des migrations du tag (oto-backend#1163).
# ============================================================================
set -uo pipefail

HEALTH_PATH="/.well-known/oauth-authorization-server"
# Attente du trafic public après le reload de Caddy (bg_public_ok) : un essai toutes
# les PUBLIC_PAS secondes, au plus PUBLIC_ESSAIS essais et jamais au-delà de
# PUBLIC_ATTENTE_MAX secondes — puis échec net. Pourquoi : au premier déploiement sur un
# nom d'hôte NEUF, Caddy obtient le certificat ACME APRÈS le reload (≈ 4 s mesurées le
# 01/10/2026) ; un contrôle immédiat ne reçoit aucune réponse (code 000) et rebascule
# une montée saine. 20 s couvrent cinq fois cette mesure sans retenir un échec réel
# plus longtemps que le démarrage d'une couleur. Seule l'ABSENCE de réponse HTTP se
# réessaie : un code reçu, quel qu'il soit, veut dire que Caddy route et que la
# couleur répond mal — chaque seconde d'attente serait alors servie au public.
PUBLIC_PAS=2
PUBLIC_ESSAIS=10
PUBLIC_ATTENTE_MAX=20
# Qui réécrit l'amont — nommé dans l'en-tête du fichier généré.
BG_LIB="${BASH_SOURCE[0]}"

# --- chaque variable est DÉCLARÉE par le wrapper ; `BG_ASK` seule peut être vide.
bg_exiger() {
  local v manque=""
  for v in BG_ENV BG_UNIT BG_TREE BG_PORT_blue BG_PORT_green BG_UPSTREAM BG_SNIPPET \
           BG_DOCSHARE_HOST BG_ACTIVE BG_PUBLIC BG_DRAIN_MAX BG_LOCK BG_CADDYFILE \
           BG_DRAIN BG_DRAIN_UNIT BG_LANCEUR; do
    [ -n "${!v:-}" ] || manque="$manque $v"
  done
  [ -n "${BG_ASK+declaree}" ] || manque="$manque BG_ASK"
  if [ -n "$manque" ]; then
    echo "bibliothèque bleu/vert : variable(s) non déclarée(s) par le wrapper :$manque" >&2
    return 1
  fi
  case "$BG_LANCEUR" in
    propage|versionne) ;;
    *) echo "bibliothèque bleu/vert : BG_LANCEUR='$BG_LANCEUR' (attendu propage | versionne)" >&2; return 1 ;;
  esac
}
bg_exiger || exit 2

bg_log() { echo "[$(date +%H:%M:%S)] $*"; }

# --- verrou : deux déploiements simultanés (push auto + main) ne doivent JAMAIS
# --- s'entrelacer, sinon la préprod reste à moitié basculée.
bg_lock() {
  exec 9>"$BG_LOCK"
  flock -w 900 9 || { echo "verrou $BG_LOCK non obtenu après 900 s — un déploiement tourne déjà" >&2; exit 1; }
  bg_log "verrou pris ($BG_LOCK)"
}

bg_port() { local v="BG_PORT_$1"; echo "${!v}"; }
bg_tree() { echo "${BG_TREE}-$1"; }
# --- la couleur en service est ce que dit le pointeur, et rien d'autre : un pointeur
# --- absent ou illisible est une panne à nommer, pas une invitation à supposer bleu
# --- (#967 — l'amorce d'une cible le pose à sa naissance).
bg_active() {
  local c
  c=$(cat "$BG_ACTIVE" 2>/dev/null)
  case "$c" in
    blue|green) echo "$c" ;;
    *) echo "pointeur de couleur $BG_ACTIVE absent ou illisible (« $c ») — rien n'est joué" >&2; return 1 ;;
  esac
}
bg_other()  { local c; c=$(bg_active) || return 1; [ "$c" = blue ] && echo green || echo blue; }

# --- fichier d'amont Caddy : la SEULE chose qui décide où va le trafic.
bg_write_upstream() {
  local color=$1 port; port=$(bg_port "$color")
  {
    echo "# Amont du backend oto ${BG_ENV} — FICHIER GÉNÉRÉ, ne pas éditer à la main."
    echo "# Réécrit par ${BG_LIB} à chaque bascule bleu/vert."
    echo "# Couleur active : ${color} (port ${port}) — bascule du $(date -Is)."
    echo "(${BG_SNIPPET}_upstream) {"
    echo "	reverse_proxy 127.0.0.1:${port} {"
    echo "		# Filet, plus le mécanisme : en bleu/vert l'amont est déjà chaud quand"
    echo "		# Caddy bascule. Gardé pour le cas d'un backend qui tombe et redémarre —"
    echo "		# la requête PATIENTE au lieu de prendre un 502 sur connection refused."
    echo "		lb_try_duration 60s"
    echo "		lb_try_interval 250ms"
    echo "		# Course des connexions gardées ouvertes (502 mesuré le 14/09/2026 à"
    echo "		# 21:39:31Z sur /mcp : « read tcp …->127.0.0.1:${port}: connection reset"
    echo "		# by peer »). uvicorn ferme une connexion inactive à 5 s"
    echo "		# (timeout_keep_alive, défaut) ; Caddy garde les siennes 2 min et peut"
    echo "		# réutiliser celle qu'uvicorn vient de fermer. On expire AVANT lui."
    echo "		transport http {"
    echo "			keepalive 3s"
    echo "		}"
    echo "	}"
    echo "}"
    echo "(${BG_SNIPPET}_upstream_docshare) {"
    echo "	reverse_proxy 127.0.0.1:${port} {"
    echo "		header_up Host ${BG_DOCSHARE_HOST}"
    echo "		lb_try_duration 60s"
    echo "		lb_try_interval 250ms"
    echo "		# Course des connexions gardées ouvertes (502 mesuré le 14/09/2026 à"
    echo "		# 21:39:31Z sur /mcp : « read tcp …->127.0.0.1:${port}: connection reset"
    echo "		# by peer »). uvicorn ferme une connexion inactive à 5 s"
    echo "		# (timeout_keep_alive, défaut) ; Caddy garde les siennes 2 min et peut"
    echo "		# réutiliser celle qu'uvicorn vient de fermer. On expire AVANT lui."
    echo "		transport http {"
    echo "			keepalive 3s"
    echo "		}"
    echo "	}"
    echo "}"
    # L'on-demand TLS (*.mcp.oto.cx, *.share.oto.cx) demande au backend si un
    # hostname a droit à un certificat. Cette URL DOIT suivre la bascule, sinon
    # l'émission de certificats casse dès que l'ancienne couleur s'arrête.
    [ -n "${BG_ASK:-}" ] && {
      echo "(${BG_SNIPPET}_ask) {"
      echo "	ask http://127.0.0.1:${port}/api/mcp/tls-check"
      echo "}"
    }
    true
  } > "$BG_UPSTREAM"
}

# --- installation de la version cible dans l'arbre de la couleur donnée.
bg_install() {
  local color=$1 ref=$2 tree; tree=$(bg_tree "$color")
  bg_log "install ${ref} dans ${tree} (couleur ${color})"
  cd "$tree" || return 1
  git fetch --tags --force origin || return 1
  git reset --hard "$ref" || return 1
  # Installation PAR LE VERROU (oto-backend#932). `uv sync --frozen` pose EXACTEMENT le
  # jeu de `uv.lock` dans `.venv` : monte ou redescend ce qui diffère, retire ce que le
  # verrou ne prescrit pas (pip, setuptools et wheel exceptés : uv ne les touche pas), et
  # prend oto-core au commit que le verrou désigne — plus de force-reinstall. Chaque
  # couleur porte donc le jeu de SA coordonnée, et une bascule ne change plus aucune
  # version sans commit. `.venv/bin/python` reste l'interpréteur (unité, lanceur).
  # Un tag antérieur au verrou n'a pas de `uv.lock` : refus nommé, rien n'est installé.
  if [ ! -f uv.lock ]; then
    bg_log "ERREUR : ${ref} ne porte pas de uv.lock (tag antérieur à #932) — rien n'est installé"
    return 1
  fi
  UV_PYTHON_DOWNLOADS=never uv sync --frozen --quiet || return 1
  bg_log "installé par le verrou : uv.lock $(sha256sum uv.lock | cut -c1-12)"
  BG_HEAD=$(git -C "$tree" rev-parse HEAD)
  # --- Coordonnée de ce qui va TOURNER (oto#33). Écrite par celui qui installe,
  # --- dans l'arbre qu'il vient d'écrire, AVANT que le processus ne démarre : le
  # --- backend la lit une fois au boot et la sert sur /api/version, dans
  # --- `info.version` de l'OpenAPI et en en-tête X-Oto-Version de chaque réponse.
  # --- Pourquoi ici et pas côté workflow : un run vert dit qu'un déploiement a été
  # --- LANCÉ, pas ce qui sert — `main` avance, une bascule échoue, un rollback
  # --- rebascule. Ici, `$ref` est ce qui a été demandé et `$BG_HEAD` ce que le
  # --- `git reset --hard` a réellement posé : les deux ensemble ne mentent pas.
  # --- Non versionné, donc conservé par le `git reset --hard` de l'installation
  # --- suivante jusqu'à sa réécriture ici. Un `--rollback` ne repasse PAS par
  # --- `bg_install` : la couleur qu'il réactive garde le fichier de SA propre
  # --- installation, ce qui est exactement la version qu'elle porte.
  printf '{"ref": "%s", "commit": "%s", "deployed_at": "%s"}\n' \
    "$ref" "$BG_HEAD" "$(date -Is)" > "$tree/.oto-deploy.json" || return 1
  bg_log "installé : ${BG_HEAD}"
}

# --- mode HISTORIQUE `BG_LANCEUR=propage` (retiré au lot 5b, #967) : le lanceur
# --- (résolution des secrets Secret Manager) est PROPAGÉ depuis la couleur en
# --- service, jamais réécrit depuis une copie externe : il s'éditait à la main sur
# --- la box quand un secret s'ajoutait (Pennylane le 28/08, par ex.) et l'écraser
# --- depuis le dépôt ferait disparaître ces ajouts en silence.
# --- Il est indépendant du chemin, donc le même contenu sert les deux couleurs ;
# --- on refuse de continuer s'il ne l'est plus (quelqu'un aurait re-figé un chemin).
bg_propagate_start() {
  local from=$1 to=$2 src dst
  src="$(bg_tree "$from")/start-encrypted.sh"; dst="$(bg_tree "$to")/start-encrypted.sh"
  grep -q 'dirname "\$0"' "$src" || {
    bg_log "le start-encrypted.sh de ${from} n'est PAS indépendant du chemin — il exécuterait le venv de l'autre couleur"
    return 1
  }
  cp -a "$src" "$dst" && chmod 0755 "$dst"
}

# --- démarrage + attente du 200 EN DIRECT sur le port de la couleur (pas via Caddy).
bg_start_and_wait() {
  local color=$1 port; port=$(bg_port "$color")
  bg_log "démarrage ${BG_UNIT}@${color} sur :${port}"
  systemctl start "${BG_UNIT}@${color}" || return 1
  local t0=$SECONDS
  for _ in $(seq 1 60); do
    if systemctl is-active --quiet "${BG_UNIT}@${color}" \
       && curl -fsS --max-time 5 "http://127.0.0.1:${port}${HEALTH_PATH}" -o /dev/null 2>/dev/null; then
      BG_BOOT_SECONDS=$((SECONDS - t0))
      bg_log "couleur ${color} prête en ${BG_BOOT_SECONDS}s"
      # L'amont de l'on-demand TLS bascule avec le reste : on vérifie que le
      # /api/mcp/tls-check de la NOUVELLE couleur répond, sinon l'émission de
      # certificats (*.mcp.oto.cx, *.share.oto.cx) casserait à l'arrêt de l'ancienne.
      if [ -n "${BG_ASK:-}" ]; then
        local code
        code=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' \
               "http://127.0.0.1:${port}/api/mcp/tls-check?domain=probe.invalid" 2>/dev/null)
        [ -n "$code" ] && [ "$code" != 000 ] || { bg_log "tls-check injoignable sur :${port}"; return 1; }
        bg_log "tls-check répond sur :${port} (code ${code} sur un domaine bidon — l'endpoint est là)"
      fi
      return 0
    fi
    systemctl is-active --quiet "${BG_UNIT}@${color}" || { bg_log "couleur ${color} MORTE au démarrage"; return 1; }
    sleep 2
  done
  bg_log "couleur ${color} n'a pas répondu 200 en 120 s"
  return 1
}

# --- bascule de l'amont : écrire, valider, recharger. Restaure sur échec.
bg_switch() {
  local color=$1
  cp -a "$BG_UPSTREAM" "${BG_UPSTREAM}.prev" 2>/dev/null
  bg_write_upstream "$color"
  if ! caddy validate --config "$BG_CADDYFILE" --adapter caddyfile >/dev/null 2>&1; then
    bg_log "caddy validate REFUSE la config — amont restauré, pas de bascule"
    [ -f "${BG_UPSTREAM}.prev" ] && cp -a "${BG_UPSTREAM}.prev" "$BG_UPSTREAM"
    return 1
  fi
  if ! systemctl reload caddy; then
    bg_log "reload caddy en échec — amont restauré"
    [ -f "${BG_UPSTREAM}.prev" ] && cp -a "${BG_UPSTREAM}.prev" "$BG_UPSTREAM"
    systemctl reload caddy || true
    return 1
  fi
  bg_log "amont basculé sur ${color} ($(bg_port "$color")) — reload caddy gracieux"
}

bg_public_ok() {
  local code essai=0 t0=$SECONDS
  while :; do
    essai=$((essai + 1))
    code=$(curl -fsS --max-time 20 -o /dev/null -w '%{http_code}' "$BG_PUBLIC" 2>/dev/null)
    if [ "$code" = 200 ]; then
      if [ "$essai" -eq 1 ]; then
        bg_log "trafic public OK sur $BG_PUBLIC"
      else
        bg_log "trafic public OK sur $BG_PUBLIC au ${essai}e essai, après $((SECONDS - t0))s"
      fi
      return 0
    fi
    # Une réponse HTTP autre que 200 : la couleur répond mal, on n'attend pas.
    if [ -n "$code" ] && [ "$code" != 000 ]; then
      bg_log "trafic public KO sur $BG_PUBLIC (code=${code})"
      return 1
    fi
    if [ "$essai" -ge "$PUBLIC_ESSAIS" ] || [ $((SECONDS - t0 + PUBLIC_PAS)) -gt "$PUBLIC_ATTENTE_MAX" ]; then
      bg_log "trafic public KO sur $BG_PUBLIC : aucune réponse HTTP après $((SECONDS - t0))s et ${essai} essais (plafond ${PUBLIC_ESSAIS} essais, ${PUBLIC_ATTENTE_MAX}s ; dernier code=${code:-aucun})"
      return 1
    fi
    bg_log "trafic public sans réponse sur $BG_PUBLIC (code=${code:-aucun}, essai ${essai}/${PUBLIC_ESSAIS}) — certificat en cours d'émission ? nouvel essai dans ${PUBLIC_PAS}s"
    sleep "$PUBLIC_PAS"
  done
}

# --- VIDANGE DIFFÉRÉE : le déploiement NE L'ATTEND PAS. Il se termine dès la
# --- bascule validée (quelques secondes) ; l'extinction de l'ancienne couleur part
# --- en unité transitoire, avec un plafond généreux puisqu'elle ne bloque plus rien.
# --- Une seule vidange en cours à la fois (cf. bg_run, qui arrête la précédente).
bg_schedule_drain() {
  local color=$1 port; port=$(bg_port "$color")
  local n; n=$(ss -Htn state established "( sport = :$port )" 2>/dev/null | wc -l)
  systemd-run --collect --quiet --unit="$BG_DRAIN_UNIT" \
    --description="Vidange de la couleur ${color} du backend oto ${BG_ENV}" \
    "$BG_DRAIN" "$BG_UNIT" "$color" "$port" "$BG_DRAIN_MAX" \
    && bg_log "vidange de ${color} (:${port}) confiée à ${BG_DRAIN_UNIT} — ${n} connexion(s) au moment de la bascule, plafond ${BG_DRAIN_MAX}s (journalctl -u ${BG_DRAIN_UNIT})" \
    || { bg_log "systemd-run indisponible — arrêt immédiat de ${color} (dégradé)"; systemctl stop "${BG_UNIT}@${color}"; systemctl stop "${BG_UNIT}" 2>/dev/null; }
}

# --- une seule couleur `enabled` : celle en service (un reboot ne relance qu'elle).
bg_commit_active() {
  local color=$1 old=$2
  echo "$color" > "$BG_ACTIVE"
  systemctl enable "${BG_UNIT}@${color}" >/dev/null 2>&1
  systemctl disable "${BG_UNIT}@${old}" >/dev/null 2>&1
  systemctl disable "${BG_UNIT}" >/dev/null 2>&1   # unité simple d'avant, filet désactivé
}

bg_abort() {
  local color=$1 msg=$2
  bg_log "ÉCHEC: ${msg}"
  bg_log "--- journal de la couleur ${color} ---"
  journalctl -u "${BG_UNIT}@${color}" -n 50 --no-pager
  systemctl stop "${BG_UNIT}@${color}" 2>/dev/null
  bg_log "couleur ${color} arrêtée — ${BG_ENV} reste servie par $(bg_active), rien n'a basculé"
  exit 1
}

# ============================================================================
# bg_run <ref|--rollback> — le déploiement complet.
# ============================================================================
bg_run() {
  local ref=$1 t_start=$SECONDS
  bg_lock
  local old new; old=$(bg_active) && new=$(bg_other) || exit 1
  local newport; newport=$(bg_port "$new")
  bg_log "${BG_ENV}: couleur en service = ${old}, cible = ${new} (:${newport})"

  # Garde-fou contre l'accumulation : AU PLUS UNE couleur en cours de vidange. Si
  # une vidange tourne encore, sa couleur a eu son sursis — on l'arrête maintenant,
  # c'est précisément l'arbre qu'on s'apprête à réécrire. Borne à deux instances
  # vivantes en régime normal, trois pendant une seconde.
  systemctl stop "${BG_DRAIN_UNIT}.service" 2>/dev/null
  systemctl stop "${BG_UNIT}@${new}" 2>/dev/null

  if [ "$ref" = "--rollback" ]; then
    bg_log "ROLLBACK : on rebascule sur ${new}, qui porte la version précédente ($(git -C "$(bg_tree "$new")" rev-parse --short HEAD 2>/dev/null))"
  else
    if [ "$BG_LANCEUR" = propage ]; then
      bg_propagate_start "$old" "$new" || bg_abort "$new" "propagation du lanceur en échec"
    fi
    bg_install "$new" "$ref" || bg_abort "$new" "installation de ${ref} en échec"
    if [ "$BG_LANCEUR" = versionne ] && [ ! -f "$(bg_tree "$new")/deploy/lanceur_secrets.py" ]; then
      bg_abort "$new" "${ref} ne porte pas le lanceur versionné (deploy/lanceur_secrets.py) — tag antérieur à #967"
    fi
    if declare -F bg_garde_avant_demarrage >/dev/null \
       && ! bg_garde_avant_demarrage "$(bg_tree "$new")"; then
      bg_log "REFUS avant démarrage : ${ref} est installé dans ${new}, qui n'a PAS démarré — ${BG_ENV} reste servie par ${old}, rien n'a basculé"
      exit 1
    fi
  fi

  bg_start_and_wait "$new" || bg_abort "$new" "la couleur ${new} n'est pas devenue saine"
  bg_switch "$new"          || bg_abort "$new" "bascule de l'amont Caddy en échec"

  if ! bg_public_ok; then
    bg_log "on REBASCULE sur ${old}, qui n'a jamais cessé de servir"
    bg_switch "$old"
    bg_abort "$new" "vérification du trafic public en échec après bascule"
  fi

  bg_commit_active "$new" "$old"
  bg_schedule_drain "$old"

  bg_log "=== ${BG_ENV} OK : ${old} -> ${new} en $((SECONDS - t_start))s (démarrage ${BG_BOOT_SECONDS:-?}s) — déploiement TERMINÉ, la vidange continue en fond ==="
  journalctl -u "${BG_UNIT}@${new}" -n 10 --no-pager
}
