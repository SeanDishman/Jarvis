import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/obsidian-setup'


def release(repo):
    request = urllib.request.Request('https://api.github.com/repos/'+repo+'/releases/latest',headers={'User-Agent':'Jarvis-local-setup'})
    with urllib.request.urlopen(request,timeout=30) as response:
        return json.load(response)


def download(asset):
    path = CACHE / asset['name']
    expected = asset.get('digest','')
    if not expected.startswith('sha256:'):
        raise RuntimeError('The publisher release has no SHA256 digest')
    def valid():
        if not path.exists():
            return False
        with path.open('rb') as stream:
            return hashlib.file_digest(stream,'sha256').hexdigest() == expected[7:]
    if not valid():
        with urllib.request.urlopen(asset['browser_download_url'],timeout=60) as response,path.open('wb') as output:
            while chunk := response.read(1024*1024):
                output.write(chunk)
    if not valid():
        raise RuntimeError('Downloaded asset did not match its publisher checksum')
    return path


def extract(executable,archive,directory):
    subprocess.run([str(executable),'x',str(archive),'-o'+str(directory),'-y','-bd'],check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL)


def main():
    CACHE.mkdir(parents=True,exist_ok=True)
    archive_release = release('ip7z/7zip')
    bootstrap = download(next(a for a in archive_release['assets'] if a['name'] == '7zr.exe'))
    archive_installer = download(next(a for a in archive_release['assets'] if a['name'].endswith('-x64.exe')))
    archive_tools = ROOT/'.runtime/archive-tools'
    extract(bootstrap,archive_installer,archive_tools)
    obsidian_release = release('obsidianmd/obsidian-releases')
    asset = next(a for a in obsidian_release['assets'] if a['name'].endswith('.exe') and 'arm64' not in a['name'])
    print('Downloading official '+asset['name']+'...',flush=True)
    installer = download(asset)
    staging = CACHE/'extracted'
    extract(archive_tools/'7z.exe',installer,staging)
    payloads = list(staging.rglob('app-64.7z'))
    if len(payloads) != 1:
        raise RuntimeError('Could not identify the Windows app payload')
    destination = ROOT/'.runtime/obsidian'
    extract(archive_tools/'7z.exe',payloads[0],destination)
    if not (destination/'Obsidian.exe').exists():
        raise RuntimeError('Obsidian executable is missing')
    metadata = {'version':obsidian_release['tag_name'],'source':asset['browser_download_url'],'sha256':asset['digest']}
    (destination/'jarvis-source.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'version':obsidian_release['tag_name'],'path':str(destination/'Obsidian.exe')}),flush=True)


if __name__ == '__main__':
    main()
