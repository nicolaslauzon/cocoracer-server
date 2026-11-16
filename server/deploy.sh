#!/usr/bin/env bash
# Deploy the event server on this machine, reachable only from private networks
# (and, with --proxy, from the --allow ranges).
#
#   sudo server/deploy.sh [--port 8080] [--race-port 8000] [--iface IF]
#                         [--interval SECONDS] [--enable-ufw] [--yes]
#                         [--proxy [--proxy-port 80] [--allow CIDR]...]
#   sudo server/deploy.sh --wipe-state [--yes]
#   sudo server/deploy.sh --uninstall
#
# The server runs untrusted team code. Each car's controller runs in its own
# process under a seccomp filter and a Landlock ruleset (docs/adr/0007),
# which need libseccomp, the server's pyseccomp bindings and a kernel with
# Landlock: the deploy checks the sandbox works and stops if it does not. Around that, the deploy is what contains the code:
#   - it runs as the `cocoracer-server` system user, from a root-owned copy of the code
#     in /opt/cocoracer-server, under a hardened systemd unit (no access to /home, a
#     read-only filesystem but for its state directory, a private /tmp, no
#     capabilities, memory and task caps);
#   - systemd's IP allow-list lets it talk to localhost and the private
#     ranges (10/8, 172.16/12, 192.168/16) only, both ways, so team code
#     cannot reach the internet or the tailnet and nothing off a private
#     network reaches the server;
#   - ufw opens the two ports to those three ranges, and nowhere else. The
#     deploy stops if ufw is inactive, since its rules would then do nothing:
#     --enable-ufw turns it on for the whole machine (--yes never does), and
#     an interactive run asks. SSH stays open: unless a ufw rule already
#     covers sshd's port, the deploy allows it, tagged `ssh`, which neither a
#     redeploy nor --uninstall removes.
# Nothing in the unit or the rules names this machine's address, so a new
# DHCP lease at the venue needs no redeploy.
#
# --proxy also puts Caddy in front of the server, in its own unit
# (cocoracer-server-proxy), for a wider network than the LAN, such as a university's:
# plain HTTP on --proxy-port, forwarding the pages and the live view to the
# server on localhost. The server's unit still
# talks to localhost and the private ranges only, so team code stays as
# fenced in as before; ufw opens the proxy port to the private ranges and to
# each --allow range. Redeploying without --proxy removes the proxy.
#
# Re-running it redeploys the current checkout. The event (teams, pushes,
# races, the freeze) is saved in /var/lib/cocoracer-server and survives a redeploy,
# a restart or a reboot; a redeploy only signs everyone out and drops a race
# in flight. --wipe-state is the one way to start an empty event: it stops
# the service, empties the state directory and starts it again.

set -euo pipefail

SERVICE=cocoracer-server
SERVICE_USER=cocoracer-server
PREFIX=/opt/cocoracer-server
CONF_DIR=/etc/cocoracer-server
STATE_DIR=/var/lib/cocoracer-server
ENV_FILE=$CONF_DIR/env
UNIT_FILE=/etc/systemd/system/$SERVICE.service
UFW_TAG=cocoracer-server
PROXY_SERVICE=cocoracer-server-proxy
PROXY_UNIT_FILE=/etc/systemd/system/$PROXY_SERVICE.service
PROXY_CONF_DIR=/etc/$PROXY_SERVICE
CADDY=/usr/bin/caddy
# Where the proxy serves the race worker's live view; the server's pages
# link it there (views.LIVE_PROXY_PATH).
LIVE_PROXY_PATH=/live/ws
PYTHON=/usr/bin/python3.12
# Who may reach the server, and whom it may reach: every private IPv4 range.
PRIVATE_RANGES="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"

ARGS=("$@")
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

PORT=8080
RACE_PORT=8000
IFACE=
INTERVAL=
ASSUME_YES=0
ENABLE_UFW=0
UNINSTALL=0
WIPE_STATE=0
PROXY=0
PROXY_PORT=80
ALLOW=()

die() { echo "deploy: $*" >&2; exit 1; }
say() { echo "==> $*"; }

usage() {
    sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --port) PORT=${2:?}; shift 2 ;;
        --race-port) RACE_PORT=${2:?}; shift 2 ;;
        --iface) IFACE=${2:?}; shift 2 ;;
        --interval) INTERVAL=${2:?}; shift 2 ;;
        --yes|-y) ASSUME_YES=1; shift ;;
        --enable-ufw) ENABLE_UFW=1; shift ;;
        --proxy) PROXY=1; shift ;;
        --proxy-port) PROXY_PORT=${2:?}; shift 2 ;;
        --allow) ALLOW+=("${2:?}"); shift 2 ;;
        --uninstall) UNINSTALL=1; shift ;;
        --wipe-state) WIPE_STATE=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "deploy: unknown argument: $1" >&2; usage 2 ;;
    esac
done

# --wipe-state and --uninstall stop short of installing the code, so a deploy
# option given with them would be silently ignored.
if [[ $WIPE_STATE -eq 1 || $UNINSTALL -eq 1 ]]; then
    for opt in "${ARGS[@]}"; do
        case $opt in
            --port|--race-port|--iface|--interval|--enable-ufw|--proxy|--proxy-port|--allow)
                die "$opt deploys the code, which --wipe-state and --uninstall do not; deploy first (sudo $0 without them), then wipe" ;;
        esac
    done
fi

[[ $EUID -eq 0 ]] || die "run it as root: sudo $0 ${ARGS[*]}"
for tool in systemctl ufw ip ss rsync curl; do
    command -v "$tool" >/dev/null || die "missing required tool: $tool"
done

# Delete every ufw rule tagged with our comment, highest number first so the
# numbers of the rules still to delete do not shift.
remove_ufw_rules() {
    local numbers
    numbers=$(ufw status numbered | grep -F "# $UFW_TAG" \
        | sed -n 's/^\[ *\([0-9]\+\)\].*/\1/p' | sort -rn || true)
    for n in $numbers; do
        ufw --force delete "$n" >/dev/null
    done
}

# The TCP ports listening on an address other than loopback, one per line
# ("0.0.0.0:22"): what enabling ufw would cut off unless a rule allows it.
exposed_ports() {
    ss -ltnH | awk '{print $4}' \
        | grep -Ev '^(127\.|\[::1\]|\[::ffff:127\.)|%lo:' | sort -u || true
}

# Keep SSH reachable once ufw is on: allow each port sshd listens on, from
# anywhere as before ufw, unless a rule already names that port (one the
# admin wrote, perhaps narrower, is left as it is). Tagged `ssh`, not
# $UFW_TAG, so a redeploy or --uninstall never takes it away.
allow_ssh() {
    command -v sshd >/dev/null || return 0
    local ports rules p
    ports=$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' | sort -u || true)
    rules=$(ufw status)
    for p in ${ports:-22}; do
        if grep -Eq "^($p(/tcp)?|OpenSSH)( |\$)" <<<"$rules"; then
            continue
        fi
        say "allowing SSH on port $p, so ufw does not lock you out"
        ufw allow "$p/tcp" comment ssh >/dev/null
    done
}

# Stop and remove the proxy, if a deploy with --proxy installed one. Its
# state directory goes with it only on --uninstall.
remove_proxy() {
    if [[ -f $PROXY_UNIT_FILE ]]; then
        say "stopping and removing the $PROXY_SERVICE service"
        systemctl disable --now "$PROXY_SERVICE" 2>/dev/null || true
        rm -f "$PROXY_UNIT_FILE"
        systemctl daemon-reload
    fi
    rm -rf "$PROXY_CONF_DIR"
}

# Status is read whole, then matched: `ufw status | grep -q` can fail under
# pipefail when grep exits before ufw has finished writing.
ufw_active() {
    local out
    out=$(ufw status)
    [[ $out == *"Status: active"* ]]
}

# Stop now, before anything is installed, if ufw is inactive: `ufw allow`
# succeeds while it is off, and the rules then protect nothing. Only
# --enable-ufw, or a yes to the question, lets the deploy turn it on (later,
# once our rules are in, so the two ports are never cut off).
check_ufw_active() {
    ufw_active && return 0
    [[ $ENABLE_UFW -eq 1 ]] && return 0
    local ports
    ports=$(exposed_ports)
    echo "ufw is inactive: no firewall rule, this deploy's or any other, is applied."
    if [[ -n $ports ]]; then
        echo "These ports listen beyond loopback; once ufw is on, the machine's"
        echo "other ports are closed to incoming connections unless a rule allows them:"
        echo "$ports" | sed 's/^/    /'
    else
        echo "No other port listens beyond loopback right now."
    fi
    if [[ $ASSUME_YES -eq 0 && -t 0 ]]; then
        read -r -p "Enable ufw now? Incoming connections not allowed by a rule will be blocked for the whole machine [y/N] " answer
        if [[ $answer =~ ^[yY] ]]; then
            ENABLE_UFW=1
            return 0
        fi
    fi
    die "ufw is inactive; allow what you need above (sudo ufw allow ...), then rerun with --enable-ufw, or enable it yourself (sudo ufw enable)"
}

# Loud, because a firewall that lets everything in by default makes the
# cocoracer-server rules pointless, and `ufw status` alone does not show it. Reject
# closes the machine as well as deny does.
check_ufw_default_deny() {
    local out
    out=$(ufw status verbose)
    [[ $out =~ Default:\ (deny|reject)\ \(incoming\) ]] && return 0
    echo
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "!! WARNING: ufw's default for incoming connections is not 'deny'."
    echo "!! Every port on this machine is open to the network, not only the two"
    echo "!! cocoracer-server ones. Fix it with: sudo ufw default deny incoming"
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo
    return 1
}

if [[ $UNINSTALL -eq 1 ]]; then
    say "stopping and removing the $SERVICE service"
    systemctl disable --now "$SERVICE" 2>/dev/null || true
    rm -f "$UNIT_FILE"
    systemctl daemon-reload
    remove_proxy
    say "removing the firewall rules tagged $UFW_TAG"
    remove_ufw_rules
    rm -rf "$PREFIX" "$CONF_DIR" "$STATE_DIR" \
        "/var/lib/$PROXY_SERVICE" "/var/lib/private/$PROXY_SERVICE"
    if id "$SERVICE_USER" >/dev/null 2>&1; then
        userdel "$SERVICE_USER"
    fi
    say "uninstalled"
    exit 0
fi

if [[ $WIPE_STATE -eq 1 ]]; then
    if [[ $ASSUME_YES -eq 0 ]]; then
        echo "This erases every team, push and race saved in $STATE_DIR."
        read -r -p "Continue? [y/N] " answer
        [[ $answer =~ ^[yY] ]] || die "aborted"
    fi
    was_active=0
    if systemctl is-active --quiet "$SERVICE"; then
        was_active=1
        say "stopping the $SERVICE service"
        systemctl stop "$SERVICE"
    fi
    say "emptying $STATE_DIR"
    if [[ -d $STATE_DIR ]]; then
        find "$STATE_DIR" -mindepth 1 -delete
    fi
    if [[ $was_active -eq 1 ]]; then
        say "starting the $SERVICE service on an empty event"
        systemctl start "$SERVICE"
    fi
    say "state wiped"
    exit 0
fi

[[ -x $PYTHON ]] || die "missing $PYTHON (apt install python3.12-venv)"
# Every car's seccomp filter is built with the system's libseccomp, through
# the pyseccomp bindings pip installs below; without it no car would race.
# ldconfig may sit outside root's PATH (/sbin on some systems) and its
# output format varies, so look several ways before giving up: the loader
# cache through any known ldconfig, the loader itself, then the usual files.
have_libseccomp=0
for ldconfig in ldconfig /sbin/ldconfig /usr/sbin/ldconfig; do
    if "$ldconfig" -p 2>/dev/null | grep -q 'libseccomp\.so\.2'; then
        have_libseccomp=1
        break
    fi
done
if [[ $have_libseccomp -eq 0 ]] \
    && "$PYTHON" -c 'import ctypes; ctypes.CDLL("libseccomp.so.2")' 2>/dev/null; then
    have_libseccomp=1
fi
if [[ $have_libseccomp -eq 0 ]]; then
    # shellcheck disable=SC2312
    for lib in /lib/*/libseccomp.so.2 /usr/lib/*/libseccomp.so.2 \
        /lib64/libseccomp.so.2 /usr/lib64/libseccomp.so.2 \
        /lib/libseccomp.so.2 /usr/lib/libseccomp.so.2; do
        if [[ -e $lib ]]; then
            have_libseccomp=1
            break
        fi
    done
fi
[[ $have_libseccomp -eq 1 ]] \
    || die "missing libseccomp, which sandboxes every car (apt install libseccomp2)"
[[ $PORT =~ ^[0-9]+$ && $RACE_PORT =~ ^[0-9]+$ ]] || die "ports must be numbers"
[[ $PORT != "$RACE_PORT" ]] || die "--port and --race-port must differ"
if [[ $PROXY -eq 1 ]]; then
    [[ -x $CADDY ]] || die "--proxy needs $CADDY (apt install caddy)"
    [[ $PROXY_PORT =~ ^[0-9]+$ ]] || die "ports must be numbers"
    [[ $PROXY_PORT != "$PORT" && $PROXY_PORT != "$RACE_PORT" ]] \
        || die "--proxy-port must differ from --port and --race-port"
    for range in "${ALLOW[@]}"; do
        "$PYTHON" -c 'import ipaddress, sys
ipaddress.ip_network(sys.argv[1])' "$range" 2>/dev/null \
            || die "--allow $range is not a network (such as 203.0.113.0/24)"
    done
else
    for opt in "${ARGS[@]}"; do
        if [[ $opt == --proxy-port || $opt == --allow ]]; then
            die "$opt only applies with --proxy"
        fi
    done
fi
[[ -z $INTERVAL || $INTERVAL =~ ^[0-9]+([.][0-9]+)?$ ]] || die "--interval must be a number of seconds"
# Seconds between qualifications; empty keeps the server's default (120).
EXTRA_ARGS=${INTERVAL:+ --interval $INTERVAL}

# --- which network --------------------------------------------------------

if [[ -z $IFACE ]]; then
    IFACE=$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p')
    [[ -n $IFACE ]] || die "no default route; name the LAN interface with --iface"
fi
case $IFACE in
    lo|tailscale*|docker*|br-*|virbr*|veth*|wg*|tun*)
        die "$IFACE is not a LAN interface; name the right one with --iface" ;;
esac
CIDR=$(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' | head -n1)
[[ -n $CIDR ]] || die "$IFACE has no IPv4 address"
ADDR=${CIDR%/*}
"$PYTHON" -c 'import ipaddress, sys
sys.exit(0 if ipaddress.ip_address(sys.argv[1]).is_private else 1)' "$ADDR" \
    || die "$ADDR on $IFACE is not a private address; no team could reach the server"
# The address is only for checking the server answers and for the message at
# the end: the unit and the firewall rules name none, so they outlive it.
say "LAN: $IFACE, address $ADDR for now (the admin page shows the current one)"

check_ufw_active

if systemctl is-active --quiet "$SERVICE" && [[ $ASSUME_YES -eq 0 ]]; then
    echo "The server is running. Redeploying restarts it: the teams, pushes and"
    echo "races are kept, but everyone is signed out and a race in flight is lost."
    read -r -p "Continue? [y/N] " answer
    [[ $answer =~ ^[yY] ]] || die "aborted"
fi

# --- user and code --------------------------------------------------------

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    say "creating the system user $SERVICE_USER"
    useradd --system --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi

say "installing the code into $PREFIX"
install -d -m 0755 "$PREFIX" "$PREFIX/src"
for pkg in cocoracer server; do
    rsync -a --delete \
        --exclude .venv --exclude __pycache__ --exclude '*.egg-info' \
        --exclude tests --exclude build --exclude dist \
        --exclude '.*' \
        "$REPO/$pkg/" "$PREFIX/src/$pkg/"
done
rm -rf "$PREFIX/venv"
"$PYTHON" -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --disable-pip-version-check \
    "$PREFIX/src/cocoracer" "$PREFIX/src/server"
# Fail closed: a car whose filter cannot be installed never races, so a
# machine where the bindings or the kernel cannot do it would race no car.
say "checking every car's sandbox (seccomp, Landlock) works here"
"$PREFIX/venv/bin/python" -m cocoracer_server.sandbox \
    || die "the cars' sandbox does not work on this machine (see above); no car could race"
# The build leaves egg-info and build/ in the copied sources; the installed
# packages live in the venv, so the sources are no longer needed.
rm -rf "$PREFIX/src"
# The start kit the pages offer for download, kept: the server zips it on
# every request.
rsync -a --delete --exclude __pycache__ --exclude '.*' \
    "$REPO/start_kit/" "$PREFIX/start_kit/"
# Root owns everything, the service user can only read it: team code cannot
# rewrite the server it runs under.
chown -R root:"$SERVICE_USER" "$PREFIX"
chmod -R u=rwX,g=rX,o= "$PREFIX"

# --- admin password -------------------------------------------------------

install -d -m 0700 "$CONF_DIR"
if [[ ! -s $ENV_FILE ]]; then
    say "generating the admin password"
    umask 077
    "$PYTHON" -c 'import secrets
print("COCORACER_ADMIN_PASSWORD=" + secrets.token_urlsafe(12))' > "$ENV_FILE"
fi
chmod 0600 "$ENV_FILE"
chown root:root "$ENV_FILE"

# --- service --------------------------------------------------------------

say "writing $UNIT_FILE"
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Race server for programming competitions
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WorkingDirectory=$PREFIX
ExecStart=$PREFIX/venv/bin/cocoracer-server --host 0.0.0.0 --port $PORT --race-port $RACE_PORT --state-dir $STATE_DIR --start-kit $PREFIX/start_kit$EXTRA_ARGS
Restart=on-failure
RestartSec=2

# Network: localhost and the private ranges only, in and out, for the server
# and every race process it spawns. The ranges, not this lease's subnet, so a
# new address at the venue keeps working.
IPAddressDeny=any
IPAddressAllow=localhost $PRIVATE_RANGES
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

# Filesystem: nothing writable but a private /tmp and the event's state in
# $STATE_DIR (created and owned by the service user), no view of /home.
ProtectSystem=strict
StateDirectory=$SERVICE
StateDirectoryMode=0700
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
UMask=0077

# Privileges and kernel.
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RemoveIPC=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged
# seccomp(2) and Landlock's three calls, which @system-service leaves out:
# every car's process sandboxes itself with them (docs/adr/0007). Without
# them the car's first such call kills it.
SystemCallFilter=seccomp landlock_create_ruleset landlock_add_rule landlock_restrict_self

# A hostile controller cannot take the machine down with it. Each car is
# capped at 512 MB past its own start (RLIMIT_AS); should the whole field
# still reach MemoryMax, the kernel kills a car first (oom_score_adj 1000),
# then the race worker (500), and OOMPolicy=continue keeps the unit and the
# web server running. A full field of cars takes about 0.5 GB with
# the worker and the server (docs/adr/0007), so 4G leaves room for about six
# cars at their whole budget. No swap, so a runaway race is killed in
# seconds rather than swapping the laptop into a crawl.
MemoryMax=4G
MemorySwapMax=0
OOMPolicy=continue
TasksMax=256

[Install]
WantedBy=multi-user.target
EOF

# --- firewall -------------------------------------------------------------

say "opening ports $PORT and $RACE_PORT to $PRIVATE_RANGES"
# Removes the rules of an earlier deploy too, including the old per-subnet
# ones pinned to an interface, and an earlier proxy's.
remove_ufw_rules
for range in $PRIVATE_RANGES; do
    for p in "$PORT" "$RACE_PORT"; do
        ufw allow from "$range" to any port "$p" proto tcp \
            comment "$UFW_TAG" >/dev/null
    done
done
if [[ $PROXY -eq 1 ]]; then
    PROXY_RANGES="$PRIVATE_RANGES${ALLOW[*]:+ ${ALLOW[*]}}"
    say "opening the proxy's port $PROXY_PORT to $PROXY_RANGES"
    for range in $PROXY_RANGES; do
        ufw allow from "$range" to any port "$PROXY_PORT" proto tcp \
            comment "$UFW_TAG" >/dev/null
    done
fi
allow_ssh
if ! ufw_active; then
    # check_ufw_active let an inactive ufw through only on --enable-ufw or a yes.
    say "enabling ufw"
    ufw --force enable >/dev/null
fi
UFW_DEFAULT_OK=1
check_ufw_default_deny || UFW_DEFAULT_OK=0

# --- proxy ----------------------------------------------------------------

if [[ $PROXY -eq 1 ]]; then
    say "writing $PROXY_CONF_DIR/Caddyfile and $PROXY_UNIT_FILE"
    install -d -m 0755 "$PROXY_CONF_DIR"
    # Plain HTTP on any name or address, so the proxy names no address
    # either (docs/adr/0006).
    cat > "$PROXY_CONF_DIR/Caddyfile" <<CADDYFILE
{
	admin off
	auto_https off
}

http://:$PROXY_PORT {
	handle $LIVE_PROXY_PATH {
		rewrite * /ws
		reverse_proxy 127.0.0.1:$RACE_PORT
	}
	handle {
		reverse_proxy 127.0.0.1:$PORT
	}
}
CADDYFILE
    chmod 0644 "$PROXY_CONF_DIR/Caddyfile"
    # Caddy runs apart from team code, as a throw-away user that can bind
    # the port and keep its state, and nothing else.
    cat > "$PROXY_UNIT_FILE" <<UNIT
[Unit]
Description=HTTP proxy in front of the race server for programming competitions
After=network-online.target $SERVICE.service
Wants=network-online.target

[Service]
Type=simple
DynamicUser=yes
StateDirectory=$PROXY_SERVICE
StateDirectoryMode=0700
Environment=HOME=%S/$PROXY_SERVICE XDG_DATA_HOME=%S/$PROXY_SERVICE XDG_CONFIG_HOME=%S/$PROXY_SERVICE
ExecStart=$CADDY run --adapter caddyfile --config $PROXY_CONF_DIR/Caddyfile
Restart=on-failure
RestartSec=2
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
UNIT
else
    remove_proxy
fi

systemctl daemon-reload
systemctl enable --quiet "$SERVICE"
systemctl restart "$SERVICE"
if [[ $PROXY -eq 1 ]]; then
    systemctl enable --quiet "$PROXY_SERVICE"
    systemctl restart "$PROXY_SERVICE"
fi

say "waiting for the server"
url="http://$ADDR:$PORT"
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "$url/"; then
        break
    fi
    sleep 0.5
done
curl -fsS -o /dev/null "$url/" \
    || die "the server did not answer at $url; see: journalctl -u $SERVICE -e"
if [[ $PROXY -eq 1 ]]; then
    proxy_url="http://$ADDR"
    [[ $PROXY_PORT == 80 ]] || proxy_url+=":$PROXY_PORT"
    for _ in $(seq 1 30); do
        if curl -fsS -o /dev/null "$proxy_url/"; then
            break
        fi
        sleep 0.5
    done
    curl -fsS -o /dev/null "$proxy_url/" \
        || die "the proxy did not answer at $proxy_url; see: journalctl -u $PROXY_SERVICE -e"
fi

password=$(sed -n 's/^COCORACER_ADMIN_PASSWORD=//p' "$ENV_FILE")
echo
echo "The server is up at $url"
echo "  admin password: $password   (kept in $ENV_FILE)"
echo "  live race view: port $RACE_PORT"
if [[ $PROXY -eq 1 ]]; then
    echo "  through proxy:  $proxy_url, and any name or address forwarded to port $PROXY_PORT"
fi
[[ -z $INTERVAL ]] || echo "  qualifications: every $INTERVAL s after the previous race (not the default 120)"
echo "  logs:           journalctl -u $SERVICE -f"
echo "  saved state:    $STATE_DIR (kept across redeploys; sudo $0 --wipe-state empties it)"
echo "  stop / remove:  sudo systemctl stop $SERVICE / sudo $0 --uninstall"
[[ $UFW_DEFAULT_OK -eq 1 ]] \
    || echo "  WARNING:        ufw lets every port in by default; sudo ufw default deny incoming"
