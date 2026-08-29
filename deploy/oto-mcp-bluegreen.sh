#!/bin/bash
# ============================================================================
# Bibliothèque bleu/vert du backend oto — SOURCÉE par les deux déploiements :
#   /opt/deploy/oto-backend.sh         (PROD,     oto-mcp@blue|green)
#   /opt/deploy/oto-backend-canari.sh  (PRÉPROD,  oto-mcp-canari@blue|green)
#
# Source versionnée : otomata-tech/oto-backend, deploy/oto-mcp-bluegreen.sh
#   — modifier là, puis recopier ici (/opt/deploy/ sur oto-platform).
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
#   4. on vérifie le trafic public, puis on DRAINE l'ancienne couleur (on attend que
#      ses connexions établies tombent à 0, plafond BG_DRAIN_MAX) ;
#   5. seulement alors on l'arrête. Échec à n'importe quelle étape : on ne bascule
#      pas (ou on rebascule), l'ancienne couleur n'a jamais cessé de servir.
#
# Variables attendues AVANT le `source` (cf. les deux wrappers) :
#   BG_ENV            prod | canari                (libellé)
#   BG_UNIT           oto-mcp | oto-mcp-canari     (gabarit systemd, sans @)
#   BG_TREE           /opt/oto-mcp | /opt/oto-mcp-canari   (arbre = <BG_TREE>-<couleur>)
#   BG_PORT_blue      port de la couleur bleue
#   BG_PORT_green     port de la couleur verte
#   BG_UPSTREAM       /etc/caddy/upstream-oto-<env>.conf   (fichier GÉNÉRÉ)
#   BG_SNIPPET        oto_prod | oto_canari        (préfixe des snippets Caddy)
#   BG_DOCSHARE_HOST  Host forcé pour le snippet doc-share (/p/d/*)
#   BG_ASK            1 si l'env porte le `ask` de l'on-demand TLS (prod), sinon vide
#   BG_ACTIVE         /etc/oto-mcp/active-<env>    (fichier pointeur de couleur)
#   BG_PUBLIC         URL publique de vérification post-bascule
#   BG_DRAIN_MAX      plafond du drain, en secondes
# ============================================================================
set -uo pipefail

HEALTH_PATH="/.well-known/oauth-authorization-server"
LOCK="/var/lock/oto-mcp-bluegreen-${BG_ENV}.lock"

bg_log() { echo "[$(date +%H:%M:%S)] $*"; }

# --- verrou : deux déploiements simultanés (push auto + main) ne doivent JAMAIS
# --- s'entrelacer, sinon la préprod reste à moitié basculée.
bg_lock() {
  exec 9>"$LOCK"
  flock -w 900 9 || { echo "verrou $LOCK non obtenu après 900 s — un déploiement tourne déjà" >&2; exit 1; }
  bg_log "verrou pris ($LOCK)"
}

bg_port() { local v="BG_PORT_$1"; echo "${!v}"; }
bg_tree() { echo "${BG_TREE}-$1"; }
bg_active() { cat "$BG_ACTIVE" 2>/dev/null || echo blue; }
bg_other()  { [ "$(bg_active)" = blue ] && echo green || echo blue; }

# --- fichier d'amont Caddy : la SEULE chose qui décide où va le trafic.
bg_write_upstream() {
  local color=$1 port; port=$(bg_port "$color")
  {
    echo "# Amont du backend oto ${BG_ENV} — FICHIER GÉNÉRÉ, ne pas éditer à la main."
    echo "# Réécrit par /opt/deploy/oto-mcp-bluegreen.sh à chaque bascule bleu/vert."
    echo "# Couleur active : ${color} (port ${port}) — bascule du $(date -Is)."
    echo "(${BG_SNIPPET}_upstream) {"
    echo "	reverse_proxy 127.0.0.1:${port} {"
    echo "		# Filet, plus le mécanisme : en bleu/vert l'amont est déjà chaud quand"
    echo "		# Caddy bascule. Gardé pour le cas d'un backend qui tombe et redémarre —"
    echo "		# la requête PATIENTE au lieu de prendre un 502 sur connection refused."
    echo "		lb_try_duration 60s"
    echo "		lb_try_interval 250ms"
    echo "	}"
    echo "}"
    echo "(${BG_SNIPPET}_upstream_docshare) {"
    echo "	reverse_proxy 127.0.0.1:${port} {"
    echo "		header_up Host ${BG_DOCSHARE_HOST}"
    echo "		lb_try_duration 60s"
    echo "		lb_try_interval 250ms"
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
  local tag
  tag=$(grep -oP 'oto-core\.git@\K[^"]+' pyproject.toml) || return 1
  # pip NE réinstalle PAS une dep VCS déjà présente -> force-reinstall d'oto-core
  # depuis le tag LU du pyproject (source unique, pas de hardcode).
  ./.venv/bin/pip install -e . --quiet || return 1
  ./.venv/bin/pip install --force-reinstall --quiet \
    "oto-core[browser] @ git+https://github.com/otomata-tech/oto-core.git@${tag}" || return 1
  BG_HEAD=$(git -C "$tree" rev-parse HEAD)
  bg_log "installé : ${BG_HEAD}"
}

# --- le lanceur (résolution des secrets Secret Manager) est PROPAGÉ depuis la
# --- couleur en service, jamais réécrit depuis une copie externe : il est édité à
# --- la main sur la box quand un secret s'ajoute (Pennylane le 28/08, par ex.) et
# --- l'écraser depuis le dépôt ferait disparaître ces ajouts en silence.
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
  if ! caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
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
  local code
  code=$(curl -fsS --max-time 20 -o /dev/null -w '%{http_code}' "$BG_PUBLIC" 2>/dev/null)
  [ "$code" = 200 ] || { bg_log "trafic public KO sur $BG_PUBLIC (code=${code:-aucun})"; return 1; }
  bg_log "trafic public OK sur $BG_PUBLIC"
}

# --- VIDANGE DIFFÉRÉE : le déploiement NE L'ATTEND PAS. Il se termine dès la
# --- bascule validée (quelques secondes) ; l'extinction de l'ancienne couleur part
# --- en unité transitoire, avec un plafond généreux puisqu'elle ne bloque plus rien.
# --- Une seule vidange en cours à la fois (cf. bg_run, qui arrête la précédente).
bg_schedule_drain() {
  local color=$1 port; port=$(bg_port "$color")
  local n; n=$(ss -Htn state established "( sport = :$port )" 2>/dev/null | wc -l)
  systemd-run --collect --quiet --unit="oto-mcp-drain-${BG_ENV}" \
    --description="Vidange de la couleur ${color} du backend oto ${BG_ENV}" \
    /opt/deploy/oto-mcp-drain.sh "$BG_UNIT" "$color" "$port" "$BG_DRAIN_MAX" \
    && bg_log "vidange de ${color} (:${port}) confiée à oto-mcp-drain-${BG_ENV} — ${n} connexion(s) au moment de la bascule, plafond ${BG_DRAIN_MAX}s (journalctl -u oto-mcp-drain-${BG_ENV})" \
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
  local old new; old=$(bg_active); new=$(bg_other)
  local newport; newport=$(bg_port "$new")
  bg_log "${BG_ENV}: couleur en service = ${old}, cible = ${new} (:${newport})"

  # Garde-fou contre l'accumulation : AU PLUS UNE couleur en cours de vidange. Si
  # une vidange tourne encore, sa couleur a eu son sursis — on l'arrête maintenant,
  # c'est précisément l'arbre qu'on s'apprête à réécrire. Borne à deux instances
  # vivantes en régime normal, trois pendant une seconde.
  systemctl stop "oto-mcp-drain-${BG_ENV}.service" 2>/dev/null
  systemctl stop "${BG_UNIT}@${new}" 2>/dev/null

  if [ "$ref" = "--rollback" ]; then
    bg_log "ROLLBACK : on rebascule sur ${new}, qui porte la version précédente ($(git -C "$(bg_tree "$new")" rev-parse --short HEAD 2>/dev/null))"
  else
    bg_propagate_start "$old" "$new" || bg_abort "$new" "propagation du lanceur en échec"
    bg_install "$new" "$ref" || bg_abort "$new" "installation de ${ref} en échec"
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
