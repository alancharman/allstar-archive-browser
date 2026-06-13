#!/usr/bin/env bash
# install-archweb-qso.sh - Install/update the QSO builder test branch as a separate systemd service.
# Usage: sudo bash install-archweb-qso.sh

set -euo pipefail

########################
# CONFIG (test deploy)
########################
GIT_URL="https://github.com/alancharman/allstar-archive-browser.git"
GIT_BRANCH="feature-qso-builder"
INSTALL_DIR="/opt/archweb-qso"
SERVICE_NAME="archweb-qso"
APP_USER="recordings"
APP_GROUP="${APP_USER}"
APP_PORT="5001"

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

ensure_firewall_port() {
  if command -v firewall-cmd >/dev/null 2>&1; then
    log "Opening TCP port ${APP_PORT} in firewalld ..."
    firewall-cmd --permanent --add-port="${APP_PORT}/tcp"
    firewall-cmd --reload
  else
    log "firewall-cmd not found; skipping firewall changes for port ${APP_PORT}."
  fi
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
  chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR"
}

clone_or_update_repo() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Updating existing git repo in $INSTALL_DIR ..."
    sudo -u "$APP_USER" bash -lc "
      set -e
      cd '$INSTALL_DIR'
      if ! git remote | grep -qx origin; then
        git remote add origin '$GIT_URL'
      else
        git remote set-url origin '$GIT_URL'
      fi
      git fetch --prune origin
      git checkout -B '$GIT_BRANCH' || true
      git branch --set-upstream-to='origin/$GIT_BRANCH' '$GIT_BRANCH' 2>/dev/null || true
      git reset --hard 'origin/$GIT_BRANCH'
      git clean -fd
    "
  elif [[ -d "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
    log "Existing non-empty dir at $INSTALL_DIR -> converting to a git checkout..."
    sudo -u "$APP_USER" bash -lc "
      set -e
      cd '$INSTALL_DIR'
      BK=\$HOME/archweb_qso_backup_\$(date +%s)
      mkdir -p \"\$BK\"
      for f in archive_browser.py install-archweb.sh install-archweb-qso.sh README.md requirements.txt .gitignore; do
        [ -e \"\$f\" ] && cp -a \"\$f\" \"\$BK/\" || true
      done
      git init
      if ! git remote | grep -qx origin; then
        git remote add origin '$GIT_URL'
      else
        git remote set-url origin '$GIT_URL'
      fi
      git fetch --prune origin
      git checkout -B '$GIT_BRANCH' 'origin/$GIT_BRANCH'
      git reset --hard 'origin/$GIT_BRANCH'
      git clean -fd
    "
  else
    log "Cloning repo $GIT_URL ($GIT_BRANCH) into $INSTALL_DIR ..."
    sudo -u "$APP_USER" git clone --branch "$GIT_BRANCH" --single-branch "$GIT_URL" "$INSTALL_DIR"
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

  if [[ "$grp" == "asterisk" ]]; then
    log "Archive group is 'asterisk' -> adding $APP_USER to that group and setting g+rX ..."
    usermod -aG asterisk "$APP_USER" || true
    chmod -R g+rx "$(dirname "$archive_root")" || true
    find "$archive_root" -type f -exec chmod g+r {} \; || true
  else
    log "Using ACLs to grant read to $APP_USER ..."
    setfacl -R -m "u:$APP_USER:rx" "$archive_root" || true
    find "$archive_root" -type f -exec setfacl -m "u:$APP_USER:r" {} \; || true
    setfacl -dR -m "u:$APP_USER:rx" "$archive_root" || true
    setfacl -dR -m "u:$APP_USER:r"  "$archive_root" || true
  fi

  sudo -u "$APP_USER" bash -lc "ls -ld '$archive_root' >/dev/null" || \
    echo "WARNING: $APP_USER still may not be able to traverse $archive_root"
}

write_systemd_unit() {
  log "Writing systemd unit /etc/systemd/system/${SERVICE_NAME}.service ..."
  cat >/etc/systemd/system/${SERVICE_NAME}.service <<UNIT
[Unit]
Description=AllStar Archive Browser (QSO builder test)
After=network-online.target
Wants=network-online.target

[Service]
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${INSTALL_DIR}
Environment=PATH=${INSTALL_DIR}/.venv/bin:/usr/bin
Environment=ARCHIVE_ROOT=${ARCHIVE_ROOT}
Environment=BIND_HOST=0.0.0.0
Environment=BIND_PORT=${APP_PORT}
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
  systemctl restart "${SERVICE_NAME}.service"
  systemctl --no-pager --full status "${SERVICE_NAME}.service" || true
}

main() {
  require_root

  log "Installing prerequisites..."
  pkg_install python3 python3-venv ffmpeg git acl

  NODE_NUMBER="$(prompt_node_number)"
  ARCHIVE_ROOT="/var/spool/asterisk/monitor/${NODE_NUMBER}"
  log "Branch: $GIT_BRANCH"
  log "Node: $NODE_NUMBER"
  log "Archive root: $ARCHIVE_ROOT"
  log "Test URL: http://<Pi_IP>:${APP_PORT}/"

  ensure_user
  ensure_dir
  clone_or_update_repo
  create_venv_and_deps
  ensure_archive_permissions "$ARCHIVE_ROOT"
  ensure_firewall_port
  write_systemd_unit
  enable_service

  log "Done. Open:  http://<Pi_IP>:${APP_PORT}/"
  log "Tip: journalctl -u ${SERVICE_NAME} -f   (to watch logs)"
}

main "$@"
