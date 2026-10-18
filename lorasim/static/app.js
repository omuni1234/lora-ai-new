'use strict';
const $=id=>document.getElementById(id);
let token=sessionStorage.getItem('lorasim-token')||'', selected=null, saved=[], dirty=false;
const changed=new Set();
let current={schema_version:1,name:'my-experiment',seed:7,duration_s:180,area_m:[2000,2000],gateways:[{id:'gw0',position:[1000,1000,15]}],groups:[{name:'sensors',count:12,placement:'grid',confirmed:true,policy:{kind:'fixed',options:{sf:7,tp_dbm:14,cr:1}},timing:{kind:'periodic',options:{interval_s:60,random_phase:true}},mobility:{kind:'stationary'}}]};
function notice(message,error=false){$('notice').textContent=message;$('notice').className=error?'error':'';}
async function api(url,options={}){const r=await fetch('/api'+url,{...options,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{}),...(options.headers||{})}});if(!r.ok){let body=await r.json().catch(()=>({detail:r.statusText}));throw Error(typeof body.detail==='string'?body.detail:JSON.stringify(body.detail));}return r.json();}
function body(){return JSON.stringify({yaml:$('yaml').value});}
function editor(){ $('yaml').value=JSON.stringify(current,null,2); }
function tab(yaml){$('yamlPane').hidden=!yaml;$('form').hidden=yaml;for(const [id,on] of [['yamlTab',yaml],['formTab',!yaml]]){$(id).classList.toggle('active',on);$(id).setAttribute('aria-selected',on);}}
function fillForm(){const g=current.groups?.[0];if(!g)return;$('name').value=current.name;$('seed').value=current.seed;$('duration').value=current.duration_s;[$('width').value,$('height').value]=current.area_m;$('count').value=g.count;$('confirmed').checked=g.confirmed;$('policy').value=g.policy?.kind||'fixed';$('mobility').value=g.mobility?.kind||'stationary';$('sf').value=g.policy?.options?.sf||7;$('tp').value=g.policy?.options?.tp_dbm??14;$('cr').value=g.policy?.options?.cr||1;$('interval').value=g.timing?.options?.interval_s||60;$('speed').value=g.mobility?.options?.speed_m_s??1.5;dirty=false;changed.clear();}
async function sync(){current=await api('/validate',{method:'POST',body:body()});fillForm();}
function apply(){
  const val=id=>Number($(id).value);
  current.name=$('name').value; current.seed=val('seed');
  current.duration_s=val('duration'); current.area_m=[val('width'),val('height')];
  const groupFields=['count','confirmed','policy','sf','tp','cr','interval','mobility','speed'];
  if(current.groups.length!==1 && groupFields.some(k=>changed.has(k)))
    throw Error('複数グループの編集は詳細 YAML で行ってください。');
  let g=current.groups[0];
  if(changed.has('count')) g.count=val('count');
  if(changed.has('confirmed')) g.confirmed=$('confirmed').checked;
  if(['policy','sf','tp','cr'].some(k=>changed.has(k))){
    const kind=$('policy').value;
    if(!['fixed','random','bandit'].includes(kind)) throw Error('カスタム方策は詳細 YAML で編集してください。');
    if(kind==='fixed'){
      g.policy={kind,options:{...(g.policy.kind===kind?g.policy.options:{}),sf:val('sf'),tp_dbm:val('tp'),cr:val('cr')}};
    }else if(g.policy.kind!==kind){
      g.policy={kind,options:{actions:[{sf:7,tp_dbm:8},{sf:9,tp_dbm:14},{sf:12,tp_dbm:14}]}};
    }
  }
  if(changed.has('interval')) g.timing={kind:'periodic',options:{...(g.timing?.kind==='periodic'?g.timing.options:{}),interval_s:val('interval')}};
  if(changed.has('mobility')||changed.has('speed')){
    const mobility=$('mobility').value;
    if(mobility==='waypoints'){
      if(g.mobility?.kind!=='waypoints') g.mobility={kind:'waypoints',options:{points:[[0,0,0,1.5],[current.duration_s,current.area_m[0],current.area_m[1],1.5]]}};
    }else if(mobility==='random_walk'){
      g.mobility={kind:mobility,options:{...(g.mobility?.kind===mobility?g.mobility.options:{}),speed_m_s:val('speed')}};
    }else if(mobility==='stationary'){
      g.mobility={kind:mobility};
    }else throw Error('カスタム移動モデルは詳細 YAML で編集してください。');
  }
  editor(); dirty=false; changed.clear();
}
async function action(fn){try{await fn();}catch(e){notice(e.message,true);}}
async function loadProfiles(){saved=await api('/profiles');$('profiles').replaceChildren(new Option('プロファイルを選択',''));saved.forEach((p,i)=>$('profiles').add(new Option(p.name,String(i))));}
async function refresh(){const [jobs,worker]=await Promise.all([api('/jobs'),api('/worker')]);$('worker').textContent=worker.online?'● Worker online · '+worker.workers+' processes':'○ Worker offline';$('worker').classList.toggle('offline',!worker.online);for(const [id,s] of [['running','running'],['queued','queued'],['complete','completed']])$(id).textContent=jobs.filter(j=>j.status===s).length;
$('jobs').replaceChildren();if(!jobs.length){const e=document.createElement('p');e.className='empty';e.textContent='実験を実行すると、ここに進捗が表示されます。';$('jobs').append(e);}for(const j of jobs){const el=document.createElement('div');el.className='job';el.tabIndex=0;el.setAttribute('role','button');const top=document.createElement('div');top.className='job-top';const n=document.createElement('span');n.className='job-name';n.textContent=j.name;const s=document.createElement('span');s.className='job-status';s.textContent=j.status+' · '+Math.round(j.progress*100)+'%';top.append(n,s);const meta=document.createElement('div');meta.className='job-meta';meta.textContent=j.id.slice(0,12)+' · '+new Date(j.created*1000).toLocaleString()+' · t='+j.sim_time.toFixed(1)+' s';const p=document.createElement('progress');p.max=1;p.value=j.progress;p.setAttribute('aria-label',j.name+' 進捗');el.append(top,meta,p);el.onclick=()=>action(()=>details(j.id));el.onkeydown=e=>{if(e.key==='Enter')el.click();};$('jobs').append(el);}if(selected)await details(selected);}
async function details(id){selected=id;const [j,files]=await Promise.all([api('/jobs/'+id),api('/jobs/'+id+'/files')]);$('detailStatus').textContent=j.status;const b=$('detailBody');b.replaceChildren();const pre=document.createElement('pre');pre.textContent=JSON.stringify(j.summary||{job_id:j.id,progress:j.progress,sim_time_s:j.sim_time,error:j.error},null,2);b.append(pre);const reuse=document.createElement('button');reuse.className='secondary';reuse.textContent='設定を再利用';reuse.onclick=()=>action(async()=>{$('yaml').value=j.profile;await sync();tab(true);notice('設定を読み込みました。');});b.append(reuse);if(['running','queued'].includes(j.status)){const c=document.createElement('button');c.className='secondary';c.textContent='実行をキャンセル';c.onclick=()=>action(async()=>{await api('/jobs/'+id+'/cancel',{method:'POST'});await refresh();});b.append(c);}const list=document.createElement('div');list.className='file-list';for(const f of files){const a=document.createElement('a');a.className='file';a.href='#';a.textContent=f.name+' · '+(f.bytes/1024).toFixed(1)+' KB';a.onclick=e=>{e.preventDefault();action(async()=>{const r=await fetch('/api/jobs/'+id+'/files/'+f.name,{headers:token?{Authorization:'Bearer '+token}:{}});if(!r.ok)throw Error('ダウンロード失敗');const u=URL.createObjectURL(await r.blob());const link=document.createElement('a');link.href=u;link.download=f.name.split('/').pop();link.click();setTimeout(()=>URL.revokeObjectURL(u),1000);});};list.append(a);}b.append(list);}
$('form').addEventListener('input',e=>{dirty=true;changed.add(e.target.id);});
$('applyForm').onclick=()=>action(async()=>{apply();await sync();notice('設定を反映し、検証しました。');});
$('yamlTab').onclick=()=>action(async()=>{if(dirty)apply();tab(true);});$('formTab').onclick=()=>action(async()=>{await sync();tab(false);});
$('validate').onclick=()=>action(async()=>{if(dirty)apply();await sync();notice('検証に成功しました。');});
$('save').onclick=()=>action(async()=>{if(dirty)apply();await api('/profiles/'+encodeURIComponent($('profileName').value),{method:'PUT',body:body()});await loadProfiles();notice('プロファイルを保存しました。');});
$('run').onclick=()=>action(async()=>{if(dirty)apply();$('run').disabled=true;try{const j=await api('/jobs',{method:'POST',body:body()});selected=j.id;notice('ジョブをキューへ登録しました。');await refresh();}finally{$('run').disabled=false;}});
$('profiles').onchange=()=>action(async()=>{if($('profiles').value==='')return;const p=saved[Number($('profiles').value)];$('profileName').value=p.name;$('yaml').value=p.yaml;await sync();tab(true);notice('プロファイルを読み込みました。');});
$('refresh').onclick=()=>action(refresh);$('auth').onclick=()=>{const t=prompt('APIトークン（空欄で解除）',token);if(t!==null){token=t;sessionStorage.setItem('lorasim-token',token);action(async()=>{await loadProfiles();await refresh();});}};
editor();action(async()=>{await loadProfiles();await refresh();});setInterval(()=>action(refresh),2000);
