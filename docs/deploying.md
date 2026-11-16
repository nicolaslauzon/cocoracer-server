# Deploying the server

`server/deploy.sh` installs the server on a dedicated Linux machine and runs
it as a systemd service. It runs as root, creates a system user and changes
the ufw firewall, so do not run it on a machine you use for anything else.

## Requirements

The deploy needs Linux x86_64 with Landlock and systemd, Python 3.12 at
`/usr/bin/python3.12`, and network access for pip.

```bash
apt install python3.12-venv libseccomp2 ufw rsync curl iproute2
```

## Deploy

From the repository root:

```bash
sudo server/deploy.sh                # web app on 8080, live view on 8000
sudo server/deploy.sh --enable-ufw   # also turn ufw on, for the whole machine
sudo server/deploy.sh --wipe-state   # empty the saved event and restart
sudo server/deploy.sh --uninstall    # remove everything, the saved event too
```

The script prints the address teams open and the admin password. It keeps
the password in `/etc/cocoracer-server/env`. `--port`, `--race-port`,
`--interval` and `--iface` change the ports, the seconds between
qualifications and the network interface.

Run the same command again to deploy a new version. A redeploy signs
everyone out and drops a race in progress, and it keeps the event.

## The saved event

The server saves teams, submissions, races and replays in
`/var/lib/cocoracer-server` after every change, and restores them after a
redeploy, a crash or a reboot. Only `--wipe-state` and `--uninstall` erase
them. If the server cannot read `state.json`, it refuses to start. Move the
file aside, do not delete it.

## The firewall

The deploy stops if ufw is off, because every port on the machine would be
open to the network. It lists the ports that ufw would close. Allow the
ones you still need with `sudo ufw allow`, then rerun with `--enable-ufw`.
SSH stays open.

Only private networks can reach the server: 10.0.0.0/8, 172.16.0.0/12 and
192.168.0.0/16. The service itself can only talk to those ranges, so team
code cannot reach the internet. A new DHCP address does not need a
redeploy, but the start kits teams already downloaded name the old address,
so ask the venue for a DHCP reservation.

## Team code

Each car's controller runs in its own sandboxed process, with no network,
no files beyond its imports, no new processes and 512 MB of memory. The
service runs as a user that cannot log in, on a read-only filesystem
without `/home`. The deploy checks that the sandbox works and stops if it
does not. [ADR 0007](adr/0007-one-process-per-car.md) has the details.

## Beyond the LAN

To open the server to a wider network, such as a campus, put Caddy in front
and name that network's ranges with `--allow`, once per range:

```bash
sudo apt install caddy
sudo systemctl disable --now caddy
sudo server/deploy.sh --proxy --allow 203.0.113.0/24
```

Caddy serves plain HTTP on port 80, which `--proxy-port` changes, and
forwards to the server. If the machine sits behind a router, forward that
port to it. Redeploying without `--proxy` removes the proxy.
[ADR 0006](adr/0006-a-proxy-opens-the-server-beyond-the-lan.md) has the details.

## Limits

- The server speaks plain HTTP, so team passwords cross the network in
  clear text.
- A restart signs everyone out and drops a race in progress.
