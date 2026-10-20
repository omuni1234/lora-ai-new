'use strict';

// Independent, request-driven preflight tool. Does not edit profiles or run jobs.
class LinkBudgetCalculator {
 constructor({root, api, el, info, getProfile, editChannel}) {
  Object.assign(this,{root,api,el,info,getProfile});
  this.visible=false;this.serial=0;this.initialized=false;this.timer=null;
  this.form=el('form',{class:'calculator-form'});
  this.fields={};
  const field=(key,title,description,options)=>{
   const input=Array.isArray(options)?el('select',{id:'calc-'+key},options.map(([v,t])=>el('option',{value:v},t))):el('input',{id:'calc-'+key,type:'number',step:'any',required:true,...options});
   if(!Array.isArray(options))input.required=options.required!==false;
   this.fields[key]=input;
   return el('label',{for:'calc-'+key},el('span',{class:'caption'},title,info(description,title+'の説明')),input);
  };
  const main=el('div',{class:'calculator-fields'},
   field('tp','送信電力 TP [dBm]','送信側の電力。試算用の値で、実験プロファイルは変更しません。',{min:-20,max:27,value:14}),
   field('sf','拡散率 SF','SFによって受信感度と送信時間が変わります。',[7,8,9,10,11,12].map(x=>[x,'SF'+x])),
   field('cr','符号化率 CR','現行PHYはCRによる感度改善を扱いません。CRを変えると送信時間が変わります。',[1,2,3,4].map(x=>[x,'4/'+(4+x)])));
  const extra=el('div',{class:'calculator-fields'},
   field('direction','送受信方向','ULはED→GW、DLはGW→ED。受信側の感度テーブルを使用します。',[['uplink','UL · ED → GW'],['downlink','DL · GW → ED']]),
   field('probe','確認する距離 [m]','送受信点間の直線距離。この位置の受信電力と感度からの余裕を表示します。',{min:0,max:10000000,value:1000}),
   field('max','横軸の最大距離 [m]','空欄は受信限界と確認距離から自動設定。表示上限は10,000,000 mです。',{min:1,max:10000000,placeholder:'自動',required:false}),
   field('margin','必要な余裕 [dB]','受信感度に加える試算用マージン。0なら本体の感度しきい値そのもの。実験の判定は変更しません。',{min:0,max:60,value:0}),
   field('bytes','PHYパケット長 [byte]','送信時間の計算だけに使用します。既定はアプリ20 byte＋本モデルのヘッダ9 byte。ADRコマンド等を含むなら総長を指定してください。',{min:1,max:255,step:1,value:29}));
  this.settings=el('p',{class:'calculator-model hint'});
  this.status=el('p',{class:'hint',role:'status','aria-live':'polite'},'計算すると、設定した伝搬式による目安を表示します。');
  this.errors=el('div',{role:'alert'});
  this.result=el('div',{class:'calculator-result'});
  this.form.append(main,el('details',{class:'calculator-options'},el('summary',{},'試算条件・距離・パケット長'),extra),
   el('div',{class:'actions'},el('button',{type:'submit',class:'primary small'},'計算する'),
    el('button',{type:'button',class:'small',onclick:()=>{this.defaults();this.refresh();}},'共通候補から初期値を取り込む'),
    el('button',{type:'button',class:'quiet small',onclick:editChannel},'伝搬設定を編集')));
  this.form.addEventListener('submit',e=>{e.preventDefault();this.refresh();});
  this.form.addEventListener('input',()=>this.invalidate());
  root.append(this.form,this.errors,this.result,this.settings,this.status);
  document.addEventListener('visibilitychange',()=>{clearTimeout(this.timer);++this.serial;if(!document.hidden&&this.visible)this.refresh();});
 }
 defaults() {
  const p=this.getProfile(),a=p.action_space||{};
  for(const[k,v]of Object.entries({tp:a.powers_dbm?.[0]??14,sf:a.spreading_factors?.[0]??7,cr:a.coding_rates?.[0]??1,bytes:Math.min(255,(p.groups?.[0]?.payload_bytes??20)+9)}))this.fields[k].value=v;
  this.initialized=true;
 }
 setVisible(visible) {
  this.visible=visible;clearTimeout(this.timer);++this.serial;
  if(visible){if(!this.initialized)this.defaults();this.refresh();}
 }
 invalidate() {
  clearTimeout(this.timer);++this.serial;
  this.result.classList.add('calculator-stale');
  this.status.textContent='設定変更あり · 再計算待ち';
  if(this.visible&&!document.hidden)this.timer=setTimeout(()=>this.refresh(),300);
 }
 async refresh() {
  clearTimeout(this.timer);
  if(!this.visible||document.hidden)return;
  const serial=++this.serial;
  this.result.classList.add('calculator-stale');this.errors.replaceChildren();
  if(!this.form.checkValidity()){
   this.status.textContent='入力を確認してください。';
   this.errors.append(this.el('p',{class:'diagnostic error'},'Error · 数値の形式または入力範囲が正しくありません。ヒント：TP −20〜27、距離 0〜10,000,000、余裕 0〜60、PHY長は整数で1〜255を入力してください。横軸の最大距離は1以上または空欄です。'));
   return;
  }
  const f=this.fields,channel=structuredClone(this.getProfile().channel||{});
  this.status.textContent='計算中…';
  try {
   const data=await this.api('/api/calculator','POST',{
    channel,radio:{tp_dbm:Number(f.tp.value),sf:Number(f.sf.value),cr:Number(f.cr.value)},
    direction:f.direction.value,probe_distance_m:Number(f.probe.value),
    max_distance_m:f.max.value===''?null:Number(f.max.value),margin_db:Number(f.margin.value),phy_bytes:Number(f.bytes.value)});
   if(serial!==this.serial||!this.visible)return;
   this.result.classList.remove('calculator-stale');this.draw(data);
   const c=data.channel;
   this.settings.textContent=`LogDistance · n=${c.exponent} / 基準距離 ${c.reference_distance_m} m / 基準損失 ${c.reference_loss_db} dB · BW 125 kHz`;
   this.status.textContent='計算済み · 現在の伝搬設定を使用';
  } catch(e) {
   if(serial!==this.serial)return;
   this.status.textContent='計算できませんでした。';
   for(const d of e.diagnostics||[{message:'計算に失敗しました。',hint:'接続状態と伝搬設定を確認してください。'}])this.errors.append(this.el('article',{class:'diagnostic error'},this.el('p',{},d.message),this.el('p',{class:'diagnostic-hint'},'ヒント：'+d.hint)));
  }
 }
 draw(d) {
  const el=this.el,fmt=(v,n=1)=>v.toLocaleString('ja-JP',{maximumFractionDigits:n}),power=v=>fmt(v,2)+' dBm';
  const range=d.range_state==='below_at_reference'?'感度条件を満たす距離なし':d.range_state==='beyond_numeric_range'?'数値表示範囲外':fmt(d.range_m)+' m';
  const card=(title,value,detail)=>el('div',{},el('span',{},title),el('strong',{},value),el('small',{},detail));
  const metrics=el('div',{class:'calculator-metrics'},
   card(d.margin_db?'余裕込みの受信限界':'感度上の受信限界',range,d.margin_db?`必要な余裕 ${fmt(d.margin_db)} dB`:'干渉なしの距離の目安'),
   card(`${fmt(d.probe.distance_m)} m の受信電力`,power(d.probe.received_dbm),`感度からの余裕 ${fmt(d.probe.margin_db,2)} dB`),
   card('送信時間',fmt(d.airtime_ms,3)+' ms',`${d.phy_bytes} byte · CR 4/${4+d.radio.cr}`));
  const chart=el('div',{class:'calculator-chart'}),w=860,h=315,L=88,R=26,T=34,B=56,pw=w-L-R,ph=h-T-B;
  const lo=Math.floor((Math.min(...d.received_dbm,d.sensitivity_dbm)-10)/20)*20;
  const hi=Math.ceil((Math.max(...d.received_dbm,d.required_dbm)+10)/20)*20;
  const x=v=>L+v/d.max_distance_m*pw,y=v=>T+(hi-v)/(hi-lo)*ph;
  const threshold=y(d.required_dbm),rx=d.direction==='uplink'?'GW':'ED',tx=d.direction==='uplink'?'ED':'GW';
  let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="距離に対する受信電力。${tx}から${rx}への${d.margin_db?'余裕込みの':'感度上の'}受信限界は${range}"><title>距離に対する受信電力</title><desc>横軸は送受信点間距離、縦軸は受信電力。${d.notes[2]}</desc>`;
  svg+=`<rect x="${L}" y="${T}" width="${pw}" height="${ph}" rx="4" fill="#f6f9fc"/><rect x="${L}" y="${T}" width="${pw}" height="${Math.max(0,threshold-T)}" fill="#eaf5f1"/>`;
  for(let v=lo;v<=hi;v+=Math.max(20,Math.ceil((hi-lo)/120)*20))svg+=`<path d="M${L} ${y(v)}H${w-R}" stroke="#dce6eb"/><text x="${L-9}" y="${y(v)+4}" text-anchor="end">${fmt(v,0)}</text>`;
  for(let i=0;i<=5;i++)svg+=`<text x="${x(d.max_distance_m*i/5)}" y="${h-B+22}" text-anchor="middle">${fmt(d.max_distance_m*i/5,0)}</text>`;
  svg+=`<path d="M${L} ${threshold}H${w-R}" stroke="#ba7b20" stroke-width="1.5" stroke-dasharray="6 4"/><text x="${w-R-5}" y="${threshold-7}" text-anchor="end" class="calculator-threshold">${rx}受信感度 ${power(d.sensitivity_dbm)}${d.margin_db?' ＋ '+fmt(d.margin_db)+' dB':''}</text>`;
  if(d.margin_db)svg+=`<path d="M${L} ${y(d.sensitivity_dbm)}H${w-R}" stroke="#b0a593" stroke-dasharray="2 4"/>`;
  if(d.range_m!==null&&d.range_m<=d.max_distance_m){
   const xx=x(d.range_m);
   svg+=`<path d="M${xx} ${threshold}V${h-B}" stroke="#ba7b20" stroke-dasharray="4 4"/><circle cx="${xx}" cy="${threshold}" r="5" fill="#ba7b20"/><text x="${Math.min(w-R-8,xx+9)}" y="${Math.min(h-B-8,threshold+19)}" text-anchor="${xx>w-150?'end':'start'}" class="calculator-threshold">${fmt(d.range_m)} m</text>`;
  }
  const points=d.distances_m.map((v,i)=>`${x(v).toFixed(3)},${y(d.received_dbm[i]).toFixed(3)}`).join(' ');
  svg+=`<polyline points="${points}" fill="none" stroke="#167d91" stroke-width="3" stroke-linejoin="round"/>`;
  if(d.probe.distance_m<=d.max_distance_m)svg+=`<circle cx="${x(d.probe.distance_m)}" cy="${y(d.probe.received_dbm)}" r="5" fill="#fff" stroke="#167d91" stroke-width="2"><title>${fmt(d.probe.distance_m)} m: ${power(d.probe.received_dbm)}</title></circle>`;
  svg+=`<rect x="24" y="23" width="24" height="31" rx="4" fill="#e0eef2" stroke="#167d91"/><path d="M36 23V14M29 17Q36 8 43 17" fill="none" stroke="#167d91"/><text x="36" y="70" text-anchor="middle" class="calculator-source">${tx}</text><text x="${L}" y="18">送信 ${power(d.radio.tp_dbm)} · SF${d.radio.sf}</text><text x="${L+pw/2}" y="${h-9}" text-anchor="middle">送受信点間の距離 [m]</text><text transform="translate(17 175) rotate(-90)" text-anchor="middle">受信電力 [dBm]</text></svg>`;
  chart.innerHTML=svg;
  const outside=d.range_state==='beyond_numeric_range'||d.range_m>d.max_distance_m;
  const note=el('p',{class:'calculator-verdict '+(d.probe.meets_required?'meets':'misses')},`${fmt(d.probe.distance_m)} m：${d.probe.meets_required?'感度条件を満たします':'感度条件を満たしません'}（伝搬損失 ${fmt(d.probe.path_loss_db,2)} dB）`);
  this.result.replaceChildren(chart,metrics,note);
  if(outside)this.result.append(el('p',{class:'hint'},'受信限界はグラフの表示範囲より先にあります。横軸の最大距離を増やすと確認できます（最大10,000,000 m）。'));
  if(d.probe.distance_m>d.max_distance_m)this.result.append(el('p',{class:'hint'},'確認距離は表示範囲外です。数値はその距離で計算しています。'));
  this.result.append(el('p',{class:'calculator-caveat'},d.notes[0]),el('details',{},el('summary',{},'計算式・読み方'),el('p',{class:'hint'},'PL(d) = L₀ + 10n log₁₀(max(d/d₀, 1))、受信電力 = TP − PL。受信電力 ≥ 受信感度 ＋ 必要な余裕の距離を表示します。'),...d.notes.slice(1).map(n=>el('p',{class:'hint'},n))));
 }
}

// One Calculator entry, independent request-driven preflight tools.
class PropagationCalculator {
 constructor(context){
  this.visible=false;this.selected='link';const {root,el}=context;
  this.panes={};this.buttons={};this.tools={};
  const tabs=el('div',{class:'preflight-tabs',role:'tablist','aria-label':'Calculatorの機能'});
  for(const [key,label] of [['link','伝搬距離'],['forecast','トレードオフ予測'],['experience','経験分布'],['consultant','AIコンサルタント']]){
   this.panes[key]=el('div',{role:'tabpanel',id:'planning-'+key,'aria-label':label,hidden:key!=='link'});
   this.buttons[key]=el('button',{type:'button',role:'tab','aria-controls':'planning-'+key,'aria-selected':String(key==='link'),onclick:()=>this.select(key)},label);
   tabs.append(this.buttons[key]);
  }
  root.append(tabs,...Object.values(this.panes));
  this.tools.link=new LinkBudgetCalculator({...context,root:this.panes.link});
  this.tools.forecast=new TradeoffCalculator({...context,root:this.panes.forecast});
  this.tools.experience=new ExperienceCalculator({...context,root:this.panes.experience});
  this.tools.consultant=new ConsultantPanel({...context,root:this.panes.consultant,getForecastOptions:()=>this.tools.forecast.options()});
 }
 select(key){this.selected=key;for(const k of Object.keys(this.panes)){this.panes[k].hidden=k!==key;this.buttons[k].setAttribute('aria-selected',String(k===key));this.tools[k].setVisible(this.visible&&k===key);}}
 setVisible(v){this.visible=v;this.select(this.selected);}
 invalidate(){for(const t of Object.values(this.tools))t.invalidate();}
}
