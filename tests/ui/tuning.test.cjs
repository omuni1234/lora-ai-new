const {test}=require('node:test');
const assert=require('node:assert/strict');
const Tuning=require('../../lorasim/static/tuning.js');
test('linear/log candidates include endpoints, are finite and can be discrete',()=>{
 assert.deepEqual(Tuning.range(0,1,3),[0,.5,1]);
 assert.deepEqual(Tuning.range(.001,.1,3,true),[.001,.01,.1]);
 assert.deepEqual(Tuning.range(1,3,5,false,true),[1,2,3]);
 for(const a of [[1,1,3],[0,10,3,true],[0,1,100],[0,1,2.5]])assert.throws(()=>Tuning.range(...a));
});
test('registered options are parsed as JSON, never executable expressions',()=>{
 assert.deepEqual(Tuning.dimensions([{path:{value:'epsilon'},values:{value:'[0.1,0.2]'}}]),[{path:'epsilon',values:[.1,.2]}]);
 assert.throws(()=>Tuning.dimensions([{path:{value:'epsilon'},values:{value:'process.exit()'}}]));
 assert.throws(()=>Tuning.dimensions([{path:{value:'epsilon'},values:{value:'[]'}}]));
});
test('default horizons are capped while preserving short profiles',()=>{
 assert.equal(Tuning.budgets(6e6).at(-1),36000);
 assert.equal(Tuning.budgets(600).at(-1),600);
 assert.ok(Tuning.budgets(.01).every(x=>x>0&&x<=.01));
});
