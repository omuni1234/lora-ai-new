const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function editor(validate=async()=>({name:'loaded'})){
 const source=fs.readFileSync('lorasim/static/app.js','utf8');
 const fragment=source.slice(source.indexOf('async function refreshExamples'),source.indexOf("$('profiles').onchange"));
 const nodes={};
 for(const id of ['exampleProfiles','loadExample','profiles','experiment'])nodes[id]={value:'',disabled:false,
   children:[],replaceChildren(...items){this.children=items;},append(item){this.children.push(item);}};
 const calls=[];
 const context=vm.createContext({$:id=>nodes[id],el:(tag,attrs,text)=>({tag,...attrs,text}),
   action:f=>f,encodeURIComponent,validatedProfile:validate,
   api:async url=>{calls.push(url);return url==='/api/examples'?[{id:'tvt/test file.yaml',name:'<script>example</script>'}]:{yaml:'name: loaded'};},
   loadSchemas:async()=>calls.push('schemas'),renderAll:()=>calls.push('render'),dirty:()=>calls.push('dirty'),notice:()=>calls.push('notice')});
 vm.runInContext('let profile={name:"existing"};\n'+fragment,context);
 return {nodes,calls,context};
}

test('bundled examples list, enable, validate, import and clear experiment selection',async()=>{
 const e=editor();await vm.runInContext('refreshExamples()',e.context);
 assert.equal(e.nodes.loadExample.disabled,true);
 assert.equal(e.nodes.exampleProfiles.children[1].text,'tvt/test file.yaml — <script>example</script>');
 e.nodes.exampleProfiles.value='tvt/test file.yaml';e.nodes.exampleProfiles.onchange();
 assert.equal(e.nodes.loadExample.disabled,false);
 e.nodes.profiles.value='old';e.nodes.experiment.value='old-experiment';
 await e.nodes.loadExample.onclick();
 assert.equal(vm.runInContext('profile.name',e.context),'loaded');
 assert.equal(e.nodes.profiles.value,'');assert.equal(e.nodes.experiment.value,'');
 assert.deepEqual(e.calls,['/api/examples','/api/examples/content?id=tvt%2Ftest%20file.yaml','schemas','render','dirty','notice']);
});

test('empty selection is inert and failed validation preserves the current experiment',async()=>{
 const e=editor(async()=>{throw Error('invalid example');});
 await e.nodes.loadExample.onclick();assert.deepEqual(e.calls,[]);
 e.nodes.exampleProfiles.value='bad.yaml';e.nodes.experiment.value='existing-experiment';
 await assert.rejects(e.nodes.loadExample.onclick(),/invalid example/);
 assert.equal(vm.runInContext('profile.name',e.context),'existing');
 assert.equal(e.nodes.experiment.value,'existing-experiment');
 assert.ok(!e.calls.includes('render'));
});
