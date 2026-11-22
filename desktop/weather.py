import http.client
import ipaddress
import json
import socket
import ssl
import struct
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from desktop.common import ROOT


def physical_adapters():
    proc = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File',
                           str(ROOT / 'desktop/physical-network.ps1')], capture_output=True,
                          encoding='utf-8-sig', timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode:
        raise RuntimeError('Windows could not inspect the physical network connection.')
    return json.loads(proc.stdout)


def read_json(response, limit=150000):
    raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Network response is too large')
    return json.loads(raw)


class PhysicalHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, adapter):
        super().__init__(host, timeout=4, context=ssl.create_default_context())
        self.adapter = adapter

    def connect(self):
        last = None
        addresses = socket.getaddrinfo(self.host, 443, socket.AF_INET, socket.SOCK_STREAM)
        for family, socktype, proto, _canon, address in addresses[:2]:
            raw = socket.socket(family, socktype, proto)
            try:
                raw.settimeout(self.timeout)
                raw.setsockopt(socket.IPPROTO_IP, 31, struct.pack('!I', int(self.adapter['index'])))
                raw.bind((self.adapter['address'], 0))
                raw.connect(address)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as exc:
                raw.close()
                last = exc
        raise OSError('The physical network connection could not reach the location service') from last


def direct_location(adapter):
    with PhysicalHTTPS('ipwho.is', adapter) as connection:
        connection.request('GET', '/?fields=success,message,ip,city,region,country,latitude,longitude',
                           headers={'Accept': 'application/json', 'User-Agent': 'Jarvis-Desktop/4'})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError('The IP location service is unavailable')
        data = read_json(response)
    if not data.get('success') or not ipaddress.ip_address(data.get('ip', '')).is_global:
        raise RuntimeError('A public physical-connection address was not returned')
    if not -90 <= float(data['latitude']) <= 90 or not -180 <= float(data['longitude']) <= 180:
        raise ValueError('Invalid IP location')
    return data


def default_public_ip():
    with urllib.request.urlopen('https://api.ipify.org?format=json', timeout=4) as response:
        return read_json(response).get('ip')


CONDITIONS = {0: 'clear', 1: 'mostly clear', 2: 'partly cloudy', 3: 'overcast', 45: 'fog', 48: 'freezing fog',
              51: 'light drizzle', 53: 'drizzle', 55: 'heavy drizzle', 61: 'light rain', 63: 'rain', 65: 'heavy rain',
              71: 'light snow', 73: 'snow', 75: 'heavy snow', 80: 'rain showers', 81: 'rain showers',
              82: 'heavy showers', 95: 'thunderstorms', 96: 'thunderstorms with hail', 99: 'thunderstorms with hail'}


class Weather:
    def __init__(self, preferences):
        self.preferences = preferences
        self.cached = None
        self.cached_at = 0
        self.lock = threading.Lock()

    def current(self):
        with self.lock:
            unit = self.preferences.read()['weatherUnits']
            if self.cached and self.cached.get('unit') == unit and time.monotonic() - self.cached_at < 600:
                return self.cached.copy()
            try:
                network = physical_adapters()
                if not network.get('adapters'):
                    raise RuntimeError('No connected physical network adapter was found.')
                location = direct_location(network['adapters'][0])
                if network.get('tunnelActive') and default_public_ip() == location['ip']:
                    raise RuntimeError('The physical connection returned the VPN address, so its location was rejected.')
                params = {'latitude': location['latitude'], 'longitude': location['longitude'],
                          'current': 'temperature_2m,apparent_temperature,weather_code',
                          'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max',
                          'forecast_days': 1, 'temperature_unit': unit, 'timezone': 'auto'}
                with urllib.request.urlopen('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(params), timeout=7) as response:
                    forecast = read_json(response)
                current, daily = forecast['current'], forecast['daily']
                result = {'ok': True, 'location': ', '.join(filter(None, (location.get('city'), location.get('region')))),
                          'location_method': 'Approximate public IP location through the physical network adapter',
                          'temperature': current['temperature_2m'], 'feels_like': current['apparent_temperature'],
                          'condition': CONDITIONS.get(current['weather_code'], 'weather code ' + str(current['weather_code'])),
                          'high': daily['temperature_2m_max'][0], 'low': daily['temperature_2m_min'][0],
                          'rain_chance': daily['precipitation_probability_max'][0], 'unit': unit,
                          'observed_at': current['time'], 'source': 'Open-Meteo', 'url': 'https://open-meteo.com/'}
            except Exception:
                result = {'ok': False, 'unit': unit,
                          'error': 'Weather location is unavailable. Jarvis could not establish a usable location through the physical network connection. The VPN location was not used.'}
            self.cached, self.cached_at = result, time.monotonic()
            return result.copy()
