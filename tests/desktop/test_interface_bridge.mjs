import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { Backend } from '../../desktop/ui/lib/backend.mjs';

test('interface requests return verified results and preserve failures', async () => {
  const output = new PassThrough();
  const replies = [];
  output.on('data', chunk => replies.push(JSON.parse(chunk.toString())));
  const backend = new Backend({onRequest(method, params) {
    if (method !== 'move_orb') throw new Error('Unknown interface request');
    return {verified:true, orb:{monitor:params.monitor}};
  }});
  backend.attach(new PassThrough(), output);
  backend.receive(JSON.stringify({t:'request',id:'move',method:'move_orb',params:{monitor:'display-2'},deadline:Date.now()+1000}));
  backend.receive(JSON.stringify({t:'request',id:'bad',method:'unknown',deadline:Date.now()+1000}));
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(replies[0], {t:'response',id:'move',ok:true,result:{verified:true,orb:{monitor:'display-2'}}});
  assert.equal(replies[1].ok,false);
  assert.match(replies[1].error,/Unknown/);
  backend.close();
});

test('expired interface requests never execute', async () => {
  let called = false;
  const output = new PassThrough();
  const replies = [];
  output.on('data', chunk => replies.push(JSON.parse(chunk.toString())));
  const backend = new Backend({onRequest() { called = true; }});
  backend.attach(new PassThrough(),output);
  backend.receive(JSON.stringify({t:'request',id:'late',method:'move_orb',deadline:Date.now()-1}));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(called,false);
  assert.equal(replies[0].ok,false);
  assert.match(replies[0].error,/expired/);
  backend.close();
});
