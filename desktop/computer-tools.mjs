import {computerContext} from './computer-prompts.mjs';

const text = description => ({type: 'string', description});
const target = text('A current host-issued window, application, monitor or control ID from the observation.');
const message = text('A concise description of this intended step, or an evidence-based final result.');
const position = {x: {type: 'integer'}, y: {type: 'integer'}};
const pixel = edge => ({type: 'integer', description: `The ${edge} edge of the place in physical screen pixels, as in a window rect or a monitor workArea.`});
const place = {left: pixel('left'), top: pixel('top'), right: pixel('right'), bottom: pixel('bottom'),
  maximized: {type: 'boolean', description: 'True to maximize the window on the monitor holding that rectangle; false to size it to the rectangle.'}};
const definitions = {
  open_app: ['Request launch of a listed installed app.', {target}],
  inspect_window: ['Inspect the controls of a listed window without changing focus.', {target}],
  focus_window: ['Bring a listed window forward for input.', {target}],
  minimize_window: ['Minimize a listed window and verify the result, without moving focus.', {target}],
  close_window: ['Request a normal close of a listed window and verify it. Never kills processes or discards unsaved work.', {target}],
  move_window: ['Move, resize or place a listed window with your own pointer: it is brought back if minimized, dragged by its title bar to the rectangle, or maximized on that monitor, and raised above the other windows without taking keyboard focus. To put it where another window is or was, give that window exact rect and maximized state. The result reports where it ended up.', {target, ...place}],
  view_display: ['Observe a listed monitor, or all displays.', {target}],
  invoke: ['Invoke an advertised native control action.', {target}],
  click_control: ['Send a mouse click directly to a current control in its window, even while covered, without moving the real pointer or requesting foreground focus. Inspect the result to verify the app handled it.', {target, button: {type: 'string', enum: ['left', 'middle', 'right']}}],
  set_value: ['Set a native field or numeric control to a value.', {target, text: text('The requested field value.')}],
  toggle: ['Toggle a native control that advertises this action.', {target}],
  select: ['Select a native control that advertises this action.', {target}],
  expand: ['Expand a native control.', {target}],
  collapse: ['Collapse a native control.', {target}],
  scroll_into_view: ['Scroll a native control into view.', {target}],
  click: ['Click a point of the selected window screenshot. In the background it goes straight to the app with your own pointer, without focus and without moving the owner pointer.', position],
  double_click: ['Double click a point of the selected window screenshot, in the background unless focus_window was used.', position],
  right_click: ['Right click a point of the selected window screenshot, in the background unless focus_window was used.', position],
  scroll: ['Scroll at a point of the selected window screenshot, in the background unless focus_window was used.', {...position, scroll: {type: 'integer', minimum: -6, maximum: 6}}],
  key: ['Press a supported key. In the background a single key goes to the field you last clicked; shortcuts need focus_window.', {key: text('The supported key or shortcut.')}],
  type: ['Type text. In the background it goes to the field you last clicked; after focus_window it is typed in the foreground.', {text: text('Text serving the owner request.')}],
  wait: ['Wait briefly for an application transition, then observe again.', {}],
  finish_task: ['Finish after checking EVERY part of the request. completed means every goal succeeded; partial or failed means any goal is unavailable, unverified or unsuccessful, even if some windows closed. Report exact outcomes.', {outcome:{type:'string',enum:['completed','partial','failed']}}],
  report_blocked: ['Report a limitation only after completing the remaining independent requested actions that are possible.', {}],
};

export const computerTools = Object.entries(definitions).map(([name, [description, fields]]) => ({
  name, description, parameters: {type: 'object', properties: {...fields, message},
    required: [...Object.keys(fields), 'message'], additionalProperties: false},
}));

export const computerToolContext = computerContext + `
You are an agent with real desktop function tools. Call exactly one tool for the next step. The host executes it and returns its result plus a fresh observation before your next decision. A description or promise does NOT execute an action. Never simulate a tool call in prose or claim unperformed actions.
Keep the entire original request in mind throughout the tool conversation. Call finish_task only when the tool results and current inventory account for ALL requested targets. A tool result about one window does not complete a multi-window request. Use report_blocked for a remaining limitation after other independent actions are handled. The final message must describe actual outcomes, not intentions.`;

export function computerMessages(session, observation) {
  const messages = (session.messages ?? []).flatMap((message, index) => {
    if (message.role !== 'user') return [message];
    if (index !== 0) return [];
    const input = JSON.parse(message.content.find(part => part.type === 'text').text);
    return [{...message, content: [{type: 'text', text: JSON.stringify({question: input.question})}]}];
  });
  if (session.pendingComputerCall) {
    const call = session.pendingComputerCall;
    const scene = JSON.parse(observation.content.find(part => part.type === 'text').text).desktop ?? {};
    const result = scene.lastAction ?? {error: 'No execution result was recorded for this action. Inspect the fresh observation; do not assume it succeeded.'};
    messages.push({role: 'toolResult', toolCallId: call.id, toolName: call.name,
      content: [{type: 'text', text: JSON.stringify(result)}], isError: result.result?.ok === false || !!result.error, timestamp: Date.now()});
  }
  messages.push(observation);
  return messages;
}

export function computerAction(response) {
  const calls = response.content.filter(part => part.type === 'toolCall');
  if (calls.length !== 1 || !computerTools.some(tool => tool.name === calls[0].name)) {
    throw new Error('The desktop model must select exactly one available tool');
  }
  const call = calls[0];
  if (call.name === 'finish_task' && !['completed', 'partial', 'failed'].includes(call.arguments?.outcome)) {
    throw new Error('Desktop completion requires an explicit outcome');
  }
  const action = call.name === 'finish_task' ? 'done' : call.name === 'report_blocked' ? 'blocked' : call.name;
  return {call, action: {...call.arguments, action}};
}
