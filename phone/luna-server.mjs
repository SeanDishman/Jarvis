import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { timingSafeEqual } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { createLlmRuntime } from '../.runtime/openclaw/node_modules/@openclaw/ai/dist/index.mjs';
import { registerBuiltInApiProviders } from '../.runtime/openclaw/node_modules/@openclaw/ai/dist/providers.mjs';
import { desktopContext, toolsFor, executeDesktopTool, jarvisSettingsContext } from '../desktop/llm-tools.mjs';
import { ModelSettings, DEFAULT_MODEL } from '../desktop/models.mjs';
import { acknowledgementContext } from '../desktop/acknowledgement-prompts.mjs';
import { learningReminderPrompt, lockInContext } from '../desktop/lock-in-prompts.mjs';
import { stampOwnerMessage, timingNote } from '../desktop/conversation-time.mjs';
import { welcomeGreetingPrompt } from '../desktop/welcome-prompts.mjs';
import { questionReminderPrompt } from '../desktop/question-prompts.mjs';
import { interruptionContext, interruptionFormat } from '../desktop/interruption-prompts.mjs';
import { intentContext, intentFormat } from '../desktop/intent-prompts.mjs';
import { screenContext } from '../desktop/screen-prompts.mjs';
import { processContext } from '../desktop/process-prompts.mjs';
import { plannerContext, summarizerContext, researchFormats } from '../desktop/research-prompts.mjs';
import { computerTools, computerToolContext, computerMessages, computerAction } from '../desktop/computer-tools.mjs';
import { finalResponseText, desktopReplyFormat, desktopReply } from '../desktop/model-output.mjs';
import { ClaudeAccess, ModelFallback, claudeOptions, forClaude, forStorage, mustAskForTool, windowsUserEnv, ONE_TOOL, TOOL_FIRST, TOOL_NUDGE } from '../desktop/claude-fallback.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const config = JSON.parse((await fs.readFile(path.join(root, '.openclaw/openclaw.json'), 'utf8')).replace(/^\uFEFF/, ''));
const expectedAuth = Buffer.from('Bearer ' + config.gateway.auth.token);
const runtime = createLlmRuntime();
registerBuiltInApiProviders(runtime.registry);
// JARVIS_CODEX_HOME and JARVIS_OPENAI_BASE_URL exist for the tests, which stand in for OpenAI with a local server.
const codexHome = process.env.JARVIS_CODEX_HOME || path.join(os.homedir(), '.codex');
const modelSettings = new ModelSettings(root, path.join(codexHome, 'models_cache.json'), process.env.JARVIS_OPENAI_BASE_URL || undefined);
// Claude takes over when OpenAI does not answer: Haiku 5.5 first, which hands harder requests to Opus 5.5 or Fable 5.2.
// It uses the standard Anthropic settings: what Jarvis was started with, otherwise what is saved for this Windows
// account. JARVIS_CLAUDE_SAVED_KEY=off ignores the saved ones.
const fallback = new ModelFallback({ runtime, log: entry => console.log(JSON.stringify(entry)),
  access: new ClaudeAccess({ readUserEnv: process.env.JARVIS_CLAUDE_SAVED_KEY === 'off' ? async () => ({}) : windowsUserEnv }),
  enabled: async () => (await modelSettings.settings()).claudeBackup !== false });
const soulPath = path.join(root, 'prompts/SOUL.md');
let soulText = await fs.readFile(soulPath, 'utf8');
const sessions = new Map();
const listenPort = Number(process.env.JARVIS_MODEL_PORT || 18791);
const ttl = 15 * 60 * 1000;
const chatContext = `You are talking with your owner.
You have no live web browsing or device-control tools in this session. Do not claim actions you cannot take
or invent current facts. Explain uncertainty briefly.`;

function reply(res, code, payload) {
  if (res.destroyed) return;
  const body = JSON.stringify(payload);
  res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(body);
}

async function readBody(req) {
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 8388608) throw new Error('Request is too large');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function expireSessions() {
  for (const [key, session] of sessions) {
    if (!session.busy && Date.now() - session.updated > ttl) sessions.delete(key);
  }
}


const server = http.createServer(async (req, res) => {
  const supplied = Buffer.from(req.headers.authorization ?? '');
  if (supplied.length !== expectedAuth.length || !timingSafeEqual(supplied, expectedAuth)) {
    return reply(res, 401, { error: 'Unauthorized' });
  }
  if (req.method === 'GET' && req.url === '/health') {
    const selected = await modelSettings.settings().catch(() => ({}));
    return reply(res, 200, { service: 'jarvis-luna', version: 34, claudeFallback: true, backup: await fallback.status(), compoundActions: true, processGroupTargets: true, backgroundControlClicks: true, processTermination: true, orbMonitorControl: true, semanticToolRouting: true, structuredTaskOutcomes: true, computerToolConversation: true, desktopTools: true, backgroundResearch: true, computerVision: true, generalDesktopControl: true, multiMonitorControl: true, processAwareness: true, memoryUsage: true, personalTools: true, powershellDiagnostics: true, networkAddresses: true,
      model: 'openai/' + selected.conversationModel, searchModel: selected.searchModel, learningReminders: true, welcomeGreetings: true,
      auth: 'chatgpt-codex', runtime: '@openclaw/ai', style: 'British, calm and precise' });
  }
  if (req.url === '/models' && ['GET', 'POST'].includes(req.method)) {
    try {
      if (req.method === 'POST') await modelSettings.save(await readBody(req));
      return reply(res, 200, { ...await modelSettings.describe(), backup: await fallback.status() });
    } catch {
      return reply(res, 400, { error: 'Choose models from your signed-in list. The settings were not saved.' });
    }
  }
  if (req.method === 'POST' && req.url === '/backup/test') {
    // One short Claude request, so Settings can say whether the backup works.
    const result = await fallback.test(AbortSignal.timeout(25000));
    return reply(res, 200, { ...result, backup: await fallback.status() });
  }
  if (req.method === 'POST' && req.url === '/shutdown') {
    if ([...sessions.values()].some(session => session.busy)) return reply(res, 409, { error: 'A response is still running' });
    reply(res, 200, { status: 'stopping' });
    server.close();
    return;
  }
  if (req.method !== 'POST' || !['/v1/responses', '/session/end'].includes(req.url)) {
    return reply(res, 404, { error: 'Not found' });
  }
  let session;
  let controller;
  let research = false;
  let interruption = false;
  let intent = false;
  let computer = false;
  let computerAgent = false;
  let screen = false;
  let processes = false;
  let acknowledgement = false;
  let ephemeral = false;
  let sessionKey;
  let deadline;
  try {
    const body = await readBody(req);
    if (!/^[a-zA-Z0-9_-]{1,80}$/.test(body.user ?? '')) throw new Error('Invalid session');
    if (req.url === '/session/end') {
      sessions.get(body.user)?.controller?.abort();
      sessions.delete(body.user);
      return reply(res, 200, { status: 'cleared' });
    }
    if (!['openclaw/desktop', 'openclaw/research', 'openclaw/interrupt', 'openclaw/intent', 'openclaw/acknowledgement', 'openclaw/lockin', 'openclaw/computer', 'openclaw/screen', 'openclaw/processes', 'openai/gpt-6-luna'].includes(body.model)) throw new Error('Unsupported model');
    const desktop = body.model === 'openclaw/desktop';
    // Spoken lines Jarvis writes for himself: focus reminders and welcome-back greetings built around a quotation the
    // desktop checks on Wikiquote, and a question he asks the owner again.
    const learning = body.model !== 'openclaw/lockin' ? null : body.task === 'learning_reminder' ? learningReminderPrompt()
      : body.task === 'welcome_greeting' ? welcomeGreetingPrompt() : body.task === 'question_reminder' ? questionReminderPrompt() : null;
    ephemeral = desktop && body.ephemeral === true;
    research = body.model === 'openclaw/research';
    intent = body.model === 'openclaw/intent';
    acknowledgement = body.model === 'openclaw/acknowledgement' || body.model === 'openclaw/lockin';
    interruption = intent || body.model === 'openclaw/interrupt';
    screen = body.model === 'openclaw/screen';
    processes = body.model === 'openclaw/processes';
    computer = screen || body.model === 'openclaw/computer';
    computerAgent = computer && !screen;
    if (computer && (typeof body.image !== 'string' || body.image.length > 7000000 || !body.image.startsWith('iVBORw0KGgo'))) throw new Error('Invalid screenshot');
    if (research && !['plan', 'summarize'].includes(body.task)) throw new Error('Invalid research task');
    if (desktop && !/^[a-f0-9]{32}$/.test(body.desktopRequestId ?? '')) throw new Error('Invalid desktop request');
    if (typeof body.input !== 'string' || !body.input.trim() || body.input.length > (research || processes || computer ? 48000 : 6000)) throw new Error('Invalid input');
    let resolved;
    try {
      resolved = await modelSettings.resolve(research ? 'research' : 'conversation', acknowledgement && !learning ? DEFAULT_MODEL : research || computer ? body.selectedModel : undefined,
        interruption || acknowledgement ? 'default' : body.modelTier ?? 'default');
    } catch {
      return reply(res, 400, { error: 'The selected model is unavailable in your signed-in catalog. Open Model settings.' });
    }
    const {model, effort} = resolved;
    expireSessions();
    session = sessions.get(body.user);
    if (session?.busy) return reply(res, 409, { error: 'A response is already running for this call' });
    if (!session) {
      if (sessions.size >= 24) return reply(res, 429, { error: 'Too many assistant sessions' });
      session = { messages: [], updated: Date.now(), busy: false };
      sessions.set(body.user, session);
    }
    session.busy = true;
    sessionKey = body.user;
    controller = new AbortController();
    session.controller = controller;
    let clientGone = false;
    res.on('close', () => {
      if (res.writableEnded) return;
      clientGone = true;
      controller.abort();
    });
    // The ChatGPT sign-in is read when OpenAI is actually asked, so a missing sign-in also moves the request to Claude.
    const openAiAccess = async () => {
      const access = JSON.parse(await fs.readFile(path.join(codexHome, 'auth.json'), 'utf8')).tokens?.access_token;
      if (!access) throw new Error('ChatGPT/Codex sign-in is missing');
      return access;
    };
    const quickReplyTimeout = learning ? 36000 : 4000;
    // A drawing is a whole page of markup, far longer than a spoken reply, so it gets more room and more time.
    const drawing = desktop && Array.isArray(body.capabilities) && body.capabilities.includes('show_visual');
    const limitMs = acknowledgement ? quickReplyTimeout : intent ? 20000 : interruption ? 10000 : research || computer || processes ? 75000 : drawing ? 230000 : desktop ? 110000 : 30000;
    // Each model call goes to OpenAI first. If OpenAI does not answer, this request carries on with Claude.
    const turn = fallback.request({
      kind: intent ? 'intent' : interruption ? 'interrupt' : learning ? 'quote' : acknowledgement ? 'line' : computerAgent ? 'computer'
        : screen ? 'screen' : processes ? 'processes' : research ? 'research' : desktop ? 'desktop' : 'chat',
      tier: resolved.requestedTier, text: body.input, cacheKey: research || computer ? body.user : null,
      limitMs, signal: controller.signal, clientGone: () => clientGone });
    // One clock reading per request: Claude's reasoning stays valid only while the prompt before it is unchanged.
    const now = new Date();
    const userMessage = { role: 'user', content: computer ? [{type: 'text', text: body.input},
      {type: 'image', data: body.image, mimeType: 'image/png'}] : body.input, timestamp: now.getTime() };
    deadline = setTimeout(() => controller.abort(), limitMs);
    if (computerAgent) {
      const messages = computerMessages(session, userMessage);
      const systemPrompt = computerToolContext + '\nCurrent local date and time: ' + now.toString();
      let response;
      try {
        const step = (extra = []) => turn.complete(
          async () => [model, {systemPrompt, messages, tools: computerTools}, {apiKey: await openAiAccess(), transport: 'sse', reasoning: effort, maxTokens: 3000,
            timeoutMs: 70000, signal: controller.signal, sessionId: body.user,
            onPayload: payload => ({...payload, parallel_tool_calls: false, tool_choice: 'required'})}],
          route => [{systemPrompt: systemPrompt + (mustAskForTool(route) ? ONE_TOOL : ''), messages: forClaude([...messages, ...extra]), tools: computerTools},
            claudeOptions({route, toolChoice: 'required', replyTokens: 3000, timeoutMs: 70000, signal: controller.signal, sessionId: body.user})]);
        response = await step();
        // Opus 5.5 and Fable 5.2 cannot be made to call a tool, so if one of them answered in words, ask once more.
        if (turn.used && !response.content.some(part => part.type === 'toolCall')) response = await step([response, {role: 'user', content: TOOL_NUDGE}]);
      } finally {
        clearTimeout(deadline);
      }
      const selected = computerAction(response);
      session.messages = forStorage([...messages, response]);
      session.pendingComputerCall = selected.call;
      session.updated = Date.now();
      session.computerFinished = ['done', 'blocked'].includes(selected.action.action);
      return reply(res, 200, {status: 'completed', model: turn.used?.model ?? 'openai/' + model.id, reasoning: turn.used?.effort ?? effort,
        requestedTier: resolved.requestedTier, modelFallback: turn.used ? false : resolved.fallback, backup: Boolean(turn.used),
        output: [{type: 'message', role: 'assistant', content: [{type: 'output_text', text: JSON.stringify(selected.action)}]}]});
    }
    const soul = soulText = await fs.readFile(soulPath, 'utf8').catch(() => soulText);
    const context = learning?.prompt ?? (body.model === 'openclaw/lockin' ? lockInContext : acknowledgement ? acknowledgementContext : processes ? processContext : screen ? screenContext : intent ? intentContext : interruption ? interruptionContext : research ? (body.task === 'plan' ? plannerContext : summarizerContext) : desktop ? desktopContext : chatContext);
    const settingsContext = desktop ? jarvisSettingsContext(body.jarvisSettings, await modelSettings.settings().catch(() => null)) : '';
    const messages = [...(research || interruption || computer || processes || acknowledgement || ephemeral ? [] : session.messages), userMessage];
    const timed = desktop;
    const tools = desktop ? toolsFor(body.capabilities) : [];
    const sourceLinks = [];
    let response;
    const format = learning?.format ?? (screen ? undefined : intent ? intentFormat : interruption ? interruptionFormat : research ? researchFormats[body.task] : desktop ? desktopReplyFormat : undefined);
    const replyTokens = learning ? 2000 : acknowledgement ? 120 : intent ? 2400 : interruption ? 600 : computer ? 2000 : processes ? 600 : research ? (body.task === 'plan' ? 450 : 1100) : drawing ? 16000 : desktop ? 2400 : 220;
    const timeoutMs = acknowledgement ? quickReplyTimeout : drawing ? 200000 : research || computer || processes || desktop ? 70000 : 30000;
    const systemPrompt = (handler, level) => (research || interruption || computer || processes || acknowledgement ? '' : soul + '\n') + context + settingsContext
      + '\nCurrent local date and time: ' + now.toString() + (timed ? timingNote : '') + '\nModel handling this request: ' + handler + '. Reasoning effort: ' + (level || 'default') + '.';
    const stamped = () => timed ? messages.map(message => stampOwnerMessage(message, now.getTime())) : messages;
    // What only steers this request, such as a reminder to use a tool, is not kept in the conversation.
    const passing = new Set();
    try {
      for (let round = 0; round < 5; round++) {
        response = await turn.complete(
          async () => [model, {systemPrompt: systemPrompt(model.id, effort), messages: stamped(), tools: round < 4 ? tools : []}, {
            apiKey: await openAiAccess(), transport: 'sse', reasoning: effort,
            maxTokens: learning || acknowledgement || intent || interruption || drawing ? replyTokens : effort === 'high' ? 8192 : effort === 'medium' ? 4096 : replyTokens,
            responseFormat: format,
            ...(desktop ? {onPayload: payload => ({...payload, parallel_tool_calls:false,
              tool_choice:round === 0 && tools.length ? 'required' : 'auto'})} : {}),
            timeoutMs, signal: controller.signal, sessionId: body.user,
          }],
          // The prompt and tool list stay the same on every round, because Claude's reasoning from an earlier round is
          // only accepted while what came before it is unchanged. The last round rules tools out instead of removing them.
          route => [{systemPrompt: systemPrompt(route.model.id, route.effort) + (tools.length && mustAskForTool(route) ? TOOL_FIRST : ''),
            messages: forClaude(stamped()), tools},
            claudeOptions({route, format, replyTokens, timeoutMs, signal: controller.signal, sessionId: body.user,
              toolChoice: !tools.length ? undefined : round >= 4 ? 'none' : round === 0 ? 'required' : undefined})]);
        messages.push(response);
        const calls = response.content.filter(part => part.type === 'toolCall');
        if (!calls.length) {
          // Opus 5.5 and Fable 5.2 cannot be made to call a tool. If tools were selected and neither was used, ask once.
          if (round === 0 && tools.length && turn.used && mustAskForTool(turn.route) && !passing.size) {
            const nudge = {role: 'user', content: TOOL_NUDGE};
            messages.push(nudge);
            passing.add(response).add(nudge);
            continue;
          }
          break;
        }
        for (const call of calls) {
          let result;
          if (!tools.some(tool => tool.name === call.name) || round >= 4) {
            result = { ok: false, error: 'This tool is unavailable for the current request' };
          } else {
            try {
              result = await executeDesktopTool(call, body.desktopRequestId, expectedAuth.toString(), controller.signal);
            } catch {
              result = { ok: false, error: 'The desktop tool timed out or is unavailable' };
            }
          }
          if (call.name === 'search_web' && result.ok) sourceLinks.push(...(result.results ?? []).map(({title,url}) => ({title,url})));
          if (call.name === 'read_webpage' && result.ok) sourceLinks.push({title: result.title, url: result.url});
          if (call.name === 'web_search_status' && result.ok) sourceLinks.push(...(result.sources ?? []).filter(row => row.url));
          if (call.name.startsWith('browser_') && result.ok && result.url) sourceLinks.push({title: result.title || 'Browser page', url: result.url});
          if (call.name === 'get_weather' && result.ok) sourceLinks.push({title: result.source, url: result.url});
          if (call.name === 'get_briefing' && result.weather?.ok) sourceLinks.push({title: result.weather.source, url: result.weather.url});
          messages.push({ role: 'toolResult', toolCallId: call.id, toolName: call.name,
            content: [{ type: 'text', text: JSON.stringify(result).slice(0,26000) }], isError: result.ok === false, timestamp: Date.now() });
        }
      }
    } finally {
      clearTimeout(deadline);
    }
    const outcome = desktop ? desktopReply(response) : null;
    const answer = outcome?.reply ?? finalResponseText(response);
    if (!answer) throw new Error('The model returned no response text');
    if (!research && !interruption && !computer && !processes && !acknowledgement && !ephemeral) {
      session.turns ??= [];
      session.turns.push(forStorage(messages.slice(session.messages.length).filter(message => !passing.has(message))));
      session.turns = session.turns.slice(-6);
      session.messages = session.turns.flat();
    }
    session.updated = Date.now();
    // backup tells the desktop that Claude answered because OpenAI did not.
    reply(res, 200, { status: 'completed', model: turn.used?.model ?? 'openai/' + model.id, reasoning: turn.used?.effort ?? effort,
      ...(outcome ? {taskStatus:outcome.status} : {}),
      requestedTier: resolved.requestedTier, modelFallback: turn.used ? false : resolved.fallback, backup: Boolean(turn.used), sources: sourceLinks.slice(0,8),
      output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: answer }] }] });
  } catch (error) {
    console.error(JSON.stringify({ event: 'model_request_failed', type: error.name }));
    reply(res, 502, { error: 'The selected model could not answer. Check Codex sign-in, model access and connection.' });
  } finally {
    clearTimeout(deadline);
    if (session && controller && session.controller === controller) {
      session.busy = false; session.controller = undefined;
      if ((research || interruption || computer && (!computerAgent || session.computerFinished) || processes || acknowledgement || ephemeral) && sessions.get(sessionKey) === session) sessions.delete(sessionKey);
    }
  }
});
server.requestTimeout = 120000;
server.headersTimeout = 10000;
server.on('error', error => { console.error(error.code || 'Server error'); process.exitCode = 1; });
server.listen(listenPort, '127.0.0.1', () => console.log(JSON.stringify({ event: 'luna_service_ready', port: listenPort })));
setInterval(expireSessions, 60000).unref();
