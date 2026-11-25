"""Whether a request comes from a paired phone on the home Wi-Fi (mqucifer/print-shop#214).

Where a request came from is the socket's peer address, never a header's: a header is what the
sender says, and a proxy says what it likes. So a request can't claim to be local. It comes
from a phone's own address on the home network, or it's refused, in words.

The page listens only on the Mac's address on the home Wi-Fi (`PHONE_HOME`, with its network,
such as `A.B.C.D/24`), so no other interface reaches it: not loopback, not a VPN's. And a
request is refused, for Start and pairing, when it comes:

- **through the Mac itself** (loopback, or the Mac's own address): a tunnel, `ssh -R`,
  `tailscale serve`, ngrok, a script or a session's `curl`;
- **from outside the home network,** or from its network or broadcast address;
- **through the router:** a port forward shows as it. The router's address is `PHONE_ROUTER`, or
  the default route's; if neither can be read, Start is refused, as where a request came from
  can't be told;
- **through Home Assistant's host** (`PHONE_HA_HOST`, or `MQTT_HOST`), resolved once as the page
  starts: HA's remote access, an ingress proxy, or an automation's `rest_command`;
- **carrying any proxy's mark** (`Forwarded`, `X-Forwarded-*`, `Via`, `X-Real-IP`, …).

**Over HTTPS** (mqucifer/print-shop#312), once `PHONE_NAME`, `PHONE_TLS_CERT` and `PHONE_TLS_KEY`
are all set (`tls.py`): the page is served over HTTPS at its name and `PHONE_PORT`, and its
own `Host` and `Origin` are the name's, not the address's. Where it listens, and every check
above, are the same. With one or two of them set, the page stays off and says which is
missing: a half-done setup never falls back to plain HTTP.

**Not covered** (the Sponsor, 2026-11-24, on mqucifer/print-shop#214): a VPN that hands its
clients addresses on the home range (some routers' WireGuard or IPsec servers, bridged OpenVPN),
and a machine on the home network relaying for one (a VPN's subnet router), look like a phone at
home. There's no such VPN in the house. **If one is ever added, this check has to change first**:
binding a phone to its Wi-Fi hardware address was weighed and left out, as the Sponsor's phone
rotates its private Wi-Fi address and would be refused.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlunsplit

from print_shop.env import SETTINGS_PATH, load_env
from print_shop.phone.tls import CERT_KEY, KEY_KEY, NAME_KEY, TlsFiles

__all__ = [
    "DEFAULT_PORT", "HA_KEY", "HOME_KEY", "HTTPS_KEYS", "Home", "HomeRefused", "PORT_KEY",
    "PROXY_HEADERS", "ROUTER_KEY", "default_router",
]

logger = logging.getLogger(__name__)

#: The Mac's address on the home Wi-Fi, with its network: `A.B.C.D/24`.
HOME_KEY = "PHONE_HOME"
#: The page's port on that address.
PORT_KEY = "PHONE_PORT"
#: The router's address, where the default route can't say it.
ROUTER_KEY = "PHONE_ROUTER"
#: Home Assistant's host, where it isn't the broker's (`MQTT_HOST`).
HA_KEY = "PHONE_HA_HOST"
DEFAULT_PORT = 8765
#: The settings that serve the page over HTTPS: all three, or none.
HTTPS_KEYS = (NAME_KEY, CERT_KEY, KEY_KEY)
#: A DNS name's label: letters, digits and hyphens, not at either end.
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\Z")

#: Headers a proxy adds. Any of them, or any `X-Forwarded-…` or `X-Original-…`, refuses Start.
PROXY_HEADERS = frozenset({
    "forwarded", "forwarded-for", "via", "x-real-ip", "client-ip", "x-client-ip",
    "true-client-ip", "cf-connecting-ip", "fastly-client-ip", "x-cluster-client-ip",
    "x-proxyuser-ip", "x-forwarded", "x-host",
})
_PROXY_PREFIXES = ("x-forwarded-", "x-original-")

#: The refusal's words, which the page says under "You're away from home".
THROUGH_THE_MAC = "this came through the Mac itself, not from a phone on the home Wi-Fi"
OUTSIDE = "this came from outside the home network"
THROUGH_THE_ROUTER = ("this came through the router (a port forward, or a way in from outside), "
                      "not from a phone on the home Wi-Fi")
THROUGH_HA = "this came through Home Assistant's host, not from a phone on the home Wi-Fi"
NO_ROUTER = ("the router's address couldn't be read, so where this came from can't be told. "
             f"Set {ROUTER_KEY} in the settings")
class HomeRefused(ValueError):
    """The settings don't place the page on a home network: said, and no page is served."""


@dataclass(frozen=True)
class Home:
    """The Mac on the home Wi-Fi, and what a phone there can't be."""

    #: The Mac's address on the home Wi-Fi, which the page listens on.
    address: ipaddress.IPv4Address
    network: ipaddress.IPv4Network
    port: int = DEFAULT_PORT
    #: The router's address, or None if it couldn't be read: then Start is refused.
    router: ipaddress.IPv4Address | None = None
    #: Home Assistant's address, resolved once as the page starts, or None where none is named.
    ha: ipaddress.IPv4Address | None = None
    #: The page's name (`PHONE_NAME`) and its certificate's files, over HTTPS; both None over
    #: plain HTTP (mqucifer/print-shop#312).
    name: str | None = None
    tls: TlsFiles | None = None

    def __post_init__(self) -> None:
        where = ipaddress.IPv4Interface(f"{self.address}/{self.network.prefixlen}")
        if where.network != self.network:
            raise HomeRefused(f"{self.address} isn't in {self.network}")
        _private(self.address, HOME_KEY)
        if self.address in (self.network.network_address, self.network.broadcast_address):
            raise HomeRefused(f"{HOME_KEY} is its network's own address, not the Mac's")
        if not 0 < self.port < 65536:
            raise HomeRefused(f"{PORT_KEY} {self.port} isn't a port")
        if self.port == 80:
            raise HomeRefused(f"{PORT_KEY} can't be 80: a browser leaves it out of the page's "
                              "address, which the page then can't tell is its own")
        if (self.name is None) != (self.tls is None):
            raise HomeRefused(f"HTTPS needs {', '.join(HTTPS_KEYS[:-1])} and {HTTPS_KEYS[-1]} "
                              "together")
        if self.name is not None:
            _a_name(self.name)
            if self.port == 443:
                raise HomeRefused(f"{PORT_KEY} can't be 443 over HTTPS: a browser leaves it out "
                                  "of the page's address, which the page then can't tell is its "
                                  "own")

    @classmethod
    def from_settings(
        cls, path: Path | None = None, *, router: Callable[[], str | None] | None = None,
        resolve: Callable[[str], str] = socket.gethostbyname,
    ) -> Home | None:
        """The home, from `PHONE_HOME` (and `PHONE_PORT`, `PHONE_ROUTER`, `PHONE_HA_HOST` or
        `MQTT_HOST`, and `PHONE_NAME`, `PHONE_TLS_CERT` and `PHONE_TLS_KEY` for HTTPS) in the
        settings; None if `PHONE_HOME` isn't set, which leaves the phone's Start off. Home
        Assistant's host is resolved here, once.

        Raises:
            HomeRefused: If `PHONE_HOME` is malformed, public, loopback or link-local, the port is
                80, Home Assistant's host is named and can't be found, or only some of the HTTPS
                settings are set, or `PHONE_NAME` isn't a name.
        """
        env = load_env(path or SETTINGS_PATH)
        given = env.get(HOME_KEY, "").strip()
        if not given:
            return None
        try:
            where = ipaddress.IPv4Interface(given)
        except ValueError as exc:
            raise HomeRefused(
                f"{HOME_KEY}={given!r} isn't an IPv4 address with its network, such as "
                "A.B.C.D/24") from exc
        if where.network.prefixlen == 32:
            raise HomeRefused(f"{HOME_KEY} needs its network, such as A.B.C.D/24")
        try:
            port = int(env.get(PORT_KEY, "") or DEFAULT_PORT)
        except ValueError as exc:
            raise HomeRefused(f"{PORT_KEY} isn't a number") from exc
        named = env.get(ROUTER_KEY, "").strip() or (router or default_router)()
        found: ipaddress.IPv4Address | None = None
        if named:
            try:
                found = ipaddress.IPv4Address(named)
            except ValueError:
                logger.warning("the router's address %r isn't an IPv4 address", named)
            if found is not None and found not in where.network:
                logger.warning("the router %s isn't on the home network %s", found, where.network)
                found = None
        ha_name = env.get(HA_KEY, "").strip() or env.get("MQTT_HOST", "").strip()
        ha: ipaddress.IPv4Address | None = None
        if ha_name:
            try:
                ha = ipaddress.IPv4Address(resolve(ha_name))
            except (OSError, ValueError) as exc:
                raise HomeRefused(
                    f"Home Assistant's host ({HA_KEY}, or MQTT_HOST) couldn't be found, so a "
                    "request through it couldn't be told") from exc
        name, tls = _https(env)
        return cls(address=where.ip, network=where.network, port=port, router=found, ha=ha,
                   name=name, tls=tls)

    @property
    def origin(self) -> str:
        """The page's own origin: what `Origin` must say, and every link to the page begins with
        (the run's line, the card's, and Home Assistant's notices)."""
        # Built, not written as a URL: it's the page's own address, which nothing calls, and
        # `tests/test_config_boundary.py` keeps written URLs to the model's proxy alone.
        return urlunsplit(("https" if self.tls is not None else "http", self.host, "", "", ""))

    @property
    def host(self) -> str:
        """What `Host` must say: the page's name over HTTPS, else its address; and its port."""
        return f"{self.name or self.address}:{self.port}"

    def why_not(self, peer: str, headers: Iterable[str]) -> str:
        """Why a request from `peer` (the socket's address) with `headers` (its headers' names)
        isn't from a phone on the home Wi-Fi, in words; "" if it is."""
        proxied = sorted(name for name in (h.lower() for h in headers)
                         if name in PROXY_HEADERS or name.startswith(_PROXY_PREFIXES))
        if proxied:
            return (f"this came through a proxy (it carries {_header(proxied[0])}), not straight "
                    "from a phone on the home Wi-Fi")
        try:
            ip = ipaddress.ip_address(peer)
        except ValueError:
            return OUTSIDE
        if isinstance(ip, ipaddress.IPv6Address):
            if ip.ipv4_mapped is None:
                return THROUGH_THE_MAC if ip.is_loopback else OUTSIDE
            ip = ip.ipv4_mapped
        if ip.is_loopback or ip == self.address:
            return THROUGH_THE_MAC
        if ip not in self.network or ip in (self.network.network_address,
                                            self.network.broadcast_address):
            return OUTSIDE
        if self.router is None:
            return NO_ROUTER
        if ip == self.router:
            return THROUGH_THE_ROUTER
        if self.ha is not None and ip == self.ha:
            return THROUGH_HA
        return ""


def _private(address: ipaddress.IPv4Address, key: str) -> None:
    if address.is_loopback:
        raise HomeRefused(f"{key} is loopback: the page listens only on the home Wi-Fi")
    if address.is_link_local or address.is_unspecified or address.is_multicast:
        raise HomeRefused(f"{key} {address} isn't an address on a home network")
    if not address.is_private:
        raise HomeRefused(f"{key} {address} is a public address: the page is for the home "
                          "network only, and listens on nothing else")


def _https(env: dict[str, str]) -> tuple[str | None, TlsFiles | None]:
    """`PHONE_NAME` and the certificate's files, all three or none (mqucifer/print-shop#312)."""
    given = {key: env.get(key, "").strip() for key in HTTPS_KEYS}
    if not any(given.values()):
        return None, None
    missing = [key for key in HTTPS_KEYS if not given[key]]
    if missing:
        have = [key for key in HTTPS_KEYS if given[key]]
        raise HomeRefused(
            f"{_and(missing)} {'is' if len(missing) == 1 else 'are'}n't set ({_and(have)} "
            f"{'is' if len(have) == 1 else 'are'}): HTTPS needs all three, and the page isn't "
            "served as plain HTTP for a half-done setup")
    name = given[NAME_KEY].lower()
    _a_name(name)
    return name, TlsFiles(Path(given[CERT_KEY]).expanduser(), Path(given[KEY_KEY]).expanduser())


def _a_name(name: str) -> None:
    """`PHONE_NAME` is a DNS name, in lower case, with no port: not an address, nor a wildcard."""
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        raise HomeRefused(f"{NAME_KEY} is an address: it's the page's name, such as print.<domain>")
    labels = name.split(".")
    if (name != name.lower() or len(name) > 253 or len(labels) < 2
            or not all(_LABEL.match(label) for label in labels) or labels[-1].isdigit()):
        raise HomeRefused(f"{NAME_KEY}={name!r} isn't a name such as print.<domain>, in lower "
                          "case, with no port")


def _and(keys: list[str]) -> str:
    return keys[0] if len(keys) == 1 else f"{', '.join(keys[:-1])} and {keys[-1]}"


def _header(name: str) -> str:
    return "-".join(part.capitalize() if part != "ip" else "IP" for part in name.split("-"))


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=5,
                              check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def default_router() -> str | None:
    """The default route's gateway, read from the system, or None if it can't be read."""
    out = _run(["route", "-n", "get", "default"] if sys.platform == "darwin"
               else ["ip", "-4", "route", "show", "default"])
    for line in out.splitlines():
        words = line.split()
        if sys.platform == "darwin" and words[:1] == ["gateway:"] and len(words) > 1:
            return words[1]
        if "via" in words and words.index("via") + 1 < len(words):
            return words[words.index("via") + 1]
    return None
