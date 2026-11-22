import test from 'node:test';
import assert from 'node:assert/strict';
import {finalResponseText} from '../../desktop/model-output.mjs';

const part = (text, phase) => ({type:'text', text, textSignature:JSON.stringify({v:1,id:'message-'+phase,phase})});

test('commentary and final JSON messages produce one final action', () => {
  const draft = {action:'focus_window',target:'window:11',message:'First idea'};
  const final = {action:'minimize_window',target:'window:22',message:'Minimize Task Manager'};
  const response = {content:[part(JSON.stringify(draft), 'commentary'),part(JSON.stringify(final), 'final_answer')]};
  assert.deepEqual(JSON.parse(finalResponseText(response)), final);
});

test('intermediate commentary alone is never treated as a completed answer or action', () => {
  assert.equal(finalResponseText({content:[part('{"action":"done"}', 'commentary')]}), '');
});

test('spoken replies omit commentary and preserve final text', () => {
  assert.equal(finalResponseText({content:[part('I will check. ', 'commentary'),part('It is done.', 'final_answer')]}), 'It is done.');
});

test('legacy untagged text and multiple content parts remain supported', () => {
  assert.equal(finalResponseText({content:[{type:'thinking',thinking:'internal'},
    {type:'text',text:'First '},{type:'text',text:'sentence.',textSignature:'legacy-id'}]}), 'First sentence.');
});
