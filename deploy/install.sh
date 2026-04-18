#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="${TEACHERMATE_APP_NAME:-teachermate}"
APP_USER="${TEACHERMATE_APP_USER:-root}"
APP_GROUP="${TEACHERMATE_APP_GROUP:-$APP_USER}"
APP_HOST="${TEACHERMATE_APP_HOST:-127.0.0.1}"
APP_PORT="${TEACHERMATE_APP_PORT:-8081}"
PUBLIC_PORT="${TEACHERMATE_PUBLIC_PORT:-80}"
BASIC_AUTH_USER="${TEACHERMATE_BASIC_AUTH_USER:-teacher}"
COMPILE_MAX_TURNS="${TEACHERMATE_COMPILE_MAX_TURNS:-24}"
COMPILE_RETRY_MAX_TURNS="${TEACHERMATE_COMPILE_RETRY_MAX_TURNS:-48}"
ENV_DIR="${TEACHERMATE_ENV_DIR:-/etc/teachermate}"
ENV_FILE="${TEACHERMATE_ENV_FILE:-$ENV_DIR/teachermate.env}"
HTPASSWD_PATH="${TEACHERMATE_HTPASSWD_PATH:-/etc/nginx/.htpasswd_${APP_NAME}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_PATH="/etc/systemd/system/${APP_NAME}.service"
NGINX_SITE_AVAILABLE="/etc/nginx/sites-available/${APP_NAME}"
NGINX_SITE_ENABLED="/etc/nginx/sites-enabled/${APP_NAME}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
APP_LOCAL_BIN="${APP_HOME}/.local/bin"

info() {
  printf '[teachermate-deploy] %s\n' "$*"
}

fail() {
  printf '[teachermate-deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    fail "Run this script as root."
  fi
}

require_user() {
  id "$APP_USER" >/dev/null 2>&1 || fail "User ${APP_USER} does not exist."
  getent passwd "$APP_USER" >/dev/null 2>&1 || fail "Could not resolve home for ${APP_USER}."
}

prompt_secret_if_missing() {
  local var_name="$1"
  local prompt_text="$2"
  local current_value="${!var_name:-}"

  if [[ -n "$current_value" ]]; then
    return
  fi

  if [[ -t 0 ]]; then
    local value
    read -r -s -p "${prompt_text}: " value
    printf '\n'
    [[ -n "$value" ]] || fail "${var_name} cannot be empty."
    printf -v "$var_name" '%s' "$value"
    export "$var_name"
    return
  fi

  fail "${var_name} is required. Export it before running the script."
}

install_system_packages() {
  info "Installing system packages."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y nginx apache2-utils curl ca-certificates python3.11 python3.11-venv
}

ensure_python_runtime() {
  command -v python3.11 >/dev/null 2>&1 || fail "python3.11 was not installed."
  python3.11 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required.")
PY
}

ensure_claude_cli() {
  if command -v claude >/dev/null 2>&1; then
    info "Claude CLI already present: $(command -v claude)"
    return
  fi

  info "Installing Claude CLI via Anthropic native installer."
  HOME="$APP_HOME" bash -lc 'curl -fsSL https://claude.ai/install.sh | bash'
  export PATH="${APP_LOCAL_BIN}:${PATH}"
  command -v claude >/dev/null 2>&1 || fail "Claude CLI installation failed."
}

ensure_virtualenv() {
  info "Preparing Python virtual environment."
  if [[ ! -x "${REPO_DIR}/.venv/bin/python" ]]; then
    python3.11 -m venv "${REPO_DIR}/.venv"
  fi

  "${REPO_DIR}/.venv/bin/python" -m ensurepip --upgrade
  "${REPO_DIR}/.venv/bin/python" -m pip install -U pip setuptools wheel hatchling -i https://pypi.org/simple --default-timeout 120

  "${REPO_DIR}/.venv/bin/python" -m pip install \
    "fastapi>=0.115.0" \
    "uvicorn>=0.34.0" \
    "jinja2>=3.1.0" \
    "python-multipart>=0.0.20" \
    "markitdown[all]>=0.1.5" \
    "claude-agent-sdk>=0.1.59" \
    "pytest>=8.3.0" \
    -i https://pypi.org/simple \
    --default-timeout 120

  "${REPO_DIR}/.venv/bin/python" -c "import claude_agent_sdk" >/dev/null
}

ensure_runtime_dirs() {
  info "Ensuring runtime directories exist."
  mkdir -p \
    "${REPO_DIR}/imports" \
    "${REPO_DIR}/raw" \
    "${REPO_DIR}/wiki" \
    "${REPO_DIR}/artifacts" \
    "${REPO_DIR}/state/imports" \
    "${REPO_DIR}/state/jobs" \
    "${REPO_DIR}/state/chats" \
    "${REPO_DIR}/state/artifacts"
  chown -R "${APP_USER}:${APP_GROUP}" \
    "${REPO_DIR}/imports" \
    "${REPO_DIR}/raw" \
    "${REPO_DIR}/wiki" \
    "${REPO_DIR}/artifacts" \
    "${REPO_DIR}/state"
}

write_env_file() {
  local api_key="${ANTHROPIC_API_KEY:-}"
  if [[ -z "$api_key" && -f "$ENV_FILE" ]]; then
    api_key="$(awk -F= '/^ANTHROPIC_API_KEY=/{print substr($0, index($0,"=")+1)}' "$ENV_FILE")"
  fi
  export ANTHROPIC_API_KEY="$api_key"

  info "Writing environment file to ${ENV_FILE}."
  mkdir -p "$ENV_DIR"
  cat >"$ENV_FILE" <<EOF
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
TEACHERMATE_COMPILE_MAX_TURNS=${COMPILE_MAX_TURNS}
TEACHERMATE_COMPILE_RETRY_MAX_TURNS=${COMPILE_RETRY_MAX_TURNS}
EOF
  chmod 600 "$ENV_FILE"
}

write_systemd_service() {
  info "Writing systemd service to ${SERVICE_PATH}."
  cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=TeacherMate FastAPI service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PATH=${REPO_DIR}/.venv/bin:${APP_LOCAL_BIN}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=${REPO_DIR}/.venv/bin/uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT} --workers 1
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30
NoNewPrivileges=yes
PrivateTmp=yes
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
}

write_nginx_config() {
  info "Writing nginx config to ${NGINX_SITE_AVAILABLE}."
  cat >"$NGINX_SITE_AVAILABLE" <<EOF
server {
    listen ${PUBLIC_PORT};
    listen [::]:${PUBLIC_PORT};
    server_name _;

    client_max_body_size 50m;

    auth_basic "TeacherMate";
    auth_basic_user_file ${HTPASSWD_PATH};

    location / {
        proxy_pass http://${APP_HOST}:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 30s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        send_timeout 600s;
    }
}
EOF

  ln -sf "$NGINX_SITE_AVAILABLE" "$NGINX_SITE_ENABLED"
  rm -f /etc/nginx/sites-enabled/default
}

configure_basic_auth() {
  local password="${BASIC_AUTH_PASSWORD:-}"
  if [[ -z "$password" && -f "$HTPASSWD_PATH" ]]; then
    info "Basic Auth file exists, keeping existing credentials."
    if getent group www-data >/dev/null 2>&1; then
      chown root:www-data "$HTPASSWD_PATH"
      chmod 640 "$HTPASSWD_PATH"
    else
      chmod 644 "$HTPASSWD_PATH"
    fi
    return
  fi

  export BASIC_AUTH_PASSWORD="$password"
  prompt_secret_if_missing "BASIC_AUTH_PASSWORD" "Enter Basic Auth password for ${BASIC_AUTH_USER}"
  info "Writing Basic Auth credentials to ${HTPASSWD_PATH}."
  htpasswd -cb "$HTPASSWD_PATH" "$BASIC_AUTH_USER" "$BASIC_AUTH_PASSWORD" >/dev/null
  if getent group www-data >/dev/null 2>&1; then
    chown root:www-data "$HTPASSWD_PATH"
    chmod 640 "$HTPASSWD_PATH"
  else
    chmod 644 "$HTPASSWD_PATH"
  fi
}

enable_services() {
  info "Reloading systemd and nginx."
  systemctl daemon-reload
  systemctl enable --now "$APP_NAME"
  nginx -t
  systemctl enable nginx
  systemctl restart nginx
}

print_summary() {
  cat <<EOF

Deployment complete.

App root:          ${REPO_DIR}
Service name:      ${APP_NAME}
Env file:          ${ENV_FILE}
Nginx site:        ${NGINX_SITE_AVAILABLE}
Basic Auth user:   ${BASIC_AUTH_USER}
App listen:        ${APP_HOST}:${APP_PORT}
Public entry:      http://<server-public-ip>:${PUBLIC_PORT}/

Useful commands:
  systemctl status ${APP_NAME}
  journalctl -u ${APP_NAME} -n 200
  systemctl restart ${APP_NAME}
  systemctl restart nginx

Important:
  - Open ${PUBLIC_PORT}/tcp in your cloud security group or firewall.
  - Do not expose ${APP_PORT}/tcp publicly.
EOF
}

main() {
  require_root
  require_user
  install_system_packages
  ensure_python_runtime
  ensure_claude_cli
  ensure_virtualenv
  ensure_runtime_dirs
  write_env_file
  write_systemd_service
  configure_basic_auth
  write_nginx_config
  enable_services
  print_summary
}

main "$@"
