import audioop
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from desktop.common import ROOT


def main():
    os.environ['HF_HUB_OFFLINE'] = '1'
    from PIL import Image,ImageDraw,ImageFont
    from desktop.document_search import scan_document,DocumentSearch
    from desktop.files import FileTools
    from desktop.preferences import Preferences
    from desktop.offline_speech import LABEL,device,transcribe
    from phone.voice import synthesize_wav
    report = {}
    with tempfile.TemporaryDirectory(dir=ROOT/'.cache',prefix='local-verification-') as temporary:
        folder = Path(temporary)
        image = Image.new('RGB',(1200,240),'white')
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf',50)
        ImageDraw.Draw(image).text((40,70),'Invoice total 125 dollars',fill='black',font=font)
        image_path = folder/'scanned.png'
        print('Checking local OCR...',flush=True)
        image.save(image_path)
        extracted = scan_document(image_path)
        assert '125' in extracted['text'],extracted
        report['ocr'] = extracted['text']
        image.save(folder/'scan.pdf','PDF')
        extracted_pdf = scan_document(folder/'scan.pdf')
        assert '125' in extracted_pdf['text'],extracted_pdf
        report['scanned_pdf'] = True
        (folder/'travel.txt').write_text('The holiday itinerary includes a seaside hotel and an airport transfer.',encoding='utf-8')
        (folder/'garden.txt').write_text('Water the tomato plants and prune the apple trees.',encoding='utf-8')
        prefs = Preferences(folder/'settings.json')
        prefs.save({'fileRoots':[str(folder)],'fileWriteRoots':[]})
        search = DocumentSearch(FileTools(prefs),folder/'index.sqlite3')
        print('Checking local semantic search...',flush=True)
        found = search.search('vacation accommodation')
        assert found['mode'] == 'semantic and content',found
        assert found['files'] and found['files'][0]['name'] == 'travel.txt',found
        report['semantic_search'] = {'match':found['files'][0]['name'],'mode':found['mode']}
        wav = synthesize_wav('Jarvis, remind me in ten minutes to take a break.')
        with wave.open(io.BytesIO(wav),'rb') as source:
            pcm = source.readframes(source.getnframes())
            rate = source.getframerate()
        if rate != 16000:
            pcm,_ = audioop.ratecv(pcm,2,1,rate,16000,None)
        started = time.monotonic()
        print(f'Checking local {LABEL}...',flush=True)
        heard = transcribe(pcm)
        assert 'remind' in heard.lower() and 'minute' in heard.lower(),heard
        report['offline_speech'] = {'text':heard,'seconds':round(time.monotonic()-started,2),'device':device()}
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
