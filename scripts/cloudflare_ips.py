"""Print nginx trusted-proxy directives from Cloudflare's public IP-list API."""
import ipaddress
import json
import urllib.request

URL = 'https://api.cloudflare.com/client/v4/ips'


def render(payload):
    if payload.get('success') is not True:
        raise ValueError('Cloudflare IP-list request failed')
    networks = []
    for version, key in ((4, 'ipv4_cidrs'), (6, 'ipv6_cidrs')):
        values = payload['result'][key]
        if not isinstance(values, list) or not values:
            raise ValueError('Empty Cloudflare address family')
        for value in values:
            network = ipaddress.ip_network(value, strict=True)
            if network.version != version or network.prefixlen == 0 or not network.is_global:
                raise ValueError('Invalid trusted proxy range')
            networks.append(network)
    return '# Source: ' + URL + '\n' + ''.join(f'set_real_ip_from {n};\n' for n in networks)


def main():
    with urllib.request.urlopen(URL, timeout=15) as response:
        payload = json.loads(response.read(100_000))
    print(render(payload), end='')


if __name__ == '__main__':
    main()
