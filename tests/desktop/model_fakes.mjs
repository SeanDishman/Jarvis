// Stand-ins for the services the model server talks to, so its fallback can be tested without real accounts:
// OpenAI's ChatGPT backend, an Anthropic Messages endpoint and the desktop tool service. Each listens on a free
// local port, records what it was sent and answers from a script the test sets.
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const NODE = process.execPath;

function listen(handler) {
  return new Promise((resolve) => {
    const sockets = new Set();
    const server = http.createServer(handler);
    server.on('connection', (socket) => {
      sockets.add(socket);
      socket.on('close', () => sockets.delete(socket));
    });
    server.listen(0, '127.0.0.1', () => resolve({
      port: server.address().port,
      close: () => new Promise((done) => {
        for (const socket of sockets) socket.destroy();
        server.close(() => done());
      }),
    }));
  });
}

async function bodyOf(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}

function sse(res, events, { named = false } = {}) {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
  for (const event of events) res.write((named ? `event: ${event.type}\n` : '') + `data: ${JSON.stringify(event)}\n\n`);
  res.end();
}

// ---------------------------------------------------------------- OpenAI

function openAiEvents(step, serial) {
  const id = 'resp_' + serial;
  const usage = { input_tokens: 12, output_tokens: 6, total_tokens: 18, input_tokens_details: { cached_tokens: 0 }, output_tokens_details: { reasoning_tokens: 0 } };
  const item = step.tool
    ? { id: 'fc_' + serial, type: 'function_call', status: 'completed', call_id: 'call_' + serial, name: step.tool, arguments: JSON.stringify(step.input ?? {}) }
    : { id: 'msg_' + serial, type: 'message', status: 'completed', role: 'assistant', content: [{ type: 'output_text', text: step.text, annotations: [] }] };
  const response = { id, object: 'response', status: 'completed', model: 'gpt-test', output: [item], usage };
  const events = [{ type: 'response.created', response: { id, object: 'response', status: 'in_progress', output: [] } }];
  if (step.tool) {
    events.push({ type: 'response.output_item.added', output_index: 0, item: { ...item, status: 'in_progress', arguments: '' } },
      { type: 'response.function_call_arguments.delta', item_id: item.id, output_index: 0, delta: item.arguments },
      { type: 'response.function_call_arguments.done', item_id: item.id, output_index: 0, arguments: item.arguments });
  } else {
    events.push({ type: 'response.output_item.added', output_index: 0, item: { ...item, status: 'in_progress', content: [] } },
      { type: 'response.content_part.added', item_id: item.id, output_index: 0, content_index: 0, part: { type: 'output_text', text: '', annotations: [] } },
      { type: 'response.output_text.delta', item_id: item.id, output_index: 0, content_index: 0, delta: step.text },
      { type: 'response.output_text.done', item_id: item.id, output_index: 0, content_index: 0, text: step.text },
      { type: 'response.content_part.done', item_id: item.id, output_index: 0, content_index: 0, part: item.content[0] });
  }
  events.push({ type: 'response.output_item.done', output_index: 0, item }, { type: 'response.completed', response });
  return events;
}

/**
 * script entries: {text}, {tool, input}, either with delayMs to finish late, {status, body} for an error, 'hang'
 * (never answers) or 'stall' (answers with headers and then nothing). The last entry repeats.
 */
export async function fakeOpenAi(script = []) {
  const state = { script: [...script], requests: 0 };
  const server = await listen(async (req, res) => {
    await bodyOf(req);
    state.requests += 1;
    const step = state.script.length > 1 ? state.script.shift() : state.script[0];
    if (step === 'hang') return;
    if (step === 'stall') return res.writeHead(200, { 'Content-Type': 'text/event-stream' }), res.flushHeaders();
    if (!step || step.status) {
      res.writeHead(step?.status ?? 500, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(step?.body ?? { error: { message: 'The server had an error', type: 'server_error' } }));
    }
    const events = openAiEvents(step, state.requests);
    if (!step.delayMs) return sse(res, events);
    // Starts answering at once and takes its time to finish, like a long reply.
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    const line = (event) => `data: ${JSON.stringify(event)}\n\n`;
    res.write(line(events[0]));
    await new Promise((resolve) => setTimeout(resolve, step.delayMs));
    for (const event of events.slice(1)) res.write(line(event));
    res.end();
  });
  return { ...server, state, url: `http://127.0.0.1:${server.port}`, set: (next) => { state.script = [...next]; } };
}

// ---------------------------------------------------------------- Claude

function claudeEvents(step, model, serial) {
  const blocks = [];
  if (step.thinking) blocks.push({ start: { type: 'thinking', thinking: '', signature: '' }, deltas: [{ type: 'signature_delta', signature: 'sig_' + serial }] });
  if (step.text !== undefined) blocks.push({ start: { type: 'text', text: '' }, deltas: [{ type: 'text_delta', text: step.text }] });
  if (step.tool) blocks.push({ start: { type: 'tool_use', id: 'toolu_' + serial, name: step.tool, input: {} },
    deltas: [{ type: 'input_json_delta', partial_json: JSON.stringify(step.input ?? {}) }] });
  const events = [{ type: 'message_start', message: { id: 'msg_' + serial, type: 'message', role: 'assistant', model, content: [],
    stop_reason: null, stop_sequence: null, usage: { input_tokens: 20, output_tokens: 1 } } }];
  blocks.forEach((block, index) => {
    events.push({ type: 'content_block_start', index, content_block: block.start });
    for (const delta of block.deltas) events.push({ type: 'content_block_delta', index, delta });
    events.push({ type: 'content_block_stop', index });
  });
  const stop = step.refusal ? 'refusal' : step.tool ? 'tool_use' : 'end_turn';
  events.push({ type: 'message_delta', delta: { stop_reason: stop, stop_sequence: null, ...(step.refusal ? { stop_details: { type: 'refusal', category: step.refusal } } : {}) },
    usage: { output_tokens: 9 } }, { type: 'message_stop' });
  return events;
}

/**
 * answer(request) returns {text}, {tool, input}, {thinking: true, ...}, {refusal: category} or {status, body}.
 * request is {model, body, headers}; every request is kept in .requests.
 */
export async function fakeClaude(answer) {
  const requests = [];
  const state = { answer };
  const server = await listen(async (req, res) => {
    const body = JSON.parse((await bodyOf(req)).toString('utf8') || '{}');
    const request = { path: req.url, model: body.model, body, headers: req.headers };
    requests.push(request);
    const step = await state.answer(request, requests.length);
    if (step === 'hang') return;
    if (step.status) {
      res.writeHead(step.status, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify(step.body ?? { type: 'error', error: { type: 'api_error', message: 'Service failed' } }));
    }
    sse(res, claudeEvents(step, body.model, requests.length), { named: true });
  });
  return { ...server, requests, url: `http://127.0.0.1:${server.port}`, set: (next) => { state.answer = next; } };
}

// ---------------------------------------------------------------- desktop tools

export async function fakeDesktop(result = { ok: true }) {
  const calls = [];
  const server = await listen(async (req, res) => {
    const call = JSON.parse((await bodyOf(req)).toString('utf8'));
    calls.push(call);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(typeof result === 'function' ? result(call) : result));
  });
  return { ...server, calls };
}

// ---------------------------------------------------------------- the model server under test

function freePort() {
  return new Promise((resolve) => {
    const probe = http.createServer();
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

async function codexHome(folder) {
  await fs.mkdir(folder, { recursive: true });
  const payload = Buffer.from(JSON.stringify({ 'https://api.openai.com/auth': { chatgpt_account_id: 'account-for-tests' } })).toString('base64url');
  await fs.writeFile(path.join(folder, 'auth.json'), JSON.stringify({ tokens: { access_token: `e30.${payload}.signature` } }));
  let chosen = {};
  try {
    chosen = JSON.parse((await fs.readFile(path.join(ROOT, 'desktop/model-settings.json'), 'utf8')).replace(/^﻿/, ''));
  } catch {}
  const slugs = new Set(['gpt-6-luna', 'gpt-6.2-sol', 'gpt-6.2-astra', chosen.conversationModel, chosen.searchModel].filter((slug) => slug && slug !== 'same'));
  await fs.writeFile(path.join(folder, 'models_cache.json'), JSON.stringify({ models: [...slugs].map((slug) => ({
    slug, display_name: slug, visibility: 'list', context_window: 272000,
    supported_reasoning_levels: [{ effort: 'low' }, { effort: 'medium' }, { effort: 'high' }] })) }));
}

/**
 * Start phone/luna-server.mjs against the given stand-ins. Leave claude out to run with no Anthropic key at all.
 * Returns ask(body) for /v1/responses, get(route), post(route, body), the server's log lines and stop().
 */
export async function startModelServer({ openAi, claude, desktop, key = 'sk-ant-api03-test-key', env = {} }) {
  const folder = await fs.mkdtemp(path.join(ROOT, '.cache/fallback-test-'));
  await codexHome(folder);
  const port = await freePort();
  const token = JSON.parse((await fs.readFile(path.join(ROOT, '.openclaw/openclaw.json'), 'utf8')).replace(/^﻿/, '')).gateway.auth.token;
  const settings = { ...process.env };
  for (const name of ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL']) delete settings[name];
  // The real key saved in Windows is never used by a test: with no stand-in, there is no key at all.
  Object.assign(settings, { JARVIS_MODEL_PORT: String(port), JARVIS_CODEX_HOME: folder, JARVIS_OPENAI_BASE_URL: openAi.url,
    JARVIS_DESKTOP_PORT: String(desktop?.port ?? 9), JARVIS_CLAUDE_SAVED_KEY: 'off' },
    claude ? { ANTHROPIC_BASE_URL: claude.url, ...(key ? { ANTHROPIC_API_KEY: key } : {}) } : {}, env);
  const child = spawn(NODE, [path.join(ROOT, 'phone/luna-server.mjs')], { cwd: ROOT, env: settings, stdio: ['ignore', 'pipe', 'pipe'] });
  const log = [];
  let output = '';
  const ready = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('The model server did not start: ' + output.slice(-800))), 20000);
    const read = (chunk) => {
      output += chunk;
      for (const line of String(chunk).split(/\r?\n/)) {
        try {
          const entry = JSON.parse(line);
          log.push(entry);
          if (entry.event === 'luna_service_ready') {
            clearTimeout(timer);
            resolve();
          }
        } catch {}
      }
    };
    child.stdout.on('data', read);
    child.stderr.on('data', read);
    child.once('exit', (code) => reject(new Error(`The model server exited with ${code}: ${output.slice(-800)}`)));
  });
  await ready;
  const call = async (route, body, method = body === undefined ? 'GET' : 'POST', signal) => {
    for (let attempt = 0; ; attempt += 1) {
      try {
        const response = await fetch(`http://127.0.0.1:${port}${route}`, { method, signal,
          headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json', Connection: 'close' }, body: body === undefined ? undefined : JSON.stringify(body) });
        return { status: response.status, body: await response.json() };
      } catch (error) {
        // On a busy PC a local connection occasionally fails before anything was sent. Nothing ran, so try again.
        if (error?.cause?.syscall !== 'connect' || attempt >= 3) throw error;
      }
    }
  };
  let serial = 0;
  return {
    port, log,
    output: () => output,
    running: () => child.exitCode === null,
    get: (route) => call(route),
    post: (route, body = {}) => call(route, body),
    ask: (body, signal) => call('/v1/responses', { user: 'test-' + (++serial), ...body }, 'POST', signal),
    events: (name) => log.filter((entry) => entry.event === name),
    stop: async () => {
      child.removeAllListeners('exit');
      child.kill();
      await new Promise((resolve) => child.once('exit', resolve));
      await fs.rm(folder, { recursive: true, force: true });
    },
  };
}

export function textOf(reply) {
  return reply.body.output?.[0]?.content?.[0]?.text;
}
