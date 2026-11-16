# A proxy opens the server beyond the LAN, and the server stays fenced in

The deploy kept the server on private networks with two fences: ufw let the
web and live-view ports in from the private ranges only, and the unit's
`IPAddressAllow` let the server talk to localhost and the private ranges
only. The second fence is what contains team code, which runs with no
sandbox (ADR 0002). It applies in both directions, so it cannot let a
university's addresses in without letting team code out to them.

We want the server reachable from the university network, so `--proxy`
puts Caddy in front of it, in a unit of its own. Caddy takes the
connections from the wider network and forwards them to the server on
localhost. The server's unit, and with it every race process, keeps the same
allow-list. ufw opens only the proxy's port to the extra ranges (`--allow`).

The live view used to be its own port, which the browser reached directly.
Through the proxy the browser reaches it as `/live/ws` on the pages' own
origin instead, so one forwarded port carries everything. The server
recognises a proxied request by its X-Forwarded-Proto, which uvicorn trusts
from loopback only, and links that path and names that origin in the kit.

The proxy is plain HTTP, on port 80, so team passwords cross the university
network in clear text, as they cross the LAN. They are event passwords,
made up for the day, and we accept that.

Considered and rejected:

- **Widening `IPAddressAllow` and ufw to the university's range.** No new
  moving part, but team code could then reach every machine on that network.
- **HTTPS with a self-signed certificate** (Caddy's internal authority). It
  worked, but every browser warns, and teams would learn to click through.
- **HTTPS with a Let's Encrypt certificate.** The university blocks inbound
  connections from the internet, so Let's Encrypt cannot validate over HTTP
  or TLS, and DNS validation needs a domain of our own outside `ulaval.ca`
  and a certificate to obtain and carry into the deploy: more moving parts
  than the event is worth.
- **nginx.** It is in Ubuntu's main archive, but Caddy's configuration for
  the same proxy is shorter.
