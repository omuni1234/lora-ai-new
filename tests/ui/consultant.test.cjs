const {test}=require('node:test');
const assert=require('node:assert/strict');
const {AIEventParser}=require('../../lorasim/static/consultant.js');
test('SSE framing handles arbitrary chunks and Japanese text',()=>{
 const es=[],p=new AIEventParser(e=>es.push(e));
 const raw=': keepalive\r\n\r\ndata: {"type":"delta","text":"受信成功"}\r\n\r\ndata: {"type":"done"}\n\n';
 for(const ch of raw)p.push(ch);p.finish();
 assert.deepEqual(es,[{type:'delta',text:'受信成功'},{type:'done'}]);
});
test('SSE catches malformed and unfinished responses',()=>{
 assert.throws(()=>new AIEventParser(()=>{}).push('data: invalid\n\n'));
 const p=new AIEventParser(()=>{});p.push('data: {"text":"partial');assert.throws(()=>p.finish());
});
test('UTF-8 decoding survives multibyte splits before SSE parsing',()=>{
 const out=[],parser=new AIEventParser(e=>out.push(e)),d=new TextDecoder();
 for(const byte of new TextEncoder().encode('data: {"text":"考察です。"}\n\n'))parser.push(d.decode(Uint8Array.of(byte),{stream:true}));
 parser.push(d.decode());parser.finish();assert.equal(out[0].text,'考察です。');
});
