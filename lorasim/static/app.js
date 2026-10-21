'use strict';
const $=id=>document.getElementById(id), clone=x=>structuredClone(x);
let profile={}, schema={}, defaults={}, catalog={}, savedProfiles=[], experiments=[], currentView='experiment', selectedJob=null, selectedItem=null, mapData=null, analysisData=null, modelSpec={kind:'fixed',options:{}}, previewVisible=true, busyPoll=false, analysisBusy=false, analysisRevision=0;
const pageOffsets=new Map();
let calculator=null, mapRenderer=null, mapFrame=null, preflightTab="layout";
const labels={name:'名前',seed:'乱数seed',duration_s:'シミュレーション時間 [s]',area_m:'エリア [m]',channel:'伝搬・干渉',mac:'Class A MAC',sub_bands:'サブバンド',gateways:'Gateway',groups:'EDグループ',assignments:'条件による割り当て',tracking:'ストリーミング・メモリ上限',notifications:'進捗Webhook',channel_backend:'伝搬計算バックエンド',energy:'消費電力',models:'プロファイル内のモデル定義',policy:'選択アルゴリズム',mobility:'移動モデル',timing:'送信タイミング',count:'ED数',placement:'初期配置',positions:'初期座標 [x,y,z]',position:'座標 [x,y,z]',payload_bytes:'ペイロード [byte]',confirmed:'Confirmed UL',max_attempts:'最大送信試行数',observe_position:'EDに位置観測を許可',tags:'タグ',ids:'ED ID',radio:'無線',mcu:'MCU（null＝全体設定）',options:'パラメータ',kind:'実装',ref:'ライブラリ参照',enabled:'有効',initial:'初期送信設定',mode:'計算方式'};
const colors={Received:'#238aa0',Interference:'#db9952',NoMoreReceivers:'#a885be',UnderSensitivity:'#da7083',LostBecauseTx:'#6e91c4',WrongFrequency:'#a2aebd',MixedLoss:'#807d88',Censored:'#bec7d0',Cancelled:'#aab4bf',SimulationEnd:'#aab4bf'};
const causeLabel={Received:'受信成功',Interference:'干渉',NoMoreReceivers:'復調器不足',UnderSensitivity:'感度未満',LostBecauseTx:'GW送信中',WrongFrequency:'周波数不一致',MixedLoss:'複合損失',Censored:'判定未了'};
const color=k=>colors[k]||['#3887a5','#9e81b4','#c38c54','#7b9f62','#d07887','#6385be'][Array.from(k).reduce((a,c)=>a+c.charCodeAt(0),0)%6];
function el(tag,attrs={},...children){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k==='class')e.className=v;else if(k==='text')e.textContent=v;else if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else if(k==='checked')e.checked=v;else e.setAttribute(k,v);}for(const child of children.flat())if(child!==null&&child!==undefined)e.append(child instanceof Node?child:document.createTextNode(String(child)));return e;}
function notice(text,error=false){delete $('notice').dataset.signature;$('notice').setAttribute('role','status');$('notice').replaceChildren(el('span',{},text),el('button',{class:'small quiet notice-close','aria-label':'通知を閉じる',onclick:()=>{$('notice').style.display='none';}},'閉じる'));$('notice').className=error?'error':'';$('notice').style.display='block';}
class DiagnosticError extends Error{constructor(messages){super(messages[0]?.message||'処理に失敗しました。');this.diagnostics=messages;}}
function localDiagnostic(e){return {severity:'error',code:'BROWSER',path:'',message:/[ぁ-んァ-ン一-龯]/.test(e.message||'')?e.message:'画面の処理中にエラーが発生しました。',hint:'入力内容と接続状態を確認してください。解消しない場合は技術詳細を添えて報告してください。',detail:String(e.stack||e.message||e)};}
async function api(path,method='GET',body){
 const token=sessionStorage.getItem('lorasim-token')||'';let r;
 try{r=await fetch(path,{method,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})});}
 catch(e){throw new DiagnosticError([{severity:'error',code:'CONNECTION',message:'APIサーバーに接続できません。',hint:'ネットワーク、APIプロセス、接続先URLを確認してください。接続断だけではバックグラウンド実験は停止しません。',detail:e.message}]);}
 if(!r.ok){let data;const raw=await r.text();try{data=JSON.parse(raw);}catch{}const error=new DiagnosticError(data?.diagnostics||[{severity:'error',code:'HTTP_'+r.status,message:`サーバーがリクエストを処理できませんでした（HTTP ${r.status}）。`,hint:'接続設定、API・プロキシのログを確認してください。',detail:typeof data?.detail==='string'?data.detail:raw.slice(0,8000)}]);error.status=r.status;throw error;}
 try{return await r.json();}catch(e){throw new DiagnosticError([{severity:'error',code:'RESPONSE_FORMAT',message:'APIからの応答を読み取れません。',hint:'APIと画面のバージョン、接続先URL、プロキシ設定を確認してください。',detail:e.message}]);}
}
function reportError(e){if(currentView==='experiment'&&e.diagnostics?.some(d=>d.path))setConfigDiagnostics(e.diagnostics);renderDiagnostics('notice',e.diagnostics||[localDiagnostic(e)],true);$('notice').className='diagnostic-notice';$('notice').style.display='block';$('notice').setAttribute('role','alert');}
const action=fn=>async(...args)=>{const button=args[0]?.currentTarget;if(button?.tagName==='BUTTON')button.disabled=true;try{await fn(...args);}catch(e){reportError(e);}finally{if(button?.tagName==='BUTTON')button.disabled=false;}};
const sections=[
 {id:'general',menu:'基本設定',title:'基本設定・プロファイル',description:'保存する条件、実験時間、乱数シード、エリアを設定します。',keys:['name','duration_s','seed','area_m']},
 {id:'devices',title:'ED・モデルの設定',description:'EDグループごとに配置、選択アルゴリズム、送信タイミング、移動モデルを設定します。',keys:['groups']},
 {id:'gateways',title:'ゲートウェイ',description:'GWの配置、受信チャネル、同時復調数を設定します。',keys:['gateways']},
 {id:'bands',title:'周波数・行動空間',description:'サブバンド、UL/DL許可帯域、SF×CH×CR×TPの候補集合を設定します。',keys:['sub_bands','ul_sub_bands','dl_sub_bands','action_space']},
 {id:'radio',title:'伝搬・MAC',description:'伝搬損失と干渉モデル、Class Aの受信ウィンドウを設定します。',keys:['channel','mac']},
 {id:'assignments',menu:'モデルの割り当て',title:'条件による割り当て',description:'グループ・タグ・ED IDでモデルを割り当てます。複数一致すると後の指定を優先します。',keys:['assignments','models']},
 {id:'energy',title:'消費エネルギー',description:'無線とMCUの消費エネルギーの計算条件を設定します。',keys:['energy']},
 {id:'execution',title:'実行・記録・通知',description:'高速化、保存とメモリの上限、進捗通知を設定します。',keys:['execution','tracking','channel_backend','notifications']},
 {id:'yaml',title:'YAMLを編集',description:'宣言的な設定を編集し「GUIに反映・検証」で適用します。未適用の編集内容では実行されません。',keys:[]}
];
let selectedSection='general',configDiagnostics=[],diagnosticTimer=null,configRevision=0,diagnosticSeq=0,yamlDraft=false;
function renderMenu(){
 $('settingsMenu').replaceChildren(...sections.map((section,i)=>{const count=configDiagnostics.filter(d=>section.keys.includes((d.path||'').split('.')[0])).length;return el('button',{class:'settings-menu-item'+(selectedSection===section.id?' active':''),'aria-current':selectedSection===section.id?'page':'false',onclick:()=>selectSection(section.id)},el('span',{class:'menu-number'},String(i+1).padStart(2,'0')),el('span',{},section.menu||section.title),count?el('span',{class:'menu-count'+(configDiagnostics.some(d=>d.severity==='error'&&section.keys.includes((d.path||'').split('.')[0]))?'':' warning')},count):null);}));
}
function selectSection(id){selectedSection=id;renderAll();$('editorPanel').scrollTop=0;if(window.innerWidth<=760)$('editorPanel').scrollIntoView({behavior:'smooth',block:'start'});}
function jumpToField(path){const parts=path.split('.');for(let i=0;i<parts.length;i++)if(/^\d+$/.test(parts[i]))pageOffsets.set(parts.slice(0,i).join('.'),Math.floor(Number(parts[i])/50)*50);const section=sections.find(s=>s.keys.includes(parts[0]));if(!section)return;switchView('experiment');selectedSection=section.id;renderAll();const input=[...$('configForm').querySelectorAll('input,select,textarea')].find(e=>e.getAttribute('aria-label')===path);const target=input||[...$('configForm').querySelectorAll('details')].find(e=>e.dataset.path===path)||$('editorPanel');for(let e=target;e;e=e.parentElement)if(e.tagName==='DETAILS')e.open=true;target.scrollIntoView({behavior:'smooth',block:'center'});if(input)input.focus({preventScroll:true});}
function renderDiagnostics(id,messages,links=false){
 const root=$(id),signature=JSON.stringify(messages);if(root.dataset.signature===signature&&root.childElementCount)return;root.dataset.signature=signature;root.replaceChildren();
 const card=d=>{const box=el('article',{class:'diagnostic '+d.severity},el('div',{class:'diagnostic-title'},d.severity==='warning'?'Warning · 要確認':'Error · 処理できません',el('span',{class:'diagnostic-code'},d.code||'')),el('p',{},d.message),el('p',{class:'diagnostic-hint'},'ヒント：'+d.hint));if(d.path)box.append(links?el('button',{class:'diagnostic-path',onclick:()=>jumpToField(d.path)},'設定を確認 → '+d.path):el('span',{class:'hint'},'設定項目: '+d.path));if(d.detail)box.append(el('details',{},el('summary',{},'技術詳細（元のエラー）'),el('pre',{},d.detail)));return box;};
 root.append(...messages.slice(0,4).map(card));if(messages.length>4)root.append(el('details',{class:'diagnostic-more'},el('summary',{},`ほか ${messages.length-4} 件の診断`),messages.slice(4).map(card)));
}
function markFields(){for(const e of $('configForm').querySelectorAll('input,select,textarea')){const path=e.getAttribute('aria-label');const found=configDiagnostics.filter(d=>d.path&&(path===d.path||path?.startsWith(d.path+'.')));const error=found.some(d=>d.severity==='error');e.classList.toggle('field-invalid',error);e.classList.toggle('field-warning',!error&&found.length>0);e.setAttribute('aria-invalid',String(error));}for(const e of $('configForm').querySelectorAll('details[data-path]'))e.classList.toggle('invalid-group',configDiagnostics.some(d=>d.severity==='error'&&(d.path===e.dataset.path||d.path?.startsWith(e.dataset.path+'.'))));}
function setConfigDiagnostics(messages,full=false){configDiagnostics=messages;const errors=messages.filter(d=>d.severity==='error').length,warnings=messages.length-errors;$('validationState').textContent=errors?`Error ${errors} 件`:warnings?`Warning ${warnings} 件`:full?'検証済み':'入力チェック済み';$('validationState').className='badge '+(errors?'error':warnings?'warning':'success');$('validationNote').textContent=errors?'該当する設定を修正してください。元のエラーは技術詳細で確認できます。':full?'設定とモデル初期化を検証しました。実行中の動作や学習の成功を保証するものではありません。':'設定形式と公開された定数をチェックしました。モデル初期化は「設定を検証」で確認できます。';renderDiagnostics('configMessages',messages,true);renderMenu();markFields();}
async function checkConfiguration(){const seq=++diagnosticSeq,rev=configRevision;try{const r=await api('/api/diagnostics','POST',{yaml:JSON.stringify(profile)});if(seq===diagnosticSeq&&rev===configRevision)setConfigDiagnostics(r.diagnostics);}catch(e){if(seq===diagnosticSeq&&rev===configRevision)setConfigDiagnostics(e.diagnostics||[localDiagnostic(e)]);}}
async function validatedProfile(text){clearTimeout(diagnosticTimer);++diagnosticSeq;const revision=configRevision;try{const r=await api('/api/validate?report=true','POST',{yaml:text});if(revision!==configRevision)throw new DiagnosticError([{severity:'error',code:'CONFIG_CHANGED',message:'検証中に設定が変更されました。',hint:'現在の設定でもう一度検証してください。'}]);yamlDraft=false;setConfigDiagnostics(r.diagnostics,true);return r.profile;}catch(e){setConfigDiagnostics(e.diagnostics||[localDiagnostic(e)]);throw e;}}
function resolve(s){if(s?.$ref)return {...schema.$defs[s.$ref.split('/').pop()],...Object.fromEntries(Object.entries(s).filter(([k])=>k!=='$ref'))};return s||{};}
function normalize(s,v){s=resolve(s);if(s.anyOf){return {...resolve(s.anyOf.find(x=>x.type!=='null')||{}),...Object.fromEntries(Object.entries(s).filter(([k])=>k!=='anyOf'))};}return s;}
function defaultValue(s){s=resolve(s);if(s.default!==undefined)return clone(s.default);if(s.anyOf)return null;if(s.enum)return s.enum[0];if(s.type==='object'||s.properties){const o={};for(const[k,v]of Object.entries(s.properties||{}))o[k]=defaultValue(v);return o;}if(s.type==='array'){return s.prefixItems?s.prefixItems.map(defaultValue):[];}if(s.type==='integer'||s.type==='number')return s.minimum??0;if(s.type==='boolean')return false;return '';}
function infer(v){return Array.isArray(v)?{type:'array',items:v.length?infer(v[0]):{type:'string'}}:v&&typeof v==='object'?{type:'object'}:{type:typeof v==='number'?'number':typeof v==='boolean'?'boolean':'string'};}
function categoryFor(path){return path.includes('mobility')?'mobility':path.includes('timing')?'timing':'policy';}
function syncYaml(){if(!yamlDraft)$('yaml').value=JSON.stringify(profile,null,2);}
const pluginSchemas={policy:{},timing:{},mobility:{}};
let previewDirty=false, tooltipSeq=0;
function info(textOrLoad,label='設定の説明'){
 const button=el('button',{type:'button',class:'info','aria-label':label},'i');
 let tip=null,active=false;
 const show=async()=>{if(active)return;active=true;tip=el('div',{class:'tooltip',role:'tooltip',id:'tip-'+(++tooltipSeq)},'読み込み中…');document.body.append(tip);button.setAttribute('aria-describedby',tip.id);const r=button.getBoundingClientRect();tip.style.left=Math.min(window.innerWidth-330,Math.max(8,r.left))+'px';tip.style.top=Math.min(window.innerHeight-230,r.bottom+8)+'px';try{const text=typeof textOrLoad==='function'?await textOrLoad():textOrLoad;if(active){tip.textContent=text;tip.style.top=Math.max(8,Math.min(r.bottom+8,window.innerHeight-tip.offsetHeight-12))+'px';}}catch(e){if(active)tip.textContent=e.message;}};
 const hide=()=>{active=false;tip?.remove();tip=null;button.removeAttribute('aria-describedby');};
 button.addEventListener('mouseenter',show);button.addEventListener('mouseleave',hide);button.addEventListener('focus',show);button.addEventListener('blur',hide);button.onclick=e=>{e.preventDefault();e.stopPropagation();active?hide():show();};button.onkeydown=e=>{if(e.key==='Escape')hide();};return button;
}
function caption(key,s={}){const name=s.title||labels[key]||(/^\d+$/.test(key)?'項目 '+(Number(key)+1):key);return el('span',{class:'caption'},name,info(s.description||name+' の設定値です。',name+'の説明'));}
function inlineSchema(s,defs=s.$defs||{},seen=[]){if(!s||typeof s!=='object')return s;if(Array.isArray(s))return s.map(x=>inlineSchema(x,defs,seen));if(s.$ref){const key=s.$ref.split('/').pop();if(seen.includes(key))return {type:'object'};return {...inlineSchema(defs[key]||{},defs,[...seen,key]),...Object.fromEntries(Object.entries(s).filter(([k])=>k!=='$ref'))};}return Object.fromEntries(Object.entries(s).filter(([k])=>k!=='$defs').map(([k,v])=>[k,inlineSchema(v,defs,seen)]));}
function componentValue(value,cat,seen=[]){if(!value.ref)return value;if(seen.includes(value.ref))throw Error('循環したモデル参照です');const base={...(catalog[cat]||{}),...(profile.models?.[cat]||{})}[value.ref];if(!base)throw Error('モデル参照が見つかりません: '+value.ref);const resolved=componentValue(base,cat,[...seen,value.ref]);return {...resolved,action_space:value.action_space??resolved.action_space,options:{...resolved.options,...value.options}};}
function dirty(){if(preflightTab==='abstract')$('abstractTool').dataset.stale='true';calculator?.invalidate();previewDirty=true;$('map').classList.add('map-stale');configRevision++;++diagnosticSeq;$('mapInfo').textContent='設定変更あり · 再確認してください';$('validationState').textContent='変更あり・確認待ち';$('validationState').className='badge';$('validationNote').textContent='前回の診断は変更前の設定に対するものです。入力を止めると再チェックします。';syncYaml();clearTimeout(diagnosticTimer);diagnosticTimer=setTimeout(checkConfiguration,900);}
function newAutoIds(){for(const [collection,key,prefix] of [['gateways','id','gw'],['groups','name','group'],['sub_bands','id','band']]){const rows=profile[collection]||[],used=new Set(rows.map(x=>x[key]).filter(Boolean));let i=0;for(const row of rows)if(!row[key]){while(used.has(prefix+i))i++;row[key]=prefix+i;used.add(row[key]);}}}
function frequencyOptions(direction){const allowed=profile[direction+'_sub_bands'];return (profile.sub_bands||[]).filter(b=>allowed===null||allowed===undefined||allowed.includes(b.id)).flatMap(b=>(b.channels_hz||[]).map(f=>({value:f,label:`${(f/1e6).toFixed(3)} MHz · ${b.id}`})));}
function buildField(parent,key,value,s,path,onChange){
 const original=resolve(s);s=normalize(s,value);if(!s.type&&!s.properties)s={...infer(value),...s};if(s.const!==undefined)s={...s,enum:[s.const]};
 const title=caption(key,{...s,title:s.title||labels[key]});
 if(['ul_sub_bands','dl_sub_bands'].includes(key)){
  const box=el('details',{},el('summary',{},title)),all=el('input',{type:'checkbox',checked:value===null,onchange:e=>{onChange(e.target.checked?null:(profile.sub_bands||[]).map(b=>b.id));renderAll();}});box.append(el('label',{class:'check'},all,'すべて許可'));
  for(const band of profile.sub_bands||[])box.append(el('label',{class:'check'},el('input',{type:'checkbox',checked:value===null||value.includes(band.id),onchange:e=>{const allowed=new Set(value??profile.sub_bands.map(b=>b.id));e.target.checked?allowed.add(band.id):allowed.delete(band.id);onChange([...allowed]);renderAll();}}),`${band.id} · ${band.low_hz/1e6}–${band.high_hz/1e6} MHz`));parent.append(box);return;
 }
 if(value===null){parent.append(el('div',{class:'fieldrow'},el('label',{},title),el('button',{class:'small',type:'button',onclick:()=>{onChange(defaultValue({...s,default:undefined}));renderAll();}},'設定する')));return;}
 const nullable=original.anyOf?.some(x=>x.type==='null');
 if(s.properties?.kind&&s.properties?.options||s.title==='Component'||(value&&typeof value==='object'&&!Array.isArray(value)&&'kind'in value&&'options'in value)){
  const cat=categoryFor(path),wrap=el('details',{'data-path':path},el('summary',{},title));wrap.open=true;
  let effective;try{effective=componentValue(value,cat);}catch(e){effective=value;wrap.append(el('p',{class:'hint'},e.message));}
  const meta=pluginSchemas[cat][effective.kind],selector=el('select',{'aria-label':path+' implementation'});
  for(const[k,m]of Object.entries(pluginSchemas[cat]))if(!m.deprecated||k===effective.kind)selector.append(el('option',{value:k},m.label+(m.deprecated?'（旧名・互換）':'')));
  if(!pluginSchemas[cat][value.kind])selector.append(el('option',{value:value.kind},value.kind));
  const refs={...(catalog[cat]||{}),...(profile.models?.[cat]||{})};for(const k of Object.keys(refs))selector.append(el('option',{value:'@'+k},'保存済み: '+k));selector.append(el('option',{value:'__custom'},'独自Pythonクラス…'));selector.value=value.ref?'@'+value.ref:value.kind;
  selector.onchange=()=>{onChange(selector.value==='__custom'?{kind:'my_package:MyClass',options:{}}:selector.value.startsWith('@')?{kind:'fixed',ref:selector.value.slice(1),options:{}}:newComponent(cat,selector.value));renderAll();};wrap.append(el('label',{},caption('kind',schema.$defs.Component.properties.kind),selector));
  if(meta)wrap.append(el('p',{class:'hint'},meta.description));
  const bound=cat==='policy'&&(effective.action_space!=null||meta?.uses_action_space);
  if(cat==='policy'&&meta?.accepts_action_space){
   wrap.append(el('label',{class:'check'},el('input',{type:'checkbox',checked:bound,...(meta.uses_action_space?{disabled:true}:{}),onchange:e=>{const next={...(value.ref?effective:value),ref:null,options:clone(effective.options||{}),action_space:e.target.checked?defaultSpaceSelection(meta):null};if(e.target.checked)for(const key of meta.space_options||[])delete next.options[key];onChange(next);renderAll();}}),'全体のパラメータ空間を使用',info('SF・CH・CR・TPは下の候補集合で指定し、モデルへ全送信設定を渡します。旧プロファイルはオフで元の動作を維持します。')));
   if(bound)spaceEditor(wrap,effective.action_space||{},v=>{onChange({...value,action_space:v});renderAll();},path+'.action_space',effective.kind);
  }else if(cat==='policy')wrap.append(el('p',{class:'hint'},'このモデルは原方式固有のパラメータ制御を使います。全体行動空間の注入には未対応です。'));

  if(meta?.requires_position)wrap.append(el('p',{class:'hint'},'このモデルを使うEDグループは「EDによる位置観測を許可」をオンにしてください。'));
  if(!value.ref&&value.kind.includes(':')){const input=el('input',{value:value.kind,'aria-label':path+'.kind'});wrap.append(el('label',{},caption('kind',schema.$defs.Component.properties.kind),input),el('button',{class:'small',onclick:action(async()=>{const m=await api('/api/plugins/schema?'+new URLSearchParams({category:cat,kind:input.value}));pluginSchemas[cat][input.value]=m;onChange({kind:input.value,options:clone(m.defaults)});renderAll();renderLibrary();notice('公開された設定項目を読み込みました。');})},'設定項目を読み込む'));}
  const opts={...clone(meta?.defaults||{}),...clone(effective.options||{})},optsSchema=meta?inlineSchema(meta.schema):{type:'object'};
  if(meta?.module==='lorasim.plugins.mobility.waypoints:Waypoints'&&!('mode' in (effective.options||{}))&&'points' in (effective.options||{}))opts.mode='timed';
  const visibility=meta?.option_visibility,visibleMode=visibility?opts[visibility.field]:null;
  if(visibility){const keys=visibility.values[visibleMode];if(keys){for(const key of Object.keys(opts))if(!keys.includes(key))delete opts[key];for(const key of Object.keys(optsSchema.properties||{}))if(!keys.includes(key))delete optsSchema.properties[key];}}
  if(bound)for(const key of meta?.space_options||[]){delete opts[key];if(optsSchema.properties)delete optsSchema.properties[key];}
  buildField(wrap,'options',opts,{...optsSchema,title:'モデルの定数',description:meta?.legacy?'旧インターフェースです。Optionsクラスを追加すると型・説明を公開できます。':'各モデルが公開した定数。参照モデルもグループ単位で上書きできます。'},path+'.options',v=>{onChange({...value,options:visibility?{...effective.options,...v}:v});if(visibility&&v[visibility.field]!==visibleMode)renderAll();});
  if(value.ref)wrap.append(el('button',{class:'small',onclick:()=>{onChange({...value,options:{}});renderAll();}},'上書きを解除して参照値に戻す'));
  if(nullable)wrap.append(el('button',{class:'small',onclick:()=>{onChange(null);renderAll();}},'未指定に戻す'));parent.append(wrap);return;
 }
 if(s.type==='object'||s.properties){
  if(path==='action_space')parent.append(el('p',{class:'space-total'},spaceCount(value)+' · 全体で利用可能'));
  const wrap=el('details',{'data-path':path},el('summary',{},title));if(path.includes('library')||/^[0-9]+$/.test(key)||key==='options'||key==='start')wrap.open=true;
  const fields=el('div',{class:'nested'});wrap.append(fields);
  const values={...value};for(const[k,cs]of Object.entries(s.properties||{}))if(!(k in values)&&cs.default!==undefined)values[k]=clone(cs.default);
  for(const[k,v]of Object.entries(values)){if(k==='model_files'||k==='schema_version')continue;let cs=s.properties?.[k]||s.additionalProperties||infer(v);if(cs===true)cs=infer(v);buildField(fields,k,v,cs,path+'.'+k,nv=>{value[k]=nv;onChange(value);});if(!s.properties||s.additionalProperties)fields.append(el('button',{class:'small',onclick:()=>{delete value[k];onChange(value);renderAll();}},k+' を削除'));}
  const unused=Object.keys(s.properties||{}).filter(k=>!(k in values)&&!['schema_version','model_files'].includes(k));
  if(unused.length){const pick=el('select',{'aria-label':path+' optional field'},unused.map(k=>el('option',{value:k},s.properties[k].title||labels[k]||k)));fields.append(el('div',{class:'addrow'},pick,el('button',{class:'small',onclick:()=>{value[pick.value]=defaultValue(s.properties[pick.value]);onChange(value);renderAll();}},'+ 項目')));}
  if(!s.properties||s.additionalProperties){const name=el('input',{placeholder:'キー名','aria-label':path+' new key'}),type=el('select',{'aria-label':path+' new type'},['number','string','boolean','object','array'].map(k=>el('option',{value:k},k)));fields.append(el('div',{class:'addrow'},name,type,el('button',{class:'small',onclick:()=>{if(!name.value||['__proto__','prototype','constructor'].includes(name.value))return;value[name.value]=defaultValue({type:type.value});onChange(value);renderAll();}},'+')));}
  if(nullable)fields.append(el('button',{class:'small',onclick:()=>{onChange(null);renderAll();}},'未指定に戻す'));parent.append(wrap);return;
 }
 if(s.type==='array'||Array.isArray(value)){
  const wrap=el('details',{'data-path':path},el('summary',{},title,` · ${value.length}`)),start=Math.min(pageOffsets.get(path)||0,Math.max(0,value.length-1));
  for(let i=start;i<Math.min(value.length,start+50);i++){const idx=i,item=el('div',{class:'arrayitem'}),header=el('div',{class:'arrayhead'},value[i]?.name||value[i]?.id||'#'+i);if(!s.prefixItems)header.append(el('button',{class:'small',onclick:()=>{value.splice(idx,1);onChange(value);renderAll();}},'削除'));item.append(header);let cs=s.prefixItems?.[i]||s.items||infer(value[i]);if(key==='actions'&&!cs.properties)cs=schema.$defs.Radio;if(resolve(cs).type==='object')cs={...resolve(cs),title:value[i]?.name||value[i]?.id||'項目 '+(i+1),description:s.description};if(['frequencies_hz'].includes(key))cs={...cs,format:'frequency'};if(s.prefixItems)cs={...cs,title:(key==='area_m'?['幅 [m]','奥行き [m]']:s.prefixItems.length===4?['時刻 [s]','X座標 [m]','Y座標 [m]','高さ [m]']:['X座標 [m]','Y座標 [m]','高さ [m]'])[i],description:s.description};buildField(item,String(i),value[i],cs,path+'.'+i,nv=>{value[idx]=nv;onChange(value);});wrap.append(item);}
  if(value.length>50)wrap.append(el('div',{class:'actions'},el('button',{class:'small',onclick:()=>{pageOffsets.set(path,Math.max(0,start-50));renderAll();}},'前へ'),el('span',{class:'hint'},`${start+1}–${Math.min(value.length,start+50)}`),el('button',{class:'small',onclick:()=>{pageOffsets.set(path,Math.min(Math.floor((value.length-1)/50)*50,start+50));renderAll();}},'次へ')));
  if(!s.prefixItems){const flexible=!s.items||Object.keys(s.items).length===0;const picker=el('select',{'aria-label':path+' element type'},['number','string','boolean','object','array'].map(t=>el('option',{value:t},t)));const button=el('button',{class:'small',onclick:()=>{let v=defaultValue(flexible?{type:picker.value}:s.items);if(key==='gateways')v={...v,id:'',position:[profile.area_m[0]/2,profile.area_m[1]/2,15],frequencies_hz:frequencyOptions('ul').map(f=>f.value)};if(key==='groups')v={...clone(defaults.profile.groups[0]),name:''};if(key==='sub_bands')v={id:'',low_hz:870000000,high_hz:871000000,duty_cycle:.01,max_tp_dbm:14,channels_hz:[870100000]};value.push(v);onChange(value);newAutoIds();renderAll();},},'+ 追加');wrap.append(flexible?el('div',{class:'addrow'},picker,button):button);}parent.append(wrap);return;
 }
 let input;const attrs={'aria-label':path};
 const frequencies=(key==='frequency_hz'||key==='rx2_frequency_hz'||s.format==='frequency')?frequencyOptions(key==='rx2_frequency_hz'?'dl':'ul'):null;
 if(frequencies){input=el('select',attrs,frequencies.map(f=>el('option',{value:f.value},f.label)));if(!frequencies.some(f=>f.value===value))input.prepend(el('option',{value},`${value} Hz（候補未登録・制約を確認）`));input.value=value;input.onchange=()=>onChange(Number(input.value));}
 else if(s.enum){input=el('select',attrs,s.enum.map(x=>el('option',{value:x},x)));input.value=value;input.onchange=()=>onChange(typeof value==='number'?Number(input.value):input.value);}
 else if(s.type==='boolean')input=el('input',{...attrs,type:'checkbox',checked:value,onchange:e=>onChange(e.target.checked)});
 else{const numeric=s.type==='integer'||s.type==='number';input=el('input',{...attrs,type:numeric?'number':'text',value,step:s.type==='integer'?'1':'any',oninput:e=>onChange(numeric?e.target.valueAsNumber:e.target.value)});if(s.minimum!==undefined)input.min=s.minimum;if(s.maximum!==undefined)input.max=s.maximum;}
 if(key==='id')input.placeholder='空欄で自動採番';if(path.startsWith('sub_bands.'))input.addEventListener('change',()=>{newAutoIds();renderAll();});
 const label=el('label',{class:s.type==='boolean'?'check':''});if(s.type==='boolean')label.append(input,title);else label.append(title,input);if(path==='name'){const row=el('div',{class:'fieldrow'},input,el('button',{type:'button',onclick:()=>{try{const name=ProfileSummary.name(profile,componentValue);input.value=name;onChange(name);}catch(e){reportError(e);}},title:'ED数・選択器・移動モデル・日本時間・一意IDから名称を反映'},'名称を反映'));label.append(row);}parent.append(label);
}
function renderAll(){if(Object.keys(analysisModules).length)renderModuleEditor();const open=new Set([...document.querySelectorAll('#configForm details[open]')].map(e=>e.dataset.path));$('configForm').replaceChildren();const section=sections.find(s=>s.id===selectedSection);$('editorTitle').textContent=section.title;$('editorDescription').textContent=section.description;$('editorIndex').textContent=String(sections.indexOf(section)+1).padStart(2,'0');$('profileManagement').hidden=section.id!=='general';$('yamlPanel').hidden=section.id!=='yaml';
 for(const k of section.keys){const v=profile[k];if(v===undefined)continue;buildField($('configForm'),k,v,schema.properties?.[k]||infer(v),k,nv=>{profile[k]=nv;dirty();});}for(const d of $('configForm').querySelectorAll('details'))if(open.has(d.dataset.path)||section.keys.includes(d.dataset.path))d.open=true;
 $('modelForm').replaceChildren();buildField($('modelForm'),'model',modelSpec,{$ref:'#/$defs/Component',title:'モデル定義',description:'実装と再利用する定数の組み合わせです。'},'library.'+$('modelCategory').value,v=>{modelSpec=v;});renderMenu();markFields();syncYaml();}
async function loadSchemas(){const pending=[];function scan(v,cat){if(!v||typeof v!=='object')return;if(v.kind&&v.kind.includes(':')&&!pluginSchemas[cat]?.[v.kind])pending.push([cat,v.kind]);for(const[k,x]of Object.entries(v))if(typeof x==='object')scan(x,['policy','timing','mobility'].includes(k)?k:cat);}scan(profile,'policy');scan(catalog,'policy');for(const[cat,kind]of pending){try{pluginSchemas[cat][kind]=await api('/api/plugins/schema?'+new URLSearchParams({category:cat,kind}));}catch(e){reportError(e);}}}
function download(name,text,type='application/json'){const url=URL.createObjectURL(new Blob([text],{type})),a=el('a',{href:url,download:name});a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function editModel(cat,name,spec){$('modelCategory').value=cat;$('modelName').value=name;modelSpec=clone(spec);renderAll();$('modelEditorTitle').textContent=name;}
function renderLibrary(){const search=$('modelSearch').value.toLowerCase(),cat=$('modelCategory').value;$('library').replaceChildren();for(const[k,m]of Object.entries(pluginSchemas[cat])){if(m.deprecated||!(k+' '+m.label).toLowerCase().includes(search))continue;$('library').append(el('article',{class:'modelcard'},el('div',{class:'sectiontitle'},el('h3',{},m.label),el('span',{class:'badge'},m.module.startsWith('lorasim.plugins.')?'組み込み':'独自実装')),el('code',{},m.module),el('p',{class:'hint'},m.description),el('button',{class:'small',onclick:()=>editModel(cat,'my-'+k,newComponent(cat,k))},'この実装から定義を作る'),el('button',{class:'small',onclick:action(async()=>{editModel(cat,'my-'+k,newComponent(cat,k));await tuningPanel.load();$('tuningPanel').scrollIntoView({behavior:'smooth'});})},'このモデルを調整')));}for(const[name,spec]of Object.entries(catalog[cat]||{})){if(!(name+' '+spec.kind).toLowerCase().includes(search))continue;const uses=(profile.groups||[]).filter(g=>g[cat]?.ref===name).map(g=>g.name);$('library').append(el('article',{class:'modelcard saved'},el('div',{class:'sectiontitle'},el('h3',{},name),el('span',{class:'badge'},'保存済み')),el('code',{},spec.kind),el('p',{class:'hint'},uses.length?'設定中のグループ: '+uses.join(', '):'現在のグループでの直接参照なし'),el('div',{class:'actions'},el('button',{class:'small',onclick:()=>editModel(cat,name,spec)},'設定を編集'),el('button',{class:'small',onclick:()=>editModel(cat,name+'-copy',spec)},'複製'),el('button',{class:'small',onclick:action(async()=>{editModel(cat,name,spec);await tuningPanel.load();$('tuningPanel').scrollIntoView({behavior:'smooth'});})},'このモデルを調整'))));}}
async function refreshCatalog(){catalog=await api('/api/models');renderLibrary();}
const experimentInfoCache=new Map();
async function experimentText(id){let item=experimentInfoCache.get(id);if(!item||Date.now()-item.at>10000){item={at:Date.now(),data:await api('/api/experiments/'+id+'/info')};experimentInfoCache.set(id,item);}const e=item.data,r=e.latest_run,env=e.environment,fmt=v=>v?new Date(v*1000).toLocaleString('ja-JP'):'未実行';return [e.name,'ID: '+e.id,e.description,`作成: ${fmt(e.created)}`,`開始: ${fmt(e.first_started)} / 最終終了: ${fmt(e.last_finished)}`,`実行時間の合計: ${num(e.wall_total_s)} s`,Object.entries(e.statuses).map(([k,v])=>k+': '+v).join(' · '),r?`最新Run: ${r.ed_count} ED / ${r.gw_count} GW / 仮想時間 ${r.duration_s} s / seed ${r.seed}`:'Runなし',r?Object.entries(r.models).slice(0,4).map(([k,v])=>k+': '+Object.values(v).join(' / ')).join('\n'):'',env?`最後に実行した環境 (${e.environment_run_id.slice(0,8)}):\n${env.platform}\nPython ${env.python} / NumPy ${env.numpy} / LoRaSim ${env.simulator_version}`:'実行環境: 記録なし'].filter(Boolean).join('\n');}
async function refreshProfiles(){savedProfiles=await api('/api/profiles');const current=$('profiles').value;$('profiles').replaceChildren(el('option',{value:''},'新しいプロファイル'),...savedProfiles.map((p,i)=>el('option',{value:String(i)},p.name)));$('profiles').value=current;}
async function refreshExperiments(){experiments=await api('/api/experiments');const chosen=$('experiment').value,checked=new Set([...$('experimentChecks').querySelectorAll('input:checked')].map(x=>x.value));$('experiment').replaceChildren(el('option',{value:''},'新規作成（プロファイル名）'),...experiments.map(e=>el('option',{value:e.id},e.name+' · '+e.runs+' Runs · '+e.id.slice(0,6))));$('experiment').value=chosen;$('experimentChecks').replaceChildren(...experiments.map(e=>el('label',{class:'check experimentcheck'},el('input',{type:'checkbox',value:e.id,checked:checked.has(e.id),onchange:analysisChanged}),el('span',{},e.name,el('small',{class:'hint'},` · ${e.completed}/${e.runs} 完了 · ${e.id.slice(0,6)}`)),info(()=>experimentText(e.id),e.name+'の実験情報'))));}
function switchView(view){currentView=view;calculator?.setVisible(view==='experiment'&&preflightTab==='calculator');$('runActions').hidden=view!=='experiment';$('resultHeading').textContent=view==='analysis'?'選択中の個別Run結果':'結果・アブストラクト';for(const id of ['experiment','analysis','models','documents']){$(id==='experiment'?'settingsPane':id+'Pane').hidden=view!==id;$(id+'View').hidden=view!==id;}document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b.dataset.view===view));document.querySelector('.pagehead p').textContent=view==='documents'?'左で本文を検索・選択し、中央で仕様と使い方を確認できます。':view==='analysis'?'左でExperimentを選択し、下で分析モジュールを構成します。上に結果が表示されます。':'左で項目を選び、中央で編集・確認。進捗と結果は右側で確認できます。';$('pageTitle').textContent={experiment:'実験ワークスペース',analysis:'比較・分析',models:'モデルライブラリ',documents:'ドキュメント'}[view];if(view==='analysis')action(refreshExperiments)();if(view==='models'){renderLibrary();action(()=>tuningPanel.refreshList())();}document.querySelector('main').classList.toggle('documents-open',view==='documents');if(view==='documents')action(()=>documents.open())();}
const statusLabel={queued:'待機中',running:'実行中',completed:'完了',failed:'失敗',cancelled:'停止済み',interrupted:'中断'};
async function refreshJobs(){const jobs=await api('/api/jobs');$('jobs').replaceChildren(...jobs.slice(0,50).map(j=>el('article',{class:'job-item'+(j.id===selectedJob?' selected':'')},el('div',{class:'job-top'},el('button',{class:'quiet',onclick:action(()=>selectJob(j.id))},j.name),el('span',{class:'status '+j.status},statusLabel[j.status]||j.status)),el('div',{class:'job-bottom'},el('span',{},j.id.slice(0,8)+' · '+(100*j.progress).toFixed(1)+'%'),['queued','running'].includes(j.status)?el('button',{class:'small',onclick:action(async()=>{await api('/api/jobs/'+j.id+'/cancel','POST');await refreshJobs();})},'停止'):el('button',{class:'small',onclick:action(()=>selectJob(j.id))},j.status==='failed'||j.status==='interrupted'?'原因を確認':'結果を表示')))));
 if(!jobs.length)$('jobs').append(el('p',{class:'hint'},'登録されたRunはありません。'));if(jobs.length>50)$('jobs').append(el('p',{class:'hint'},'最新50 Runを表示しています。全件の分析はExperimentから選択できます。'));
 if(selectedJob){const id=selectedJob,item=await api('/api/jobs/'+id);if(id===selectedJob){selectedItem=item;renderResult(item);}}return jobs;}
async function selectJob(id){selectedJob=id;$('files').replaceChildren();const item=await api('/api/jobs/'+id);if(id!==selectedJob)return;selectedItem=item;renderResult(item);await refreshJobs();if(id===selectedJob)await renderFiles();}
function pct(x){return x===null||x===undefined?'—':(100*x).toFixed(1)+'%';}
function num(x){return x===null||x===undefined?'—':Number(x).toPrecision(4);}
function metric(title,value,unit=''){return el('div',{},el('span',{},title),el('strong',{},value,el('small',{},unit)));}
function renderResult(j){$('selectedRun').textContent=j.name+' · '+j.id.slice(0,12);$('resultStatus').textContent=statusLabel[j.status]||j.status;$('progressStatus').textContent=statusLabel[j.status]||j.status;$('progressStatus').className='badge '+(j.status==='failed'||j.status==='interrupted'?'error':j.status==='completed'?'success':'');const elapsed=j.started?Math.max(0,(j.finished||Date.now()/1000)-j.started):null;$('runProgress').replaceChildren(el('p',{},j.name),el('strong',{class:'progress-value'},pct(j.progress)),el('progress',{max:1,value:j.progress,'aria-label':'選択した実験の進捗'}),el('div',{class:'detailrow'},'シミュレーション時刻',el('b',{},num(j.sim_time)+' s')),el('div',{class:'detailrow'},'実行経過時間',el('b',{},elapsed===null?'—':Math.floor(elapsed/60)+'分 '+Math.floor(elapsed%60)+'秒')),el('p',{class:'hint'},j.status==='queued'?'workerが取得するまで待機します。workerの稼働を確認してください。':j.status==='running'?'バックグラウンドで実行中。画面を閉じても処理は継続します。':j.status==='failed'||j.status==='interrupted'?'実験は正常完了していません。以下の原因とヒントを確認してください。':'終了したRunの保存結果を表示しています。'));renderDiagnostics('runDiagnostics',j.diagnostics||[],false);$('rawSummary').textContent=JSON.stringify(j.summary||{progress:j.progress,error:j.error},null,2);const a=j.summary?.abstract;$('metrics').replaceChildren(metric('ED平均PRR',pct(a?.mean_ed_prr)),metric('無線 + MCU',num(j.summary?.energy?j.summary.energy.radio_j+j.summary.energy.mcu_j:null),' J'),metric('対象ED',a?`${a.eligible_ed_count}/${a.ed_count}`:'—'),metric('Jain index',num(a?.fairness?.jain)));$('lossSummary').replaceChildren();$('runDetails').replaceChildren();if(a){const strip=el('div',{class:'strip'});for(const[k,v]of Object.entries(a.fractions))strip.append(el('span',{style:`width:${100*v}%;background:${color(k)}`,title:(causeLabel[k]||k)+' '+pct(v)}));$('lossSummary').append(el('h3',{},'損失要因・成功率'),strip);for(const[k,v]of Object.entries(a.fractions))$('lossSummary').append(el('div',{class:'lossrow'},el('i',{style:'background:'+color(k)}),el('span',{},causeLabel[k]||k),el('span',{},pct(v))));for(const[k,v]of Object.entries({'UL送信（判定済）':a.uplinks,'判定未了':a.censored,'未送信 / 対象外ED':a.inactive_ed_count,'Packet加重PRR':pct(a.pooled_prr),'実行時間 [s]':num(j.summary.wall_s),'無線 [J]':num(j.summary.energy.radio_j),'MCU [J]':num(j.summary.energy.mcu_j)}))$('runDetails').append(el('div',{class:'detailrow'},k,el('b',{},v)));}if(j.error&&!j.diagnostics?.length)renderDiagnostics('runDiagnostics',[localDiagnostic(Error(j.error))]);}
async function renderFiles(){if(!selectedJob)return;const id=selectedJob,files=await api('/api/jobs/'+id+'/files');if(id!==selectedJob)return;$('files').replaceChildren(...files.slice(0,100).map(f=>el('button',{onclick:action(async()=>{const r=await fetch('/api/jobs/'+id+'/files/'+f.name,{headers:sessionStorage.getItem('lorasim-token')?{Authorization:'Bearer '+sessionStorage.getItem('lorasim-token')}:{}});if(!r.ok){let data;try{data=await r.json();}catch{}throw new DiagnosticError(data?.diagnostics||[{severity:'error',code:'DOWNLOAD',message:'出力ファイルをダウンロードできませんでした。',hint:'接続設定とファイルの有無を確認してください。',detail:'HTTP '+r.status}]);}const url=URL.createObjectURL(await r.blob());el('a',{href:url,download:f.name.split('/').pop()}).click();setTimeout(()=>URL.revokeObjectURL(url),1000);})},f.name)));}
async function runPreview(){if(!$('visualEnabled').checked)return;newAutoIds();const revision=configRevision;$('preview').disabled=true;try{const result=await api('/api/preview','POST',{yaml:JSON.stringify(profile),horizon_s:Number($('previewHorizon').value),limit:Number($('previewLimit').value)});if(revision!==configRevision){notice('確認中に設定が変更されたため、プレビューを更新しませんでした。もう一度「設定を確認する」を押してください。');return;}mapData=result;$('map').classList.remove('map-stale');setConfigDiagnostics(mapData.diagnostics||[]);previewDirty=false;$('mapInfo').textContent=`確認用 · ${mapData.devices.length} / ${mapData.ed_count} ED`;$('mapTime').max=mapData.horizon_s;$('mapTime').value=0;$('mapTimeValue').textContent='0 s';$('previewNote').textContent='確認用サンプル · MAC待機・干渉・ACK・学習を含みません。'+(mapData.warnings.length?' 一部のモデルは省略。EDの詳細を確認してください。':'');$('previewNote').title=mapData.note;$('previewDevices').replaceChildren(...mapData.devices.map(d=>el('option',{value:d.id},'ED '+d.id+' · '+d.group)));drawMap();inspectDevice(mapData.devices[0]);}finally{$('preview').disabled=false;}}
function inspectDevice(d){if(!d)return;$('previewDevices').value=d.id;$('coordinates').textContent=`ED ${d.id} · ${d.group}`;$('previewInspector').replaceChildren(el('h3',{},`ED ${d.id} の設定確認`));for(const[k,v]of Object.entries(d.models||{}))$('previewInspector').append(el('div',{class:'detailrow'},labels[k],el('b',{},v.label+' ('+v.kind+')')));for(const warning of d.warnings||[])$('previewInspector').append(el('p',{class:'hint'},warning));const table=el('table',{},el('thead',{},el('tr',{},['生成回数','予定時刻 [s]','SF / TP / CR','周波数 [MHz]'].map(x=>el('th',{},x)))),el('tbody',{},(d.schedule||[]).map(r=>el('tr',{},[r.tx_index,r.time_s.toFixed(3),r.radio?`${r.radio.sf} / ${r.radio.tp_dbm} dBm / 4/${r.radio.cr+4}`:'プレビュー非対応',r.radio?(r.radio.frequency_hz/1e6).toFixed(3):'—'].map(x=>el('td',{},x))))));$('previewInspector').append(el('p',{class:'hint'},`初回生成時刻: ${num(d.first_generation_s)} s。モデルの定数は下記で確認できます。`),table,el('details',{},el('summary',{},'適用されたモデルと定数'),el('pre',{},JSON.stringify(d.models,null,2))));}
function drawMap(){
 if(!previewVisible||currentView!=='experiment'||preflightTab!=='layout'||document.hidden)return;
 mapRenderer?.draw(mapData,{at:Number($('mapTime').value),paths:$('showPaths').checked,traffic:$('showTraffic').checked,waypoints:$('showWaypoints').checked});
}
function scheduleMapDraw(){if(mapFrame===null)mapFrame=requestAnimationFrame(()=>{mapFrame=null;drawMap();});}
function esc(x){return String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function stacked(target,items,labels,xTitle){const width=800,height=290,L=48,R=16,T=18,B=65,pw=width-L-R,ph=height-T-B;let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(xTitle)}">`;for(let i=0;i<=4;i++){const y=T+ph*(1-i/4);svg+=`<line x1="${L}" x2="${width-R}" y1="${y}" y2="${y}" stroke="#e4ebf2"/><text x="5" y="${y+3}">${i*25}%</text>`;}const step=pw/Math.max(1,items.length),bw=Math.max(1,step*.74),keys=[...new Set(items.flatMap(o=>Object.keys(o)))].sort((a,b)=>a==='Received'?-1:b==='Received'?1:a.localeCompare(b));items.forEach((parts,i)=>{let base=0;for(const k of keys){const v=parts[k]||0;svg+=`<rect x="${L+i*step+(step-bw)/2}" y="${T+ph*(1-base-v)}" width="${bw}" height="${ph*v}" fill="${color(k)}"><title>${esc(labels[i])}: ${esc(causeLabel[k]||k)} ${pct(v)}</title></rect>`;base+=v;}if(i%Math.max(1,Math.ceil(items.length/12))===0)svg+=`<text text-anchor="middle" x="${L+(i+.5)*step}" y="${T+ph+18}">${esc(labels[i])}</text>`;});svg+=`<text text-anchor="middle" x="${L+pw/2}" y="${height-7}">${esc(xTitle)}</text></svg>`;$(target).innerHTML=svg;$(target).append(el('div',{class:'chartlegend'},keys.map(k=>el('span',{},el('i',{style:'background:'+color(k)}),causeLabel[k]||k))));}
function bars(target,items,label){const w=500,h=Math.max(180,items.length*32+65),L=130,R=45,ph=h-45,max=Math.max(1e-12,...items.map(x=>x.value??0));let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}">`;items.forEach((x,i)=>{const y=20+i*32;svg+=`<text x="0" y="${y+12}">${esc(x.label.slice(0,22))}</text><rect x="${L}" y="${y}" width="${(w-L-R)*(x.value??0)/max}" height="18" rx="3" fill="#3887a5"/><text x="${L+5+(w-L-R)*(x.value??0)/max}" y="${y+12}">${x.value===null?'N/A':num(x.value)}</text>`;});svg+=`<text x="${L}" y="${h-5}">${esc(label)}</text></svg>`;$(target).innerHTML=svg;}
function renderAnalysis(){
 if(!analysisData)return;$('analysisEmpty').hidden=true;$('analysisCharts').hidden=false;$('analysisCharts').replaceChildren();
 for(const result of analysisData.modules||[]){
  const panel=el('section',{class:'panel module-results'},el('div',{class:'sectiontitle'},el('h2',{},result.label),el('span',{class:'badge'},result.status==='completed'?'集計済み':'Error')),el('p',{class:'hint'},result.description));$('analysisCharts').append(panel);
  if(result.status==='failed'){const d=el('div',{id:'module-error-'+$('analysisCharts').children.length});panel.append(d);renderDiagnostics(d.id,result.diagnostics);continue;}
  for(const [i,v] of (result.data.views||[]).entries()){
   const id='analysis-chart-'+$('analysisCharts').children.length+'-'+i,box=el('div',{class:'chart'},el('h3',{},v.title),el('div',{id}));panel.append(box);
   if(v.type==='stacked'){stacked(id,v.items,v.labels,v.unit||'');for(const t of $(id).querySelectorAll('title')){t.parentElement.dataset.tip=t.textContent;t.parentElement.setAttribute('tabindex','0');}AnalysisCharts.tooltips($(id));}
   else if(['bars','table','lines','stacked_area','heatmap','survival'].includes(v.type))AnalysisCharts.render($(id),v);
   else $(id).textContent='未対応の表示形式です。JSON出力でデータを確認してください。';
  }
  for(const note of result.data?.notes||[])panel.append(el('p',{class:'analysis-note'},note));
  if(result.sources?.length)panel.append(el('p',{class:'hint'},'指標の参考文献: ',result.sources.filter(url=>/^https?:\/\//.test(url)).map((url,i)=>el('a',{href:url,target:'_blank',rel:'noopener'},`[${i+1}] `))));
 }
 if(!analysisData.modules?.length)$('analysisCharts').append(el('p',{class:'empty panel'},'分析モジュールが選択されていません。'));
 $('analysisState').textContent=`${analysisData.runs.length} Run・${analysisData.modules?.length||0} モジュールを集計`;
 $('analysisDefinitions').textContent='送信回数はEDごとの実送信通番（再送込み）。通常の収束は1〜t累積。PRR・反復Run・時刻軸の分母は各モジュールの定義を参照してください。判定未了はPRRの分母から除外します。';
}

document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>switchView(b.dataset.view));
$('validate').onclick=action(async()=>{profile=await validatedProfile(JSON.stringify(profile));renderAll();notice(configDiagnostics.length?'設定の検証が完了しました。表示されたWarningの理由とヒントを確認してください。':'設定とモデルの初期化の検証に成功しました。');});
$('run').onclick=action(async()=>{const r=await api('/api/jobs','POST',{yaml:JSON.stringify(profile),experiment_id:$('experiment').value||null});setConfigDiagnostics(r.diagnostics||[],true);await refreshExperiments();await selectJob(r.id);notice('Runをキューに登録しました。workerがバックグラウンドで実行します。');});
let exampleEntries=[], selectedExamples=new Set(), exampleBusy=false;
function visibleExamples(){const query=$('exampleSearch').value.trim().toLowerCase();return exampleEntries.filter(item=>(item.id+' '+item.name).toLowerCase().includes(query));}
function updateExampleSelection(){
 const shown=visibleExamples(),hidden=[...selectedExamples].filter(id=>!shown.some(item=>item.id===id)).length;
 $('exampleSelection').textContent=`${selectedExamples.size}件選択 / 全${exampleEntries.length}件（表示${shown.length}件${hidden?'、表示外の選択'+hidden+'件':''}）`;
 $('selectExamples').disabled=exampleBusy||!shown.length;
 $('clearExamples').disabled=exampleBusy||!selectedExamples.size;
 $('exampleSearch').disabled=exampleBusy;
 $('loadExample').disabled=exampleBusy||selectedExamples.size!==1;
 $('queueExamples').disabled=exampleBusy||!selectedExamples.size;
 $('queueExamples').textContent=exampleBusy?'処理中…':`選択したYAMLを一括実行（${selectedExamples.size}件）`;
}
function renderExamples(){
 const shown=visibleExamples();
 $('exampleProfiles').replaceChildren(...shown.map(item=>{
  const checkbox=el('input',{type:'checkbox',checked:selectedExamples.has(item.id),onchange:event=>{
   if(exampleBusy)return;
   if(event.target.checked)selectedExamples.add(item.id);else selectedExamples.delete(item.id);
   updateExampleSelection();
  }});
  checkbox.disabled=exampleBusy;
  return el('label',{class:'example-choice'},checkbox,el('span',{},el('strong',{},item.id),el('small',{},item.name)));
 }));
 if(!shown.length)$('exampleProfiles').append(el('p',{class:'hint'},'一致するYAMLはありません。'));
 updateExampleSelection();
}
async function refreshExamples(){exampleEntries=await api('/api/examples');selectedExamples=new Set([...selectedExamples].filter(id=>exampleEntries.some(item=>item.id===id)));renderExamples();}
$('exampleSearch').oninput=renderExamples;
$('selectExamples').onclick=()=>{if(exampleBusy)return;for(const item of visibleExamples())selectedExamples.add(item.id);renderExamples();};
$('clearExamples').onclick=()=>{if(exampleBusy)return;selectedExamples.clear();renderExamples();};
$('loadExample').onclick=async()=>{
 if(exampleBusy||selectedExamples.size!==1)return;
 const id=[...selectedExamples][0];exampleBusy=true;renderExamples();
 try{
  const item=await api('/api/examples/content?id='+encodeURIComponent(id));
  const loaded=await validatedProfile(item.yaml);profile=loaded;
  $('profiles').value='';$('experiment').value='';await loadSchemas();renderAll();dirty();
  notice('同梱YAMLを読み込みました。編集後は「エディタの設定を実行」を押してください。');
 }catch(e){reportError(e);}finally{exampleBusy=false;renderExamples();}
};
$('queueExamples').onclick=async()=>{
 if(exampleBusy||!selectedExamples.size)return;
 const ids=exampleEntries.filter(item=>selectedExamples.has(item.id)).map(item=>item.id);
 let added=0,failed=0,uncertain=0;
 exampleBusy=true;renderExamples();$('exampleQueueResults').replaceChildren();
 try{
  for(const [index,id] of ids.entries()){
   $('exampleQueueStatus').textContent=`キュー登録中：${index+1} / ${ids.length}件（登録済み${added}件）。登録完了までこの画面を開いておいてください。`;
   let submitting=false;
   try{
    const item=await api('/api/examples/content?id='+encodeURIComponent(id));
    submitting=true;
    // Reuse normal validation and durable queue insertion, one YAML at a time.
    const job=await api('/api/jobs','POST',{yaml:item.yaml,experiment_id:null});
    if(typeof job?.id!=='string'||!job.id)throw Error('Run IDを含む登録応答を取得できませんでした。');
    added++;selectedExamples.delete(id);
    $('exampleQueueResults').append(el('p',{class:'example-result'},el('span',{},id+'：登録済み '),el('button',{class:'small quiet',onclick:action(()=>selectJob(job.id))},'Run '+job.id.slice(0,8))));
   }catch(e){
    const rejected=e.status>=400&&e.status<500&&e.status!==408;
    if(submitting&&!rejected){
     uncertain++;selectedExamples.delete(id);
     $('exampleQueueResults').append(el('p',{class:'example-result error'},id+'：登録状況を確認できません。Run一覧を確認してから再選択してください。 '+e.message));
     break; // A lost POST response may already have queued the Run; never retry it.
    }
    failed++;
    $('exampleQueueResults').append(el('p',{class:'example-result error'},id+'：未登録。 '+e.message));
    if(e.diagnostics?.some(d=>d.code==='CONNECTION')||e.status===401||e.status===403)break;
   }
   updateExampleSelection();
  }
 }finally{
  exampleBusy=false;renderExamples();
  $('exampleQueueStatus').textContent=`キュー登録：${added} / ${ids.length}件、失敗${failed}件${uncertain?'、登録状況不明'+uncertain+'件':''}、未処理${ids.length-added-failed-uncertain}件。登録済みRunはworkerが順次実行します。`;
 }
 const refreshed=await Promise.allSettled([refreshExperiments(),refreshJobs()]);
 for(const result of refreshed)if(result.status==='rejected')reportError(result.reason);
};
$('profiles').onchange=action(async()=>{const p=savedProfiles[Number($('profiles').value)];if(!p)return;profile=await validatedProfile(p.yaml);await loadSchemas();renderAll();dirty();notice('プロファイルを読み込みました。');});
$('saveProfile').onclick=action(async()=>{await api('/api/profiles/'+encodeURIComponent(profile.name),'PUT',{yaml:JSON.stringify(profile)});await refreshProfiles();notice('プロファイルを保存しました。');});
$('applyYaml').onclick=action(async()=>{profile=await validatedProfile($('yaml').value);await loadSchemas();renderAll();dirty();notice('YAMLをGUIへ反映しました。');});
$('importProfile').onchange=action(async e=>{const f=e.target.files[0];if(f){profile=await validatedProfile(await f.text());await loadSchemas();renderAll();dirty();notice('ファイルを読み込みました。');}});
$('exportProfile').onclick=action(async()=>{const result=await api('/api/profile-yaml','POST',{yaml:JSON.stringify(profile)});download(profile.name+'.yaml',result.yaml,'application/yaml');});
$('saveModel').onclick=action(async()=>{await api('/api/models/'+$('modelCategory').value+'/'+encodeURIComponent($('modelName').value),'PUT',modelSpec);await refreshCatalog();renderAll();notice('再利用モデルを保存しました。');});
$('modelCategory').onchange=()=>{const cat=$('modelCategory').value,kind=Object.keys(pluginSchemas[cat])[0];modelSpec=newComponent(cat,kind);$('modelEditorTitle').textContent='新しいモデル定義';renderAll();renderLibrary();};
$('modelSearch').oninput=renderLibrary;
$('preview').onclick=action(runPreview);
$('showTraffic').onchange=$('showPaths').onchange=$('showWaypoints').onchange=drawMap;
$('visualEnabled').onchange=()=>{previewVisible=$('visualEnabled').checked;$('mapViewport').hidden=!previewVisible;if(previewVisible)scheduleMapDraw();$('mapInfo').textContent=previewVisible?'手動で設定を確認できます':'プレビューOFF';};
$('mapTime').oninput=()=>{$('mapTimeValue').textContent=$('mapTime').value+' s';drawMap();};
$('previewDevices').onchange=()=>inspectDevice(mapData?.devices.find(d=>d.id===Number($('previewDevices').value)));
$('experimentInfo').append(info(()=> $('experiment').value?experimentText($('experiment').value):'実行時にプロファイル名で新規Experimentを作成します。','選択したExperimentの情報'));
$('refreshJobs').onclick=action(refreshJobs);
$('analyze').onclick=action(async()=>{const ids=[...$('experimentChecks').querySelectorAll('input:checked')].map(x=>x.value);if(!ids.length)throw Error('Experimentを1つ以上選択してください。');if(analysisBusy)return;analysisBusy=true;const revision=analysisRevision;$('analyze').disabled=true;try{analysisData=await api('/api/analysis','POST',{experiment_ids:ids,tx_start:Number($('txStart').value),tx_end:$('txEnd').value?Number($('txEnd').value):null,include_mcu:$('includeMCU').checked,bins:Number($('bins').value),ed_count:$('edCount').value?Number($('edCount').value):null,modules:clone(moduleSelections)});renderAnalysis();analysisConsultant.setAnalysis(analysisData);if(revision!==analysisRevision)analysisChanged();notice(`${analysisData.runs.length} Runを集計しました。`);}finally{analysisBusy=false;$('analyze').disabled=false;}});
$('exportAnalysis').onclick=()=>{if(analysisData)download('analysis.json',JSON.stringify(analysisData,null,2));};
$('exportCSV').onclick=()=>{if(!analysisData)return;const rows=[['experiment','run_id','ed_count','eligible_ed_count','mean_ed_prr','jain','gini','cv','energy_j','mean_ed_j'],...analysisData.runs.map(r=>[r.experiment_name,r.id,r.ed_count,r.eligible_ed_count,r.mean_ed_prr,r.fairness?.jain,r.fairness?.gini,r.fairness?.cv,r.energy.measured?r.energy.total_j:null,r.energy.measured?r.energy.mean_ed_j:null])];download('analysis.csv',rows.map(row=>row.map(x=>'"'+String(x??'').replace(/"/g,'""')+'"').join(',')).join('\n'),'text/csv');};
$('credentials').onclick=()=>{$('token').value=sessionStorage.getItem('lorasim-token')||'';$('connectDialog').showModal();};$('saveToken').onclick=()=>{sessionStorage.setItem('lorasim-token',$('token').value);setTimeout(()=>location.reload(),50);};
let connectionError=null;
let workerSnapshot=null,workerLimitDirty=false;
function renderWorker(w){
 workerSnapshot=w;const running=w.counts?.running||0,queued=w.counts?.queued||0;
 $('worker').textContent=w.online?`● ${running} Run実行中 / 上限 ${w.workers}`:'○ worker 未確認';
 $('worker').className='badge '+(w.online?'online':'offline');
 $('worker').title=w.online?'Runごとに独立したプロセスを使います。':'workerの生存通知を確認できません。ログと起動状態を確認してください。';
 $('workerCounts').textContent=`実行中 ${running} · 待機 ${queued} · CPU枠 ${w.cpu_count}`;
 $('workerLimitState').textContent=w.online?(w.workers===w.requested_workers?'適用済み':'変更を反映中'):'次回worker起動時に適用';
 if(!workerLimitDirty)$('workerLimit').value=w.requested_workers;
 $('workerLimitNote').textContent=(w.configured_workers==null?'起動設定を使用。':'GUI設定を保存済み（起動設定より優先）。')+(w.requested_workers>w.cpu_count?' 同時実行数がCPU枠を超えています。CPU競合により遅くなる場合があります。':'')+(running>w.requested_workers?' 上限を超えて実行中のRunは完了まで継続し、新しいRunは空きができるまで待機します。':'');
}
$('workerLimit').oninput=()=>{workerLimitDirty=true;};
$('applyWorkerLimit').onclick=action(async()=>{const workers=Number($('workerLimit').value);if(!Number.isInteger(workers)||workers<1||workers>256)throw Error('同時実行数は1〜256の整数で指定してください。');const w=await api('/api/worker/settings','PUT',{workers});workerLimitDirty=false;renderWorker(w);notice('同時実行数を保存しました。workerが稼働中なら次の空き枠確認から適用します。');});
$('resetWorkerLimit').onclick=action(async()=>{const w=await api('/api/worker/settings','DELETE');workerLimitDirty=false;renderWorker(w);notice('同時実行数をworkerの起動設定に戻しました。');});
async function poll(){if(busyPoll||document.hidden)return;busyPoll=true;try{const w=await api('/api/worker');renderWorker(w);if(connectionError){connectionError=null;renderDiagnostics('connectionDiagnostics',[]);}if(currentView==='models')await tuningPanel.refresh();if(currentView==='experiment'){const previous=selectedItem?.status;await refreshJobs();if(previous!==selectedItem?.status)await renderFiles();}else if(selectedJob){const id=selectedJob,item=await api('/api/jobs/'+id);if(id===selectedJob){selectedItem=item;renderResult(item);}}}catch(e){$('worker').textContent='接続エラー';connectionError=e;renderDiagnostics('connectionDiagnostics',e.diagnostics||[localDiagnostic(e)]);}finally{busyPoll=false;}}

const spaceDimensions=[['spreading_factors','SF'],['frequencies_hz','CH'],['coding_rates','CR'],['powers_dbm','TP']];
function spaceCount(a){const lengths=spaceDimensions.map(([k])=>(a[k]||[]).length);return lengths.map((n,i)=>`${spaceDimensions[i][1]} ${n}`).join(' × ')+' = '+lengths.reduce((a,b)=>a*b,1)+' 行動';}
function defaultSpaceSelection(meta){const global=profile.action_space||defaults.profile.action_space,result={};for(const key of meta.fixed_space_dimensions||[]){const values=global[key]||[];result[key]=[values.includes(key==='powers_dbm'?14:1)?(key==='powers_dbm'?14:1):values[0]];}return result;}
function newComponent(cat,kind){const m=pluginSchemas[cat][kind],options=clone(m?.defaults||{}),value={kind,options};if(cat==='policy'&&m?.accepts_action_space){value.action_space=defaultSpaceSelection(m);for(const k of m.space_options||[])delete options[k];}return value;}
function spaceEditor(parent,selection,onChange,path,kind){
 const meta=pluginSchemas.policy[kind],global=profile.action_space||defaults.profile.action_space,space={...global,...selection};for(const[k]of spaceDimensions)if(selection[k]==null)space[k]=global[k];
 const box=el('div',{class:'space-editor'},el('p',{class:'space-total'},spaceCount(space)),el('p',{class:'hint'},kind==='fixed'?'固定モデルはこの集合の先頭の組み合わせを返します。固定したい各値を1つずつ選択してください。':'1値の次元は固定、複数値の次元は選択対象です。学習状態はEDごとに独立します。'));
 if(meta?.space_description)box.append(el('p',{class:'comparison-note'},meta.space_description,info('制御則の詳細と原方式との差分は docs/ADR_COMPARISON.md を参照してください。')));
 for(const[k,name]of spaceDimensions){
  const inherit=selection[k]==null,all=global[k]||[],values=selection[k]||all;
  if(meta?.fixed_space_dimensions?.includes(k)){
   const select=el('select',{'aria-label':path+'.'+k+'.fixed'},all.map(v=>el('option',{value:v},k==='coding_rates'?'4/'+(4+v):v+' dBm')));
   if(values.length!==1||!all.includes(values[0]))select.prepend(el('option',{value:''},'固定値を1つ選択してください'));
   select.value=values.length===1&&all.includes(values[0])?String(values[0]):'';
   select.onchange=()=>onChange({...selection,[k]:[Number(select.value)]});
   box.append(el('fieldset',{},el('legend',{},name+' · 固定',info('このモデルは共通空間モードで1値に固定します。全体の候補から選択してください。')),select));continue;
  }
  const row=el('fieldset',{},el('legend',{},name,info('全体候補の部分集合だけ指定できます。全体を継承すると追加した候補も自動反映します。')),el('label',{class:'check'},el('input',{type:'checkbox',checked:inherit,'aria-label':path+'.'+k+'.inherit',onchange:e=>onChange({...selection,[k]:e.target.checked?null:[all[0]]})}),'全体を継承'));
  for(const v of [...new Set([...all,...values])])row.append(el('label',{class:'check'},el('input',{type:'checkbox',checked:values.includes(v),...(inherit?{disabled:true}:{}),'aria-label':path+'.'+k+'.'+v,onchange:e=>{const next=values.filter(x=>x!==v);if(e.target.checked)next.push(v);onChange({...selection,[k]:next});}}),(k==='frequencies_hz'?v/1e6+' MHz':k==='coding_rates'?'4/'+(4+v):v)+(all.includes(v)?'':'（全体に未登録）')));
  box.append(row);
 }
 parent.append(box);
}
let analysisModules={},moduleSelections=[];
function analysisChanged(){++analysisRevision;analysisConsultant.invalidate();if(analysisData)$('analysisState').textContent='条件変更あり・再分析してください';}
function renderModuleEditor(){
 $('moduleChoices').replaceChildren(...Object.entries(analysisModules).filter(([k])=>!moduleSelections.some(x=>x.kind===k)).map(([k,m])=>el('option',{value:k},m.label)));
 $('moduleList').replaceChildren(...moduleSelections.map((item,index)=>{
  const m=analysisModules[item.kind],box=el('div',{class:'analysis-module'}),toggle=el('input',{type:'checkbox',checked:item.enabled,'aria-label':m.label+'を有効にする',onchange:e=>{item.enabled=e.target.checked;analysisChanged();}});
  box.append(el('div',{class:'sectiontitle'},el('label',{class:'check'},toggle,el('strong',{},m.label),info(m.description)),el('button',{class:'small',onclick:()=>{moduleSelections.splice(index,1);analysisChanged();renderModuleEditor();}},'削除')));
  if(Object.keys(m.schema.properties||{}).length)buildField(box,'options',item.options,{...inlineSchema(m.schema),title:'分析条件'},'analysis.'+item.kind,v=>{item.options=v;analysisChanged();});
  return box;
 }));
}
$('addModule').onclick=()=>{const kind=$('moduleChoices').value;if(!kind)return;moduleSelections.push({kind,enabled:true,options:clone(analysisModules[kind].defaults)});analysisChanged();renderModuleEditor();};
$('loadAnalysisModule').onclick=action(async()=>{const kind=$('customAnalysisModule').value.trim();const meta=await api('/api/analysis/modules/schema?'+new URLSearchParams({kind}));analysisModules[kind]=meta;if(!moduleSelections.some(x=>x.kind===kind))moduleSelections.push({kind,enabled:true,options:clone(meta.defaults)});analysisChanged();renderModuleEditor();});
for(const id of ['txStart','txEnd','edCount','bins','includeMCU'])$(id).addEventListener('change',analysisChanged);
function selectPreflightTab(tab) {
 preflightTab=tab;
 const calculating=tab==='calculator';
 $('layoutTool').hidden=tab!=='layout';$('calculatorTool').hidden=!calculating;$('abstractTool').hidden=tab!=='abstract';$('mapActions').hidden=tab!=='layout';
 $('preflightPanel').classList.toggle('calculator-open',tab!=='layout');
 for(const [id,selected] of [['layoutTab',tab==='layout'],['calculatorTab',calculating],['abstractTab',tab==='abstract']]){
  $(id).setAttribute('aria-selected',String(selected));$(id).tabIndex=selected?0:-1;
 }
 if(calculating){
  try{
   if(!calculator){
    if(typeof PropagationCalculator!=='function')throw new Error('Calculatorの画面ファイルを読み込めませんでした。画面を再読み込みし、APIと画面ファイルを同じ版へ更新してください。');
    $('calculatorTool').replaceChildren();
    calculator=new PropagationCalculator({root:$('calculatorTool'),api,el,info,getProfile:()=>profile,editChannel:()=>selectSection('radio')});
   }
   calculator.setVisible(currentView==='experiment');
  }catch(e){$('calculatorTool').replaceChildren(el('p',{class:'diagnostic error',role:'alert'},e.message));}
 }else calculator?.setVisible(false);
 if(tab==='abstract')renderProfileAbstract();
 if(tab==='layout')drawMap();
}
mapRenderer=new PreviewMap($('map'),$('mapViewport'));
new ResizeObserver(scheduleMapDraw).observe($('mapViewport'));
window.addEventListener('resize',scheduleMapDraw);
$('layoutTab').onclick=()=>selectPreflightTab('layout');
$('calculatorTab').onclick=()=>selectPreflightTab('calculator');
$('abstractTab').onclick=()=>selectPreflightTab('abstract');
for(const [i,id] of ['layoutTab','calculatorTab','abstractTab'].entries())$(id).addEventListener('keydown',e=>{
 if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
  e.preventDefault();const tabs=['layout','calculator','abstract'],j=e.key==='Home'?0:e.key==='End'?2:(i+(e.key==='ArrowRight'?1:2))%3;
  selectPreflightTab(tabs[j]);$(tabs[j]+'Tab').focus();
 }
});
function renderProfileAbstract(){
 const root=$('abstractTool');delete root.dataset.stale;root.replaceChildren(el('div',{class:'sectiontitle'},el('h3',{},'入力された実験設定'),el('button',{type:'button',onclick:renderProfileAbstract},'最新の入力値で更新')),el('p',{class:'hint'},'GUIへ反映済みの入力値です。モデル参照は展開して表示します。検証・保存・実行は行いません。YAMLの未適用編集は含みません。'));
 try{
  const models=ProfileSummary.models(profile,componentValue),overview=el('div');root.append(overview);
  AnalysisCharts.table(overview,{title:'実験条件の概要',columns:['項目','値'],rows:[['プロファイル',profile.name],['ED数',(profile.groups||[]).reduce((n,g)=>n+g.count,0)],['選択器',models.policy.join(', ')],['移動モデル',models.mobility.join(', ')],['時間 [s]',profile.duration_s],['seed',profile.seed],['エリア [m]',JSON.stringify(profile.area_m)],['GW数',profile.gateways?.length||0]]});
  for(const section of sections.filter(s=>s.keys.length)){
   const box=el('details',{},el('summary',{},section.title)),target=el('div');box.append(target);root.append(box);const rows=[];
   for(const k of section.keys)if(k in profile)rows.push(...ProfileSummary.flatten(profile[k],schema.properties?.[k]||{},k,resolve));
   let rendered=false;box.addEventListener('toggle',()=>{if(box.open&&!rendered){AnalysisCharts.table(target,{title:section.title,columns:['項目','設定パス','入力値'],rows});rendered=true;}});
  }
  const expanded=[];
  function visit(v,path){if(!v||typeof v!=='object')return;if(v.kind&&v.options){const cat=categoryFor(path),spec=componentValue(v,cat),meta=pluginSchemas[cat][spec.kind];expanded.push([path,spec.kind,JSON.stringify({...meta?.defaults,...spec.options}),JSON.stringify(spec.action_space??null)]);return;}for(const[k,x]of Object.entries(v))visit(x,path?path+'.'+k:k);}
  for(const k of ['groups','assignments'])visit(profile[k],k);
  const box=el('details',{},el('summary',{},'参照展開後のモデル定数（既定値を含む）')),target=el('div');box.append(target);root.append(box);let ready=false;box.addEventListener('toggle',()=>{if(box.open&&!ready){AnalysisCharts.table(target,{title:'モデル定数',columns:['設定箇所','実装','定数','行動空間の上書き'],rows:expanded});ready=true;}});
 }catch(e){root.append(el('p',{class:'diagnostic error'},e.message));}
}


window.aiSettings=new AISettingsDialog({api,el});
const analysisConsultant=new AnalysisConsultant({root:$('analysisConsultant'),api,el});
for(const [id,ai] of [['analysisPlotsTab',false],['analysisAITab',true]])$(id).onclick=()=>{
 $('analysisPlots').hidden=ai;$('analysisConsultant').hidden=!ai;$('analysisView').classList.toggle('ai-active',ai);
 $('analysisPlotsTab').setAttribute('aria-selected',String(!ai));$('analysisAITab').setAttribute('aria-selected',String(ai));
 if(ai)analysisConsultant.chat.refreshConfig();
};
$('openAISettings').onclick=()=>{$('connectDialog').close();window.aiSettings.open();};

action(async()=>{[schema,defaults]=await Promise.all([api('/api/schema'),api('/api/defaults')]);Object.assign(pluginSchemas,defaults.plugins);analysisModules=await api('/api/analysis/modules');moduleSelections=['abstract','outcomes','convergence','prr_by_count','fairness','energy'].map(kind=>({kind,enabled:true,options:clone(analysisModules[kind].defaults)}));renderModuleEditor();profile=clone(defaults.profile);modelSpec=newComponent('policy','fixed');await Promise.all([refreshCatalog(),refreshProfiles(),refreshExperiments(),refreshExamples()]);await loadSchemas();renderAll();await checkConfiguration();await runPreview();await poll();setInterval(poll,4000);})();

$('map').onclick=event=>{if(!mapData)return;const nearest=mapRenderer.pick(event.clientX,event.clientY,mapData.devices);if(nearest)inspectDevice(nearest);};

const staticHelp={profiles:'保存した実験条件を読み込みます。モデル定義は読み込み時に参照先を解決します。',importProfile:'YAMLまたはJSONのプロファイルを取り込みます。',txStart:'各EDの実送信通番は1始まり。累積収束だけは表示の開始位置で、集計は常に1〜t回です。他のモジュールはこの開始回数で区間を絞ります。',txEnd:'終了通番を含みます。空欄なら記録の最後まで集計します。',edCount:'このED数のRunだけを分析します。空欄なら全てのED数が対象です。',bins:'累積曲線の表示点数を制限します。各点は1〜tの全履歴で計算し、メモリ上限に応じて点数を減らします。',includeMCU:'無線電力に加えて推論・学習・待機のMCUエネルギーを集計します。',fairness:'各EDのPRR分布から公平性を評価します。全EDが成功ゼロの場合はN/Aです。',modelCategory:'モデルの責務に合わせて種類を選択してください。',modelSearch:'内蔵・保存済みモデルの名前または実装名で絞り込みます。',modelName:'再利用定義の名前。同名で保存すると更新されます。既存Runのスナップショットは変更されません。',visualEnabled:'配置確認パネルの表示を切り替えます。実験中のポーリングや追跡処理は実行しません。',showTraffic:'MACの待機を含まない、アプリケーションメッセージの生成予定地点です。',showWaypoints:'確認対象の移動モデルが共有する地点集合を最大1000点表示。EDはこれらの地点上から開始します。',showPaths:'確認対象EDの軌跡を有限個の座標サンプルで表示します。',previewHorizon:'プレビューだけの時間範囲です。実験時間を超える部分は切り詰めます。',previewLimit:'大規模実験でも、この上限まで等間隔にEDを抽出します。',previewDevices:'抽出されたEDを選び、適用されたモデルと定数、生成予定時刻を確認します。',mapTime:'すでに計算した軌跡をブラウザ内で切り替えます。サーバ通信は発生しません。',token:'必要な場合だけAPIのBearer tokenを指定します。このタブのセッション内に保存します。'};
for(const[id,help]of Object.entries(staticHelp)){const input=$(id),label=input?.closest('label');if(label)label.insertBefore(info(help,(label.textContent.trim()||id)+'の説明'),input);}

$('yaml').addEventListener('input',()=>{yamlDraft=true;});
window.addEventListener('unhandledrejection',event=>{reportError(event.reason instanceof Error?event.reason:Error(String(event.reason)));});

window.addEventListener('error',event=>{if(event.error)reportError(event.error);});
document.addEventListener('visibilitychange',()=>{if(!document.hidden){poll();scheduleMapDraw();if(previewDirty)checkConfiguration();}});

const documents=new DocumentationReader({api,el,download,reportError});

const tuningPanel=new TuningPanel({root:$('tuningPanel'),api,el,info,getSource:tuningSource,onSaved:async()=>{await refreshCatalog();renderAll();notice('調整したモデル定義をライブラリに追加しました。各EDグループの参照から選択できます。');},onJob:async id=>{await selectJob(id);},error:reportError});

function tuningSource(){
 if(yamlDraft)throw Error('YAMLの未反映の編集があります。実験設定の「GUIに反映・検証」を先に実行してください。');
 const p=clone(profile);p.models=p.models||{};
 for(const category of ['policy','mobility','timing'])p.models[category]={...clone(catalog[category]||{}),...(p.models[category]||{})};
 return {category:$('modelCategory').value,definition:clone(componentValue(modelSpec,$('modelCategory').value)),name:$('modelName').value,profile:p,timings:profile.groups.map(g=>componentValue(g.timing,'timing'))};
}
