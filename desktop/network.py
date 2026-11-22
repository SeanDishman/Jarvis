from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import ipaddress
import json
import re
import socket
import subprocess
import urllib.request

from desktop.powershell import host_arguments


def adapter_snapshot():
    process = subprocess.run(host_arguments('network-info.ps1'), capture_output=True, timeout=10,
        encoding='utf-8-sig', creationflags=subprocess.CREATE_NO_WINDOW)
    if process.returncode:
        raise RuntimeError('Windows network adapter information is unavailable.')
    data = json.loads(process.stdout)
    if not isinstance(data.get('adapters'), list) or type(data.get('vpnDetected')) is not bool:
        raise ValueError('Invalid Windows network information')
    return data


def basic_adapter_snapshot():
    import psutil
    rows = []
    statistics = psutil.net_if_stats()
    for name, addresses in psutil.net_if_addrs().items():
        if name not in statistics or not statistics[name].isup:
            continue
        values = []
        for address in addresses:
            if address.family not in (socket.AF_INET, socket.AF_INET6):
                continue
            value = ipaddress.ip_address(address.address.split('%')[0])
            if value.is_loopback or value.is_unspecified:
                continue
            values.append({'address':address.address, 'family':'IPv4' if value.version == 4 else 'IPv6',
                           'prefixLength':None})
        if values:
            rows.append({'name':name, 'index':None, 'physical':None,
                         'vpnHint':bool(re.search(r'VPN|WireGuard|Wintun|\bTAP\b|\bTUN\b|Proton|Nord|Surfshark|AnyConnect|Mullvad|Express', name, re.I)),
                         'addresses':values})
    return {'adapters':rows, 'vpnDetected':any(row['vpnHint'] for row in rows)}


def public_address(family):
    endpoint = 'https://api.ipify.org?format=json' if family == 4 else 'https://api6.ipify.org?format=json'
    request = urllib.request.Request(endpoint, headers={'Accept':'application/json', 'User-Agent':'Jarvis-Desktop'})
    with urllib.request.urlopen(request, timeout=4) as response:
        data = json.loads(response.read(2048))
    address = ipaddress.ip_address(data['ip'])
    if address.version != family or not address.is_global:
        raise ValueError('The address service did not return a public IP address.')
    return str(address)


def inspect_network(kind='public'):
    if kind not in ('public', 'local', 'vpn', 'isp', 'all'):
        raise ValueError('Choose public, local, vpn, isp or all addresses.')
    result = {'ok': True, 'requested': kind, 'checkedAt': datetime.now().astimezone().isoformat(),
              'adapters': [], 'publicIPv4': None, 'publicIPv6': None, 'vpnDetected': None,
              'ispPublicIP': None, 'vpnExitConfirmed': False, 'warnings': []}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='jarvis-network') as pool:
        adapters = pool.submit(adapter_snapshot)
        checks = {family: pool.submit(public_address, family) for family in (4, 6)} if kind != 'local' else {}
        try:
            data = adapters.result()
            result.update(adapters=data['adapters'], vpnDetected=data['vpnDetected'])
        except Exception:
            try:
                result.update(basic_adapter_snapshot())
                result['warnings'].append('Using basic interface information; adapter hardware details are unavailable.')
            except Exception:
                result['warnings'].append('Windows adapter information is unavailable; VPN status is unknown.')
        for family, future in checks.items():
            try:
                result['publicIPv' + str(family)] = future.result()
            except Exception:
                result['warnings'].append(f'Public IPv{family} could not be measured on the current connection.')
    result['publicMeaning'] = ('The public addresses are those observed by ipify for Jarvis HTTPS traffic. '
        'A VPN or proxy can change them. Split tunneling means other apps may use different addresses.')
    result['vpnMeaning'] = ('VPN detection uses active adapter names and is only a hint. A detected adapter '
        'does not prove this traffic uses it; no detected adapter does not prove a VPN is absent.')
    result['ispMeaning'] = ('The underlying ISP public address is not independently verified. Windows local '
        'adapter addresses are not proof of the ISP public address. Do not call a private, tunnel, or public '
        'VPN/proxy address the raw ISP address. No VPN was disconnected or bypassed.')
    result['ok'] = bool(result['adapters'] if kind == 'local' else result['publicIPv4'] or result['publicIPv6'])
    if not result['ok']:
        result['error'] = 'That network address could not be read right now.'
    return result
