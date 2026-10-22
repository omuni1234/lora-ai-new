const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const fixtures=[
 {id:'a-100',name:'TVT-SEMA-N100',runs:2,completed:2},
 {id:'b-200',name:'TVT-DQN-N200',runs:1,completed:1},
 {id:'c-100',name:'Bandit-N100',runs:1,completed:0},
];
function picker(){
 const source=fs.readFileSync('lorasim/static/app.js','utf8');
 const nodes={};
 for(const id of ['experiment','experimentSearch','experimentSearchError','experimentSelection','experimentChecks','selectExperiments','clearExperiments','analyze','txStart','txEnd','includeMCU','bins','edCount'])nodes[id]={value:'',disabled:false,textContent:'',hidden:false,children:[],
  setAttribute(key,value){this[key]=value;},replaceChildren(...items){this.children=items;},append(item){this.children.push(item);}};
 nodes.txStart.value='1';nodes.bins.value='100';
 let data=fixtures,changes=0;
 const calls=[];
 const context=vm.createContext({$:id=>nodes[id],el:(tag,attrs,...children)=>({tag,...attrs,children}),info:()=>({}),experimentText:()=>'',
  api:async(url,method,body)=>{calls.push({url,method,body});return url==='/api/experiments'?data:{runs:[]};},
  analysisChanged:()=>changes++,action:f=>f,clone:v=>v,renderAnalysis:()=>{},notice:()=>{},analysisConsultant:{setAnalysis:()=>{}}});
 vm.runInContext('let experiments=[],analysisBusy=false,analysisRevision=0,analysisData=null,moduleSelections=[];\n'+source.slice(source.indexOf('let selectedAnalysisExperiments='),source.indexOf('function switchView(')),context);
 vm.runInContext(source.slice(source.indexOf("$('analyze').onclick="),source.indexOf("$('exportAnalysis').onclick=")),context);
 const chosen=()=>Array.from(vm.runInContext('selectedExperimentIds()',context));
 const rows=()=>nodes.experimentChecks.children.filter(n=>n.tag==='label');
 return {nodes,calls,chosen,rows,changes:()=>changes,
  refresh:()=>vm.runInContext('refreshExperiments()',context),
  replace:dataNext=>{data=dataNext;},
  search:value=>{nodes.experimentSearch.value=value;nodes.experimentSearch.oninput();},
  toggle:(id,checked)=>rows().find(row=>row.children[0].value===id).children[0].onchange({target:{checked}})};
}

test('regular expressions match names or IDs, including anchors, alternation and case',async()=>{
 const p=picker();await p.refresh();
 assert.equal(p.rows().length,3);assert.equal(p.nodes.clearExperiments.disabled,true);
 p.search('^tvt-(sema|dqn)-N(100|200)$');
 assert.equal(p.rows().length,2);p.nodes.selectExperiments.onclick();
 assert.deepEqual(p.chosen(),['a-100','b-200']);
 assert.equal(p.nodes.selectExperiments.disabled,true);
 p.search('^c-');assert.equal(p.rows().length,1);
 assert.equal(p.rows()[0].children[0].value,'c-100');
});

test('individual and bulk choices survive filtering; clearing includes hidden choices',async()=>{
 const p=picker();await p.refresh();p.toggle('a-100',true);
 p.search('Bandit');assert.match(p.nodes.experimentSelection.textContent,/表示外の選択1件/);
 p.nodes.selectExperiments.onclick();assert.deepEqual(p.chosen(),['a-100','c-100']);
 p.toggle('c-100',false);assert.deepEqual(p.chosen(),['a-100']);
 p.search('');assert.equal(p.rows()[0].children[0].checked,true);
 p.search('DQN');p.nodes.clearExperiments.onclick();
 assert.deepEqual(p.chosen(),[]);assert.equal(p.nodes.clearExperiments.disabled,true);
});

test('invalid regex reports a Japanese hint, disables bulk selection and preserves choices',async()=>{
 const p=picker();await p.refresh();p.toggle('a-100',true);
 p.search('[');assert.equal(p.nodes.experimentSearch['aria-invalid'],'true');
 assert.equal(p.nodes.experimentSearchError.hidden,false);
 assert.match(p.nodes.experimentSearchError.textContent,/正規表現.*ヒント/);
 assert.equal(p.nodes.selectExperiments.disabled,true);assert.equal(p.rows().length,0);
 p.nodes.selectExperiments.onclick();assert.deepEqual(p.chosen(),['a-100']);
 p.search('^TVT-SEMA');assert.equal(p.nodes.experimentSearch['aria-invalid'],'false');
 assert.equal(p.nodes.experimentSearchError.hidden,true);assert.equal(p.rows()[0].children[0].checked,true);
});

test('no matches and an empty experiment catalogue have explicit empty states',async()=>{
 const p=picker();await p.refresh();p.search('^missing$');
 assert.equal(p.nodes.selectExperiments.disabled,true);
 assert.match(p.nodes.experimentChecks.children[0].children[0],/一致するExperimentはありません/);
 p.replace([]);await p.refresh();
 assert.match(p.nodes.experimentChecks.children[0].children[0],/まだ登録されていません/);
});

test('refresh retains selections and the filter, pruning removed IDs and invalidating analysis',async()=>{
 const p=picker();await p.refresh();p.nodes.selectExperiments.onclick();p.search('DQN');
 const before=p.changes();await p.refresh();assert.equal(p.changes(),before);
 assert.equal(p.rows().length,1);assert.deepEqual(p.chosen(),['a-100','b-200','c-100']);
 p.replace(fixtures.slice(1));await p.refresh();
 assert.deepEqual(p.chosen(),['b-200','c-100']);assert.equal(p.changes(),before+1);
 assert.equal(p.nodes.experimentSearch.value,'DQN');
});

test('analysis submits all selected IDs even when some are hidden, without selecting other matches',async()=>{
 const p=picker();await p.refresh();p.search('SEMA|DQN');p.nodes.selectExperiments.onclick();
 const before=p.changes();p.search('DQN');assert.equal(p.changes(),before);
 await p.nodes.analyze.onclick();
 assert.deepEqual(Array.from(p.calls.find(c=>c.url==='/api/analysis').body.experiment_ids),['a-100','b-200']);
 p.nodes.clearExperiments.onclick();await assert.rejects(p.nodes.analyze.onclick(),/Experimentを1つ以上/);
});

test('experiment names remain text nodes rather than HTML',async()=>{
 const p=picker();p.replace([{...fixtures[0],name:'<img src=x onerror=alert(1)>'}]);await p.refresh();
 assert.equal(p.rows()[0].children[1].children[0],'<img src=x onerror=alert(1)>');
});
