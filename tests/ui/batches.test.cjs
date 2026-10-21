const {test}=require('node:test');
const assert=require('node:assert/strict');
const Batch=require('../../lorasim/static/batches.js');
test('ED count lists support commas, Japanese commas, spaces and newlines',()=>{
 assert.deepEqual(Batch.counts('100, 200, 300'),[100,200,300]);
 assert.deepEqual(Batch.counts(' 100、200\n300 '),[100,200,300]);
 assert.deepEqual(Batch.counts('1 1000000'),[1,1000000]);
});
test('bad or duplicate ED counts cannot become a batch',()=>{
 for(const value of ['', '0', '-2', '1.5', 'NaN', 'Infinity', '1e3', '1000001', '100,100', 'abc', Array.from({length:51},(_,i)=>i+1).join(',')])
  assert.throws(()=>Batch.counts(value));
});
