'use strict';

// Generation opportunities only; no polling and no simulation/selector execution.
class ExperienceCalculator {
 constructor({root,api,el,info,getProfile}) {
  Object.assign(this,{root,api,el,info,getProfile});this.serial=0;this.fields={};
  this.fields.concentration_basis=el('select',{'aria-label':'集中を確認する単位'},el('option',{value:'stationary_site'},'静止地点 · 同じ場所への反復経験'),el('option',{value:'spatial_cell'},'全送信地点 · 静止／移動を含む地図メッシュ'));
  const field=(key,title,description,value,attrs={})=>{
   const input=el('input',{type:'number',step:'any',value,required:value!=='',...attrs,'aria-label':title});
   input.required=value!=='';
   this.fields[key]=input;
   return el('label',{},el('span',{class:'caption'},title,info(description,title+'の説明')),input);
  };
  this.form=el('form',{class:'calculator-form'},el('p',{class:'calculator-caveat'},'移動×送信タイミングが、各EDに偏った経験を与える条件か確認します。静止と移動が半々でも、同じ静止地点への経験集中は残ります。選択器の学習・通信成否は計算しません。'),
   el('div',{class:'calculator-fields'},
    field('start_s','確認開始 [s]','集計区間の開始。モデルは0秒から進めます。',0,{min:0,max:1e9}),
    field('end_s','確認終了 [s]','空欄は実験時間と24時間の短い方。終了時刻ちょうどの生成は除外します。','',{min:.000001,max:1e9,placeholder:'自動 · 最大24時間'}),
    field('site_resolution_m','静止地点の集約幅 [m]','原点基準の3次元セルで同じ地点をまとめます。地図メッシュとは別です。',1,{min:.000001,max:100000})),
   el('details',{},el('summary',{},'集中の確認基準・標本・処理上限'),el('div',{class:'calculator-fields'},
    el('label',{},el('span',{class:'caption'},'集中を確認する単位',info('静止地点を対象にするか、移動中も含めて同じメッシュへの送信集中を調べるか選択します。')),this.fields.concentration_basis),
    field('concentration_threshold','1地点への集中基準 [%]','全経験候補に対して、選んだ単位の1地点が占める割合。研究設計用の任意基準です。',30,{min:.01,max:100}),
    field('minimum_experiences','基準判定に必要な経験候補数','これより少ないED、または状態不明のあるEDは判定保留にします。',30,{min:1,max:20000,step:1}),
    field('device_samples','標本ED数','グループごとに標本化。各EDは独立して集計し、全体表示だけ重みを補正します。',32,{min:1,max:128,step:1}),
    field('device_id','特定のED番号','空欄なら標本化。指定するとそのEDだけを確認します（0始まり）。','',{min:0,step:1,placeholder:'全体から標本化'}),
    field('max_generations','1台あたりの生成回数上限','開始時刻前も含めた上限。超過時は部分結果を表示せず、区間短縮を案内します。全体20万回まで。',5000,{min:1,max:20000,step:1}),
    field('time_samples','滞在分布の時刻標本数','時間で見た分布を層化一様時刻で近似します。送信位置・静止地点の回数は区間内全生成から集計します。',1024,{min:64,max:4096,step:1}),
    field('grid_size','地図の縦・横分割数','送信地点と時間分布を比較する2次元メッシュ。分割数でTVが変わるため同条件で比較してください。',16,{min:4,max:32,step:1}),
    field('seed','標本抽出シード','ED・時間標本の抽出だけに使用。移動・送信モデルの乱数はプロファイルのseedを使います。',73,{min:0,max:4294967295,step:1}))),
   el('div',{class:'actions'},this.run=el('button',{type:'submit',class:'primary'},'経験分布を確認する')));
  this.status=el('p',{class:'hint',role:'status'},'現在のプロファイルの移動・送信タイミングを使います。');
  this.error=el('div',{role:'alert'});this.result=el('div',{class:'experience-result'});
  this.form.onsubmit=e=>{e.preventDefault();this.refresh();};this.form.oninput=()=>this.invalidate();
  root.append(this.form,this.status,this.error,this.result);
 }
 options(){const o={};for(const[k,f]of Object.entries(this.fields))o[k]=k==='concentration_basis'?f.value:f.value===''?null:Number(f.value);o.concentration_threshold/=100;return o;}
 setVisible(v){this.visible=v;}
 invalidate(){++this.serial;this.result.classList.add('calculator-stale');this.status.textContent='設定変更あり · 「経験分布を確認する」で再確認してください。';}
 async refresh(){
  if(!this.form.reportValidity())return;
  const serial=++this.serial;this.run.disabled=true;this.error.replaceChildren();this.result.classList.add('calculator-stale');this.status.textContent='移動・送信予定を確認中…';
  try{const d=await this.api('/api/calculator/experience','POST',{yaml:JSON.stringify(this.getProfile()),options:this.options()});
   if(serial!==this.serial)return;this.data=d;this.draw(d);this.result.classList.remove('calculator-stale');
   this.status.textContent=`確認済み · ${d.start_s}〜${d.end_s} s · 標本 ${d.sampled_devices} / ${d.ed_count} ED · ${d.wall_s.toFixed(2)}秒`;
  }catch(e){if(serial===this.serial){planningErrors(this.error,e,this.el);this.status.textContent='確認できませんでした。';}}finally{this.run.disabled=false;}
 }
 draw(d){
  const el=this.el,pct=ExperienceCalculator.pct;
  const names={concentrated:'集中基準に該当',below_criterion:'今回の基準には非該当',insufficient:'標本不足・状態不明'};
  const metric=d.options.concentration_basis==='spatial_cell'?'dominant_cell_fraction':'dominant_site_fraction';
  const basis=metric==='dominant_cell_fraction'?'地図メッシュ':'静止地点';
  const ordered=[...d.devices].sort((a,b)=>(b[metric]??-1)-(a[metric]??-1));
  const select=el('select',{'aria-label':'経験分布を確認するED'},ordered.map(r=>el('option',{value:r.id},`ED ${r.id} · ${r.group} · 最大${basis} ${pct(r[metric])} · ${names[r.concentration_state]}`)));
  this.detail=el('div');select.onchange=()=>this.drawDevice(d,d.devices.find(r=>r.id===Number(select.value)));
  const table=el('table',{},el('thead',{},el('tr',{},['ED / グループ','静止 / 移動 / 不明',`最大${basis} / 全経験`,'静止地点数','最長連続','確認基準'].map(x=>el('th',{},x)))),
   el('tbody',{},ordered.map(r=>el('tr',{},el('td',{},el('button',{type:'button',class:'quiet small',onclick:()=>{select.value=r.id;select.onchange();}},`ED ${r.id} · ${r.group}`)),
    el('td',{},`${r.states.stationary} / ${r.states.moving} / ${r.states.unknown}`),el('td',{},pct(r[metric])),el('td',{},String(r.stationary_site_count)),el('td',{},String(r.longest_same_site_streak)),el('td',{},names[r.concentration_state])))));
  this.result.replaceChildren(el('div',{class:'experience-finding'},
   el('strong',{},`集中基準に該当：標本 ${d.concentrated_devices} / 判定可能 ${d.assessed_devices} ED`),
   el('p',{},`1つの${basis}が全経験候補の${pct(d.options.concentration_threshold)}以上。判定保留 ${d.sampled_devices-d.assessed_devices} ED。非該当は「偏りなし」を意味しません。`),
   el('p',{class:'hint'},'同じ移動・送信条件で選択器を比較し、この偏りに対処できたかを本実験で評価してください。確認結果から学習性能は判定しません。')),
   el('label',{},'各EDの経験を見る',select),this.detail,
   el('details',{},el('summary',{},'標本ED一覧・全体指標'),el('div',{class:'forecast-table'},table),
    el('p',{class:'hint'},`最大静止地点占有率のED平均 ${pct(d.aggregate.mean_dominant_site_fraction)}。送信と滞在のTV：ED別平均 ${pct(d.aggregate.mean_spatial_tv)} / 分布を合算したTV ${pct(d.aggregate.pooled_spatial_tv)}。全体への合算で個体の偏りが隠れることがあります。`)),
   ...d.warnings.map(w=>el('p',{class:'diagnostic warning'},w)),
   el('details',{},el('summary',{},'読み方・計算範囲'),...d.notes.map(n=>el('p',{class:'hint'},n))),
   el('button',{type:'button',class:'small',onclick:()=>planningDownload(d,'experience-distribution.json')},'確認結果JSONを保存'));
  if(ordered.length)this.drawDevice(d,ordered[0]);
 }
 drawDevice(d,r){
  const el=this.el,pct=ExperienceCalculator.pct,n=r.generations;
  const card=(title,value,note)=>el('div',{},el('span',{},title),el('strong',{},value),el('small',{},note));
  const table=el('table',{},el('thead',{},el('tr',{},['地点の代表座標 [m]','経験候補数','全経験中','静止経験中'].map(s=>el('th',{},s)))),el('tbody',{},r.stationary_sites.map(s=>el('tr',{},el('td',{},s.xyz.map(x=>x.toFixed(1)).join(', ')),el('td',{},String(s.count)),el('td',{},pct(s.all_fraction)),el('td',{},pct(s.stationary_fraction))))));
  const maps=el('div',{class:'experience-maps'},this.map(d,r.tx_grid,'送信生成時の地点分布',n),this.map(d,r.residence_grid,'時間で見た滞在分布',d.options.time_samples));
  const timeline=el('div',{class:'experience-timeline'});timeline.innerHTML=ExperienceCalculator.timelineSVG(r.timeline,d.start_s,d.end_s);
  this.detail.replaceChildren(el('h3',{},`ED ${r.id} · ${r.group} · ${n} 回の経験候補`),el('div',{class:'calculator-metrics'},
   card('静止中 / 移動中',`${r.states.stationary} / ${r.states.moving} 回`,`不明 ${r.states.unknown} 回 · 静止割合 ${n?pct(r.states.stationary/n):'—'}`),
   card('最大静止地点 / 全経験',pct(r.dominant_site_fraction),`静止経験だけでは ${pct(r.dominant_stationary_fraction)}`),
   card('同地点の最長連続経験',r.longest_same_site_streak+' 回',`静止地点 ${r.stationary_site_count} 個 / 実効地点数 ${r.effective_stationary_sites?.toFixed(2)??'—'}`)),
   el('p',{class:'hint'},`静止の時間割合 ${pct(r.time_state_fractions.stationary)} / 静止の送信割合 ${n?pct(r.states.stationary/n):'—'}。送信と滞在の地図分布のずれ（TV）${pct(r.spatial_tv)}。全送信地点の最大メッシュ占有率 ${pct(r.dominant_cell_fraction)} / 実効メッシュ数 ${r.effective_spatial_cells?.toFixed(2)??'—'}。`),
   maps,el('h3',{},'時間順の経験候補 · 静止／移動／不明'),timeline,
   el('div',{class:'chartlegend forecast-legend'},['静止','#257a94','移動','#dd9636','不明','#9aa5b1'].reduce((a,v,i,all)=>i%2?a:[...a,el('span',{},el('i',{style:'background:'+all[i+1]}),v)],[])),
   el('h3',{},'静止地点別の経験候補'),r.stationary_sites.length?el('div',{class:'forecast-table'},table):el('p',{class:'hint'},'静止中の送信生成はありません。状態不明は静止地点として数えません。'),
   el('p',{class:'hint'},r.site_table_omitted?`上位20地点を表示（残り ${r.site_table_omitted} 地点）。指標には全地点を含みます。`:'同じ場所に留まる経験が何回重なったかを示します。'));
 }
 map(d,counts,title,total){
  const el=this.el,box=el('div',{class:'experience-map'},el('h4',{},title)),chart=el('div');
  chart.innerHTML=ExperienceCalculator.mapSVG(counts,d.area_m,d.options.grid_size,total);
  const outside=counts[counts.length-1];
  box.append(chart,el('p',{class:'hint'},`横0〜${d.area_m[0]} m / 縦0〜${d.area_m[1]} m。各マスの色は全体に占める割合。濃い色ほど集中。エリア外 ${total?ExperienceCalculator.pct(outside/total):'—'}。`));return box;
 }
 static pct(v){return v===null||v===undefined?'—':(v*100).toFixed(1)+'%';}
 static mapSVG(counts,area,n,total){
  const width=400,height=400*area[1]/area[0],cw=width/n,ch=height/n;
  let s=`<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="地点別の経験または滞在割合。縦横の距離尺度は同じ"><rect width="${width}" height="${height}" fill="#f2f6fa"/>`;
  for(let j=0;j<n*n;j++){const v=total?counts[j]/total:0,opacity=v?Math.min(1,.12+.88*Math.sqrt(v)):0;
   s+=`<rect x="${(j%n)*cw}" y="${height-(Math.floor(j/n)+1)*ch}" width="${cw}" height="${ch}" fill="#176b84" fill-opacity="${opacity}" stroke="#d9e4ec" stroke-width=".3"><title>x ${(j%n)*area[0]/n}〜${(j%n+1)*area[0]/n} m, y ${Math.floor(j/n)*area[1]/n}〜${(Math.floor(j/n)+1)*area[1]/n} m：${counts[j]} / ${total} (${ExperienceCalculator.pct(v)})</title></rect>`;
  }
  return s+'</svg>';
 }
 static timelineSVG(bins,start,end){
  const colors=['#257a94','#dd9636','#9aa5b1'],names=['静止','移動','不明'],w=800,h=145,left=35,top=12,base=113,max=Math.max(1,...bins.map(b=>b.reduce((a,c)=>a+c,0))),bw=(w-left-10)/bins.length;
  let s=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="時間区間別の送信生成回数"><text x="0" y="14">${max}</text><text x="12" y="${base}">0</text>`;
  bins.forEach((b,i)=>{let y=base;b.forEach((v,k)=>{const bh=v/max*(base-top);y-=bh;s+=`<rect x="${left+i*bw}" y="${y}" width="${bw-1}" height="${bh}" fill="${colors[k]}"><title>${(start+(end-start)*i/bins.length).toFixed(1)}〜${(start+(end-start)*(i+1)/bins.length).toFixed(1)} s：${names[k]} ${v} 回</title></rect>`;});});
  return s+`<text x="${left}" y="139">${start} s</text><text x="${w-10}" y="139" text-anchor="end">${end} s</text></svg>`;
 }
}
