'use strict';

const planningLabels={Received:'受信成功',Interference:'干渉',LostBecauseTx:'GW送信中',UnderSensitivity:'感度未満',NoMoreReceivers:'復調器不足',WrongFrequency:'周波数不一致',MixedLoss:'複合損失'};
const planningColors={Received:'#238aa0',Interference:'#db9952',LostBecauseTx:'#6e91c4',UnderSensitivity:'#da7083',NoMoreReceivers:'#a885be',WrongFrequency:'#a2aebd',MixedLoss:'#807d88'};
function planningErrors(root,error,el){root.replaceChildren(...(error.diagnostics||[{message:error.message||'処理に失敗しました。',hint:'入力内容と接続設定を確認してください。'}]).map(d=>el('article',{class:'diagnostic error'},el('p',{},d.message),el('p',{class:'hint'},'ヒント：'+d.hint))));}
function planningDownload(value,name){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

class TradeoffCalculator{
 constructor({root,api,el,info,getProfile}){
  Object.assign(this,{root,api,el,getProfile});this.serial=0;this.data=null;
  this.status=el('p',{role:'status',class:'hint'},'現在のプロファイルを読み取り、短い確率計算で比較します。本実験は開始しません。');
  this.error=el('div',{role:'alert'});this.result=el('div');this.fields={};
  const field=(k,label,description,input)=>{this.fields[k]=input;return el('label',{},el('span',{class:'caption'},label,info(description)),input);};
  const fields=el('div',{class:'calculator-fields'},
   field('action_mode','選択分布の仮定','プロファイル：固定器は固定、ADRは初期値、学習器は候補一様。候補一様：全体／ED候補を一様に利用。学習後の選択比率を予測する機能ではありません。',el('select',{'aria-label':'選択分布の仮定'},el('option',{value:'profile'},'プロファイルから推定'),el('option',{value:'uniform'},'全体／ED候補を一様利用'))),
   field('snapshot_s','配置を確認する時刻 [s]','移動の全軌跡平均ではなく、この時刻の位置で評価。0〜600秒。',el('input',{type:'number',min:0,max:600,value:0,step:'any','aria-label':'配置を確認する時刻'})),
   field('device_samples','標本ED数','最大128台の配置・モデル割当を標本化。少数EDの割当や感度境界では増やしてください。',el('input',{type:'number',min:8,max:128,value:48,'aria-label':'標本ED数'})),
   field('overlap_trials','候補ごとの重複試行数','ランダムな重複電波を合成する回数。増やすと標本誤差が減ります。近似の誤差自体はなくなりません。',el('input',{type:'number',min:32,max:512,value:96,'aria-label':'重複試行数'})),
   field('seed','予測用乱数シード','シミュレーション本体の乱数状態は消費しません。値を変えて推定の揺らぎを確認できます。',el('input',{type:'number',min:0,max:4294967295,value:73,'aria-label':'予測用乱数シード'})),
   field('custom_interval_s','独自モデルの平均間隔 [s]','独自送信タイミングだけに使用。periodic／poissonはプロファイルから読み取ります。',el('input',{type:'number',min:.000001,step:'any',placeholder:'内蔵モデルでは不要','aria-label':'独自モデルの平均間隔'})));
  this.form=el('form',{},el('p',{class:'calculator-caveat'},'設定探索用の近似予測です。学習器の優劣や実験結果を保証しません。干渉・GW送信中・感度未満が競合する条件を探すために使います。'),
   el('details',{},el('summary',{},'予測の仮定・計算量を調整'),fields));
  this.run=el('button',{class:'primary',type:'submit'},'プロファイルから予測する');
  this.form.append(el('div',{class:'actions'},this.run));
  this.form.onsubmit=e=>{e.preventDefault();this.refresh();};this.form.oninput=()=>this.invalidate();
  root.append(this.form,this.status,this.error,this.result);
 }
 options(){const result={};for(const[k,f]of Object.entries(this.fields))result[k]=k==='action_mode'?f.value:f.value===''?null:Number(f.value);return result;}
 setVisible(v){this.visible=v;}
 invalidate(){++this.serial;this.result.classList.add('calculator-stale');this.status.textContent='設定変更あり · 「プロファイルから予測する」で更新してください。';}
 async refresh(){
  if(!this.form.reportValidity())return;
  const serial=++this.serial;this.run.disabled=true;this.error.replaceChildren();this.status.textContent='確率モデルで計算中…';
  try{const data=await this.api('/api/calculator/forecast','POST',{yaml:JSON.stringify(this.getProfile()),options:this.options()});
   if(serial!==this.serial)return;this.data=data;this.draw(data);this.result.classList.remove('calculator-stale');this.status.textContent=`予測済み · ${data.ed_count.toLocaleString()} ED／標本 ${data.sampled_devices} ED · ${data.wall_s.toFixed(2)}秒 · profile ${data.profile_sha256.slice(0,10)}`;
  }catch(e){if(serial===this.serial){planningErrors(this.error,e,this.el);this.status.textContent='予測できませんでした。';}}finally{this.run.disabled=false;}
 }
 draw(data){
  const el=this.el,pct=v=>(v*100).toFixed(1)+'%',base=data.scenarios[0],keys=Object.keys(planningLabels);
  const chart=el('div',{class:'forecast-chart','aria-label':'条件ごとの推定UL成功・損失割合'});
  for(const row of data.scenarios){
   if(row.unavailable){chart.append(el('p',{class:'hint'},row.label+'：'+row.unavailable));continue;}
   const bar=el('div',{class:'forecast-bar',role:'img','aria-label':row.label+' '+keys.map(k=>planningLabels[k]+pct(row.fractions[k])).join('、')});
   for(const k of keys){const v=row.fractions[k];if(v>1e-6)bar.append(el('span',{style:`width:${v*100}%;background:${planningColors[k]}`,title:planningLabels[k]+' '+pct(v)},v>=.08?pct(v):''));}
   chart.append(el('div',{class:'forecast-row'},el('span',{},row.label),bar,el('b',{title:'受信成功率'},pct(row.fractions.Received))));
  }
  const legend=el('div',{class:'chartlegend forecast-legend'},keys.map(k=>el('span',{},el('i',{style:'background:'+planningColors[k]}),planningLabels[k])));
  const metrics=el('div',{class:'calculator-metrics'},
   el('div',{},el('span',{},'推定UL試行数'),el('strong',{},base.uplinks_per_s.toFixed(3)+' /s'),el('small',{},'再送・ED待ちの近似を含む')),
   el('div',{},el('span',{},'推定DL送信数'),el('strong',{},base.downlinks_per_s.toFixed(3)+' /s'),el('small',{},'成功UL → RX1/RX2 ACK')),
   el('div',{},el('span',{},'平均UL送信時間'),el('strong',{},base.mean_airtime_ms.toFixed(1)+' ms'),el('small',{},'GW送信占有率 '+base.gateway_tx_fraction.map(pct).join(' / '))));
  const table=el('table',{},el('thead',{},el('tr',{},['比較条件',...keys.map(k=>planningLabels[k]),'潜在干渉','受信候補GWなし'].map(x=>el('th',{},x)))),el('tbody',{},data.scenarios.filter(r=>r.fractions).map(r=>el('tr',{},el('td',{},r.label),...keys.map(k=>el('td',{},pct(r.fractions[k]))),el('td',{},r.intrinsic_interference===null?'N/A':pct(r.intrinsic_interference)),el('td',{},pct(r.coverage_gap))))));
  const tradeoffs=el('div',{class:'forecast-findings'},el('h3',{},'トレードオフの兆候'));
  if(!data.tradeoffs.length)tradeoffs.append(el('p',{},'今回の比較では、3損失のうち一方が0.5ポイント以上改善し、他方が0.5ポイント以上悪化する条件は見つかりませんでした。エリアの広さ・候補TP・送信間隔などを変えて再確認してください。'));
  for(const t of data.tradeoffs)tradeoffs.append(el('p',{},el('b',{},t.scenario+'：'),Object.entries(t.delta_pp).map(([k,v])=>`${planningLabels[k]} ${v>=0?'+':''}${v.toFixed(1)} pt`).join(' / ')));
  this.result.replaceChildren(metrics,
   el('p',{class:'hint'},`GW送信・復調器制限を除いた潜在干渉：${base.intrinsic_interference===null?'N/A':pct(base.intrinsic_interference)}（受信候補があるEDに条件付け）／受信候補GWなし：${pct(base.coverage_gap)}。`),
   el('p',{class:'hint'},'損失原因は競合します。GW送信中が減った分、同じ電波条件でも干渉・感度未満に分類される割合が増える場合があります。'),
   el('h3',{},'ED等重みの推定割合 · 各行100%'),legend,chart,tradeoffs);
  if(base.success_bounds[1]-base.success_bounds[0]>.001)this.result.append(el('p',{class:'hint'},`GW受信相関が不明な場合の成功率範囲：${base.success_bounds.map(pct).join('〜')}。表示値は独立近似で、この範囲はモデル誤差の信頼区間ではありません。`));
  this.result.append(el('details',{},el('summary',{},'数値表・前提・注意点'),el('div',{class:'forecast-table'},table),...data.notes.map(t=>el('p',{class:'hint'},t)),...data.warnings.map(t=>el('article',{class:'diagnostic warning'},t)),el('p',{class:'hint'},base.sampling_error_note)),
   el('button',{class:'small',onclick:()=>planningDownload(data,'tradeoff-forecast.json')},'予測JSONを保存'));
 }
}

class ConsultantPanel{
 constructor({root,api,el,getProfile,getForecastOptions}){
  Object.assign(this,{root,api,el,getProfile,getForecastOptions});this.serial=0;
  this.source=el('select',{'aria-label':'AI相談の対象'},el('option',{value:''},'現在編集中のプロファイル'));
  this.include=el('input',{type:'checkbox'});this.error=el('div',{role:'alert'});
  this.preview=el('pre',{class:'consultant-context',tabindex:0});
  this.prepare=el('button',{type:'button',class:'small',onclick:()=>this.prepareContext()},'送信データを確認する');
  this.source.onchange=this.include.onchange=()=>this.invalidate();const chat=el('div');
  root.append(el('p',{class:'calculator-caveat'},'プロファイルや保存済みRunのアブストラクトをAIに相談します。送信ボタンを押したときだけ接続先へ送ります。生パケット記録は添付しません。'),
   el('label',{},'相談対象',this.source),el('label',{class:'check'},this.include,'近似予測を添付する'),el('button',{type:'button',class:'quiet small',onclick:()=>{this.loaded=false;this.source.replaceChildren(el('option',{value:''},'現在編集中のプロファイル'));this.invalidate();this.setVisible(true);}},'相談対象のRun一覧を更新'),this.prepare,this.error,
   el('details',{},el('summary',{},'送信するデータ（通知URL・認証情報は除外）'),this.preview),chat);
  this.chat=new AIConversation({root:chat,api,el,defaultQuestion:'干渉・GW送信中・感度未満のトレードオフを比較できる実験条件にしたいです。現在の条件の問題点と、優先して試す設定変更を提案してください。'});
 }
 async setVisible(v){this.visible=v;if(v){this.chat.refreshConfig();if(!this.loaded){try{const jobs=await this.api('/api/jobs');this.loaded=true;for(const j of jobs)this.source.append(this.el('option',{value:j.id},`${j.name} · ${j.id.slice(0,8)} · ${j.status}`));}catch(e){planningErrors(this.error,e,this.el);}}}}
 invalidate(){++this.serial;this.chat.invalidate();this.preview.textContent='設定または対象が変更されました。「送信データを確認する」を押してください。';}
 async prepareContext(){const serial=++this.serial;this.prepare.disabled=true;this.chat.invalidate();this.error.replaceChildren();
  try{const d=await this.api('/api/consultant/preview','POST',{yaml:JSON.stringify(this.getProfile()),job_id:this.source.value||null,forecast_options:this.include.checked?this.getForecastOptions():null});
   if(serial!==this.serial)return;this.preview.textContent=JSON.stringify(d.context,null,2);this.chat.setContext(d.context,{config:d.connection,source:this.source.value?'保存Run':'編集中の設定'});
  }catch(e){if(serial===this.serial)planningErrors(this.error,e,this.el);}finally{this.prepare.disabled=false;}}
}
