import assert from 'node:assert/strict';
import test from 'node:test';
import { REST, VISIT_BOX, VISIT_SCALE, WELCOME_SCALE, boxFor, placement, plan, sample, totalMs, visitScale }
  from '../../desktop/ui/renderer/lib/motion.mjs';
import { learningReminderPrompt } from '../../desktop/lock-in-prompts.mjs';
import { welcomeGreetingPrompt } from '../../desktop/welcome-prompts.mjs';
import { desktopTools } from '../../desktop/llm-tools.mjs';

test('lock-in keeps its window while a welcome visit gets room to grow bigger', () => {
  assert.equal(boxFor(), VISIT_BOX);
  assert.equal(boxFor(VISIT_SCALE), VISIT_BOX);
  assert.ok(WELCOME_SCALE > VISIT_SCALE + 0.5);
  assert.ok(boxFor(WELCOME_SCALE) > VISIT_BOX);
});

test('the welcome glide reaches full size and its pop is never cut off by the window', () => {
  const box = boxFor(WELCOME_SCALE);
  const held = box / 2;
  // From his corner on the second monitor to the middle of the main monitor.
  const spec = plan({ id: 1, from: { x: 3770, y: 1000 }, to: { x: 960, y: 516 },
                      offset: { from: { x: REST, y: REST }, to: { x: held, y: held } }, scale: { from: 1, to: WELCOME_SCALE } });
  let peak = 0;
  for (let elapsed = 0; elapsed <= totalMs(spec) + 1; elapsed += 1) {
    const state = sample(spec, elapsed);
    const place = placement(state, box);
    peak = Math.max(peak, place.scale);
    if (state.arrived) assert.ok(Math.abs(place.scale - state.scale) < 1e-9, `clipped at ${elapsed}ms`);
  }
  assert.ok(peak > WELCOME_SCALE, `the pop overshoots before settling: ${peak}`);
  const end = placement(sample(spec, totalMs(spec)), box);
  assert.ok(Math.abs(end.scale - WELCOME_SCALE) < 1e-9);
  assert.deepEqual({ cx: end.cx, cy: end.cy }, { cx: held, cy: held });
});

test('visit sizes asked for by the desktop are kept within bounds', () => {
  for (const value of [undefined, null, 'big', NaN]) assert.equal(visitScale(value), VISIT_SCALE);
  assert.equal(visitScale(2.6), 2.6);
  assert.equal(visitScale(9), 3);
  assert.equal(visitScale(0.5), 1.2);
});

test('both quotation prompts keep Jarvis in the first person and ask for checkable quotations', () => {
  for (const { prompt, format } of [welcomeGreetingPrompt(), learningReminderPrompt()]) {
    assert.ok(prompt.startsWith('You are Jarvis.'), prompt.slice(0, 40));
    assert.match(prompt, /first person as I, never as Jarvis/);
    assert.match(prompt, /looks every quotation up/);
    assert.deepEqual(format.schema.properties.candidates.items.required, ['author', 'quote', 'lead', 'close']);
  }
  assert.deepEqual(learningReminderPrompt().format.schema.required, ['candidates', 'plain']);
  const greeting = welcomeGreetingPrompt();
  assert.equal(greeting.format.name, 'welcome_greeting');
  assert.deepEqual(greeting.format.schema.required, ['candidates', 'plain', 'reminders']);
  assert.deepEqual(greeting.format.schema.properties.reminders, { type: 'string' });
  for (const phrase of ['Good morning, sir', 'Good afternoon, sir', 'Good evening, sir', "let's get to work", 'partOfDay', 'awayHours']) {
    assert.ok(greeting.prompt.includes(phrase), phrase);
  }
});

test('the greeting passes on the reminders that are due, and only those', () => {
  const { prompt } = welcomeGreetingPrompt();
  assert.match(prompt, /reminders \(what he asked you to remind him of that is due now, in his own words/);
  assert.match(prompt, /keeping its meaning and its key words/);
  assert.match(prompt, /Lead and close never mention the reminders/);
  assert.match(prompt, /When reminders is empty, return an empty string/);
});

test('a reminder can be set for a morning or a day without a time, and every reminder waits for him', () => {
  const tool = desktopTools.find((entry) => entry.name === 'create_reminder');
  assert.deepEqual(tool.parameters.required, ['text', 'due_at', 'delay_seconds', 'morning_of', 'repeat', 'interval_seconds']);
  assert.equal(tool.parameters.properties.morning_of.type, 'string');
  assert.match(tool.parameters.properties.morning_of.description, /YYYY-MM-DD/);
  for (const phrase of ['in the morning, tomorrow, the day after tomorrow', 'at 3 pm', 'first mouse or keyboard input',
                        'greeting he gets when he first comes to the computer that day', 'Set exactly one of three']) {
    assert.ok(tool.description.includes(phrase), phrase);
  }
});
