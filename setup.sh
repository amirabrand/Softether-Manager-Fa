#!/usr/bin/env bash
#
# One-line installer for Softether-Manager (bilingual FA/EN edition).
#
#   sudo bash setup.sh
#
# Everything happens from this single command:
#   1. checks the few tools packaging needs,
#   2. builds the web frontend if a prebuilt copy is not present,
#   3. stages a local release archive (tar.gz + sha256),
#   4. hands it to scripts/install.sh, which installs the panel, the
#      SoftEther VPN Server, and the systemd service, and prints the
#      address, username and password when it is done.
#
# Any argument you pass is forwarded to scripts/install.sh, so
#
#   sudo bash setup.sh --port 8080 --password 'my-secret' --path mypath
#
# installs with those values instead of the generated defaults.
#
set -euo pipefail

# ---------------------------------------------------------------- appearance
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'
else
  BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; OFF=""
fi

say()  { printf '%s\n' "  $*"; }
step() { printf '\n%s%s%s\n' "$BOLD" "==> $*" "$OFF"; }
ok()   { printf '%s\n' "${GREEN}  [OK]${OFF} $*"; }
warn() { printf '%s\n' "${YELLOW}  [!]${OFF} $*" >&2; }
die()  { printf '%s\n' "${RED}  [x]${OFF} $*" >&2; exit 1; }

# ---------------------------------------------------------------- locate root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
[[ -d app && -d Library && -f scripts/install.sh ]] ||
  die "This script must sit in the Softether-Manager project root. (این اسکریپت باید در ریشه پروژه اجرا شود.)"

# ---------------------------------------------------------------- arguments
EXTRA_ARGS=("$@")

have_user_value() {  # whether the operator supplied a given flag themselves
  local flag="$1" a
  for a in ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}; do
    [[ "$a" == "$flag" ]] && return 0
  done
  return 1
}

random_token() {  # no dependence on openssl; python3 is guaranteed by preflight
  python3 - "$1" <<'PY'
import secrets, string, sys
n = int(sys.argv[1])
alphabet = string.ascii_letters + string.digits
print("".join(secrets.choice(alphabet) for _ in range(n)))
PY
}
random_path() {
  python3 -c 'import secrets; print(secrets.token_hex(3))'
}

# ---------------------------------------------------------------- preflight
[[ $EUID -eq 0 ]] || die "Run as root:  sudo bash setup.sh   (باید با sudo اجرا شود)"

step "Checking packaging tools (بررسی ابزارها)"
MISSING=()
command -v tar       >/dev/null 2>&1 || MISSING+=(tar)
command -v sha256sum >/dev/null 2>&1 || MISSING+=(coreutils)
command -v python3   >/dev/null 2>&1 || MISSING+=(python3)
if (( ${#MISSING[@]} > 0 )); then
  if command -v apt-get >/dev/null 2>&1; then
    say "Installing missing packages: ${MISSING[*]}"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq || true
    apt-get install -y -qq "${MISSING[@]}" >/dev/null ||
      die "Could not install ${MISSING[*]}. Install them and run again."
  else
    die "Missing tools: ${MISSING[*]}. Install them and run again."
  fi
fi
ok "tar, sha256sum, python3 present"

# ---------------------------------------------------------------- frontend
if [[ -f app/web/out/index.html ]]; then
  step "Frontend (built)"
  ok "Prebuilt frontend found in app/web/out -- no Node.js needed"
else
  step "Building the web frontend (ساخت رابط وب)"
  if ! command -v node >/dev/null 2>&1; then
    say "Node.js not found; installing Node 20 ..."
    if command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then
      if command -v curl >/dev/null 2>&1; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
      else
        wget -qO- https://deb.nodesource.com/setup_20.x | bash - >/dev/null
      fi
      apt-get install -y -qq nodejs >/dev/null
    elif command -v apt-get >/dev/null 2>&1; then
      apt-get install -y -qq nodejs npm >/dev/null
    else
      die "Node.js is required to build the frontend. Install Node 18+ and npm, then re-run."
    fi
  fi
  ( cd app/web && npm install --no-audit --no-fund && npm run build ) ||
    die "Frontend build failed."
  ok "Frontend built into app/web/out"
fi

# ---------------------------------------------------------------- version stamp
if [[ ! -f VERSION ]]; then
  printf 'v1.0.0-fa.1\n' > VERSION
fi

# ---------------------------------------------------------------- stage release
step "Staging local release package (بسته‌بندی نسخه محلی)"
STAGING="$(mktemp -d /tmp/sem-release.XXXXXX)"
trap 'rm -rf "$STAGING"' EXIT
TREE="$STAGING/tree"
mkdir -p "$TREE"

cp -a app "$TREE/app"
cp -a Library "$TREE/Library"
cp -a requirements.txt "$TREE/requirements.txt"
cp -a scripts "$TREE/scripts"
for extra in run.py VERSION README.md CHANGES-FA.md; do
  [[ -e "$extra" ]] && cp -a "$extra" "$TREE/$extra"
done
find "$TREE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$TREE/app/web/node_modules" "$TREE/app/web/.next" "$TREE/app/web/tsconfig.tsbuildinfo"

# The release must carry the built frontend; fail loudly rather than ship a
# package the real installer would refuse.
[[ -f "$TREE/app/web/out/index.html" ]] ||
  die "app/web/out is missing -- the frontend was not built."

( cd "$TREE" && tar -czf "$STAGING/softether-manager.tar.gz" . ) ||
  die "Could not create the release archive."
( cd "$STAGING" && sha256sum softether-manager.tar.gz > softether-manager.tar.gz.sha256 )
mkdir -p "$STAGING/latest"
mv "$STAGING/softether-manager.tar.gz" "$STAGING/softether-manager.tar.gz.sha256" "$STAGING/latest/"
ok "Release archive staged ($(du -h "$STAGING/latest/softether-manager.tar.gz" | cut -f1))"

# ---------------------------------------------------------------- defaults
# Mirror install.sh's own upgrade test so both agree on fresh vs upgrade.
INSTALLED_BEFORE=0
if [[ -f /etc/systemd/system/softether-manager.service || -d /opt/softether-manager ]]; then
  INSTALLED_BEFORE=1
fi

INSTALL_ARGS=(--non-interactive --yes)

if (( INSTALLED_BEFORE == 0 )); then
  # Fresh install: generate credentials the operator can read at the end.
  if ! have_user_value --username; then
    INSTALL_ARGS+=(--username admin)
  fi
  if ! have_user_value --password; then
    GENERATED_PASSWORD="$(random_token 14)"
    INSTALL_ARGS+=(--password "$GENERATED_PASSWORD")
  fi
  if ! have_user_value --port; then
    INSTALL_ARGS+=(--port 8443)
  fi
  if ! have_user_value --path; then
    GENERATED_PATH="$(random_path)"
    INSTALL_ARGS+=(--path "$GENERATED_PATH")
  fi
fi

# ---------------------------------------------------------------- install
step "Installing the panel + SoftEther VPN Server (نصب پنل و SoftEther)"
SEM_RELEASE_BASE="$STAGING" bash scripts/install.sh "${INSTALL_ARGS[@]}" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} ||
  die "Installation failed. Nothing was half-installed silently -- see the messages above."

# ---------------------------------------------------------------- summary
if (( INSTALLED_BEFORE == 0 )) && [[ -n "${GENERATED_PASSWORD:-}" ]]; then
  printf '\n%s\n' "${BOLD}  ─────────────────────────────────────────────${OFF}"
  printf '%s\n'  "  ${BOLD}نام کاربری:${OFF}  admin"
  printf '%s\n'  "  ${BOLD}رمز عبور:${OFF}    ${GREEN}${GENERATED_PASSWORD}${OFF}"
  printf '%s\n'  "  ${BOLD}پورت:${OFF}        8443"
  printf '%s\n'  "  ${BOLD}مسیر مخفی:${OFF}   /${GENERATED_PATH}"
  printf '%s\n'  "  ${DIM}این اطلاعات را جایی ذخیره کنید!${OFF}"
  printf '%s\n'  "  ${DIM}Address: http://<server-ip>:8443/${GENERATED_PATH}${OFF}"
  printf '%s\n' "${BOLD}  ─────────────────────────────────────────────${OFF}"
  printf '%s\n' "  ${DIM}Panel service:  systemctl status softether-manager${OFF}"
  printf '%s\n' "  ${DIM}Management CLI: sem${OFF}"
fi
