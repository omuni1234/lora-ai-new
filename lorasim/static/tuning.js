'use strict';
/** Model search UI. All simulations and search state live in the worker/database. */
class TuningPanel {
 static range(min,max,count,log=false,integer=false){
  if(!Number.isFinite(min)||!Number.isFinite(max)||min>=max||!Number.isInteger(count)||count<2||count>64||(log&&min<=0))throw Error('範囲は最小 < 最大、分割数は2〜64の整数、対数範囲は正の値にしてください。');
  const values=Array.from({length:count},(_,i)=>log?Math.exp(Math.log(min)+(Math.log(max)-Math.log(min))*i/(count-1)):min+(max-min)*i/(count-1));
  values[0]=min;values[count-1]=max;
  return [...new Set(values.map(v=>integer?Math.round(v):Number(v.toPrecision(10))))];
 }
 static dimensions(rows){
  return rows.map(row=>{
   const values=JSON.parse(row.values.value);
   if(!row.path.value.trim()||!Array.isArray(values)||values.length<2)throw Error('探索項目のパスと、2値以上のJSON配列を入力してください。');
   return {path:row.path.value.trim(),values};
  });
 }
 static budgets(duration,minimum=0){
  const end=Math.min(duration,36000);
  const floor=Math.min(end,minimum);
  return [...new Set([Math.max(end/9,floor),Math.max(end/3,floor),end].map(x=>Math.min(end,Number(x.toPrecision(6)))))];
 }
 constructor({root,api,el,info,getSource,onSaved,onJob,error}){
  Object.assign(this,{root,api,el,info,getSource,onSaved,onJob,error});this.rows=[];this.id=null;this.snapshot=null;
  const run=fn=>async e=>{const b=e?.currentTarget;if(b)b.disabled=true;try{await fn();}catch(err){this.error(err);}finally{if(b)b.disabled=false;}};
  this.run=run;
  this.fields={};
  const field=(key,label,value,hint,type='number')=>{
   const input=el('input',{type,value,'aria-label':'調整 '+label,step:'any'});this.fields[key]=input;
   return el('label',{},label,info(hint,label+'の説明'),input);
  };
  this.title=el('p',{class:'hint'},'下の定義エディタでモデルを選び「編集モデルと現在の実験条件を取り込む」を押してください。');
  this.groups=el('div',{class:'tuning-groups'});
  this.parameters=el('div',{class:'tuning-parameters'});
  this.suggestion=el('select',{'aria-label':'調整 候補項目'});
  this.suggestionNote=el('p',{class:'hint'});
  this.method=el('select',{'aria-label':'調整 探索方法'},
   el('option',{value:'halving'},'段階的な絞り込み（短時間から延長）'),
   el('option',{value:'annealing'},'逐次局所探索（焼きなまし・ランダム再探索）'),
   el('option',{value:'grid'},'網羅探索（全組み合わせを最長時間で評価）'));
  this.method.onchange=()=>this.methodNote.textContent=this.method.value==='grid'?'全候補を同じ最長評価時間で比較します。途中の絞り込みはしません。':this.method.value==='annealing'?'候補値の隣を探索し、悪化もexp(スコア差/温度)の確率で受け入れます。上位候補は最長時間で再評価。局所解・鞍点からの脱出は保証しません。':'少数seed・短い時間で評価し、上位候補だけ時間を延長。元のモデル定義は各段階に残して比較します。';
  this.methodNote=el('p',{class:'hint'});
  this.method.onchange();
  const basic=el('div',{class:'tuning-grid'},
   field('name','探索名','モデル調整','ライブラリ定義とは別の探索履歴名です。','text'),
   el('label',{},'探索方法',this.method),
   field('max_candidates','探索候補の上限',12,'元定義の比較用評価は別枠。網羅探索は直積がこの数以内のときだけ開始できます。2〜128。'),
   field('budgets','段階ごとの評価時間 [s]','600,1800,5400','カンマ区切り。各評価は時刻0から再実行。網羅探索は最後の値だけ使います。元実験の時間以下にしてください。','text'),
   field('seeds','探索用seed','1,2','カンマ区切り。全候補で同じseedを使用します。','text'),
   field('validation_seeds','確認用seed','101,102','探索とは異なるseed。勝者を確定してから元定義と再評価し、この結果で順位を選び直しません。','text'));
  const advanced=el('details',{},el('summary',{},'探索・観測下限の詳細'),el('div',{class:'tuning-grid'},
   field('reduction','各段階の絞り込み倍率',3,'段階的探索で候補数を約1/この倍率に減らします。'),
   field('finalists','局所探索の再評価候補数',3,'短時間評価の上位を最長時間で再評価します。元定義は別途残します。'),
   field('search_seed','探索順の乱数seed',7,'候補抽出、局所移動、悪化の受理に使用。シミュレーションseedとは別です。'),
   field('temperature','初期温度',.05,'局所探索でスコア悪化を許容する程度。0より大きい値。'),
   field('cooling','温度の減衰率',.9,'候補評価ごとに温度へ掛けます。0より大きく1以下。'),
   field('restart_probability','ランダム再探索の確率',.15,'隣接候補ではなく未評価領域へ移る確率。0〜1。'),
   field('min_coverage','判定済みULがあるEDの最低割合',.9,'短すぎる評価で一部EDだけの成績を最適化することを避けます。全EDに対する割合。'),
   field('min_uplinks_per_ed','EDあたり平均UL数の下限',1,'全EDで割った判定済みUL数。全EDがこの回数以上送ったという意味ではありません。')));
  const weightFields=el('div',{class:'tuning-grid'});
  for(const [key,label,hint] of [
   ['interference','干渉損失の追加ペナルティ','PRRに含まれる損失をさらに重視します。ED等重みの割合に掛けます。'],
   ['gateway_tx','GW送信中損失の追加ペナルティ','GW送信中のUL損失をさらに重視します。'],
   ['sensitivity','感度未満の追加ペナルティ','感度未満のUL損失をさらに重視します。'],
   ['fairness','公平性の加点重み','全EDのPRRに対するJain indexを加点。全EDが成功0件のときは0として扱います。'],
   ['energy','消費エネルギーの減点重み [1/J]','成功ULあたりの総消費エネルギー[J]に掛けます。無線消費電力モデルが有効な場合のみ。']])
    weightFields.append(field('w_'+key,label,0,hint));
  this.includeMCU=el('input',{type:'checkbox','aria-label':'調整 MCUを含む'});
  const objective=el('details',{},el('summary',{},'評価指標・トレードオフの重み'),el('p',{class:'hint'},'最大化：全ED平均PRR − 干渉/GW送信/感度の追加損失ペナルティ ＋ 公平性の加点 − エネルギーの減点。未送信EDのPRRは0として評価。対象グループ以外も含むネットワーク全体の成績です。'),weightFields,el('label',{class:'check'},this.includeMCU,'MCU消費エネルギーを含める'));
  this.estimate=el('div',{'aria-live':'polite',class:'tuning-estimate'});
  this.form=el('details',{},el('summary',{},'探索条件を設定・確認'),basic,this.methodNote,el('h3',{},'モデルを適用するEDグループ'),this.groups,
   el('h3',{},'調整する項目'),el('div',{class:'actions'},this.suggestion,el('button',{onclick:()=>this.addSuggested()},'候補を追加'),el('button',{onclick:()=>this.addDimension()},'項目を手動登録')),
   this.suggestionNote,this.parameters,advanced,objective,el('div',{class:'actions'},
    el('button',{onclick:run(()=>this.preview())},'評価予算を確認'),
    el('button',{class:'primary',onclick:run(()=>this.start())},'バックグラウンドで調整開始')),this.estimate);
  this.form.hidden=true;
  this.historySelect=el('select',{'aria-label':'調整 探索履歴'});
  this.historySelect.onchange=run(async()=>{this.id=this.historySelect.value||null;if(!this.id)this.result.replaceChildren();await this.refresh();});
  this.result=el('div',{'aria-live':'polite',class:'tuning-results'});
  root.append(el('div',{class:'sectiontitle'},el('div',{},el('span',{class:'eyebrow'},'HYPERPARAMETER TUNING'),el('h2',{},'短い評価からモデルを調整'))),el('p',{class:'hint'},'PHY/MACとED数は保持し、評価時間と候補への予算配分を変えます。画面を閉じてもworkerが継続。短時間の成績は長時間での最適性を保証しません。'),
   el('button',{onclick:run(()=>this.load())},'編集モデルと現在の実験条件を取り込む'),this.title,this.form,
   el('h3',{},'探索履歴・結果'),el('div',{class:'actions'},this.historySelect,el('button',{onclick:run(()=>this.refreshList())},'履歴を更新')),this.result);
 }
 async load(){
  this.snapshot=structuredClone(this.getSource());
  const {category,definition,profile,name}=this.snapshot;
  const data=await this.api('/api/tuning/suggestions','POST',{category,definition});
  this.suggestions=data.parameters;this.suggestion.replaceChildren(...this.suggestions.map((d,i)=>this.el('option',{value:i},d.label+' · '+d.path)));
  this.suggestionNote.textContent=data.hint;
  this.title.textContent=`対象：${name||definition.kind}（${data.label}）／ ${profile.name} ／ ${profile.groups.reduce((n,g)=>n+g.count,0)} ED ／ ${profile.duration_s} s。取り込み時点のモデル・実験条件を固定しています。`;
  this.groups.replaceChildren(...profile.groups.map(g=>this.el('label',{class:'check'},this.el('input',{type:'checkbox',value:g.name,checked:profile.groups.length===1||g[category]?.kind===definition.kind}),g.name+' · '+g.count+' ED')));
  const warmup=Math.max(3,...['batch_size','history_range'].map(k=>definition.options?.[k]).filter(Number.isFinite));
  const minimum=Math.max(0,...(this.snapshot.timings||[]).map(t=>{
   const o=t.options||{},interval=t.kind==='periodic'?(o.interval_s||60):t.kind==='poisson'?(o.mean_s||60):0;
   return interval*(warmup+1)+Math.max(o.first_s||0,o.start?.max_s||0,o.start?.at_s||0);
  }));
  this.fields.name.value=(name||definition.kind)+' 調整';this.fields.budgets.value=TuningPanel.budgets(profile.duration_s,minimum).join(',');
  this.rows=[];this.parameters.replaceChildren();this.estimate.replaceChildren();this.form.hidden=false;this.form.open=true;
  if(this.suggestions.length)this.addSuggested();
 }
 addSuggested(){const d=this.suggestions?.[Number(this.suggestion.value)];if(d&&!this.rows.some(r=>r.path.value===d.path))this.addDimension(d);}
 addDimension(d={}){
  const {el}=this;
  const row={path:el('input',{value:d.path||'','aria-label':'調整 options内のパス',placeholder:'例 epsilon / dqn.learning_rate'}),
   values:el('input',{value:JSON.stringify(d.values||[.01,.1,.2]),'aria-label':'調整 候補値JSON'})};
  const low=el('input',{type:'number',value:.001,step:'any','aria-label':'調整 範囲の最小値'}),high=el('input',{type:'number',value:.1,step:'any','aria-label':'調整 範囲の最大値'}),count=el('input',{type:'number',value:5,'aria-label':'調整 範囲の分割数'}),log=el('input',{type:'checkbox'}),integer=el('input',{type:'checkbox'});
  const card=el('article',{class:'tuning-dimension'},el('div',{class:'sectiontitle'},el('strong',{},d.label||'独自項目',this.info(d.description||'options内のパスを指定。入れ子はドット区切り、配列は0始まりの番号。値はJSON配列。','探索項目の説明')),el('button',{class:'small',onclick:()=>{this.rows=this.rows.filter(x=>x!==row);card.remove();}},'削除')),
   el('div',{class:'tuning-grid'},el('label',{},'options内のパス',row.path),el('label',{},'候補値（JSON配列）',row.values)),
   el('details',{},el('summary',{},'数値範囲から候補を生成'),el('div',{class:'tuning-grid'},el('label',{},'最小値',low),el('label',{},'最大値',high),el('label',{},'分割数',count)),el('div',{class:'actions'},el('label',{class:'check'},log,'対数間隔'),el('label',{class:'check'},integer,'整数に丸める'),el('button',{onclick:this.run(async()=>{row.values.value=JSON.stringify(TuningPanel.range(+low.value,+high.value,+count.value,log.checked,integer.checked));})},'候補値に反映'))));
  this.rows.push(row);this.parameters.append(card);
 }
 request(){
  if(!this.snapshot)throw Error('編集モデルと実験条件を先に取り込んでください。');
  const list=k=>this.fields[k].value.split(',').map(x=>{if(!x.trim())throw Error('数値リストに空欄があります。');return Number(x.trim());});
  const req={yaml:JSON.stringify(this.snapshot.profile),category:this.snapshot.category,definition:this.snapshot.definition,
   name:this.fields.name.value,groups:[...this.groups.querySelectorAll('input:checked')].map(x=>x.value),
   dimensions:TuningPanel.dimensions(this.rows),method:this.method.value,budgets_s:list('budgets'),seeds:list('seeds'),validation_seeds:list('validation_seeds'),
   weights:{include_mcu:this.includeMCU.checked}};
  for(const k of ['max_candidates','reduction','finalists','search_seed','temperature','cooling','restart_probability','min_coverage','min_uplinks_per_ed'])req[k]=Number(this.fields[k].value);
  for(const k of ['interference','gateway_tx','sensitivity','fairness','energy'])req.weights[k]=Number(this.fields['w_'+k].value);
  return req;
 }
 showEstimate(data){
  const {el}=this;this.estimate.replaceChildren(el('strong',{},`直積 ${data.grid_size} 候補 ／ 最大 ${data.max_runs} Runs ／ 元実験 約${data.full_run_equivalents.toFixed(2)}回分の仮想時間`),
   el('p',{class:'hint'},'比較用の元定義・確認seed・段階ごとの再実行を含む上限見積もりです。実時間の予測ではなく、モデルの学習コストやCPU並列数で変わります。'),
   ...data.warnings.map(w=>el('p',{class:'hint'},'⚠ '+w)));
 }
 async preview(){this.showEstimate(await this.api('/api/tuning/preview','POST',this.request()));}
 async start(){const data=await this.api('/api/tuning','POST',this.request());this.id=data.id;this.showEstimate(data.state.estimate);this.form.open=false;await this.refreshList();}
 async refreshList(){
  const data=await this.api('/api/tuning');
  this.historySelect.replaceChildren(this.el('option',{value:''},'探索を選択'),...data.map(s=>this.el('option',{value:s.id},s.name+' · '+this.status(s.status)+' · '+s.id.slice(0,6))));
  this.historySelect.value=this.id||'';await this.refresh();
 }
 status(s){return {running:'調整中',completed:'完了',failed:'失敗',cancelled:'中止',queued:'待機',interrupted:'中断'}[s]||s;}
 async refresh(){
  if(!this.id)return;const id=this.id,data=await this.api('/api/tuning/'+id);if(id===this.id)this.renderResult(data);
 }
 renderResult(data){
  const {el}=this,s=data.state,c=data.config,root=this.result;
  if(this.renderedId===data.id && (this.signature===JSON.stringify(data)||root.querySelector('.tuning-export')?.contains(document.activeElement)))return;
  this.signature=JSON.stringify(data);this.renderedId=data.id;
  const option=[...this.historySelect.options].find(x=>x.value===data.id);if(option)option.textContent=data.name+' · '+this.status(data.status)+' · '+data.id.slice(0,6);
  const stages={search:'探索',refine:'最長時間の再評価',validation:'独立seedでの確認',done:'終了'};
  root.replaceChildren(el('div',{class:'sectiontitle'},el('h3',{},data.name),el('span',{class:'badge'},this.status(data.status)+' · '+stages[s.phase])),
   el('p',{class:'hint'},`評価完了 ${s.history.length} 組 ／ 元定義は C0 ／ Experiment ${data.experiment_id}。乱数seedごとに独立したRunとして保存します。`));
  if(s.error)root.append(el('p',{class:'diagnostic error'},s.error));
  if(data.status==='running')root.append(el('button',{class:'small',onclick:this.run(async()=>{await this.api('/api/tuning/'+data.id+'/cancel','POST');await this.refreshList();})},'探索と評価Runを中止'));
  const actions=el('div',{class:'actions'},el('button',{onclick:()=>this.download(data)},'探索設定・結果JSONを保存'));
  root.append(actions);
  if(data.exports?.length)root.append(el('p',{class:'hint'},'保存済みモデル：'+data.exports.map(x=>'C'+x.candidate+' → '+x.name).join(' ／ ')));
  if(data.live.length){
   const live=el('details',{},el('summary',{},'現在の評価Run（'+data.live.length+'件）'));
   for(const j of data.live)live.append(el('div',{class:'detailrow'},el('button',{class:'small quiet',onclick:this.run(()=>this.onJob(j.id))},j.name),el('span',{},this.status(j.status)+' '+Math.round(j.progress*100)+'%')));
   root.append(live);
  }
  const groups=new Map();
  for(const t of s.history){const key=t.phase+' / '+t.duration_s; if(!groups.has(key))groups.set(key,[]);groups.get(key).push(t);}
  for(const trials of groups.values()){
   const t0=trials[0],box=el('section',{class:'tuning-stage'});root.append(box);
   AnalysisCharts.table(box,{title:stages[t0.phase]+' · '+t0.duration_s+' s（同じ予算だけで比較）',
    columns:['候補','定数','スコア平均','seed間SD','全ED PRR','干渉','GW送信中','感度未満','Jain','J/成功UL','ED観測割合','平均UL/ED','平均未判定UL','局所探索の受理','状態'],
    rows:trials.map(t=>{const r=t.result,v=s.candidates[t.candidate],avg=k=>r.samples?.length?r.samples.reduce((n,x)=>n+(x[k]||0),0)/r.samples.length:null;
     const cause=k=>r.samples?.length?r.samples.reduce((n,x)=>n+(x.fractions[k]||0),0)/r.samples.length:null;
     return ['C'+t.candidate+(v.baseline?' 元定義':''),JSON.stringify(v.params),r.mean??null,r.std??null,avg('prr'),cause('Interference'),cause('LostBecauseTx'),cause('UnderSensitivity'),avg('jain'),r.samples?.some(x=>x.energy_j_per_success===null)?null:avg('energy_j_per_success'),avg('coverage'),avg('uplinks_per_ed'),avg('censored'),t.accepted===undefined?'—':t.accepted?'受理':'棄却',r.valid?(t.reused?'既存評価を再利用':'有効'):(r.errors||[]).join(' / ')];})});
   box.prepend(el('h3',{},stages[t0.phase]+' · '+t0.duration_s+' s'),el('p',{class:'hint'},'同じ評価時間・seed集合だけで比較しています。PRR・損失は0〜1。SDはseed間の標本標準偏差で、信頼区間ではありません。'));
   const points=trials.filter(t=>t.result.valid).map(t=>{const samples=t.result.samples.map(x=>x.prr);return {x:t.candidate,y:samples.reduce((a,b)=>a+b,0)/samples.length,low:Math.min(...samples),high:Math.max(...samples),n:samples.length};});
   if(points.length>1){const chart=el('div',{class:'research-chart'});AnalysisCharts.render(chart,{type:'lines',title:'候補ごとの全ED平均PRR',x_label:'候補ID（探索順）',y_label:'全ED平均PRR',y_max:1,series:[{label:stages[t0.phase]+' '+t0.duration_s+' s',points}]});box.append(chart);}
  }
  if(s.winner!==null){
   root.append(el('p',{},`探索で選んだ候補：C${s.winner}。確認用seedの結果による候補の選び直しはしていません。`));
   const confirm=s.history.filter(t=>t.phase==='validation'&&t.result.valid),base=confirm.find(t=>t.candidate===0),win=confirm.find(t=>t.candidate===s.winner);
   if(base&&win)root.append(el('p',{class:'hint'},`独立seedでのスコア差（選択候補 − 元定義）：${(win.result.mean-base.result.mean).toFixed(5)}。seed数 ${win.result.samples.length}。差は統計的な改善保証ではありません。`));
  }
  const evaluated=[...new Set(s.history.filter(t=>t.result.valid).map(t=>t.candidate))];
  if(evaluated.length){
   const pick=el('select',{'aria-label':'調整 保存する候補'},evaluated.map(id=>el('option',{value:id},'C'+id+(id===s.winner?' 探索の選択候補':'')+' '+JSON.stringify(s.candidates[id].params))));
   pick.value=s.winner!==null?s.winner:evaluated[0];
   const name=el('input',{'aria-label':'調整 新しいモデル名',value:(c.definition.kind.split(':').pop()+'-tuned-'+data.id.slice(0,8)).slice(0,120)});
   const save=el('button',{class:'primary',onclick:this.run(async()=>{const r=await this.api('/api/tuning/'+data.id+'/save','POST',{candidate:Number(pick.value),name:name.value});await this.onSaved(r);save.textContent='保存しました';})},'候補を新しいモデル定義として保存');
   // Keep user edits while polling; only the result display is refreshed.
   const previous=this.saveDraft;if(previous?.id===data.id){if(evaluated.includes(previous.candidate))pick.value=previous.candidate;name.value=previous.name;}
   const draft=()=>this.saveDraft={id:data.id,candidate:Number(pick.value),name:name.value};
   pick.onchange=name.oninput=draft;
   root.append(el('div',{class:'tuning-export'},el('h3',{},'ライブラリへ追加'),el('p',{class:'hint'},'保存するのはkind・options・行動空間です。学習済み重みやQ-tableは移しません。途中候補も保存可能ですが、確認済みとは扱いません。同名の定義は上書きしません。'),el('label',{},'候補',pick),el('label',{},'新しいモデル名',name),save));
  }
 }
 download(data){
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=this.el('a',{href:url,download:'tuning-'+data.id+'.json'});a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }
}
if(typeof module!=='undefined')module.exports=TuningPanel;
