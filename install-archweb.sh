#!/usr/bin/env bash
# install-archweb.sh — Install/update the AllStar Archive Browser as a systemd service.
# Usage: sudo bash install-archweb.sh

set -euo pipefail

########################
# CONFIG (repo is baked-in as requested)
########################
GIT_URL="https://github.com/alancharman/allstar-archive-browser.git"
INSTALL_DIR="/opt/archweb"
SERVICE_NAME="archweb"
APP_USER="recordings"
APP_GROUP="${APP_USER}"  # you can change to 'asterisk' if you want the service group to be asterisk
CACHE_DIR="${INSTALL_DIR}/cache"

########################
# Helpers
########################
log() { printf "\n==> %s\n" "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "Please run as root (use sudo)."
  fi
}

pkg_install() {
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
}

prompt_node_number() {
  local node
  if [[ -n "${NODE_NUMBER:-}" ]]; then
    echo "$NODE_NUMBER"
    return
  fi

  while true; do
    read -r -p "Enter your AllStar node number (digits only): " node
    if [[ "$node" =~ ^[0-9]+$ ]]; then
      echo "$node"
      return
    fi
    echo "Invalid node number. Please enter digits only."
  done
}

ensure_user() {
  if id -u "$APP_USER" >/dev/null 2>&1; then
    log "User '$APP_USER' exists."
  else
    log "Creating user '$APP_USER'..."
    adduser --disabled-password --gecos "" "$APP_USER"
  fi
}

ensure_dir() {
  mkdir -p "$INSTALL_DIR"
  mkdir -p "$CACHE_DIR"
  chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR"
}

clone_or_update_repo() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Updating existing git repo in $INSTALL_DIR ..."
    sudo -u "$APP_USER" bash -lc "
      set -e
      cd '$INSTALL_DIR'
      # Ensure correct remote
      if ! git remote | grep -qx origin; then
        git remote add origin '$GIT_URL'
      else
        git remote set-url origin '$GIT_URL'
      fi
      git fetch --prune origin
      # Ensure we're on 'main' and track origin/main
      git checkout -B main || true
      git branch --set-upstream-to=origin/main main 2>/dev/null || true
      # Hard reset to remote state (idempotent deploy)
      git reset --hard origin/main
      git clean -fd
    "
  elif [[ -d "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
    log "Existing non-empty dir at $INSTALL_DIR → converting to a git checkout..."
    sudo -u "$APP_USER" bash -lc "
      set -e
      cd '$INSTALL_DIR'
      # Backup common local files that might be overwritten
      BK=\$HOME/archweb_backup_\$(date +%s)
      mkdir -p \"\$BK\"
      for f in archive_browser.py install-archweb.sh README.md requirements.txt .gitignore; do
        [ -e \"\$f\" ] && cp -a \"\$f\" \"\$BK/\" || true
      done
      # Initialize and point at the right remote
      git init
      if ! git remote | grep -qx origin; then
        git remote add origin '$GIT_URL'
      else
        git remote set-url origin '$GIT_URL'
      fi
      git fetch --prune origin
      git checkout -B main origin/main
      git reset --hard origin/main
      git clean -fd
    "
  else
    log "Cloning repo $GIT_URL into $INSTALL_DIR ..."
    sudo -u "$APP_USER" git clone "$GIT_URL" "$INSTALL_DIR"
  fi
}

create_venv_and_deps() {
  log "Creating/refreshing Python venv and installing deps..."
  sudo -u "$APP_USER" bash -lc "
    set -e
    cd '$INSTALL_DIR'
    python3 -m venv .venv
    . .venv/bin/activate
    if [[ -f requirements.txt ]]; then
      pip install --upgrade pip
      pip install -r requirements.txt
    else
      pip install --upgrade pip flask
    fi
  "
}

ensure_archive_permissions() {
  local archive_root="$1"
  log "Ensuring '$APP_USER' can read $archive_root ..."
  if [[ ! -d "$archive_root" ]]; then
    echo "WARNING: $archive_root does not exist yet. Skipping permission fixes." >&2
    return 0
  fi

  local grp
  grp="$(stat -c %G "$archive_root" || echo "")"
  local archive_parent archive_grandparent
  archive_parent="$(dirname "$archive_root")"
  archive_grandparent="$(dirname "$archive_parent")"

  if [[ "$grp" == "asterisk" ]]; then
    log "Archive group is 'asterisk' -> adding $APP_USER to that group and checking targeted access ..."
    usermod -aG asterisk "$APP_USER" || true
    chmod g+rx "$archive_grandparent" || true
    chmod g+rx "$archive_parent" || true
    chmod g+rx "$archive_root" || true

    if sudo -u "$APP_USER" bash -lc "find '$archive_root' -maxdepth 1 -type f -readable -print -quit >/dev/null"; then
      log "Existing group permissions look sufficient; skipping recursive repair."
    else
      log "Access check failed -> repairing permissions only inside $archive_root ..."
      find "$archive_root" -type d -exec chmod g+rx {} \; || true
      find "$archive_root" -type f -exec chmod g+r {} \; || true
    fi
  else
    log "Using ACLs to grant read to $APP_USER within $archive_root ..."
    chmod o+rx "$archive_grandparent" "$archive_parent" "$archive_root" 2>/dev/null || true
    setfacl -R -m "u:$APP_USER:rx" "$archive_root" || true
    find "$archive_root" -type f -exec setfacl -m "u:$APP_USER:r" {} \; || true
    setfacl -dR -m "u:$APP_USER:rx" "$archive_root" || true
    setfacl -dR -m "u:$APP_USER:r"  "$archive_root" || true
  fi

  # Quick sanity
  sudo -u "$APP_USER" bash -lc "find '$archive_root' -maxdepth 1 \\( -type d -o -type f \\) -print -quit >/dev/null" || \
    echo "WARNING: $APP_USER still may not be able to traverse/read $archive_root"
}

write_systemd_unit() {
  log "Writing systemd unit /etc/systemd/system/${SERVICE_NAME}.service ..."
  cat >/etc/systemd/system/${SERVICE_NAME}.service <<UNIT
[Unit]
Description=AllStar Archive Browser
After=network-online.target
Wants=network-online.target

[Service]
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
Environment=PATH=${INSTALL_DIR}/.venv/bin:/usr/bin
Environment=ARCHIVE_ROOT=${ARCHIVE_ROOT}
Environment=BIND_HOST=0.0.0.0
Environment=BIND_PORT=5000
Environment=ARCHIVE_CACHE_DB=${CACHE_DIR}/archive_browser_cache.sqlite3
ExecStart=${INSTALL_DIR}/.venv/bin/python3 ${INSTALL_DIR}/archive_browser.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
}

enable_service() {
  log "Enabling and starting ${SERVICE_NAME}.service ..."
  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}.service"
  # Restart to pick up updates even if the service was already running
  systemctl restart "${SERVICE_NAME}.service"
  systemctl --no-pager --full status "${SERVICE_NAME}.service" || true
}

main() {
  require_root

  log "Installing prerequisites..."
  pkg_install python3 python3-venv ffmpeg git acl

  NODE_NUMBER="$(prompt_node_number)"
  ARCHIVE_ROOT="/var/spool/asterisk/monitor/${NODE_NUMBER}"
  log "Node: $NODE_NUMBER"
  log "Archive root: $ARCHIVE_ROOT"

  ensure_user
  ensure_dir
  clone_or_update_repo
  create_venv_and_deps
  ensure_archive_permissions "$ARCHIVE_ROOT"
  write_systemd_unit
  enable_service

  log "Done. Open:  http://<Pi_IP>:5000/"
  log "Tip: journalctl -u ${SERVICE_NAME} -f   (to watch logs)"
}

main "$@"
