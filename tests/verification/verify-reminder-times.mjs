// Checks with the real model that spoken reminder requests become the right create_reminder call: a delay, a clock
// time on the right day, or a morning that comes with the greeting. The model server runs on its own port against a
// stand-in desktop that only records the calls, so no real reminder is saved and the running Jarvis is not touched.
// Usage: node tests/verification/verify-reminder-times.mjs
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const TOOLS = ['create_reminder', 'list_reminders', 'change_reminder'];

const day = (offset) => {
  const moment = new Date();
  moment.setDate(moment.getDate() + offset);
  return moment.toLocaleDateString('en-CA');
};
const later = (hours) => new Date(new Date().setHours(new Date().getHours() + hours, 0, 0, 0));
// A clock time that is still to come today, so "at <time>" means today.
const evening = later(3);
const spokenHour = (moment) => moment.toLocaleTimeString('en-US', { hour: 'numeric' }).replace(/\s/gu, ' ').toLowerCase();
const monday = (() => {
  const moment = new Date();
  moment.setDate(moment.getDate() + ((8 - moment.getDay()) % 7 || 7));
  return moment.toLocaleDateString('en-CA');
})();

const CASES = [
  ['Jarvis, remind me in the morning to put the cigarette lighter in the car', { morning_of: day(1) }],
  ['Jarvis, remind me tomorrow to call the bank', { morning_of: day(1) }],
  ['Jarvis, remind me the day after tomorrow to renew my license', { morning_of: day(2) }],
  [`Jarvis, remind me at ${spokenHour(evening)} to take out the trash`, { at: [day(0), evening.getHours()] }],
  ['Jarvis, remind me tomorrow at 8 a.m. to stretch', { at: [day(1), 8] }],
  ['Jarvis, remind me on Monday at 3 p.m. to email the landlord', { at: [monday, 15] }],
  ['Jarvis, remind me in 20 minutes to check the oven', { delay: 1200 }],
];

function listen(handler) {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

async function body(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function result(call) {
  const args = call.arguments ?? {};
  if (call.name !== 'create_reminder') return { ok: true, reminders: [], local_time: new Date().toISOString() };
  const due = args.morning_of ? `${args.morning_of}T05:00:00` : args.due_at || new Date(Date.now() + (args.delay_seconds || 0) * 1000).toISOString();
  return { ok: true, reminder: { id: crypto.randomBytes(5).toString('hex'), text: args.text, due_at: due, repeat: args.repeat,
    state: 'scheduled', ...(args.morning_of ? { morning: true } : {}) },
    heard: args.morning_of ? 'He hears it with the greeting he gets when he first comes to the computer that day, or at his first input after the morning begins.'
      : 'He hears it at his first mouse or keyboard input after this time; if he is away, it waits until he is back.' };
}

function judge(args, expected) {
  if (!args) return 'no create_reminder call';
  const set = ['due_at', 'delay_seconds', 'morning_of'].filter((key) => args[key]);
  if (set.length !== 1) return `expected exactly one of due_at, delay_seconds, morning_of; got ${set.join(', ') || 'none'}`;
  if (expected.morning_of) return args.morning_of === expected.morning_of ? '' : `morning_of ${args.morning_of}, wanted ${expected.morning_of}`;
  if (expected.delay) return Math.abs(args.delay_seconds - expected.delay) <= 60 ? '' : `delay ${args.delay_seconds}, wanted ${expected.delay}`;
  const when = new Date(args.due_at);
  if (!/[+-]\d\d:\d\d$|Z$/.test(args.due_at || '') || Number.isNaN(when.getTime())) return `due_at ${args.due_at} has no offset`;
  const [date, hour] = expected.at;
  return when.toLocaleDateString('en-CA') === date && when.getHours() === hour && when.getMinutes() === 0 ? ''
    : `due_at ${args.due_at} is ${when.toString()}, wanted ${date} ${hour}:00`;
}

const calls = [];
const desktop = await listen(async (req, res) => {
  const call = await body(req);
  calls.push(call);
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(result(call)));
});
const modelPort = await new Promise((resolve) => {
  const probe = http.createServer().listen(0, '127.0.0.1', () => {
    const { port } = probe.address();
    probe.close(() => resolve(port));
  });
});
const token = JSON.parse((await fs.readFile(path.join(ROOT, '.openclaw/openclaw.json'), 'utf8')).replace(/^\uFEFF/, '')).gateway.auth.token;
const child = spawn(process.execPath, [path.join(ROOT, 'phone/luna-server.mjs')], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, JARVIS_MODEL_PORT: String(modelPort), JARVIS_DESKTOP_PORT: String(desktop.port) } });
let output = '';
await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('The model server did not start: ' + output.slice(-600))), 30000);
  const read = (chunk) => {
    output += chunk;
    if (output.includes('luna_service_ready')) {
      clearTimeout(timer);
      resolve();
    }
  };
  child.stdout.on('data', read);
  child.stderr.on('data', read);
  child.once('exit', (code) => reject(new Error(`The model server exited with ${code}: ${output.slice(-600)}`)));
});

let failures = 0;
try {
  for (const [text, expected] of CASES) {
    const before = calls.length;
    const started = Date.now();
    const response = await fetch(`http://127.0.0.1:${modelPort}/v1/responses`, {
      method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json', Connection: 'close' },
      body: JSON.stringify({ model: 'openclaw/desktop', user: 'verify-reminders-' + crypto.randomBytes(4).toString('hex'),
        input: text, desktopRequestId: crypto.randomBytes(16).toString('hex'), capabilities: TOOLS, modelTier: 'default' }),
    });
    const reply = await response.json();
    const made = calls.slice(before).filter((call) => call.name === 'create_reminder');
    const problem = response.status !== 200 ? `status ${response.status}: ${JSON.stringify(reply).slice(0, 200)}`
      : made.length !== 1 ? `${made.length} create_reminder calls` : judge(made[0].arguments, expected);
    if (problem) failures += 1;
    let said = reply.output?.[0]?.content?.[0]?.text ?? '';
    try {
      said = JSON.parse(said).reply ?? said;
    } catch {}
    console.log(`${problem ? 'FAIL' : 'ok  '} ${text}\n     -> ${JSON.stringify(made[0]?.arguments ?? null)}`
      + `\n     model ${reply.model}, ${Math.round((Date.now() - started) / 100) / 10}s, says: ${said}${problem ? `\n     PROBLEM: ${problem}` : ''}`);
  }
} finally {
  child.kill();
  desktop.server.close();
}
console.log(`${CASES.length - failures}/${CASES.length} reminder requests became the right call`);
process.exitCode = failures ? 1 : 0;
