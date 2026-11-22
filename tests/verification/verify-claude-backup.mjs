// Live check of the Claude backup with the real Anthropic key saved for this Windows account.
// A private copy of the model server runs with OpenAI replaced by a server that always fails, so every request has to
// be answered by Claude. Costs a few cents at most. Prints which model answered each request, or why Claude could not.
//   node tests/verification/verify-claude-backup.mjs
import { fakeDesktop, fakeOpenAi, startModelServer, textOf } from '../desktop/model_fakes.mjs';

const openAi = await fakeOpenAi([{ status: 500 }]);
const desktop = await fakeDesktop();
// claude: null means no stand-in. JARVIS_CLAUDE_SAVED_KEY=on lets this one run read the real saved key.
const server = await startModelServer({ openAi, claude: null, desktop, env: { JARVIS_CLAUDE_SAVED_KEY: 'on' } });
const report = {};
try {
  const status = (await server.get('/models')).body.backup;
  report.backup = { configured: status.configured, source: status.source, endpoint: status.host, message: status.message };
  if (status.configured) {
    const test = (await server.post('/backup/test')).body;
    report.test = test.ok ? { ok: true, model: test.model, seconds: test.milliseconds / 1000 } : { ok: false, status: test.status, error: test.error };
    if (test.ok) {
      const ask = async (label, body) => {
        const started = Date.now();
        const reply = await server.ask(body);
        report[label] = reply.status === 200
          ? { model: reply.body.model, effort: reply.body.reasoning, seconds: (Date.now() - started) / 1000, reply: String(textOf(reply)).slice(0, 200) }
          : { failed: reply.status };
      };
      await ask('routing', { model: 'openclaw/intent', input: JSON.stringify({ utterance: 'What is two plus two?', context: {} }) });
      await ask('routine', { model: 'openclaw/desktop', desktopRequestId: 'b'.repeat(32), capabilities: [], modelTier: 'default', input: 'What is two plus two?' });
      await ask('harder', { model: 'openclaw/desktop', desktopRequestId: 'c'.repeat(32), capabilities: [], modelTier: 'astra',
        input: 'I can cut my commute by moving closer to work, or keep a bigger flat further out. What should I weigh up?' });
      report.decisions = server.events('claude_route').map((entry) => ({ model: entry.model, effort: entry.effort, chosenByHaiku: entry.decided }));
    }
  }
  report.serverLog = server.log.filter((entry) => entry.event !== 'luna_service_ready');
} finally {
  await server.stop();
  await Promise.all([openAi.close(), desktop.close()]);
}
console.log(JSON.stringify(report, null, 1));
