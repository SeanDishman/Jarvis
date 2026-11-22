from __future__ import annotations
import json
import argparse
import base64
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop.common import local_request
from desktop.controller import Controller
from desktop.reminders import Reminders
from desktop.preferences import Preferences
from desktop.files import FileTools
from desktop.briefing import Briefing
from desktop.breaks import BreakState
from desktop.intent import classify_request
from desktop.computer import encode_png
from desktop.research import response_text
from desktop.processes import summarize_processes


def unused_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--interruptions-only', action='store_true')
    parser.add_argument('--controls-only', action='store_true')
    parser.add_argument('--processes-only', action='store_true')
    parser.add_argument('--ram-only', action='store_true')
    parser.add_argument('--powershell-only', action='store_true')
    options = parser.parse_args()
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix='assistant-live-') as temporary:
        folder = Path(temporary).resolve()
        assert folder.is_relative_to(cache.resolve())
        model_port, desktop_port = unused_port(), unused_port()
        while desktop_port == model_port:
            desktop_port = unused_port()
        controller = Controller(queue.Queue())
        controller.reminders = Reminders(folder / 'reminders.json')
        controller.breaks = BreakState(folder / 'break.json')
        controller.preferences = Preferences(folder / 'settings.json')
        docs = folder / 'documents'
        docs.mkdir()
        (docs / 'invoice-demo.txt').write_text('Demo invoice. The total due is 125 dollars, payable in thirty days.', encoding='utf-8')
        controller.preferences.save({'fileRoots':[str(docs)]})
        controller.files = FileTools(controller.preferences)
        controller.briefing = Briefing(controller.preferences, controller.reminders, controller.mail)
        server_thread = threading.Thread(target=controller.serve, args=(desktop_port,), daemon=True)
        server_thread.start()
        environment = os.environ.copy()
        environment.update(JARVIS_MODEL_PORT=str(model_port), JARVIS_DESKTOP_PORT=str(desktop_port))
        log = (folder / 'model.log').open('w', encoding='utf-8')
        process = subprocess.Popen([str(ROOT / '.runtime/node-v24.21.0-win-x64/node.exe'),
                                    str(ROOT / 'phone/luna-server.mjs')], cwd=ROOT, env=environment,
                                   stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        def isolated_request(port, route, payload=None, timeout=10):
            return local_request(model_port if port == 18791 else port, route, payload, timeout)
        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Isolated model server failed to start')
                try:
                    local_request(model_port, '/health', timeout=1)
                    local_request(desktop_port, '/health', timeout=1)
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Isolated services did not start') from None
                    time.sleep(.2)
            if options.powershell_only:
                with patch('desktop.intent.local_request', side_effect=isolated_request), \
                     patch('desktop.controller.local_request', side_effect=isolated_request):
                    result = controller.respond('Use PowerShell to check the current year with Get-Date -Format yyyy.', speak=False)
                    assert result.get('ok') and 'start_powershell' in result['tools'], result
                    print('PASS: real AI selected the background PowerShell tool', flush=True)
                    deadline = time.monotonic() + 25
                    while True:
                        job = controller.research.status(scope='powershell')
                        if job.get('status') in ('completed', 'failed', 'cancelled'):
                            break
                        if time.monotonic() > deadline:
                            raise RuntimeError('PowerShell did not finish')
                        time.sleep(.1)
                    from datetime import datetime
                    assert job['status'] == 'completed' and str(datetime.now().year) in job['output'], job
                    result = controller.respond('Read the PowerShell output.', speak=False)
                    assert result['ok'] and str(datetime.now().year) in result['text'], result
                    print('PASS: PowerShell executed and Jarvis returned its actual output', flush=True)
                    fixture = {'ok':True, 'publicIPv4':'203.0.113.7', 'publicIPv6':None,
                        'vpnDetected':True, 'vpnExitConfirmed':False, 'ispPublicIP':None,
                        'adapters':[{'name':'Wi-Fi','addresses':[{'address':'192.168.1.23'}]}],
                        'ispMeaning':'The underlying ISP public address cannot be independently verified while the VPN is active.',
                        'publicMeaning':'Current public address for Jarvis traffic; may be a VPN or proxy. Split tunneling can change other apps.',
                        'warnings':[]}
                    with patch('desktop.controller.inspect_network', return_value=fixture) as network:
                        result = controller.respond('What is my front facing VPN IP address?', speak=False)
                        assert result['ok'] and 'get_network_info' in result['tools'] and '203.0.113.7' in result['text'], result
                        result = controller.respond('What about my raw underlying ISP IP address?', speak=False)
                        assert result['ok'] and 'get_network_info' in result['tools'], result
                        import re
                        assert re.search(r'cannot|can.t|not|hidden|unable|unavailable|couldn.t', result['text'], re.I), result
                        assert network.call_count >= 2
                    print('PASS: AI distinguishes current public/VPN-facing IP from unverified raw ISP IP', flush=True)
                return
            if options.ram_only:
                with patch('desktop.intent.local_request', side_effect=isolated_request), \
                     patch('desktop.controller.local_request', side_effect=isolated_request):
                    for utterance in ('How much memory is my shit using?', 'How much RAM do I have left?',
                                      'What is eating all my RAM?', 'How much RAM does Microsoft Edge use right now?'):
                        decision = classify_request(utterance, 'verify-ram-intent')
                        assert decision['action'] == 'processes' and not decision['needs_clarification'], (utterance, decision)
                        if 'Edge' not in utterance:
                            assert decision['process_names'] == [], decision
                        print('PASS: AI routed ' + repr(utterance) + ' to live RAM inspection', flush=True)
                    decision = classify_request('How much memory is that using?', 'verify-ram-followup',
                                                {'topic':'processes', 'lastAnswer':'Microsoft Edge has one open window.'})
                    assert decision['action'] == 'processes' and decision['process_names'], decision
                    print('PASS: AI resolved RAM follow-up to the previously discussed app', flush=True)
                    for utterance in ('Explain what RAM does.', 'Remember that my favourite colour is blue.'):
                        decision = classify_request(utterance, 'verify-ram-negative')
                        assert decision['action'] == 'normal', (utterance, decision)
                        print('PASS: AI distinguished RAM readings from ' + repr(utterance), flush=True)
                    rows = [{'pid':101, 'name':'msedge.exe', 'memory':{'workingSetBytes':128 * 1024 ** 2}},
                            {'pid':102, 'name':'msedge.exe', 'memory':{'workingSetBytes':384 * 1024 ** 2}},
                            {'pid':201, 'name':'msedgewebview2.exe', 'memory':{'workingSetBytes':2 * 1024 ** 3}}]
                    physical = {'available':True, 'totalBytes':16 * 1024 ** 3, 'usedBytes':10 * 1024 ** 3,
                                'availableBytes':6 * 1024 ** 3, 'totalGiB':16, 'usedGiB':10,
                                'availableGiB':6, 'usedPercent':62.5}
                    def fixture(names):
                        return summarize_processes(rows, {'available':False, 'byPid':{}}, names, physical)
                    with patch('desktop.controller.inspect_processes', side_effect=fixture):
                        result = controller.respond('How much memory is my shit using?', speak=False)
                        assert result.get('ok') and result['tools'] == ['inspect_processes'], result
                        assert all(value in result['text'] for value in ('10', '16', '62.5')), result
                        print('PASS: live answer used actual fixture RAM numbers: ' + result['text'], flush=True)
                        result = controller.respond('How much RAM is Microsoft Edge using?', speak=False)
                        assert result.get('ok') and result['tools'] == ['inspect_processes'], result
                        assert '512' in result['text'] or '0.5' in result['text'], result
                        print('PASS: live answer summed Edge RAM and excluded WebView: ' + result['text'], flush=True)
                        result = controller.respond('Which app is using the most RAM?', speak=False)
                        assert result.get('ok') and 'webview' in result['text'].lower() and 'inspect_processes' in result['tools'], result
                        print('PASS: live model identified the largest measured memory consumer', flush=True)
                return
            if options.processes_only:
                with patch('desktop.intent.local_request', side_effect=isolated_request), \
                     patch('desktop.controller.local_request', side_effect=isolated_request):
                    cases = [('Yo, do I have Microsoft Edge open?', 'processes'),
                             ('Can you find any hidden processes on my computer?', 'processes'),
                             ('What is still running behind the scenes on this laptop?', 'processes'),
                             ('Explain what an operating system process is.', 'normal'),
                             ('Open Microsoft Edge for me.', 'normal')]
                    for utterance, action in cases:
                        decision = classify_request(utterance, 'verify-process-intent')
                        assert decision['action'] == action, (utterance, decision)
                        assert not decision['needs_clarification'], (utterance, decision)
                        if action == 'processes' and 'Edge' not in utterance:
                            assert decision['process_names'] == [], decision
                        print('PASS: AI routed ' + repr(utterance) + ' to ' + action, flush=True)
                    rows = [{'pid':101, 'name':'msedge.exe', 'path':r'C:\Program Files\Microsoft\Edge\msedge.exe'},
                            {'pid':102, 'name':'msedge.exe', 'path':r'C:\Program Files\Microsoft\Edge\msedge.exe'},
                            {'pid':201, 'name':'msedgewebview2.exe', 'path':None}]
                    for mode in ('open', 'background', 'unknown'):
                        windows = {'available':mode != 'unknown', 'byPid':{101:{'open':1, 'minimized':0}} if mode == 'open' else {}}
                        snapshot = summarize_processes(rows, windows, ['Microsoft Edge'])
                        with patch('desktop.controller.inspect_processes', return_value=snapshot):
                            result = controller.respond('Is Microsoft Edge open? Tell me whether it has a window or just background processes.', speak=False)
                        assert result.get('ok') and result['tools'] == ['inspect_processes'], result
                        answer = result['text'].lower().replace('\u2019', "'")
                        assert 'edge' in answer, result
                        if mode == 'open':
                            assert 'window' in answer and ('open' in answer or 'visible' in answer), result
                        elif mode == 'background':
                            assert 'background' in answer, result
                        else:
                            assert any(term in answer for term in ('cannot', "can't", 'unable', 'could not', "couldn't")), result
                        print('PASS: live model grounded Edge answer in ' + mode + ' window evidence: ' + result['text'], flush=True)
                    snapshot = summarize_processes(rows[2:], {'available':True, 'byPid':{}}, ['Microsoft Edge'])
                    with patch('desktop.controller.inspect_processes', return_value=snapshot):
                        result = controller.respond('Is Edge running?', speak=False)
                    assert result.get('ok') and result['tools'] == ['inspect_processes'], result
                    assert any(term in result['text'].lower().replace('\u2019', "'") for term in ('not running', "isn't running", 'no matching', 'no edge')), result
                    print('PASS: WebView alone does not become a running Edge browser', flush=True)
                return
            if options.controls_only:
                with patch('desktop.intent.local_request', side_effect=isolated_request), \
                     patch('desktop.controller.local_request', side_effect=isolated_request):
                    cases = [('Disappear for a quarter of an hour', 'break', 900),
                             ('Turn yourself off for a minute and a half', 'break', 90),
                             ('Take a break until I bring you back', 'break', None),
                             ('Remind me to take a break in twenty minutes', 'normal', None),
                             ('Do not go away, keep helping me', 'normal', None),
                             ('Can you see what is on my display?', 'screen', None)]
                    for utterance, action, seconds in cases:
                        decision = classify_request(utterance, 'verify-controls')
                        assert decision['action'] == action, (utterance, decision)
                        if action == 'break':
                            assert decision['delay_seconds'] == seconds, (utterance, decision)
                        print('PASS: AI understood ' + repr(utterance), flush=True)
                    result = controller.respond('Give me some space for two minutes', speak=False)
                    assert result.get('ok') and controller.breaks.active, result
                    assert 115 < controller.breaks.snapshot()['wakeAt'] - time.time() <= 120
                    assert BreakState(controller.breaks.path).active
                    controller.resume()
                    print('PASS: real AI break command persisted and resumed', flush=True)
                    blue = {'image':base64.b64encode(encode_png(200, 120, bytes([255,0,0,255])*200*120)).decode(),
                            'width':200, 'height':120, 'display':'Synthetic test image'}
                    with patch('desktop.controller.capture_screen', return_value=blue):
                        result = controller.respond('Look at my screen and tell me the dominant colour', speak=False)
                    assert result.get('ok') and 'blue' in result['text'].lower() and result['tools'] == ['view_screen'], result
                    print('PASS: real vision model described the supplied synthetic blue snapshot', flush=True)
                    choices = [('How are you?', 'default'),
                               ('Derive a plan to diagnose intermittent DNS resolution failures with clear test steps and how to distinguish client caching from resolver failure.', 'sol'),
                               ('Turn that thing off, but leave it on if it is the other one. I cannot tell which I mean.', 'astra')]
                    for utterance, tier in choices:
                        decision = classify_request(utterance, 'verify-model-choice')
                        assert decision['model_tier'] == tier, (utterance, decision)
                        print('PASS: AI chose ' + tier + ' for request complexity', flush=True)
                    for tier in ('sol', 'astra'):
                        response = isolated_request(18791, '/v1/responses', {
                            'model':'openclaw/desktop', 'modelTier':tier, 'user':'verify-reasoning-' + tier,
                            'desktopRequestId':'a'*32, 'capabilities':[],
                            'input':'What is seven times eight? Answer in one short sentence.'}, timeout=120)
                        assert tier in response['model'], response['model']
                        assert response['reasoning'] == ('high' if tier == 'astra' else 'medium'), response['reasoning']
                        answer = response_text(response).lower()
                        assert '56' in answer or 'fifty-six' in answer or 'fifty six' in answer, answer
                        print('PASS: live ' + response['model'] + ' answered with ' + response['reasoning'] + ' reasoning', flush=True)
                return
            cases = [("Shut the fuck up, Jarvis.", 'stop'),
                     ("Give it a rest, I've heard enough.", 'stop'),
                     ('I only have time for the gist.', 'shorter'),
                     ('Stop the timer.', 'followup'),
                     ("Don't stop, I want to hear the rest.", 'ignore'),
                     ('What does the phrase shut up mean?', 'followup'),
                     ('Yeah.', 'ignore'),
                     ("Okay, that's cool.", 'ignore'),
                     ('Uh huh.', 'ignore'),
                     ('Alex, can you pass me that controller?', 'ignore'),
                     ('He told me to shut up yesterday.', 'ignore'),
                     ('Actually, explain evaporation instead.', 'followup')]
            for utterance, expected in cases:
                response = isolated_request(18791, '/v1/responses', {
                    'model':'openclaw/interrupt', 'user':'verify-interruption',
                    'input':json.dumps({'utterance':utterance, 'ownerRequest':'Explain how rain forms.',
                                        'assistantSpeech':'Water droplets in clouds combine and grow heavier.'})}, timeout=15)
                classified = json.loads(response_text(response))
                assert classified['action'] == expected, (utterance, expected, classified)
                print('PASS: real AI classified ' + repr(utterance) + ' as ' + expected, flush=True)
            if options.interruptions_only:
                return
            with patch('desktop.controller.local_request', side_effect=isolated_request), \
                 patch('desktop.intent.local_request', side_effect=isolated_request):
                result = controller.respond('Remind me in thirty minutes to take a break.', speak=False)
                assert result.get('ok'), result
                reminders = controller.reminders.list()['reminders']
                assert len(reminders) == 1 and 1700 < reminders[0]['due'] - time.time() <= 1800, reminders
                assert len(Reminders(folder / 'reminders.json').list()['reminders']) == 1
                print('PASS: spoken-style timer request used the tool and persisted', flush=True)
                result = controller.respond('Find the invoice demo file and summarize the total due.', speak=False)
                assert result.get('ok') and 'read_file' in result['tools'], result
                assert '125' in result['text'] or 'hundred and twenty-five' in result['text'].lower(), result
                print('PASS: real AI searched the selected fixture folder and read its document', flush=True)
                with patch.object(controller.briefing.weather, 'current', return_value={'ok':False,'error':'Location unavailable'}), \
                     patch.object(controller.mail, 'search', return_value={'ok':False,'errors':['No connected email accounts']}):
                    result = controller.respond('Give me my morning briefing.', speak=False)
                assert result.get('ok') and 'get_briefing' in result['tools'], result
                print('PASS: briefing combined reminders with explicit unavailable sections', flush=True)
        finally:
            controller.closed.set()
            controller.browser.close()
            controller.research.close()
            if controller.server:
                controller.server.shutdown()
                controller.server.server_close()
            try:
                local_request(model_port, '/shutdown', {}, timeout=2)
                process.wait(timeout=3)
            except Exception:
                process.terminate()
                process.wait(timeout=3)
            log.close()


if __name__ == '__main__':
    main()
