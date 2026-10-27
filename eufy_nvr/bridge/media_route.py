"""Read-only, secret-free selected-route classification for pinned aiortc/aioice."""

import ipaddress


def _rfc1918(host):
    try:
        address = ipaddress.ip_address(host)
    except (ValueError, TypeError):
        return False
    return address.version == 4 and any(address in network for network in (
        ipaddress.ip_network('10.0.0.0/8'), ipaddress.ip_network('172.16.0.0/12'),
        ipaddress.ip_network('192.168.0.0/16')))


def selected_route(peer):
    """Inspect NOMINATED pairs, never merely advertised/successful candidates.

    Private implementation details are guarded and the dependencies pinned.
    Unknown remains unknown. Return no addresses, ports, account IDs or secrets.
    This classifies only the bridge-to-NVR WebRTC hop, not internet signalling.
    """
    try:
        nominated = peer.sctp.transport.transport._connection._nominated
        if not nominated:
            return 'unknown'
        routes = []
        for pair in nominated.values():
            local, remote = pair.local_candidate, pair.remote_candidate
            if local.type == 'relay' or remote.type == 'relay':
                routes.append('relay')
            elif local.type == remote.type == 'host' and _rfc1918(local.host) and _rfc1918(remote.host):
                routes.append('direct_private')
            else:
                routes.append('other')
        return routes[0] if len(set(routes)) == 1 else 'mixed'
    except (AttributeError, TypeError, ValueError):
        return 'unknown'
