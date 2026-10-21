const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const entries=[{id:'tvt/a.yaml',name:'<script>A</script>'},{id:'tvt/b.yaml',name:'B'},{id:'other.yaml',name:'C'}];
function editor({validate=async()=>({name:'loaded'}),request}={}){
 const source=fs.readFileSync('lorasim/static/app.js','utf8');
 const fragment=source.slice(source.indexOf('let exampleEntries='),source.indexOf("$('profiles').onchange"));
 const nodes={};
 for(const id of ['exampleSearch','exampleProfiles','selectExamples','clearExamples','exampleSelection','loadExample','queueExamples','exampleQueueStatus','exampleQueueResults','profiles','experiment'])nodes[id]={value:'',disabled:false,textContent:'',
   children:[],replaceChildren(...items){this.children=items;},append(item){this.children.push(item);}};
 const calls=[],errors=[];
 const api=async(url,method,body)=>{
  calls.push({url,method,body});
  if(request)return request(url,method,body);
  if(url==='/api/examples')return entries;
  if(url.startsWith('/api/examples/content'))return {yaml:'name: '+decodeURIComponent(url.split('=')[1])+'\nseed: 7'};
  return {id:'run-'+calls.filter(x=>x.method==='POST').length};
 };
 const context=vm.createContext({$:id=>nodes[id],el:(tag,attrs,...children)=>({tag,...attrs,children}),
   action:f=>f,encodeURIComponent,validatedProfile:validate,api,
   loadSchemas:async()=>calls.push({url:'schemas'}),renderAll:()=>calls.push({url:'render'}),dirty:()=>calls.push({url:'dirty'}),notice:()=>{},reportError:e=>errors.push(e),
   refreshExperiments:async()=>calls.push({url:'experiments'}),refreshJobs:async()=>calls.push({url:'jobs'}),selectJob:async()=>{}});
 vm.runInContext('let profile={name:"existing",seed:99};\n'+fragment,context);
 const chosen=()=>JSON.parse(vm.runInContext('JSON.stringify([...selectedExamples])',context));
 const text=node=>typeof node==='string'?node:[node.textContent||'',...(node.children||[]).map(text)].join(' ');
 return {nodes,calls,errors,context,chosen,text,refresh:()=>vm.runInContext('refreshExamples()',context)};
}

test('filter, select all visible, preserve hidden choices and clear all',async()=>{
 const e=editor();await e.refresh();
 assert.equal(e.nodes.loadExample.disabled,true);assert.equal(e.nodes.queueExamples.disabled,true);
 assert.equal(e.nodes.exampleProfiles.children[0].children[1].children[1].children[0],'<script>A</script>');
 e.nodes.exampleSearch.value='TVT/';e.nodes.exampleSearch.oninput();e.nodes.selectExamples.onclick();
 assert.deepEqual(e.chosen(),['tvt/a.yaml','tvt/b.yaml']);
 assert.equal(e.nodes.loadExample.disabled,true);
 e.nodes.exampleSearch.value='other';e.nodes.exampleSearch.oninput();
 assert.match(e.nodes.exampleSelection.textContent,/表示外の選択2件/);
 e.nodes.selectExamples.onclick();assert.equal(e.chosen().length,3);
 e.nodes.clearExamples.onclick();assert.deepEqual(e.chosen(),[]);
 assert.equal(e.nodes.queueExamples.disabled,true);
 e.nodes.exampleSearch.value='no-match';e.nodes.exampleSearch.oninput();assert.equal(e.nodes.selectExamples.disabled,true);
});

test('a checkbox enables single import; failed validation preserves the editor',async()=>{
 const e=editor();await e.refresh();
 const checkbox=e.nodes.exampleProfiles.children[0].children[0];checkbox.onchange({target:{checked:true}});
 assert.equal(e.nodes.loadExample.disabled,false);
 e.nodes.profiles.value='old';e.nodes.experiment.value='old-experiment';
 await e.nodes.loadExample.onclick();
 assert.equal(vm.runInContext('profile.name',e.context),'loaded');
 assert.equal(e.nodes.profiles.value,'');assert.equal(e.nodes.experiment.value,'');
 assert.ok(e.calls.some(x=>x.url==='schemas'));
 const bad=editor({validate:async()=>{throw Error('invalid example');}});await bad.refresh();
 bad.nodes.exampleProfiles.children[0].children[0].onchange({target:{checked:true}});
 bad.nodes.experiment.value='existing-experiment';await bad.nodes.loadExample.onclick();
 assert.equal(vm.runInContext('profile.name',bad.context),'existing');
 assert.equal(bad.nodes.experiment.value,'existing-experiment');assert.equal(bad.errors[0].message,'invalid example');
});

test('each selected YAML is queued once with its own settings and experiment',async()=>{
 const e=editor();await e.refresh();e.nodes.exampleSearch.value='tvt/';e.nodes.selectExamples.onclick();
 e.nodes.experiment.value='edited-experiment';await e.nodes.queueExamples.onclick();
 const posts=e.calls.filter(x=>x.method==='POST');
 assert.equal(posts.length,2);
 for(let i=0;i<2;i++){assert.equal(posts[i].url,'/api/jobs');assert.equal(posts[i].body.yaml,'name: '+entries[i].id+'\nseed: 7');assert.equal(posts[i].body.experiment_id,null);}
 assert.equal(vm.runInContext('profile.seed',e.context),99);assert.equal(e.nodes.experiment.value,'edited-experiment');
 assert.deepEqual(e.chosen(),[]);assert.equal(e.nodes.queueExamples.disabled,true);
 assert.match(e.nodes.exampleQueueStatus.textContent,/キュー登録：2 \/ 2件、失敗0件/);
 await e.nodes.queueExamples.onclick();assert.equal(e.calls.filter(x=>x.method==='POST').length,2);
});

test('a rejected YAML stays selected while other YAMLs are queued',async()=>{
 const e=editor({request:async(url,method,body)=>{
  if(url==='/api/examples')return entries;
  if(method==='POST'){if(body.yaml.includes('b.yaml'))throw Object.assign(Error('invalid configuration'),{status:422});return {id:body.yaml};}
  return {yaml:decodeURIComponent(url.split('=')[1])};
 }});
 await e.refresh();e.nodes.selectExamples.onclick();await e.nodes.queueExamples.onclick();
 assert.equal(e.calls.filter(x=>x.method==='POST').length,3);
 assert.deepEqual(e.chosen(),['tvt/b.yaml']);
 assert.match(e.nodes.exampleQueueStatus.textContent,/キュー登録：2 \/ 3件、失敗1件/);
 assert.match(e.text(e.nodes.exampleQueueResults),/invalid configuration/);
});

test('lost POST response stops the batch and never automatically resubmits an uncertain Run',async()=>{
 const e=editor({request:async(url,method)=>{
  if(url==='/api/examples')return entries;
  if(method==='POST')throw Error('connection lost');
  return {yaml:'name: example'};
 }});
 await e.refresh();e.nodes.selectExamples.onclick();await e.nodes.queueExamples.onclick();
 assert.equal(e.calls.filter(x=>x.method==='POST').length,1);
 assert.deepEqual(e.chosen(),['tvt/b.yaml','other.yaml']);
 assert.match(e.nodes.exampleQueueStatus.textContent,/登録状況不明1件、未処理2件/);
 assert.match(e.text(e.nodes.exampleQueueResults),/Run一覧を確認/);
});

test('in-flight submission locks choices and ignores a second execution click',async()=>{
 let release;
 const gate=new Promise(resolve=>{release=resolve;});
 const e=editor({request:async(url,method)=>{
  if(url==='/api/examples')return entries.slice(0,1);
  if(method==='POST'){await gate;return {id:'run-one'};}
  return {yaml:'name: one'};
 }});
 await e.refresh();e.nodes.selectExamples.onclick();
 const pending=e.nodes.queueExamples.onclick();await Promise.resolve();await Promise.resolve();
 assert.equal(e.nodes.queueExamples.disabled,true);assert.equal(e.nodes.exampleSearch.disabled,true);
 assert.equal(e.nodes.exampleProfiles.children[0].children[0].disabled,true);
 e.nodes.clearExamples.onclick();assert.equal(e.chosen().length,1);
 await e.nodes.queueExamples.onclick();release();await pending;
 assert.equal(e.calls.filter(x=>x.method==='POST').length,1);
 assert.equal(e.nodes.queueExamples.disabled,true);assert.equal(e.nodes.exampleSearch.disabled,false);
});

test('API errors retain HTTP status for distinguishing rejection from uncertain submission',async()=>{
 const source=fs.readFileSync('lorasim/static/app.js','utf8');
 const fragment=source.slice(source.indexOf('async function api('),source.indexOf('function reportError'));
 const context=vm.createContext({sessionStorage:{getItem:()=>''},DiagnosticError:class extends Error{constructor(ds){super(ds[0].message);this.diagnostics=ds;}},
  fetch:async()=>({ok:false,status:422,text:async()=>JSON.stringify({diagnostics:[{message:'invalid YAML'}]})})});
 vm.runInContext(fragment,context);
 await assert.rejects(vm.runInContext("api('/api/jobs','POST',{yaml:'bad'})",context),e=>e.status===422&&e.message==='invalid YAML');
});
