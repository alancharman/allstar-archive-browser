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
  chown -R "$APP_USER:$APP_GROUP" "$INSTALL_DIR"
}

clone_or_update_repo() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Updating existing git repo in $INSTALL_DIR ..."
    sudo -u "$APP_USER" git -C "$INSTALL_DIR" pull --ff-only
  elif [[ -d "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
    log "Existing non-empty dir at $INSTALL_DIR → converting it into a git checkout..."
    sudo -u "$APP_USER" bash -lc "
      set -e
      cd '$INSTALL_DIR'
      git init
      git remote add origin '$GIT_URL' || git remote set-url origin '$GIT_URL'
      git fetch origin

      # backup any files that would be overwritten
      CONFLICTS=\$(git diff --name-only --diff-filter=U origin/main || true)
      # or simpler: just back up known files before forcing checkout
      BK=\$HOME/archweb_backup_\$(date +%s)
      mkdir -p \"\$BK\"
      for f in archive_browser.py install-archweb.sh; do
        [ -f \"\$f\" ] && cp -a \"\$f\" \"\$BK/\"
      done

      git checkout -B main origin/main -f
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

patch_archive_root_in_code() {
  local node="$1"
  local target="/var/spool/asterisk/monitor/${node}"
  local file="$INSTALL_DIR/archive_browser.py"

  [[ -f "$file" ]] || die "$file not found. Ensure the repo contains archive_browser.py"

  log "Configuring ARCHIVE_ROOT to $target in archive_browser.py"
  # Replace existing ARCHIVE_ROOT assignment if found; otherwise inject after pathlib import.
  if grep -qE '^\s*ARCHIVE_ROOT\s*=\s*Path\(' "$file"; then
    sed -ri "s|^\s*ARCHIVE_ROOT\s*=\s*Path\(.+\)\.resolve\(\)|ARCHIVE_ROOT = Path(\"$target\").resolve()|g" "$file"
  else
    awk -v ar="$target" '
      BEGIN{done=0}
      {print}
      /from[[:space:]]+pathlib[[:space:]]+import[[:space:]]+Path/ && done==0 {
        print "\n# Injected by installer"
        print "ARCHIVE_ROOT = Path(\"" ar "\").resolve()"
        done=1
      }
    ' "$file" > "$file.tmp" && mv "$file.tmp" "$file"
  fi
  chown "$APP_USER:$APP_GROUP" "$file"
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
    log "Archive group is 'asterisk' → adding $APP_USER to that group and setting g+rX ..."
    usermod -aG asterisk "$APP_USER" || true
    # Ensure traverse perms on parent dirs and read on files
    chmod -R g+rx "$(dirname "$archive_root")" || true
    find "$archive_root" -type f -exec chmod g+r {} \; || true
  else
    log "Using ACLs to grant read to $APP_USER ..."
    setfacl -R -m "u:$APP_USER:rx" "$archive_root" || true
    find "$archive_root" -type f -exec setfacl -m "u:$APP_USER:r" {} \; || true
    setfacl -dR -m "u:$APP_USER:rx" "$archive_root" || true
    setfacl -dR -m "u:$APP_USER:r"  "$archive_root" || true
  fi

  # Quick sanity
  sudo -u "$APP_USER" bash -lc "ls -ld '$archive_root' >/dev/null" || \
    echo "WARNING: $APP_USER still may not be able to traverse $archive_root"
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
  patch_archive_root_in_code "$NODE_NUMBER"
  ensure_archive_permissions "$ARCHIVE_ROOT"
  write_systemd_unit
  enable_service

  log "Done. Open:  http://<Pi_IP>:5000/"
  log "Tip: journalctl -u ${SERVICE_NAME} -f   (to watch logs)"
}

main "$@"
