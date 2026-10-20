#!/usr/bin/env bash
# Install, update, or remove the Traffic Panel on Debian or Ubuntu.
#
#   curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo bash
#   curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo bash -s -- update
#   curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo bash -s -- uninstall
set -euo pipefail

REPO="https://github.com/MoonlessWanderer/TrafficPanel.git"
DEFAULT_APP_DIR="/usr/local/traffic-panel"
APP_DIR=""
APP_USER="trafficpanel"
APP_GROUP="trafficpanel"
SERVICE="traffic-panel"
STATE_FILE=""
UNIT_FILE=""
NGINX_FILE=""
PORT="8080"
NGINX_SITE="traffic-panel"

ACTION="install"
DOMAIN=""
EMAIL=""
ADMIN_USER=""
ADMIN_PASSWORD=""
ASSUME_YES=0

usage() {
  cat <<'EOF'
Usage:
  install.sh                 Install, and ask whether to issue HTTPS
  install.sh install [options]
  install.sh update
  install.sh uninstall [--yes]

Options for install:
  --dir PATH        Install directory. Default: /usr/local/traffic-panel
  --domain NAME     Public domain. HTTPS is issued for this name.
  --email ADDRESS   Email for the certificate authority. Required with --domain.
  --admin NAME      First admin username.
  --password TEXT   First admin password, at least 10 characters.
  --yes             Remove everything without the confirmation prompt.

Without a domain the panel listens on port 8080 without TLS.
The panel is not installed under /opt.
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    die "Run this script as root."
  fi
}

read_tty() {
  if [ -r /dev/tty ]; then
    IFS= read -r "$@" </dev/tty
  else
    IFS= read -r "$@"
  fi
}

prompt() {
  local label="$1"
  local value=""
  if [ -r /dev/tty ]; then
    read -r -p "$label" value </dev/tty
  else
    read -r -p "$label" value
  fi
  printf '%s' "$value"
}

prompt_secret() {
  local label="$1"
  local value=""
  if [ -r /dev/tty ]; then
    read -r -s -p "$label" value </dev/tty
    echo >&2
  else
    read -r -s -p "$label" value
    echo >&2
  fi
  printf '%s' "$value"
}

valid_domain() {
  [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$ ]]
}

valid_email() {
  [[ "$1" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]
}

valid_admin() {
  [[ "$1" =~ ^[A-Za-z0-9._-]{1,64}$ ]]
}

bind_app_dir() {
  APP_DIR="${APP_DIR%/}"
  case "$APP_DIR" in
    /*/*) ;;
    *) die "The install directory must be an absolute path with a parent directory." ;;
  esac
  case "$APP_DIR" in
    *[[:space:]]*|*".."*) die "The install directory is invalid." ;;
  esac
  case "$APP_DIR" in
    /opt|/opt/*)
      die "Choose a directory outside /opt."
      ;;
    /|/usr|/usr/local|/etc|/var|/var/lib|/home|/root|/tmp|/bin|/sbin|/lib|/lib64|/boot|/dev|/proc|/sys|/run|/srv|/usr/bin|/usr/sbin|/usr/lib)
      die "Refusing to use ${APP_DIR} as the install directory."
      ;;
  esac
  STATE_FILE="${APP_DIR}/data/install.state"
  UNIT_FILE="${APP_DIR}/traffic-panel.service"
  NGINX_FILE="${APP_DIR}/nginx.conf"
}

choose_install_dir() {
  if [ -z "$APP_DIR" ]; then
    if [ -r /dev/tty ]; then
      local entered
      entered="$(prompt "Install directory [${DEFAULT_APP_DIR}]: ")"
      APP_DIR="${entered:-$DEFAULT_APP_DIR}"
    else
      APP_DIR="$DEFAULT_APP_DIR"
    fi
  fi
  bind_app_dir
}

locate_installed_dir() {
  if [ -z "$APP_DIR" ]; then
    local unit="/etc/systemd/system/${SERVICE}.service"
    local dir=""
    if [ -f "$unit" ]; then
      dir="$(sed -n 's/^WorkingDirectory=//p' "$unit" | head -n 1 | tr -d '[:space:]')"
    fi
    [ -n "$dir" ] || die "The panel is not installed."
    APP_DIR="$dir"
  fi
  bind_app_dir
}

installed() {
  [ -f "$STATE_FILE" ]
}

load_state() {
  DOMAIN=""
  if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1090
    . "$STATE_FILE"
  fi
}

write_state() {
  install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 "${APP_DIR}/data"
  cat >"$STATE_FILE" <<EOF
DOMAIN=$(printf '%q' "$DOMAIN")
EMAIL=$(printf '%q' "$EMAIL")
SSL=$( [ -n "$DOMAIN" ] && printf yes || printf no )
EOF
  chown "$APP_USER:$APP_GROUP" "$STATE_FILE"
  chmod 0640 "$STATE_FILE"
}

require_deb() {
  if [ ! -f /etc/os-release ]; then
    die "This installer supports Debian and Ubuntu."
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    debian|ubuntu) ;;
    *) die "This installer supports Debian and Ubuntu. Detected: ${ID:-unknown}." ;;
  esac
}

apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y "$@"
}

ensure_python() {
  apt_install ca-certificates curl git
  local py version probe
  if command -v python3 >/dev/null 2>&1; then
    py="$(command -v python3)"
  elif command -v python >/dev/null 2>&1 && python -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)'; then
    py="$(command -v python)"
  else
    die "Python 3 was not found. Install Python 3.9 or newer and run this again."
  fi
  version="$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
  if ! "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    die "Found Python ${version} at ${py}. This panel needs Python 3.9 or newer."
  fi
  probe="$(mktemp -d)"
  if ! "$py" -m venv "${probe}/venv" >/dev/null 2>&1; then
    rm -rf "$probe"
    die "Python ${version} at ${py} cannot create a virtual environment. Install the venv package for this interpreter and run this again. On Debian or Ubuntu: apt install python3-venv"
  fi
  rm -rf "$probe"
  PYTHON="$py"
  echo "Using Python ${version} at ${py}"
}

ensure_user() {
  if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
  fi
}

run_as_app() {
  runuser -u "$APP_USER" -- env HOME="$APP_DIR" "$@"
}

write_config() {
  local secret="$1"
  local admin_user="$2"
  local admin_password="$3"
  local host_value="$4"
  local cookie_secure="$5"
  local trusted="$6"
  CONFIG_PATH="${APP_DIR}/config.py" \
  SECRET_VALUE="$secret" \
  ADMIN_USER_VALUE="$admin_user" \
  ADMIN_PASSWORD_VALUE="$admin_password" \
  HOST_VALUE="$host_value" \
  COOKIE_SECURE_VALUE="$cookie_secure" \
  TRUSTED_VALUE="$trusted" \
  PORT_VALUE="$PORT" \
  "$PYTHON" - <<'PY'
import os
from pathlib import Path

def line(name, value):
    if value is None:
        return f"{name} = None"
    if isinstance(value, bool):
        return f"{name} = {value}"
    if isinstance(value, int):
        return f"{name} = {value}"
    return f"{name} = {value!r}"

secure = os.environ["COOKIE_SECURE_VALUE"] == "true"
text = "\n".join([
    line("ENV", "production"),
    line("HOST", os.environ["HOST_VALUE"]),
    line("PORT", int(os.environ["PORT_VALUE"])),
    line("DATA_DIR", "data"),
    line("REPORTS_DIR", "data/reports"),
    line("LOG_DIR", "logs"),
    line("DATABASE_PATH", "data/panel.db"),
    line("SECRET_KEY", os.environ["SECRET_VALUE"]),
    line("ADMIN_USERNAME", os.environ["ADMIN_USER_VALUE"]),
    line("ADMIN_PASSWORD", os.environ["ADMIN_PASSWORD_VALUE"]),
    line("COOKIE_SECURE", secure),
    line("SESSION_COOKIE", "vps_session"),
    line("CSRF_COOKIE", "vps_csrf"),
    line("LOG_LEVEL", "INFO"),
    line("LOG_MAX_BYTES", 2_000_000),
    line("LOG_BACKUP_COUNT", 5),
    line("SSL_CERTFILE", ""),
    line("SSL_KEYFILE", ""),
    line("TRUSTED_HOSTS", os.environ["TRUSTED_VALUE"]),
]) + "\n"
path = Path(os.environ["CONFIG_PATH"])
path.write_text(text, encoding="utf-8")
os.chmod(path, 0o640)
PY
  chown "$APP_USER:$APP_GROUP" "${APP_DIR}/config.py"
}

preserve_config() {
  local saved="$1"
  SAVED_CONFIG="$saved" APP_CONFIG="${APP_DIR}/config.py" "$PYTHON" - <<'PY'
import ast
import os
from pathlib import Path

def values(text):
    found = {}
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found

def line(name, value):
    if value is None:
        return f"{name} = None"
    if isinstance(value, bool):
        return f"{name} = {value}"
    if isinstance(value, int):
        return f"{name} = {value}"
    return f"{name} = {value!r}"

template_path = Path(os.environ["APP_CONFIG"])
merged = values(template_path.read_text(encoding="utf-8"))
merged.update(values(Path(os.environ["SAVED_CONFIG"]).read_text(encoding="utf-8")))
order = [
    "ENV", "HOST", "PORT", "DATA_DIR", "REPORTS_DIR", "LOG_DIR", "DATABASE_PATH",
    "SECRET_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD", "COOKIE_SECURE",
    "SESSION_COOKIE", "CSRF_COOKIE", "LOG_LEVEL", "LOG_MAX_BYTES", "LOG_BACKUP_COUNT",
    "SSL_CERTFILE", "SSL_KEYFILE", "TRUSTED_HOSTS",
]
names = [name for name in order if name in merged]
names.extend(name for name in merged if name not in names)
template_path.write_text("\n".join(line(name, merged[name]) for name in names) + "\n", encoding="utf-8")
os.chmod(template_path, 0o640)
PY
  chown "$APP_USER:$APP_GROUP" "${APP_DIR}/config.py"
}

write_service() {
  cat >"$UNIT_FILE" <<EOF
[Unit]
Description=Traffic Panel
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python main.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
  chown "$APP_USER:$APP_GROUP" "$UNIT_FILE"
  chmod 0644 "$UNIT_FILE"
  ln -sfn "$UNIT_FILE" "/etc/systemd/system/${SERVICE}.service"
  systemctl daemon-reload
}

nginx_path() {
  if [ -d /etc/nginx/sites-available ]; then
    printf '%s' "/etc/nginx/sites-available/${NGINX_SITE}"
  else
    printf '%s' "/etc/nginx/conf.d/${NGINX_SITE}.conf"
  fi
}

link_nginx() {
  local dest
  dest="$(nginx_path)"
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    mv "$dest" "$NGINX_FILE"
  fi
  ln -sfn "$NGINX_FILE" "$dest"
  if [ -d /etc/nginx/sites-available ]; then
    ln -sfn "$NGINX_FILE" "/etc/nginx/sites-enabled/${NGINX_SITE}"
  fi
}

write_nginx() {
  cat >"$NGINX_FILE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
EOF
  chown "$APP_USER:$APP_GROUP" "$NGINX_FILE"
  chmod 0644 "$NGINX_FILE"
  link_nginx
  nginx -t
  systemctl enable nginx
  systemctl reload nginx
}

remove_nginx() {
  local path
  path="$(nginx_path)"
  rm -f "$path" "/etc/nginx/sites-enabled/${NGINX_SITE}"
  if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
    systemctl reload nginx || true
  fi
}

open_firewall() {
  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    if [ -n "$DOMAIN" ]; then
      ufw allow 80/tcp
      ufw allow 443/tcp
    else
      ufw allow "${PORT}/tcp"
    fi
  fi
}

wait_healthy() {
  local host_header="$1"
  local i
  for i in $(seq 1 30); do
    if curl -fsS -H "Host: ${host_header}" "http://127.0.0.1:${PORT}/health" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  journalctl -u "$SERVICE" -n 40 --no-pager || true
  return 1
}

collect_install_answers() {
  if [ -z "$DOMAIN" ] && [ -r /dev/tty ]; then
    DOMAIN="$(prompt "Domain for HTTPS (leave empty to skip SSL): ")"
  fi
  if [ -n "$DOMAIN" ]; then
    valid_domain "$DOMAIN" || die "The domain name is invalid."
    if [ -z "$EMAIL" ]; then
      [ -r /dev/tty ] || die "Pass --email when a domain is set and there is no terminal."
      EMAIL="$(prompt "Email for the certificate: ")"
    fi
    valid_email "$EMAIL" || die "The email address is invalid."
  fi
  if [ -z "$ADMIN_USER" ]; then
    [ -r /dev/tty ] || die "Pass --admin and --password, or run from a terminal."
    ADMIN_USER="$(prompt "Admin username: ")"
  fi
  valid_admin "$ADMIN_USER" || die "The admin username may contain letters, digits, dots, hyphens, and underscores."
  if [ -z "$ADMIN_PASSWORD" ]; then
    [ -r /dev/tty ] || die "Pass --password, or run from a terminal."
    ADMIN_PASSWORD="$(prompt_secret "Admin password (at least 10 characters): ")"
    local again
    again="$(prompt_secret "Repeat password: ")"
    [ "$ADMIN_PASSWORD" = "$again" ] || die "The passwords do not match."
  fi
  [ "${#ADMIN_PASSWORD}" -ge 10 ] || die "The admin password must be at least 10 characters."
}

install_panel() {
  choose_install_dir
  if installed; then
    die "The panel is already installed. Update it with: install.sh update"
  fi
  if [ -e "$APP_DIR" ]; then
    die "${APP_DIR} already exists. Remove it before installing again."
  fi
  install -d -m 0755 "$(dirname "$APP_DIR")"
  collect_install_answers
  ensure_python
  ensure_user
  git clone --depth 1 "$REPO" "$APP_DIR"
  chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
  run_as_app "$PYTHON" -m venv "${APP_DIR}/.venv"
  run_as_app "${APP_DIR}/.venv/bin/pip" install --upgrade pip
  run_as_app "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

  local secret host_value cookie_secure trusted
  secret="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  if [ -n "$DOMAIN" ]; then
    host_value="127.0.0.1"
    cookie_secure="false"
    trusted="$DOMAIN"
  else
    host_value="0.0.0.0"
    cookie_secure="false"
    trusted=""
  fi
  write_config "$secret" "$ADMIN_USER" "$ADMIN_PASSWORD" "$host_value" "$cookie_secure" "$trusted"
  write_service
  systemctl enable --now "$SERVICE"
  wait_healthy "${DOMAIN:-127.0.0.1}" || die "The panel did not become healthy. See the service log above."
  write_config "$secret" "" "" "$host_value" "$cookie_secure" "$trusted"

  if [ -n "$DOMAIN" ]; then
    apt_install nginx certbot python3-certbot-nginx
    write_nginx
    echo "Requesting a certificate for ${DOMAIN}. Port 80 must reach this server and DNS must point here."
    if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect; then
      link_nginx
      nginx -t
      systemctl reload nginx
      write_config "$secret" "" "" "127.0.0.1" "true" "$DOMAIN"
      systemctl restart "$SERVICE"
      wait_healthy "$DOMAIN" || die "The panel did not return after enabling secure cookies."
    else
      echo "HTTPS was not issued. The panel is still available at http://${DOMAIN} through the proxy."
      echo "After DNS and port 80 work, run certbot --nginx -d ${DOMAIN}."
    fi
  fi
  unset ADMIN_PASSWORD secret
  open_firewall
  write_state
  echo
  if [ -n "$DOMAIN" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    echo "Panel is available at https://${DOMAIN}"
  elif [ -n "$DOMAIN" ]; then
    echo "Panel is available at http://${DOMAIN}"
  else
    echo "Panel is available at http://SERVER-IP:${PORT}"
  fi
  echo "The first admin password is stored only as a hash in the database."
  echo "Installed in ${APP_DIR}"
}

update_panel() {
  locate_installed_dir
  installed || die "The panel is not installed."
  [ -d "${APP_DIR}/.git" ] || die "The install directory is not a git checkout."
  ensure_python
  load_state
  local saved
  saved="$(mktemp)"
  cp "${APP_DIR}/config.py" "$saved"
  run_as_app git -C "$APP_DIR" fetch --depth 1 origin main
  run_as_app git -C "$APP_DIR" reset --hard origin/main
  preserve_config "$saved"
  rm -f "$saved"
  if [ ! -x "${APP_DIR}/.venv/bin/pip" ]; then
    run_as_app "$PYTHON" -m venv "${APP_DIR}/.venv"
  fi
  run_as_app "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
  systemctl restart "$SERVICE"
  wait_healthy "${DOMAIN:-127.0.0.1}" || die "The panel did not become healthy after the update."
  write_state
  echo "Update complete."
}

uninstall_panel() {
  locate_installed_dir
  installed || die "The panel is not installed."
  load_state
  if [ "$ASSUME_YES" -ne 1 ]; then
    [ -r /dev/tty ] || die "Pass --yes to remove the panel without a prompt."
    echo "This removes the panel, its database, logs, service, proxy site, and issued certificate."
    local answer
    answer="$(prompt "Type DELETE to continue: ")"
    [ "$answer" = "DELETE" ] || die "Uninstall cancelled."
  fi
  systemctl disable --now "$SERVICE" || true
  rm -f "/etc/systemd/system/${SERVICE}.service"
  systemctl daemon-reload
  if [ -n "${DOMAIN:-}" ] && command -v certbot >/dev/null 2>&1; then
    certbot delete --cert-name "$DOMAIN" --non-interactive || true
  fi
  if [ -n "${DOMAIN:-}" ]; then
    remove_nginx
  fi
  if [ -d "$APP_DIR" ]; then
    [ -f "$STATE_FILE" ] || die "Refusing to remove ${APP_DIR}: install state is missing."
    [ -d "${APP_DIR}/.git" ] || die "Refusing to remove ${APP_DIR}: it is not the panel checkout."
    rm -rf "$APP_DIR"
  fi
  if id "$APP_USER" >/dev/null 2>&1; then
    userdel "$APP_USER" || true
  fi
  echo "The panel has been removed."
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      install|update|uninstall)
        ACTION="$1"
        shift
        ;;
      --dir)
        APP_DIR="${2:-}"
        [ -n "$APP_DIR" ] || die "Pass a directory after --dir."
        shift 2
        ;;
      --domain)
        DOMAIN="${2:-}"
        shift 2
        ;;
      --email)
        EMAIL="${2:-}"
        shift 2
        ;;
      --admin)
        ADMIN_USER="${2:-}"
        shift 2
        ;;
      --password)
        ADMIN_PASSWORD="${2:-}"
        shift 2
        ;;
      --yes)
        ASSUME_YES=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done
}

main() {
  parse_args "$@"
  need_root
  require_deb
  case "$ACTION" in
    install) install_panel ;;
    update) update_panel ;;
    uninstall) uninstall_panel ;;
  esac
}

main "$@"
