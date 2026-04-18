# TeacherMate server deployment

## One-command install

Run the installer from the project root as `root`:

```bash
export ANTHROPIC_API_KEY="your_real_api_key"
export BASIC_AUTH_PASSWORD="choose_a_strong_password"
bash deploy/install.sh
```

What the installer does:

- installs system packages
- installs Claude CLI using Anthropic's native installer
- creates the Python virtual environment and runtime dependencies
- creates runtime data directories
- writes `/etc/teachermate/teachermate.env`
- writes and enables the `systemd` service
- writes and enables the `nginx` site
- creates the Basic Auth password file

After the script finishes:

- open `80/tcp` in the cloud security group or firewall
- do not expose `8081/tcp`
- visit `http://<server-public-ip>/`

If you prefer to enter secrets interactively, omit either environment variable and the script will prompt for it.

This deployment path is for a single Linux server with:

- `systemd` managing `uvicorn`
- `nginx` reverse proxy on port `80`
- `nginx` Basic Auth in front of the app
- project rooted at `/root/project/teachermate`

It is intentionally single-process. Do not run multiple `uvicorn` workers for this app because runtime state and content directories are local to one process.

## Manual deployment

## 1. Server prerequisites

The commands below assume Ubuntu or Debian.

```bash
apt update
apt install -y nginx apache2-utils curl
```

Install Claude Code CLI if it is not already present. Anthropic's native installer currently installs the `claude` symlink in `~/.local/bin`.

```bash
curl -fsSL https://claude.ai/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
claude --version
```

## 2. Prepare the project

```bash
cd /root/project/teachermate

python3.11 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install -U pip setuptools wheel hatchling -i https://pypi.org/simple --default-timeout 120
python -m pip install \
  "fastapi>=0.115.0" \
  "uvicorn>=0.34.0" \
  "jinja2>=3.1.0" \
  "python-multipart>=0.0.20" \
  "markitdown[all]>=0.1.5" \
  "claude-agent-sdk>=0.1.59" \
  "pytest>=8.3.0" \
  -i https://pypi.org/simple \
  --default-timeout 120
```

Verify the runtime:

```bash
python -c "import claude_agent_sdk; print('claude_agent_sdk ok')"
which claude
claude --version
```

## 3. Prepare runtime directories

These directories must exist and remain writable because the app writes imports, wiki data, artifacts, and JSON state into the repository tree.

```bash
cd /root/project/teachermate
mkdir -p imports raw wiki artifacts state/imports state/jobs state/chats state/artifacts
```

## 4. Configure environment variables

Create the environment directory and copy the example:

```bash
mkdir -p /etc/teachermate
cp deploy/systemd/teachermate.env.example /etc/teachermate/teachermate.env
```

Edit `/etc/teachermate/teachermate.env` and set at least:

```bash
ANTHROPIC_API_KEY=your_real_api_key
```

Optional knobs:

```bash
TEACHERMATE_COMPILE_MAX_TURNS=24
TEACHERMATE_COMPILE_RETRY_MAX_TURNS=48
```

## 5. Install and start the systemd service

```bash
cp deploy/systemd/teachermate.service /etc/systemd/system/teachermate.service
systemctl daemon-reload
systemctl enable --now teachermate
systemctl status teachermate
```

Read logs:

```bash
journalctl -u teachermate -f
```

The app should now be listening only on `127.0.0.1:8081`.

## 6. Configure Basic Auth

Create the Basic Auth password file:

```bash
htpasswd -c /etc/nginx/.htpasswd_teachermate teacher
```

You can add more users later with:

```bash
htpasswd /etc/nginx/.htpasswd_teachermate another_user
```

## 7. Install and enable the nginx site

```bash
cp deploy/nginx/teachermate.conf /etc/nginx/sites-available/teachermate
ln -sf /etc/nginx/sites-available/teachermate /etc/nginx/sites-enabled/teachermate
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
systemctl enable nginx
```

## 8. Network rules

Keep the app port private.

- Open `80/tcp` in the cloud security group or firewall
- Do not expose `8081/tcp` publicly

On `ufw`, the minimal rule set is:

```bash
ufw allow 80/tcp
ufw deny 8081/tcp
```

## 9. Verification

Local verification:

```bash
curl -I http://127.0.0.1:8081/
curl -I http://127.0.0.1/
curl -u teacher:your_password -I http://127.0.0.1/
```

Expected results:

- `http://127.0.0.1:8081/` returns `200`
- `http://127.0.0.1/` returns `401 Unauthorized`
- `http://127.0.0.1/` with Basic Auth returns `200`

Public verification:

- Visit `http://<server-public-ip>/`
- Browser should prompt for a username and password
- After authentication, the app home page should render

## 10. Operational notes

- Service restart: `systemctl restart teachermate`
- Nginx reload: `systemctl reload nginx`
- Service logs: `journalctl -u teachermate -n 200`
- If Claude-powered features fail, check:
  - `python -c "import claude_agent_sdk"`
  - `which claude`
  - `claude --version`
  - `grep ANTHROPIC_API_KEY /etc/teachermate/teachermate.env`

## 11. Later improvements

When you have a real domain, keep the same architecture and add HTTPS in front of this nginx site. At that point, update `server_name`, obtain a certificate, and redirect `80` to `443`.
