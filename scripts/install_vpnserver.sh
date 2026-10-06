#!/usr/bin/env bash
# AMIRITPANEL -- one-command installer for the VPN server this panel manages.
#
# Automates the manual recipe:
#   1. build prerequisites          (build-essential, curl, tar, readline/ssl/zlib)
#   2. download + extract v4.44-9807-rtm to /usr/local
#   3. compile (non-interactive: the stock `make` asks for the license three
#      times -- this answers 1 automatically) + file permissions
#   4. a systemd unit that survives reboots, started and health-checked
#   5. the server administrator password, set locally through vpncmd (the
#      credential never crosses a network)
#   6. the firewall ports the VPN shop needs (ufw when present, firewalld else)
#
# Usage:
#   sudo SEM_VPN_PASSWORD='!@F64F71eshgh98@!' bash install_vpnserver.sh
#   sudo bash install_vpnserver.sh --password '!@F64F71eshgh98@!'
# With neither, a strong random password is generated and printed at the end.
#
#   SEM_DRY_RUN=1 bash install_vpnserver.sh   # print the plan, change nothing
#
# Exit codes: 0 ok, 10 not root, 11 unsupported OS, 12 no systemd,
#             13 port already in use by something else, 20 build failed,
#             21 service failed, 22 password could not be set.

set -o pipefail

SE_DIR="/usr/local/vpnserver"
SE_SERVICE="vpnserver"
MGMT_PORT="${SEM_VPN_PORT:-5555}"
SE_VERSION="v4.44-9807-rtm"
SE_URL="https://github.com/SoftEtherVPN/SoftEtherVPN_Stable/releases/download/${SE_VERSION}/softether-vpnserver-v4.44-9807-rtm-2025.04.16-linux-x64-64bit.tar.gz"
ADMIN_PASSWORD="${SEM_VPN_PASSWORD:-}"
FORCE=0
DRY_RUN="${SEM_DRY_RUN:-0}"

# Ports a VPN shop lives on:
#   80/tcp, 443/tcp   HTTP(S), OpenConnect and SSTP -- 443 crosses most networks
#   1194/udp          OpenVPN
#   500+4500/udp      L2TP/IPsec (and its NAT traversal)
#   5555/tcp          the server's own management RPC (the panel speaks here)
VPN_PORTS_TCP="80 443 5555"
VPN_PORTS_UDP="1194 500 4500"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '  \033[31m[x]\033[0m %s\n' "$*" >&2; exit "${2:-1}"; }
step() { say ""; printf '\033[1m== %s\033[0m\n' "$*"; }

# Every mutation goes through run(): in dry-run mode the whole plan prints
# without touching the machine, which is how the flow is testable anywhere.
run() {
  if [[ "$DRY_RUN" == "1" ]]; then say "  + $*"; return 0; fi
  "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --password)  ADMIN_PASSWORD="${2-}"; shift 2 ;;
    --port)      MGMT_PORT="${2-}"; shift 2 ;;
    --force)     FORCE=1; shift ;;
    -h|--help)   sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" 10 ;;
  esac
done

# ------------------------------------------------------------- preflight

step "1/6 Preflight"
if [[ "$DRY_RUN" != "1" ]]; then
  [[ $EUID -eq 0 ]] || die "run me as root (sudo)" 10
fi
command -v systemctl >/dev/null 2>&1 || die "systemd is required" 12
if command -v apt-get >/dev/null 2>&1; then
  PKG="apt"
elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
  PKG="dnf"
else
  die "no apt-get/dnf/yum found -- install the build tools by hand" 11
fi
if { exec 3<>/dev/tcp/127.0.0.1/"$MGMT_PORT"; } 2>/dev/null; then
  exec 3>&- 3<&-
  [[ "$FORCE" == "1" ]] || die "port $MGMT_PORT is already answering -- a VPN server seems installed; use --force to continue" 13
fi
ok "root, systemd and $PKG present; port $MGMT_PORT free"

# ------------------------------------------------------------- dependencies

step "2/6 Build prerequisites"
if [[ "$PKG" == "apt" ]]; then
  run env DEBIAN_FRONTEND=noninteractive apt-get update -qq
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      build-essential curl tar libreadline-dev libssl-dev zlib1g-dev
else
  run $PKG install -y gcc make curl tar openssl-devel readline-devel zlib-devel
fi
ok "compiler toolchain ready"

# ------------------------------------------------------------- download

step "3/6 Download and extract $SE_VERSION"
if [[ -x "$SE_DIR/vpnserver" && "$FORCE" != "1" ]]; then
  ok "$SE_DIR/vpnserver already exists -- keeping it (use --force to rebuild)"
else
  run curl -fL "$SE_URL" -o /tmp/softether.tar.gz
  run tar -xzf /tmp/softether.tar.gz -C /usr/local/
  ok "extracted to $SE_DIR"
fi

# ------------------------------------------------------------- compile

step "4/6 Compile (non-interactive)"
if [[ -x "$SE_DIR/vpnserver" && "$FORCE" != "1" ]]; then
  ok "binary already built -- skipping make"
else
  # The stock make() asks the human to read three license screens; `yes 1`
  # answers them and is killed by SIGPIPE the moment make stops reading.
  if [[ "$DRY_RUN" == "1" ]]; then
    run bash -c "cd $SE_DIR && yes 1 | make"
  else
    if ! (cd "$SE_DIR" && set +o pipefail && yes 1 | make > /tmp/softether-make.log 2>&1); then
      tail -n 15 /tmp/softether-make.log >&2 || true
      die "make failed -- see /tmp/softether-make.log" 20
    fi
    [[ -x "$SE_DIR/vpnserver" ]] || { tail -n 15 /tmp/softether-make.log >&2; die "make produced no vpnserver binary" 20; }
  fi
  ok "vpnserver and vpncmd built"
fi
run find "$SE_DIR" -maxdepth 1 -type f -exec chmod 600 {} +
run chmod 700 "$SE_DIR/vpnserver" "$SE_DIR/vpncmd"
ok "permissions: files 600, binaries 700"

# ------------------------------------------------------------- systemd

step "5/6 Systemd service"
run tee "/etc/systemd/system/$SE_SERVICE.service" > /dev/null <<UNIT
[Unit]
Description=AMIRITPANEL VPN Server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=forking
ExecStart=$SE_DIR/vpnserver start
ExecStop=$SE_DIR/vpnserver stop
KillMode=process
Restart=on-failure
WorkingDirectory=$SE_DIR

[Install]
WantedBy=multi-user.target
UNIT
run systemctl daemon-reload
run systemctl enable --now "$SE_SERVICE"

if [[ "$DRY_RUN" != "1" ]]; then
  READY=0
  for _ in $(seq 1 30); do
    systemctl is-active --quiet "$SE_SERVICE" || { sleep 1; continue; }
    if { exec 3<>/dev/tcp/127.0.0.1/"$MGMT_PORT"; } 2>/dev/null; then exec 3>&- 3<&-; READY=1; break; fi
    sleep 1
  done
  systemctl is-active --quiet "$SE_SERVICE" || { systemctl status "$SE_SERVICE" --no-pager -n 20 >&2; die "service is not active" 21; }
  [[ $READY -eq 1 ]] || die "service is active but nothing answers on port $MGMT_PORT" 21
fi
ok "$SE_SERVICE is active (running) and port $MGMT_PORT answers"

# ------------------------------------------------------------- admin password

step "6/6 Server administrator password"
if [[ -z "$ADMIN_PASSWORD" ]]; then
  ADMIN_PASSWORD="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)"
  GENERATED=1
else
  GENERATED=0
fi
if [[ "$DRY_RUN" == "1" ]]; then
  run "$SE_DIR/vpncmd" "localhost:$MGMT_PORT" /SERVER /CMD ServerPasswordSet '********'
  say "  (dry run: the real password is not printed)"
else
  PW_OUT="$("$SE_DIR/vpncmd" "localhost:$MGMT_PORT" /SERVER /CMD ServerPasswordSet "$ADMIN_PASSWORD" 2>&1 || true)"
  if ! printf '%s' "$PW_OUT" | grep -qi "completed successfully"; then
    printf '%s\n' "$PW_OUT" | tail -n 8 >&2
    die "the password could not be set -- set it by hand: $SE_DIR/vpncmd localhost /SERVER, then ServerPasswordSet" 22
  fi
fi
ok "ServerPasswordSet completed successfully"
[[ $GENERATED -eq 1 ]] && say "  generated password: $ADMIN_PASSWORD"

# ------------------------------------------------------------- firewall

step "Firewall (ufw / firewalld)"
FIREWALL_DONE=0
if command -v ufw >/dev/null 2>&1; then
  for p in $VPN_PORTS_TCP; do run ufw allow "$p/tcp"; done
  for p in $VPN_PORTS_UDP; do run ufw allow "$p/udp"; done
  run ufw reload
  if [[ "$DRY_RUN" != "1" ]]; then
    ufw status | grep -q "Status: active" || \
      warn "UFW is INACTIVE: the rules were recorded but the firewall is off (enable it yourself: ufw enable)"
  fi
  FIREWALL_DONE=1
elif command -v firewall-cmd >/dev/null 2>&1; then
  for p in $VPN_PORTS_TCP; do run firewall-cmd --permanent --add-port="$p/tcp"; done
  for p in $VPN_PORTS_UDP; do run firewall-cmd --permanent --add-port="$p/udp"; done
  run firewall-cmd --reload
  FIREWALL_DONE=1
fi
if [[ $FIREWALL_DONE -eq 1 ]]; then
  ok "ports open: TCP $VPN_PORTS_TCP | UDP $VPN_PORTS_UDP"
else
  warn "no ufw/firewalld on this machine -- open TCP $VPN_PORTS_TCP and UDP $VPN_PORTS_UDP in your cloud console"
fi

# ------------------------------------------------------------- result

say ""
step "The VPN server is ready."
say ""
say "  Service:   $SE_SERVICE (systemctl status $SE_SERVICE)"
say "  Directory: $SE_DIR"
say "  RPC:       127.0.0.1:$MGMT_PORT  <- point AMIRITPANEL here"
if [[ $GENERATED -eq 1 ]]; then
  say "  Password:  $ADMIN_PASSWORD   (write it down -- shown once)"
fi
say ""
say "  Next: in the panel open اتصال به سرور and give host 127.0.0.1,"
say "  port $MGMT_PORT and this administrator password. Then open the firewall"
say "  on your cloud console too, if it has one."
exit 0
