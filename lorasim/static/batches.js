'use strict';
/** Cartesian comparison authoring. Queued Runs use the ordinary job API. */
class BatchPanel {
 constructor({api,el,info,getSource,onQueued}){
  Object.assign(this,{api,el,info,getSource,onQueued});this.busy=false;
  this.dialog=el('dialog',{class:'batch-dialog','aria-labelledby':'batchTitle'});
  this.dialog.addEventListener('cancel',e=>{if(this.busy)e.preventDefault();});
  document.body.append(this.dialog);
 }
 static counts(text){
  const tokens=text.trim().split(/[,、\s]+/).filter(Boolean);
  if(!tokens.length||tokens.length>50||tokens.some(t=>!/^\d+$/.test(t)||Number(t)<1||Number(t)>1000000))throw Error('ED数は1〜1,000,000の整数を、カンマまたは改行で最大50個指定してください。');
  const values=tokens.map(Number);
  if(new Set(values).size!==values.length)throw Error('ED数が重複しています。重複を取り除いてください。');
  return values;
 }
 open(){
  if(this.busy)return;
  this.source=this.getSource();this.rows=[];this.plan=null;this.submission=null;this.completed=false;
  const {el,info}=this;
  this.close=el('button',{type:'button',onclick:()=>this.dialog.close()},'閉じる');
  this.counts=el('input',{value:String(this.source.profile.groups.reduce((n,g)=>n+g.count,0)),'aria-label':'一括実行 総ED数の候補',placeholder:'100, 200, 300',oninput:()=>this.invalidate()});
  this.picker=el('select',{'aria-label':'一括実行 追加する選択アルゴリズム'},this.source.choices.map((c,i)=>el('option',{value:i},c.label)));
  this.entries=el('div',{class:'batch-entries'});
  this.summary=el('p',{class:'batch-count','aria-live':'polite'});
  this.errors=el('div',{'aria-live':'assertive'});
  this.output=el('div',{class:'batch-preview','aria-live':'polite'});
  this.previewButton=el('button',{type:'button',onclick:()=>this.run(()=>this.preview())},'組み合わせを確認');
  this.submitButton=el('button',{type:'button',class:'primary',onclick:()=>this.run(()=>this.submit())},'確認したRunをキューに追加');
  this.fields=el('fieldset',{class:'batch-fields'},
   el('label',{},'総ED数の候補',info('各Runの全EDグループ合計。複数グループは元の比率を基に整数配分し、最低1台を残します。'),this.counts),
   el('h3',{},'比較する選択アルゴリズム'),
   el('div',{class:'actions'},this.picker,el('button',{type:'button',onclick:()=>this.add()},'リストに追加')),
   el('p',{class:'hint'},'現在のグループ設定、モデルライブラリの保存定義、内蔵モデルから選択できます。定数を調整する場合は、モデルライブラリに定義を保存してください。'),
   this.entries,this.summary,this.errors,this.output,el('div',{class:'actions batch-footer'},this.previewButton,this.submitButton));
  this.dialog.replaceChildren(
   el('div',{class:'sectiontitle'},el('div',{},el('span',{class:'eyebrow'},'BATCH COMPARISON'),el('h2',{id:'batchTitle'},'ED数 × 選択アルゴリズムの一括実行')),this.close),
   el('p',{},'候補の直積を通常のRunとして登録します。100, 200, 300 ED × Bandit, DQNなら6 Runです。'),
   el('p',{class:'hint'},`元のプロファイル：${this.source.profile.name} ／ ${this.source.profile.duration_s} s ／ seed ${this.source.profile.seed}。この画面を開いた時点の設定を使用します。`),
   el('p',{class:'comparison-note'},'標準ではアルゴリズムごとに新規Experimentを作成し、ED数の異なるRunをまとめます。保存先は各行で既存Experimentにも変更できます。通常実行のExperiment選択とは独立しています。'),
   this.fields);
  this.invalidate();this.dialog.showModal();
 }
 add(){
  const choice=this.source.choices[Number(this.picker.value)];
  if(!choice||this.rows.some(r=>r.key===choice.key))return;
  const {el}=this,row={key:choice.key,definition:structuredClone(choice.definition)};
  row.name=el('input',{value:choice.name,'aria-label':choice.label+' の比較名',oninput:()=>this.invalidate()});
  row.experiment=el('select',{'aria-label':choice.label+' の保存先Experiment',onchange:()=>this.invalidate()},
   el('option',{value:''},'新規Experiment（このアルゴリズム専用）'),
   this.source.experiments.map(e=>el('option',{value:e.id},`${e.name} · ${e.runs} Runs · ${e.id.slice(0,6)}`)));
  row.node=el('section',{class:'batch-choice'},
   el('div',{class:'sectiontitle'},el('strong',{},choice.label),el('button',{type:'button',class:'small',onclick:()=>{this.rows=this.rows.filter(r=>r!==row);row.node.remove();this.invalidate();}},'削除')),
   el('div',{class:'batch-grid'},el('label',{},'比較名',row.name),el('label',{},'保存先Experiment',row.experiment)),
   el('details',{},el('summary',{},'使用するモデル定義・候補空間'),el('pre',{},JSON.stringify(row.definition,null,2))));
  this.rows.push(row);this.entries.append(row.node);this.invalidate();
 }
 invalidate(){
  this.plan=null;this.submission=null;this.output.replaceChildren();this.errors.replaceChildren();this.submitButton.disabled=true;
  try{
   const counts=BatchPanel.counts(this.counts.value),n=counts.length*this.rows.length;
   this.summary.textContent=`ED数 ${counts.length} 候補 × アルゴリズム ${this.rows.length} 候補 = ${n} Run（上限100）`;
   this.previewButton.disabled=!n||n>100;
  }catch(e){this.summary.textContent=e.message;this.previewButton.disabled=true;}
 }
 async run(fn){
  if(this.busy||this.completed)return;
  this.busy=true;this.fields.disabled=true;this.close.disabled=true;this.errors.replaceChildren();
  try{await fn();}
  catch(e){
   const ds=e.diagnostics||[{message:e.message,hint:'候補と設定を修正して、組み合わせを再確認してください。'}];
   for(const d of ds)this.errors.append(this.el('article',{class:'diagnostic error'},
    this.el('p',{},d.message),this.el('p',{class:'diagnostic-hint'},'ヒント：'+d.hint),
    d.detail?this.el('details',{},this.el('summary',{},'技術詳細'),this.el('pre',{},d.detail)):null));
   this.errors.scrollIntoView({block:'center'});
  }finally{this.busy=false;this.fields.disabled=this.completed;this.close.disabled=false;}
 }
 async preview(){
  // A fresh confirmation invalidates the earlier request before the network call.
  this.invalidate();
  if(!this.rows.length)throw Error('選択アルゴリズムを1つ以上リストに追加してください。');
  const body={yaml:JSON.stringify(this.source.profile),ed_counts:BatchPanel.counts(this.counts.value),
   policies:this.rows.map(r=>({name:r.name.value.trim(),definition:r.definition,experiment_id:r.experiment.value||null}))};
  const plan=await this.api('/api/batches/preview','POST',body);
  this.plan=plan;this.submission={...body,plan_hash:plan.plan_hash,request_id:Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('')};
  const {el}=this;
  const table=el('table',{},el('thead',{},el('tr',{},['ED数','選択アルゴリズム','グループ配分','Experiment','設定チェック'].map(s=>el('th',{},s)))),
   el('tbody',{},plan.rows.map(r=>el('tr',{},el('td',{},r.ed_count),el('td',{},r.algorithm),
    el('td',{},r.groups.map(g=>`${g.name}: ${g.count}`).join(' / ')),
    el('td',{},r.experiment.name,el('small',{},r.experiment.id?'既存へ追加':'新規作成')),
    el('td',{},r.diagnostics.length?el('details',{},el('summary',{},`Warning ${r.diagnostics.length}件`),
     r.diagnostics.map(d=>el('p',{class:'hint'},d.message+' ヒント：'+d.hint))):'検証済み')))));
  this.planTitle=el('h3',{},`${plan.run_count} Runを登録予定 ／ 新規Experiment ${plan.new_experiments}件`);
  this.output.append(this.planTitle,
   el('div',{class:'tablewrap'},table),el('ul',{class:'hint'},plan.notes.map(s=>el('li',{},s))));
  this.submitButton.disabled=false;
  this.output.scrollIntoView({block:'start'});
 }
 async submit(){
  if(!this.submission)throw Error('先に「組み合わせを確認」を実行してください。');
  const result=await this.api('/api/batches','POST',this.submission);
  this.completed=true;this.submitButton.disabled=true;
  this.planTitle.textContent=`${result.run_count} Run登録済み ／ Experiment ${result.experiment_ids.length}件`;
  this.output.prepend(this.el('p',{class:'batch-success',role:'status'},`${result.run_count} Runをキューに追加しました。実行順・並列数は既存workerが管理します。`));
  this.output.scrollIntoView({block:'start'});
  // Keep the acknowledged result visible even if refreshing a separate panel fails.
  try{await this.onQueued(result);}catch{this.output.append(this.el('p',{class:'hint'},'登録は完了しました。一覧の取得に失敗した場合は画面を再読み込みしてください。'));}
 }
}
if(typeof module!=='undefined')module.exports=BatchPanel;
