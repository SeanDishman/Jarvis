import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from desktop.common import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install',action='store_true')
    args = parser.parse_args()
    if args.install:
        pip_root = ROOT / '.runtime/pip'
        if not (pip_root/'pip').exists():
            subprocess.run([sys.executable,'-m','pip','install','--target',str(pip_root),'--no-deps','pip==26.2.1'],check=True)
        command = 'import sys; sys.path.insert(0,sys.argv.pop(1)); from pip._internal.cli.main import main; raise SystemExit(main())'
        requirements = ['-r',str(ROOT/'desktop/features-requirements.txt')]
        if shutil.which('nvidia-smi'):
            print('NVIDIA GPU found. Adding the CUDA libraries for fast local speech...',flush=True)
            requirements += ['-r',str(ROOT/'desktop/gpu-requirements.txt')]
        subprocess.run([sys.executable,'-c',command,str(pip_root),'install','--only-binary=:all:','--target',
                        str(ROOT/'.runtime/features-python'),*requirements],check=True)
    os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING','1')
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')
    os.environ.setdefault('HF_HOME',str(ROOT/'.cache/huggingface'))
    from huggingface_hub import HfApi,snapshot_download
    from desktop.offline_speech import LABEL,MODEL_DIR
    repo = 'Systran/faster-whisper-small.en'
    print(f'Downloading {LABEL} for local speech...',flush=True)
    commit = HfApi().model_info(repo).sha
    snapshot_download(repo_id=repo,revision=commit,local_dir=str(MODEL_DIR),
                      allow_patterns=['model.bin','config.json','tokenizer.json','vocabulary.*','preprocessor_config.json'],max_workers=3)
    print('Downloading the local document semantic model...',flush=True)
    from desktop.document_search import embedding_model,MODEL,MODEL_CACHE
    model = embedding_model(download=True)
    vector = next(iter(model.embed(['Jarvis local document search is ready.'])))
    from desktop.memory import Memory
    from desktop.preferences import Preferences
    memory = Memory(Preferences())
    memory.setup()
    (ROOT/'Jarvis Files').mkdir(exist_ok=True)
    manifest = {'whisper':{'repository':repo,'revision':commit,'directory':str(MODEL_DIR)},
                'embeddings':{'model':MODEL,'directory':str(MODEL_CACHE),'dimensions':len(vector)},'files':{}}
    for folder in (MODEL_DIR,MODEL_CACHE):
        for path in sorted(folder.rglob('*')):
            if path.is_file() and '.cache' not in path.relative_to(folder).parts and path.suffix not in ('.lock','.metadata'):
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(4*1024*1024),b''):
                        digest.update(chunk)
                manifest['files'][str(path.relative_to(ROOT))] = {'bytes':path.stat().st_size,'sha256':digest.hexdigest()}
    target = ROOT/'.runtime/models/manifest.json'
    target.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'speech':LABEL,'embedding_dimensions':len(vector),'manifest':str(target)}),flush=True)


if __name__ == '__main__':
    main()
