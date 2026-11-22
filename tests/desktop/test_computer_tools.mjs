import assert from 'node:assert/strict';
import {test} from 'node:test';
import {computerAction, computerMessages, computerTools} from '../../desktop/computer-tools.mjs';

const observation = (windows, lastAction = null) => ({role: 'user', content: [
  {type: 'text', text: JSON.stringify({question: 'Close Alpha and Beta; leave Gamma alone.', desktop: {windows, lastAction}})},
  {type: 'image', data: 'private-current-image', mimeType: 'image/png'},
]});
const call = (id, target) => ({type: 'toolCall', id, name: 'close_window', arguments: {target, message: 'Closing the requested window.'}});

test('actual calls and executed results stay paired across multiple windows', () => {
  const first = call('call-1', 'window:11');
  const reasoning = {type: 'thinking', thinking: 'Continue through all requested windows', thinkingSignature: 'encrypted-state'};
  const session = {messages: [observation(['window:11', 'window:12', 'window:13']),
    {role: 'assistant', content: [reasoning, first]}], pendingComputerCall: first};
  const result = {action: 'close_window', target: 'window:11', result: {ok: true, windowState: 'closed'}};
  const messages = computerMessages(session, observation(['window:12', 'window:13'], result));
  assert.equal(messages[1].content[0], reasoning);
  assert.equal(messages[2].role, 'toolResult');
  assert.equal(messages[2].toolCallId, 'call-1');
  assert.deepEqual(JSON.parse(messages[2].content[0].text), result);
  const second = call('call-2', 'window:12');
  session.messages = [...messages, {role: 'assistant', content: [second]}];
  session.pendingComputerCall = second;
  const continued = computerMessages(session, observation(['window:13'], {
    action: 'close_window', target: 'window:12', result: {ok: true, windowState: 'closed'}}));
  assert.equal(continued.filter(row => row.role === 'toolResult').length, 2);
  assert.equal(continued.flatMap(row => row.content).filter(part => part.type === 'image').length, 1);
  assert.equal(JSON.parse(continued[0].content[0].text).question, 'Close Alpha and Beta; leave Gamma alone.');
});

test('missing and failed execution results cannot masquerade as successful calls', () => {
  const pendingComputerCall = call('c', 'window:11');
  for (const result of [null, {action: 'close_window', result: {ok: false, error: 'Access denied'}}]) {
    const messages = computerMessages({pendingComputerCall}, observation(['window:11'], result));
    assert.equal(messages[0].isError, true);
  }
});

test('prose completion and multiple simultaneous desktop actions are rejected', () => {
  assert.throws(() => computerAction({content: [{type: 'text', text: 'I closed them.'}]}));
  assert.throws(() => computerAction({content: [call('one', 'window:11'), call('two', 'window:12')]}));
  assert.throws(() => computerAction({content: [{...call('one', 'window:11'), name: 'kill_process'}]}));
  assert.equal(computerAction({content: [call('one', 'window:11')]}).action.action, 'close_window');
  assert.equal(computerAction({content: [{type: 'toolCall', id: 'finish', name: 'finish_task', arguments: {outcome:'completed',message: 'Both closed.'}}]}).action.action, 'done');
  assert.throws(() => computerAction({content: [{type: 'toolCall', id: 'finish', name: 'finish_task', arguments: {message: 'Cannot finish.'}}]}));
  assert.ok(computerTools.some(tool => tool.name === 'close_window'));
});

test('partial completion is preserved for the desktop host', () => {
  const finish = computerTools.find(tool => tool.name === 'finish_task');
  assert.ok(finish.parameters.required.includes('outcome'));
  const result = computerAction({content:[{type:'toolCall',id:'partial',name:'finish_task',
    arguments:{outcome:'partial',message:'The windows closed but process exit was not verified.'}}]});
  assert.equal(result.action.outcome,'partial');
});

test('background control clicks retain the fresh control and mouse button', () => {
  const tool = computerTools.find(tool => tool.name === 'click_control');
  assert.deepEqual(tool.parameters.properties.button.enum, ['left', 'middle', 'right']);
  const args = {target: 'control:live:7', button: 'middle', message: 'Use the requested control in the background.'};
  const result = computerAction({content: [{type: 'toolCall', id: 'click', name: 'click_control', arguments: args}]});
  assert.deepEqual(result.action, {...args, action: 'click_control'});
});
