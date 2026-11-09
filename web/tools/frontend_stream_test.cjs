'use strict';
// Exercise the production parser with split UTF-8 bytes, line endings and SSE fields.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const source = readFileSync(join(__dirname, '../frontend/public/app.js'), 'utf8');
const start = source.indexOf('  async function readSSE(');
const end = source.indexOf('  const sessions =', start);
const readSSE = new Function(source.slice(start, end) + '; return readSSE;')();

async function parse(text, chunkSize) {
  const bytes = new TextEncoder().encode(text), events = [];
  const stream = new ReadableStream({
    start(controller) {
      for (let offset = 0; offset < bytes.length; offset += chunkSize) controller.enqueue(bytes.slice(offset, offset + chunkSize));
      controller.close();
    }
  });
  await readSSE(new Response(stream, { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } }), (name, data) => events.push([name, data]));
  return events;
}

(async () => {
  const transcript = '\ufeff: heartbeat\r\n\r\nevent: reasoning\r\ndata: {"delta":\r\ndata: "생각🙂"}\r\n\r\n' +
    'unknown: ignored\nevent:content\ndata:{"delta":"답변\\n"}\n\n: ping\r\r' +
    'event: done\rdata: {"message":{"id":"saved"}}';
  const expected = [['reasoning', { delta: '생각🙂' }], ['content', { delta: '답변\n' }], ['done', { message: { id: 'saved' } }]];
  for (const size of [1, 2, 3, 7, 13, 4096]) assert.deepEqual(await parse(transcript, size), expected);
  assert.deepEqual(await parse('data: {"value":1}\n\n', 1), [['message', { value: 1 }]]);
  await assert.rejects(() => parse('event: content\ndata: invalid\n\n', 2), SyntaxError);
  await assert.rejects(() => readSSE(new Response('html'), () => {}), /스트리밍 응답/);
  let cancelled = false;
  const interrupted = new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('data: invalid\n\n')); },
    cancel() { cancelled = true; }
  });
  await assert.rejects(() => readSSE(new Response(interrupted, { headers: { 'Content-Type': 'text/event-stream' } }), () => {}), SyntaxError);
  assert.equal(cancelled, true);
  console.log('PASS SSE UTF-8/chunk/CRLF/CR/multiline/comment/EOF/error/cancel checks');
})().catch(error => { console.error(error); process.exitCode = 1; });
