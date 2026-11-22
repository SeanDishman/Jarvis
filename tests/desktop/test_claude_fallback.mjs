// Claude as the backup when OpenAI does not answer. The second half runs the real model server against stand-in
// OpenAI, Claude and desktop services, so every request it would send to Claude is inspected without spending a cent.
import assert from 'node:assert/strict';
import test from 'node:test';
import { ANTHROPIC_URL, Availability, CLAUDE, ClaudeAccess, ModelFallback, claudeMaxTokens, claudeSchema, defaultRoute, failureStatus,
  failureText, forClaude, forStorage, needsDecision, parseRoute, replyGuardMs, resolveCredentials, routeFormat, windowsUserEnv }
  from '../../desktop/claude-fallback.mjs';
import { intentFormat } from '../../desktop/intent-prompts.mjs';
import { interruptionFormat } from '../../desktop/interruption-prompts.mjs';
import { researchFormats } from '../../desktop/research-prompts.mjs';
import { desktopReplyFormat } from '../../desktop/model-output.mjs';
import { fakeClaude, fakeDesktop, fakeOpenAi, startModelServer, textOf } from './model_fakes.mjs';

const INTENT = JSON.stringify({ action: 'normal', steps: [], delay_seconds: null, wake_at: null, display: 'current', process_names: [],
  model_tier: 'default', needs_clarification: false, task_id: '', capabilities: [], system_actions: [], browser: '' });
const INTERRUPT = JSON.stringify({ action: 'stop', delay_seconds: null, wake_at: null });
const said = (text) => JSON.stringify({ reply: text, status: 'completed' });
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const systemOf = (request) => request.body.system.map((block) => block.text).join('');
const isRouter = (request) => systemOf(request).startsWith('You are the router for Jarvis');
const desktopRequest = (extra = {}) => ({ model: 'openclaw/desktop', desktopRequestId: 'a'.repeat(32), capabilities: [], input: 'What is the weather like?', ...extra });

// ---------------------------------------------------------------- the policy on its own

test('the three models and the effort levels a voice assistant can wait for', () => {
  assert.deepEqual(Object.values(CLAUDE).map((row) => row.id), ['claude-haiku-5-5', 'claude-opus-5-5', 'claude-fable-5-2']);
  assert.deepEqual(routeFormat.schema.properties.effort.enum, ['low', 'medium', 'high']);
  assert.deepEqual(routeFormat.schema.properties.model.enum, ['opus', 'fable'], 'Haiku only chooses between the two stronger models');
});

test('Haiku takes everything first, and only requests judged harder are handed up', () => {
  for (const kind of ['intent', 'interrupt', 'line', 'quote', 'chat']) {
    for (const tier of ['default', 'sol', 'astra']) {
      assert.equal(defaultRoute(kind, tier).key, 'haiku', `${kind} at ${tier} stays on Haiku`);
      assert.equal(needsDecision(kind, tier), false);
    }
  }
  for (const kind of ['desktop', 'research', 'computer', 'screen', 'processes']) {
    assert.deepEqual(defaultRoute(kind, 'default'), { key: 'haiku', effort: 'low' });
    assert.equal(needsDecision(kind, 'default'), false, 'a routine request needs no second opinion');
    assert.equal(needsDecision(kind, 'sol'), true);
    assert.equal(needsDecision(kind, 'astra'), true);
    assert.deepEqual(defaultRoute(kind, 'sol'), { key: 'opus', effort: 'medium' }, 'what sol means if Haiku cannot be asked');
    assert.deepEqual(defaultRoute(kind, 'astra'), { key: 'fable', effort: 'high' });
  }
  assert.equal(defaultRoute('intent', 'default').effort, 'medium', 'routing itself gets more thought than a one-line reply');
});

test('only a complete, known choice from Haiku is accepted', () => {
  assert.deepEqual(parseRoute('{"model":"fable","effort":"high"}'), { key: 'fable', effort: 'high' });
  assert.deepEqual(parseRoute('{"model":"opus","effort":"low"}'), { key: 'opus', effort: 'low' });
  for (const bad of ['', 'opus', '{"model":"haiku","effort":"low"}', '{"model":"opus","effort":"max"}', '{"model":"gpt","effort":"low"}', '{"effort":"low"}']) {
    assert.equal(parseRoute(bad), null, bad);
  }
});

test('reply limits leave room for thinking, which counts toward the limit on these models', () => {
  assert.equal(claudeMaxTokens(120, 'low'), 2524, 'a one-line reply still gets room to think first');
  assert.ok(claudeMaxTokens(2400, 'low') < claudeMaxTokens(2400, 'medium') && claudeMaxTokens(2400, 'medium') < claudeMaxTokens(2400, 'high'));
  assert.ok(claudeMaxTokens(2400, 'high') <= 64000);
});

function unsupported(schema, found = []) {
  if (Array.isArray(schema)) schema.forEach((item) => unsupported(item, found));
  else if (schema && typeof schema === 'object') {
    for (const [key, value] of Object.entries(schema)) {
      if (['minimum', 'maximum', 'maxItems', 'minLength', 'maxLength', 'multipleOf', 'pattern'].includes(key)) found.push(key);
      if (key === 'type' && Array.isArray(value)) found.push('type list');
      if (key === 'additionalProperties' && value !== false) found.push('open object');
      unsupported(value, found);
    }
  }
  return found;
}

test('Jarvis\'s JSON formats are turned into what Claude accepts without losing their enums', () => {
  for (const format of [intentFormat, interruptionFormat, researchFormats.plan, researchFormats.summarize, desktopReplyFormat, routeFormat]) {
    const schema = claudeSchema(format.schema);
    assert.deepEqual(unsupported(schema), [], format.name);
    assert.deepEqual(schema.required, format.schema.required, format.name);
  }
  const intent = claudeSchema(intentFormat.schema);
  assert.deepEqual(intent.properties.action.enum, intentFormat.schema.properties.action.enum, 'the action stays one of the known actions');
  assert.deepEqual(intent.properties.capabilities.items.enum, intentFormat.schema.properties.capabilities.items.enum);
  assert.deepEqual(intent.properties.delay_seconds, { anyOf: [{ type: 'number' }, { type: 'null' }] });
  assert.equal(intent.properties.steps.description, '{maxItems: 8}', 'a limit Claude cannot enforce is still told to it');
  assert.deepEqual(intent.properties.steps.items.properties.depends_on.items, { type: 'integer', description: '{minimum: 0, maximum: 7}' });
  assert.deepEqual(intentFormat.schema.properties.delay_seconds, { type: ['number', 'null'] }, 'the original format is left alone');
});

test('the key comes from the standard Anthropic settings, each key with its own endpoint', () => {
  assert.deepEqual(resolveCredentials([{ source: 'process', values: {} }, { source: 'windows', values: {} }]), { reason: 'no_key' });
  assert.deepEqual(resolveCredentials([{ source: 'process', values: { ANTHROPIC_API_KEY: ' sk-ant-api03-abc ' } }]),
    { apiKey: 'sk-ant-api03-abc', bearer: false, baseUrl: ANTHROPIC_URL, host: 'api.anthropic.com', source: 'process' });
  const saved = resolveCredentials([{ source: 'process', values: { ANTHROPIC_BASE_URL: 'https://elsewhere.example' } },
    { source: 'windows', values: { ANTHROPIC_AUTH_TOKEN: 'gateway-key', ANTHROPIC_BASE_URL: 'https://gateway.example/' } }]);
  assert.deepEqual(saved, { apiKey: 'gateway-key', bearer: true, baseUrl: 'https://gateway.example', host: 'gateway.example', source: 'windows' },
    'a saved key is never sent to an endpoint from somewhere else');
  assert.equal(resolveCredentials([{ source: 'process', values: { ANTHROPIC_API_KEY: 'k', ANTHROPIC_AUTH_TOKEN: 't' } }]).apiKey, 'k');
  assert.equal(resolveCredentials([{ source: 'windows', values: { ANTHROPIC_AUTH_TOKEN: 'sk-ant-oat01-subscription' } }]).reason, 'subscription_token',
    'a Claude subscription sign-in is refused: it is only for Claude\'s own apps');
  assert.equal(resolveCredentials([{ source: 'windows', values: { ANTHROPIC_API_KEY: 'k', ANTHROPIC_BASE_URL: 'not a url' } }]).reason, 'invalid_endpoint');
  assert.equal(resolveCredentials([{ source: 'windows', values: { ANTHROPIC_API_KEY: 'k', ANTHROPIC_BASE_URL: 'ftp://files.example' } }]).reason, 'invalid_endpoint');
});

test('the saved Windows settings are read from the registry output, and only the Anthropic ones', async () => {
  if (process.platform !== 'win32') return;
  const output = ['', 'HKEY_CURRENT_USER\\Environment', '    Path    REG_EXPAND_SZ    C:\\Tools;%USERPROFILE%\\bin',
    '    ANTHROPIC_AUTH_TOKEN    REG_SZ    saved-key', '    ANTHROPIC_BASE_URL    REG_SZ    https://gateway.example',
    '    OPENAI_API_KEY    REG_SZ    not-ours', ''].join('\r\n');
  const calls = [];
  const values = await windowsUserEnv((file, args, options, done) => { calls.push([file, args]); done(null, output); });
  assert.deepEqual(values, { ANTHROPIC_AUTH_TOKEN: 'saved-key', ANTHROPIC_BASE_URL: 'https://gateway.example' });
  assert.deepEqual(calls, [['reg', ['query', 'HKCU\\Environment']]]);
  assert.equal(await windowsUserEnv((file, args, options, done) => done(new Error('reg failed'))), null, 'a failed read is not the same as no key');
});

test('a key Jarvis was started with wins, and a saved key is found later without a restart', async () => {
  let saved = {};
  let reads = 0;
  let now = 0;
  const access = new ClaudeAccess({ env: {}, readUserEnv: async () => { reads += 1; return saved; }, now: () => now });
  assert.equal(await access.current(), null);
  saved = { ANTHROPIC_API_KEY: 'added-later' };
  now = 30000;
  assert.equal(await access.current(), null, 'the last answer is kept for a minute');
  now = 61000;
  assert.equal((await access.current()).apiKey, 'added-later');
  assert.equal(reads, 2);
  const started = new ClaudeAccess({ env: { ANTHROPIC_API_KEY: 'from-start' }, readUserEnv: async () => { throw new Error('not needed'); } });
  assert.equal((await started.current()).source, 'process');
});

test('a service that just failed is skipped for a while, longer each time, and trusted again once it answers', () => {
  const service = new Availability();
  assert.ok(service.available(0));
  service.failed(1000);
  assert.ok(!service.available(60999) && service.available(61000), 'one minute after the first failure');
  service.failed(61000);
  assert.ok(!service.available(180999) && service.available(181000), 'two minutes after the second');
  for (let count = 0; count < 12; count += 1) service.failed(200000);
  assert.ok(service.available(200000 + 600000), 'never more than ten minutes');
  service.succeeded();
  assert.ok(service.available(200001));
  service.failed(300000);
  assert.ok(service.available(360000), 'and back to one minute');
});

test('OpenAI gets part of each request\'s time to start answering', () => {
  assert.equal(replyGuardMs(4000), 2000, 'a quick line');
  assert.equal(replyGuardMs(10000), 4000);
  assert.equal(replyGuardMs(20000), 8000, 'the router');
  assert.equal(replyGuardMs(110000), 15000, 'never more than fifteen seconds');
});

test('failures are told apart by what the owner can do about them', () => {
  assert.equal(failureStatus({ errorCode: '402', errorMessage: '402: budget' }), 402);
  assert.equal(failureStatus({ errorMessage: '529: overloaded' }), 529);
  assert.equal(failureStatus({ errorCode: 'ECONNREFUSED', errorMessage: 'connect ECONNREFUSED' }), undefined);
  assert.match(failureText(402), /no balance/);
  assert.match(failureText(401), /rejected/);
  assert.match(failureText(429), /rate limited/);
  assert.match(failureText(undefined), /could not be reached/);
});

test('reasoning blocks are dropped where they would no longer be valid', () => {
  const thinking = { type: 'thinking', thinking: '', thinkingSignature: 'sig' };
  const fromClaude = { role: 'assistant', provider: 'anthropic', content: [thinking, { type: 'text', text: 'Hello' }] };
  const fromOpenAi = { role: 'assistant', provider: 'openai', content: [thinking, { type: 'toolCall', id: 'c1', name: 'get_weather', arguments: {} }] };
  const user = { role: 'user', content: 'Hi' };
  assert.deepEqual(forStorage([user, fromClaude, fromOpenAi]), [user, { ...fromClaude, content: [fromClaude.content[1]] }, fromOpenAi],
    'Claude\'s reasoning is not kept for the next request; OpenAI\'s own is');
  assert.deepEqual(forClaude([user, fromClaude, fromOpenAi]), [user, fromClaude, { ...fromOpenAi, content: [fromOpenAi.content[1]] }],
    'within one request Claude gets its own reasoning back unchanged, and none of OpenAI\'s');
});

// A runtime that answers from a script, for the timing rules that would take minutes against real servers.
function scripted({ openAi, claude }) {
  const calls = { openAi: 0, claude: [] };
  const answer = (step) => (step === 'fail' ? { stopReason: 'error', errorMessage: '500: down', errorCode: '500' } : { stopReason: 'stop', content: [{ type: 'text', text: step }] });
  return { calls,
    streamSimple() {
      calls.openAi += 1;
      const response = answer(openAi());
      return { async *[Symbol.asyncIterator]() { yield { type: 'start' }; }, result: async () => response };
    },
    async completeSimple(model, context, options) {
      calls.claude.push({ model: model.id, effort: options.reasoning });
      return answer(claude(model));
    } };
}

test('while OpenAI is down requests go straight to Claude, and OpenAI is tried again later', async () => {
  let now = 0;
  let openAiUp = false;
  const log = [];
  const runtime = scripted({ openAi: () => (openAiUp ? 'from openai' : 'fail'), claude: () => 'from claude' });
  const service = new ModelFallback({ runtime, now: () => now, log: (entry) => log.push(entry.event),
    access: new ClaudeAccess({ env: { ANTHROPIC_API_KEY: 'k' } }) });
  const ask = async () => {
    const turn = service.request({ kind: 'line', limitMs: 4000 });
    const response = await turn.complete(() => [{ id: 'gpt' }, {}, {}], (route) => [{}, { reasoning: route.effort }]);
    return [response.content[0].text, turn.used?.model ?? 'openai'];
  };
  assert.deepEqual(await ask(), ['from claude', 'anthropic/claude-haiku-5-5']);
  assert.equal(runtime.calls.openAi, 1);
  now = 30000;
  assert.deepEqual(await ask(), ['from claude', 'anthropic/claude-haiku-5-5']);
  assert.equal(runtime.calls.openAi, 1, 'OpenAI is not asked again inside the minute');
  now = 61000;
  assert.deepEqual(await ask(), ['from claude', 'anthropic/claude-haiku-5-5']);
  assert.equal(runtime.calls.openAi, 2, 'after a minute it is tried once more');
  now = 61000 + 119000;
  await ask();
  assert.equal(runtime.calls.openAi, 2, 'and after failing twice it rests two minutes');
  openAiUp = true;
  now = 61000 + 120000;
  assert.deepEqual(await ask(), ['from openai', 'openai']);
  assert.equal((await service.status()).active, false);
  assert.deepEqual(await ask(), ['from openai', 'openai']);
  assert.deepEqual(log, ['openai_unavailable', 'claude_backup_started', 'openai_unavailable', 'openai_answering_again']);
});

test('with the backup turned off or without a key, OpenAI is the only model, exactly as before', async () => {
  for (const [name, options] of [['off', { enabled: async () => false, access: new ClaudeAccess({ env: { ANTHROPIC_API_KEY: 'k' } }) }],
    ['no key', { access: new ClaudeAccess({ env: {}, readUserEnv: async () => ({}) }) }]]) {
    const runtime = scripted({ openAi: () => 'fail', claude: () => 'from claude' });
    const service = new ModelFallback({ runtime, ...options });
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await assert.rejects(service.request({ kind: 'desktop', tier: 'astra', limitMs: 110000 }).complete(() => [{ id: 'gpt' }, {}, {}], () => [{}, {}]), /500: down/, name);
    }
    assert.equal(runtime.calls.openAi, 3, name + ': every request still goes to OpenAI');
    assert.deepEqual(runtime.calls.claude, [], name);
    const status = await service.status();
    assert.equal(status.ready, false, name);
    assert.equal(status.enabled, name !== 'off');
  }
});

test('when both are failing, OpenAI is still asked rather than nobody', async () => {
  let now = 0;
  const runtime = scripted({ openAi: () => 'fail', claude: () => 'fail' });
  const service = new ModelFallback({ runtime, now: () => now, access: new ClaudeAccess({ env: { ANTHROPIC_API_KEY: 'k' } }) });
  const ask = () => service.request({ kind: 'line', limitMs: 4000 }).complete(() => [{ id: 'gpt' }, {}, {}], () => [{}, {}]);
  await assert.rejects(ask());
  assert.equal(runtime.calls.claude.length, 1);
  now = 1000;
  await assert.rejects(ask());
  assert.equal(runtime.calls.openAi, 2, 'OpenAI is asked again because Claude is resting too');
  assert.equal(runtime.calls.claude.length, 1, 'and Claude is not asked while it rests');
});

// ---------------------------------------------------------------- the real model server against stand-ins

async function withServer({ openAi: script = [{ status: 500 }], claude: answer, ...options }, run) {
  const openAi = await fakeOpenAi(script);
  const claude = answer === null ? null : await fakeClaude(answer ?? (() => ({ text: INTENT })));
  const desktop = await fakeDesktop({ ok: true, summary: 'Sunny, 20 degrees.' });
  const server = await startModelServer({ openAi, claude, desktop, ...options });
  try {
    return await run({ server, openAi, claude, desktop });
  } catch (error) {
    // What the model server itself logged, to tell a fault in it from a hiccup on a busy PC.
    console.log(`model server ${server.running() ? 'still running' : 'EXITED'}; its output:
${server.output().slice(-1500)}`);
    throw error;
  } finally {
    await server.stop();
    await Promise.all([openAi.close(), claude?.close(), desktop.close()]);
  }
}

test('OpenAI answering normally is used as before and Claude is never asked', async () => {
  await withServer({ openAi: [{ text: INTENT }, { tool: 'get_weather', input: {} }, { text: said('Sunny, sir.') }] }, async ({ server, claude, desktop }) => {
    const routed = await server.ask({ model: 'openclaw/intent', input: JSON.stringify({ utterance: 'what time is it', context: {} }) });
    assert.equal(routed.status, 200, JSON.stringify(routed.body));
    assert.equal(textOf(routed), INTENT);
    assert.match(routed.body.model, /^openai\//);
    assert.equal(routed.body.backup, false);
    const answered = await server.ask(desktopRequest({ capabilities: ['get_weather'] }));
    assert.equal(answered.status, 200, JSON.stringify(answered.body));
    assert.equal(textOf(answered), 'Sunny, sir.');
    assert.equal(desktop.calls.length, 1, 'the tool ran once');
    assert.equal(claude.requests.length, 0);
    const health = (await server.get('/health')).body;
    assert.equal(health.version >= 30 && health.claudeFallback, true);
    assert.deepEqual([health.backup.enabled, health.backup.configured, health.backup.ready, health.backup.active], [true, true, true, false]);
    assert.ok(!JSON.stringify(health).includes('sk-ant-api03-test-key'), 'the key is never reported');
  });
});

test('when OpenAI fails, Haiku does the routing, with a request Claude accepts', async () => {
  await withServer({}, async ({ server, claude, openAi }) => {
    const reply = await server.ask({ model: 'openclaw/intent', input: JSON.stringify({ utterance: 'what time is it', context: {} }) });
    assert.equal(reply.status, 200, JSON.stringify(reply.body));
    assert.equal(textOf(reply), INTENT);
    assert.deepEqual([reply.body.model, reply.body.reasoning, reply.body.backup, reply.body.modelFallback], ['anthropic/claude-haiku-5-5', 'medium', true, false]);
    assert.equal(claude.requests.length, 1);
    const { body, headers, path } = claude.requests[0];
    assert.equal(path, '/v1/messages');
    assert.equal(body.model, 'claude-haiku-5-5');
    assert.equal(headers['x-api-key'], 'sk-ant-api03-test-key');
    assert.equal(headers.authorization, undefined);
    assert.deepEqual(body.thinking, { type: 'adaptive', display: 'omitted' }, 'thinking is adaptive: the only setting all three models accept');
    assert.equal(body.output_config.effort, 'medium');
    assert.equal(body.output_config.format.type, 'json_schema');
    assert.deepEqual(unsupported(body.output_config.format.schema), []);
    assert.deepEqual(body.output_config.format.schema.properties.action.enum, intentFormat.schema.properties.action.enum);
    for (const banned of ['temperature', 'top_p', 'top_k', 'tool_choice', 'tools', 'fallbacks']) assert.equal(banned in body, false, banned);
    assert.equal(body.stream, true);
    assert.ok(body.max_tokens >= 2400 + 4000, 'room for thinking and the reply');
    assert.ok(systemOf(claude.requests[0]).startsWith('Interpret the owner\'s complete request'));
    assert.equal(body.messages.at(-1).role, 'user', 'no assistant prefill');
    assert.deepEqual(server.events('openai_unavailable').map((entry) => entry.kind), ['intent']);
    assert.equal(openAi.state.requests, 1, 'the failed request was not retried');
  });
});

test('a routine request is answered by Haiku at low effort with no second opinion', async () => {
  await withServer({ claude: () => ({ thinking: true, text: said('It is noon, sir.') }) }, async ({ server, claude }) => {
    const reply = await server.ask(desktopRequest({ modelTier: 'default', input: 'What time is it?' }));
    assert.equal(reply.status, 200, JSON.stringify(reply.body));
    assert.equal(textOf(reply), 'It is noon, sir.');
    assert.deepEqual([reply.body.model, reply.body.reasoning, reply.body.taskStatus], ['anthropic/claude-haiku-5-5', 'low', 'completed']);
    assert.equal(claude.requests.length, 1);
    assert.equal(claude.requests[0].body.output_config.effort, 'low');
    assert.match(systemOf(claude.requests[0]), /Model handling this request: claude-haiku-5-5\. Reasoning effort: low\./);
    assert.deepEqual(claude.requests[0].body.output_config.format.schema.properties.status.enum, desktopReplyFormat.schema.properties.status.enum);
  });
});

test('for a harder request Haiku picks the model and the effort, and that model answers', async () => {
  for (const [tier, choice, model, effort] of [['astra', '{"model":"fable","effort":"high"}', 'claude-fable-5-2', 'high'],
    ['sol', '{"model":"opus","effort":"low"}', 'claude-opus-5-5', 'low'],
    ['astra', '{"model":"opus","effort":"medium"}', 'claude-opus-5-5', 'medium']]) {
    await withServer({ claude: (request) => (isRouter(request) ? { text: choice } : { thinking: true, text: said('Here is the plan, sir.') }) }, async ({ server, claude }) => {
      const reply = await server.ask(desktopRequest({ modelTier: tier, input: 'My build fails only on the server and I changed three things. What is wrong?' }));
      assert.equal(reply.status, 200, JSON.stringify(reply.body));
      assert.deepEqual([reply.body.model, reply.body.reasoning, reply.body.backup], ['anthropic/' + model, effort, true]);
      assert.equal(claude.requests.length, 2, 'one question to Haiku, one answer');
      const [decision, answer] = claude.requests;
      assert.equal(decision.body.model, 'claude-haiku-5-5', 'Haiku is the one that decides');
      assert.equal(decision.body.output_config.effort, 'low');
      assert.deepEqual(decision.body.output_config.format.schema.properties, routeFormat.schema.properties);
      const asked = JSON.parse(decision.body.messages[0].content.find((block) => block.type === 'text').text);
      assert.equal(asked.tier, tier);
      assert.match(asked.request, /build fails only on the server/);
      assert.match(asked.task, /answer the owner in conversation/);
      assert.equal(answer.body.model, model);
      assert.equal(answer.body.output_config.effort, effort);
      assert.deepEqual(answer.body.thinking, { type: 'adaptive', display: 'omitted' });
      assert.ok(answer.body.max_tokens >= 2400 + { low: 1500, medium: 4000, high: 10000 }[effort]);
      assert.match(systemOf(answer), new RegExp(`Model handling this request: ${model}\\. Reasoning effort: ${effort}\\.`));
      assert.deepEqual(server.events('claude_route').map((entry) => [entry.tier, entry.model, entry.effort, entry.decided]), [[tier, model, effort, true]]);
    });
  }
});

test('if Haiku\'s choice is unusable, the tier decides: sol means Opus, astra means Fable', async () => {
  await withServer({ claude: (request) => (isRouter(request) ? { text: '{"model":"sonnet","effort":"extreme"}' } : { text: said('Done, sir.') }) }, async ({ server, claude }) => {
    const sol = await server.ask(desktopRequest({ modelTier: 'sol' }));
    assert.deepEqual([sol.body.model, sol.body.reasoning], ['anthropic/claude-opus-5-5', 'medium']);
    const astra = await server.ask(desktopRequest({ modelTier: 'astra' }));
    assert.deepEqual([astra.body.model, astra.body.reasoning], ['anthropic/claude-fable-5-2', 'high']);
    assert.deepEqual(claude.requests.map((request) => request.body.model), ['claude-haiku-5-5', 'claude-opus-5-5', 'claude-haiku-5-5', 'claude-fable-5-2']);
    assert.deepEqual(server.events('claude_route').map((entry) => entry.decided), [false, false]);
  });
});

test('on Haiku the first tool call is forced, tools run one at a time and the prompt never changes mid-reply', async () => {
  await withServer({ claude: (request, count) => (count === 1 ? { tool: 'get_weather', input: {} } : { thinking: true, text: said('Sunny and twenty degrees, sir.') }) },
    async ({ server, claude, desktop }) => {
      const reply = await server.ask(desktopRequest({ capabilities: ['get_weather'] }));
      assert.equal(reply.status, 200, JSON.stringify(reply.body));
      assert.equal(textOf(reply), 'Sunny and twenty degrees, sir.');
      assert.deepEqual(desktop.calls.map((call) => call.name), ['get_weather']);
      const [first, second] = claude.requests;
      assert.deepEqual(first.body.tool_choice, { type: 'any', disable_parallel_tool_use: true });
      assert.deepEqual(second.body.tool_choice, { type: 'auto', disable_parallel_tool_use: true });
      assert.deepEqual(first.body.tools.map((tool) => tool.name), ['get_weather']);
      assert.equal(JSON.stringify(second.body.tools), JSON.stringify(first.body.tools));
      assert.equal(systemOf(second), systemOf(first), 'same prompt, to the second, on both rounds');
      assert.equal(JSON.stringify(second.body.messages[0]), JSON.stringify(first.body.messages[0]), 'and the same first message');
      const sent = JSON.stringify(second.body.messages);
      assert.match(sent, /"type":"tool_use"/);
      assert.match(sent, /"type":"tool_result"/);
      assert.match(sent, /Sunny, 20 degrees\./, 'the tool result reached the model');
    });
});

test('on the last round tools are ruled out instead of removed', async () => {
  await withServer({ claude: (request) => (request.body.tool_choice?.type === 'none' ? { text: said('That is all I found, sir.') } : { tool: 'get_weather', input: {} }) },
    async ({ server, claude, desktop }) => {
      const reply = await server.ask(desktopRequest({ capabilities: ['get_weather'] }));
      assert.equal(reply.status, 200, JSON.stringify(reply.body));
      assert.equal(desktop.calls.length, 4);
      assert.equal(claude.requests.length, 5);
      assert.deepEqual(claude.requests[4].body.tool_choice, { type: 'none' });
      assert.equal(JSON.stringify(claude.requests[4].body.tools), JSON.stringify(claude.requests[0].body.tools), 'the tool list is unchanged on the last round');
    });
});

test('Opus and Fable cannot be forced to use a tool, so they are told to, reminded once, and the reminder is not kept', async () => {
  const answers = [{ text: '{"model":"opus","effort":"medium"}' }, { thinking: true, text: said('It is probably sunny, sir.') },
    { thinking: true, tool: 'get_weather', input: {} }, { thinking: true, text: said('Sunny and twenty degrees, sir.') }, { text: said('You asked about the weather, sir.') }];
  await withServer({ claude: (request, count) => answers[count - 1] }, async ({ server, claude, desktop }) => {
    const reply = await server.ask({ ...desktopRequest({ capabilities: ['get_weather'], modelTier: 'sol' }), user: 'one-conversation' });
    assert.equal(reply.status, 200, JSON.stringify(reply.body));
    assert.equal(textOf(reply), 'Sunny and twenty degrees, sir.');
    assert.equal(reply.body.model, 'anthropic/claude-opus-5-5');
    assert.equal(desktop.calls.length, 1);
    const [, first, reminded, afterTool] = claude.requests;
    assert.deepEqual(first.body.tool_choice, { type: 'auto', disable_parallel_tool_use: true }, 'no forced call on Opus');
    assert.match(systemOf(first), /Tools were selected for this request\. Begin by calling the tool it needs\./);
    assert.equal(systemOf(reminded), systemOf(first));
    assert.match(JSON.stringify(reminded.body.messages.at(-1)), /You replied without calling a tool/);
    assert.match(JSON.stringify(reminded.body.messages.at(-2)), /"signature":"sig_2"/, 'its reasoning goes back unchanged within the same reply');
    assert.match(JSON.stringify(afterTool.body.messages), /"type":"tool_result"/);
    // The next request in the same conversation: Haiku sees the exchange, without the reminder or any reasoning blocks.
    const next = await server.ask({ ...desktopRequest({ input: 'What did I just ask?' }), user: 'one-conversation' });
    assert.equal(next.status, 200, JSON.stringify(next.body));
    const history = JSON.stringify(claude.requests[4].body.messages);
    assert.match(history, /Sunny and twenty degrees, sir\./);
    assert.match(history, /"type":"tool_use"/);
    assert.doesNotMatch(history, /You replied without calling a tool/);
    assert.doesNotMatch(history, /It is probably sunny/, 'the answer it was reminded about is not kept either');
    assert.doesNotMatch(history, /"type":"thinking"/, 'reasoning is never carried into a later request');
  });
});

test('a reply OpenAI started is finished by Claude without running the tool twice', async () => {
  await withServer({ openAi: [{ tool: 'get_weather', input: {} }, { status: 500 }], claude: () => ({ text: said('Sunny and twenty degrees, sir.') }) },
    async ({ server, claude, desktop, openAi }) => {
      const reply = await server.ask(desktopRequest({ capabilities: ['get_weather'] }));
      assert.equal(reply.status, 200, JSON.stringify(reply.body));
      assert.equal(textOf(reply), 'Sunny and twenty degrees, sir.');
      assert.deepEqual([reply.body.model, reply.body.backup], ['anthropic/claude-haiku-5-5', true]);
      assert.equal(openAi.state.requests, 2);
      assert.equal(desktop.calls.length, 1, 'the tool OpenAI called is not called again');
      assert.equal(claude.requests.length, 1);
      const blocks = claude.requests[0].body.messages.flatMap((message) => (Array.isArray(message.content) ? message.content : []));
      const use = blocks.find((block) => block.type === 'tool_use');
      const result = blocks.find((block) => block.type === 'tool_result');
      assert.equal(use.name, 'get_weather');
      assert.match(use.id, /^[a-zA-Z0-9_-]{1,64}$/, 'the call id is one Claude accepts');
      assert.equal(result.tool_use_id, use.id, 'and the result still points at that call');
      assert.equal(claude.requests[0].body.tool_choice.type, 'auto', 'it is not made to call a tool again');
    });
});

test('an OpenAI that never answers is given up on in time, and skipped afterwards', async () => {
  for (const mode of ['hang', 'stall']) {
    await withServer({ openAi: [mode], claude: () => ({ text: INTERRUPT }) }, async ({ server, claude, openAi }) => {
      const started = Date.now();
      const reply = await server.ask({ model: 'openclaw/interrupt', input: 'stop talking' });
      const waited = Date.now() - started;
      assert.equal(reply.status, 200, mode + ': ' + JSON.stringify(reply.body));
      assert.equal(textOf(reply), INTERRUPT);
      assert.equal(reply.body.model, 'anthropic/claude-haiku-5-5');
      assert.ok(waited >= 3800 && waited < 9500, `${mode}: gave OpenAI four of its ten seconds, took ${waited}ms`);
      assert.deepEqual(server.events('openai_unavailable').map((entry) => entry.reason), [mode === 'hang' ? 'no_reply' : 'error']);
      const again = Date.now();
      assert.equal((await server.ask({ model: 'openclaw/interrupt', input: 'stop talking' })).status, 200);
      assert.ok(Date.now() - again < 3000, 'the next request does not wait on OpenAI again');
      assert.equal(openAi.state.requests, 1);
      assert.equal(claude.requests.length, 2);
    });
  }
});

test('an OpenAI that started answering is left to finish, however long the reply takes', async () => {
  await withServer({ openAi: [{ text: INTERRUPT, delayMs: 5500 }] }, async ({ server, claude }) => {
    const started = Date.now();
    const reply = await server.ask({ model: 'openclaw/interrupt', input: 'stop talking' });
    assert.equal(reply.status, 200, JSON.stringify(reply.body));
    assert.match(reply.body.model, /^openai\//);
    assert.ok(Date.now() - started >= 5400, 'longer than the four seconds it had to start');
    assert.equal(claude.requests.length, 0);
    assert.deepEqual(server.events('openai_unavailable'), []);
  });
});

test('a request the owner cancels is not sent to Claude and does not count against OpenAI', async () => {
  await withServer({ openAi: ['hang', { text: INTERRUPT }] }, async ({ server, claude, openAi }) => {
    const cancel = new AbortController();
    const pending = server.ask({ model: 'openclaw/interrupt', input: 'stop talking' }, cancel.signal).catch((error) => error.name);
    // Cancel once OpenAI has the request and is sitting on it.
    for (let waited = 0; openAi.state.requests < 1 && waited < 15000; waited += 50) await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(openAi.state.requests, 1);
    await new Promise((resolve) => setTimeout(resolve, 300));
    cancel.abort();
    assert.equal(await pending, 'AbortError');
    await new Promise((resolve) => setTimeout(resolve, 600));
    assert.equal(claude.requests.length, 0);
    assert.deepEqual(server.events('openai_unavailable'), []);
    const reply = await server.ask({ model: 'openclaw/interrupt', input: 'stop talking' });
    assert.match(reply.body.model, /^openai\//, 'OpenAI is still the first choice');
    assert.equal(openAi.state.requests, 2);
  });
});

test('a key with no balance fails clearly, is rested, and the owner is told why', async () => {
  const empty = { status: 402, body: { type: 'error', error: { type: 'budget_exceeded', message: 'Insufficient API key budget' } } };
  await withServer({ claude: () => empty }, async ({ server, claude, openAi }) => {
    const first = await server.ask({ model: 'openclaw/intent', input: JSON.stringify({ utterance: 'hello', context: {} }) });
    assert.equal(first.status, 502);
    assert.equal(claude.requests.length, 1);
    const status = (await server.get('/models')).body.backup;
    assert.deepEqual([status.configured, status.ready, status.message], [true, false, 'The Anthropic key has no balance left.']);
    const second = await server.ask({ model: 'openclaw/intent', input: JSON.stringify({ utterance: 'hello', context: {} }) });
    assert.equal(second.status, 502);
    assert.equal(claude.requests.length, 1, 'Claude is not asked again straight away');
    assert.equal(openAi.state.requests, 2, 'and OpenAI is asked instead, since nothing else is left');
    assert.deepEqual(server.events('claude_failed').map((entry) => entry.status), [402]);
  });
  await withServer({ claude: (request) => (isRouter(request) ? empty : { text: said('unreachable') }) }, async ({ server, claude }) => {
    assert.equal((await server.ask(desktopRequest({ modelTier: 'astra' }))).status, 502);
    assert.equal(claude.requests.length, 1, 'the stronger model is not tried with a key Haiku was just refused on');
  });
});

test('a refusal is about one request: it fails that request and does not rest Claude', async () => {
  await withServer({ claude: (request, count) => (count === 1 ? { refusal: 'cyber' } : { text: INTENT }) }, async ({ server, claude }) => {
    assert.equal((await server.ask({ model: 'openclaw/intent', input: JSON.stringify({ utterance: 'hello', context: {} }) })).status, 502);
    const next = await server.ask({ model: 'openclaw/intent', input: JSON.stringify({ utterance: 'hello', context: {} }) });
    assert.equal(next.status, 200, JSON.stringify(next.body));
    assert.equal(claude.requests.length, 2);
    assert.deepEqual(server.events('claude_failed'), []);
  });
});

test('without an Anthropic key nothing changes: OpenAI fails as it did before, and works as it did before', async () => {
  await withServer({ claude: null }, async ({ server, openAi }) => {
    const started = Date.now();
    const failed = await server.ask({ model: 'openclaw/interrupt', input: 'stop talking' });
    assert.equal(failed.status, 502);
    assert.match(failed.body.error, /could not answer/);
    assert.ok(Date.now() - started < 3500, 'it fails at once, with no wait added');
    const status = (await server.get('/models')).body.backup;
    assert.deepEqual([status.configured, status.ready], [false, false]);
    assert.match(status.message, /No Anthropic key is saved in Windows/);
    openAi.set([{ text: INTERRUPT }]);
    const worked = await server.ask({ model: 'openclaw/interrupt', input: 'stop talking' });
    assert.equal(worked.status, 200, 'with no backup OpenAI is never skipped');
    assert.equal((await server.post('/backup/test')).body.ok, false);
  });
});

test('ANTHROPIC_AUTH_TOKEN is sent as a bearer token and ANTHROPIC_API_KEY as an API key', async () => {
  await withServer({ key: null, env: { ANTHROPIC_AUTH_TOKEN: 'gateway-token' } }, async ({ server, claude }) => {
    assert.equal((await server.ask({ model: 'openclaw/intent', input: JSON.stringify({ utterance: 'hello', context: {} }) })).status, 200);
    assert.equal(claude.requests[0].headers.authorization, 'Bearer gateway-token');
    assert.equal(claude.requests[0].headers['x-api-key'], undefined);
    assert.equal((await server.get('/models')).body.backup.source, 'process');
  });
});

test('Settings can test the backup with one short request', async () => {
  await withServer({ openAi: [{ text: INTENT }], claude: () => ({ text: 'ready' }) }, async ({ server, claude }) => {
    const result = (await server.post('/backup/test')).body;
    assert.deepEqual([result.ok, result.model, result.backup.ready], [true, 'Claude Haiku 5.5', true]);
    assert.equal(claude.requests[0].body.model, 'claude-haiku-5-5');
    assert.equal(claude.requests[0].body.output_config.effort, 'low');
    claude.set(() => ({ status: 401, body: { type: 'error', error: { type: 'authentication_error', message: 'invalid x-api-key' } } }));
    const refused = (await server.post('/backup/test')).body;
    assert.deepEqual([refused.ok, refused.status, refused.error], [false, 401, 'The Anthropic key was rejected.']);
    claude.set(() => ({ text: 'ready' }));
    assert.equal((await server.post('/backup/test')).body.backup.ready, true, 'a test that works puts the backup back in use at once');
  });
});

test('operating the computer: one tool call per step on Claude, with the screenshot, and no reasoning kept between steps', async () => {
  const step = (desktop, user) => ({ model: 'openclaw/computer', selectedModel: 'gpt-6-luna', user, image: PNG,
    input: JSON.stringify({ question: 'Open Notepad', app: null, readOnly: false, imageSize: [1, 1], previousSteps: [], desktop }) });
  await withServer({ claude: (request, count) => (count === 1 ? { tool: 'wait', input: { message: 'Waiting for Notepad.' } }
    : { tool: 'finish_task', input: { outcome: 'completed', message: 'Notepad is open.' } }) }, async ({ server, claude }) => {
    const first = await server.ask(step({}, 'research-job1'));
    assert.equal(first.status, 200, JSON.stringify(first.body));
    assert.deepEqual(JSON.parse(textOf(first)), { message: 'Waiting for Notepad.', action: 'wait' });
    assert.deepEqual([first.body.model, first.body.backup], ['anthropic/claude-haiku-5-5', true]);
    const sent = claude.requests[0].body;
    assert.deepEqual(sent.tool_choice, { type: 'any', disable_parallel_tool_use: true }, 'Haiku must act, one action at a time');
    assert.ok(sent.tools.some((tool) => tool.name === 'finish_task') && sent.tools.length > 5);
    const image = sent.messages.at(-1).content.find((block) => block.type === 'image');
    assert.deepEqual([image.source.type, image.source.media_type, image.source.data], ['base64', 'image/png', PNG]);
    const second = await server.ask(step({ lastAction: { action: 'wait', result: { ok: true } } }, 'research-job1'));
    assert.equal(second.status, 200, JSON.stringify(second.body));
    assert.equal(JSON.parse(textOf(second)).action, 'done');
    const history = JSON.stringify(claude.requests[1].body.messages);
    assert.match(history, /"type":"tool_result"/);
    assert.doesNotMatch(history, /"type":"thinking"/);
  });
  const answers = [{ text: '{"model":"opus","effort":"low"}' }, { thinking: true, text: 'I will click the Start button.' },
    { thinking: true, tool: 'finish_task', input: { outcome: 'completed', message: 'Notepad is open.' } }];
  await withServer({ claude: (request, count) => answers[count - 1] }, async ({ server, claude }) => {
    const reply = await server.ask({ ...step({}, 'research-job2'), modelTier: 'sol' });
    assert.equal(reply.status, 200, JSON.stringify(reply.body));
    assert.equal(JSON.parse(textOf(reply)).action, 'done');
    assert.deepEqual([reply.body.model, reply.body.reasoning], ['anthropic/claude-opus-5-5', 'low']);
    const [decision, described, reminded] = claude.requests;
    assert.match(JSON.parse(decision.body.messages[0].content.find((block) => block.type === 'text').text).task, /operate Windows apps/);
    assert.deepEqual(described.body.tool_choice, { type: 'auto', disable_parallel_tool_use: true });
    assert.match(systemOf(described), /Reply with exactly one tool call and no other text\.$/);
    assert.match(JSON.stringify(reminded.body.messages.at(-1)), /You replied without calling a tool/);
    assert.equal(systemOf(reminded), systemOf(described));
  });
});
