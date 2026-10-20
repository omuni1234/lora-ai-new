'use strict';
/** Declarative, dependency-free research charts. Data is text, never executable markup. */
class AnalysisCharts {
 static escape(x){return String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
 static number(x){return x===null||x===undefined?'N/A':typeof x==='number'?new Intl.NumberFormat('ja-JP',{maximumSignificantDigits:5}).format(x):String(x);}
 static color(key){const known={Received:'#15879a',Interference:'#cf8435',LostBecauseTx:'#5479bc',UnderSensitivity:'#cb5974',NoMoreReceivers:'#9568af',MixedLoss:'#6e6e7d',WrongFrequency:'#849daa',fixed:'#216eaa',sema:'#bd477b',epsilon_greedy:'#c18320',adr:'#278e69',badr:'#9555ac'};if(known[key])return known[key];let h=0;for(const ch of String(key))h=(h*31+ch.charCodeAt(0))>>>0;return `hsl(${h%360} 52% 42%)`;}
 static labels={Received:'受信成功',Interference:'干渉',LostBecauseTx:'GW送信中',UnderSensitivity:'感度未満',NoMoreReceivers:'復調器不足',MixedLoss:'複合損失',WrongFrequency:'周波数不一致'};
 static tip(text){return `tabindex="0" data-tip="${this.escape(text)}"><title>${this.escape(text)}</title>`;}
 static frame(v,maxX,maxY,minX=0){
  const W=850,H=340,L=68,R=22,T=24,B=64,pw=W-L-R,ph=H-T-B;
  const x=n=>L+pw*(n-minX)/(maxX-minX||1),y=n=>T+ph*(1-n/(maxY||1));
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${this.escape(v.title)}">`;
  for(let i=0;i<=5;i++){const yy=y(maxY*i/5),xx=L+pw*i/5;svg+=`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="#dbe5ed"/><text x="${L-9}" y="${yy+4}" text-anchor="end">${this.number(maxY*i/5)}</text><line x1="${xx}" x2="${xx}" y1="${T}" y2="${H-B}" stroke="#edf1f5"/><text x="${xx}" y="${H-B+21}" text-anchor="middle">${this.number(minX+(maxX-minX)*i/5)}</text>`;}
  svg+=`<text x="${L}" y="14">${this.escape(v.y_label||'割合')}</text><text x="${L+pw/2}" y="${H-10}" text-anchor="middle">${this.escape(v.x_label||v.unit||'')}</text>`;
  return {svg,x,y,L,R,T,B,pw,ph,W,H};
 }
 static lines(v){
  const pts=(v.series||[]).flatMap(s=>s.points).filter(p=>Number.isFinite(p.x)),xs=pts.map(p=>p.x),maxY=v.y_max??Math.max(1e-9,...pts.map(p=>p.high??p.y??0))*1.1;
  if(!pts.length)return '<p class="hint">対象データがありません。</p>';
  const f=this.frame(v,Math.max(...xs),maxY,Math.min(...xs));let svg=f.svg;
  for(const [si,s] of v.series.entries()){
   const col=this.color(s.color_key||s.label),dash=['','7 3','2 3','10 3 2 3'][si%4];let d='',previous=null;
   for(const p of s.points){if(p.y===null||p.y===undefined){previous=null;continue;}const xx=f.x(p.x),yy=f.y(p.y);d+=previous?(v.step?`H${xx}V${yy}`:`L${xx},${yy}`):`M${xx},${yy}`;previous=p;}
   svg+=`<path d="${d}" fill="none" stroke="${col}" stroke-width="2.4" stroke-dasharray="${dash}"/>`;
   for(const [i,p] of s.points.entries()){
    if(p.y===null||p.y===undefined)continue;const xx=f.x(p.x),yy=f.y(p.y);
    const tip=`${s.label}\n${v.x_label}: ${this.number(p.x)}\n${v.y_label}: ${this.number(p.y)}${p.n!==undefined?'\n標本数: '+p.n:''}${p.attempts!==undefined?'\nUL数: '+p.attempts:''}${p.low!==undefined?'\nRun範囲: '+this.number(p.low)+' – '+this.number(p.high):''}`;
    if(p.low!==undefined)svg+=`<path d="M${xx-4},${f.y(p.low)}h8M${xx},${f.y(p.low)}V${f.y(p.high)}M${xx-4},${f.y(p.high)}h8" stroke="${col}" fill="none"/>`;
    svg+=`<circle cx="${xx}" cy="${yy}" r="${s.points.length<25?4:2.7}" fill="${col}" ${this.tip(tip)}</circle>`;
    if(s.points.length<=25||i===s.points.length-1)svg+=`<text class="point-label" x="${xx+4}" y="${yy-7}">${this.number(p.y)}</text>`;
   }
  }
  return svg+'</svg>'+this.legend(v.series.map(s=>[s.label,this.color(s.color_key||s.label)]));
 }
 static legend(entries){return '<div class="chartlegend">'+entries.map(([name,c])=>`<span><i style="background:${c}"></i>${this.escape(name)}</span>`).join('')+'</div>';}
 static stackedArea(v){
  if(!v.items.length)return '<p class="hint">対象データがありません。</p>';
  const keys=[...new Set(v.items.flatMap(Object.keys))].sort((a,b)=>a==='Received'?-1:b==='Received'?1:a.localeCompare(b)),xs=v.labels.map(Number),f=this.frame({...v,x_label:v.unit,y_label:'累積割合'},Math.max(...xs),1,Math.min(...xs));
  let svg=f.svg,base=v.items.map(()=>0);
  for(const key of keys){const top=base.map((b,i)=>b+(v.items[i][key]||0));if(xs.length===1){svg+=`<rect x="${f.L}" y="${f.y(top[0])}" width="${f.pw}" height="${f.ph*(top[0]-base[0])}" fill="${this.color(key)}"/>`;}const coords=top.map((p,i)=>`${f.x(xs[i])},${f.y(p)}`).concat([...base].reverse().map((p,j)=>`${f.x(xs[base.length-1-j])},${f.y(p)}`));svg+=`<polygon points="${coords.join(' ')}" fill="${this.color(key)}" opacity=".88"/>`;base=top;}
  xs.forEach((xx,i)=>{const left=i? (f.x(xs[i-1])+f.x(xx))/2:f.L,right=i<xs.length-1?(f.x(xx)+f.x(xs[i+1]))/2:f.W-f.R;const tip=`1〜${v.labels[i]}回の累積\n`+keys.map(k=>`${this.labels[k]||k}: ${this.number((v.items[i][k]||0)*100)}%`).join('\n');svg+=`<rect x="${left}" y="${f.T}" width="${Math.max(1,right-left)}" height="${f.ph}" fill="transparent" ${this.tip(tip)}</rect>`;});
  if(xs.length===1)svg+=`<text x="${f.L+10}" y="${f.T+20}">表示点が1つです。ホバーで内訳を確認できます。</text>`;
  return svg+'</svg>'+this.legend(keys.map(k=>[this.labels[k]||k,this.color(k)]));
 }
 static heatmap(v){
  const W=740,H=440,L=65,T=22,maxW=650,maxH=350,scale=Math.min(maxW/v.area_m[0],maxH/v.area_m[1]),w=v.area_m[0]*scale,h=v.area_m[1]*scale,g=v.grid_size;
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${this.escape(v.title)}"><rect x="${L}" y="${T}" width="${w}" height="${h}" fill="#f0f3f6"/>`;
  v.cells.forEach((cell,i)=>{const ix=i%g,iy=Math.floor(i/g),col=cell.value===null?'#edf1f4':v.mode==='sf'?['#2c7bb6','#23a4a8','#78b971','#cbbb45','#dd8545','#b94b65'][cell.value-7]:`hsl(200 65% ${96-60*cell.value}%)`;const tip=`X: ${this.number(ix*v.area_m[0]/g)}〜${this.number((ix+1)*v.area_m[0]/g)} m\nY: ${this.number(iy*v.area_m[1]/g)}〜${this.number((iy+1)*v.area_m[1]/g)} m\n送信数: ${cell.n}\n`+cell.counts.map((n,s)=>`SF${s+7}: ${n} (${cell.n?this.number(n/cell.n*100):'—'}%)`).join('\n');svg+=`<rect x="${L+ix*w/g}" y="${T+h-(iy+1)*h/g}" width="${w/g}" height="${h/g}" fill="${col}" stroke="white" stroke-width=".5" ${this.tip(tip)}</rect>`;});
  for(let i=0;i<=4;i++){svg+=`<text x="${L+w*i/4}" y="${T+h+20}" text-anchor="middle">${this.number(v.area_m[0]*i/4)}</text><text x="${L-7}" y="${T+h*(1-i/4)+4}" text-anchor="end">${this.number(v.area_m[1]*i/4)}</text>`;}
  for(const gw of v.gateways||[]){const [x,y]=gw.position;if(x<0||y<0||x>v.area_m[0]||y>v.area_m[1])continue;svg+=`<circle cx="${L+x*scale}" cy="${T+h-y*scale}" r="5" stroke="white" fill="#142e48" ${this.tip('GW '+gw.id)}</circle>`;}
  svg+=`<text x="${L+w/2}" y="${T+h+42}" text-anchor="middle">X座標 [m]</text><text x="${L}" y="14">Y座標 [m]</text></svg>`;
  return svg+this.legend(v.mode==='sf'?[7,8,9,10,11,12].map((s,i)=>['SF'+s,['#2c7bb6','#23a4a8','#78b971','#cbbb45','#dd8545','#b94b65'][i]]):[[`SF${v.sf} 選択率: 白 0% → 青 100%`,'#176a9e']])+ '<p class="hint">灰色＝標本なし／最小送信数未満。縦横は同じ距離縮尺です。</p>';
 }
 static survival(v){
  const xs=v.series[0]?.points.map(p=>p.x)||[];if(!xs.length)return '<p class="hint">対象データがありません。</p>';
  const W=850,L=175,R=25,T=20,H=85+v.series.length*34,pw=W-L-R,min=Math.min(...xs),max=Math.max(...xs),x=n=>L+pw*(n-min)/(max-min||1);
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${this.escape(v.title)}">`;
  for(const [row,s]of v.series.entries()){
   const y=T+row*34;svg+=`<text x="${L-8}" y="${y+18}" text-anchor="end">${this.escape(s.label)}</text>`;
   for(const [i,p]of s.points.entries()){
    const left=xs.length===1?L:x(p.x),right=xs.length===1?W-R:i+1<xs.length?x(xs[i+1]):W-R;
    const col=p.y===null?'#e8ebee':`hsl(200 55% ${96-59*p.y}%)`,tip=`${s.label}\n${v.x_label}: ${this.number(p.x)}\n残存割合: ${p.y===null?'N/A':this.number(p.y*100)+'%'}\n対象ED: ${p.n??'N/A'}`;
    svg+=`<rect x="${left}" y="${y}" width="${Math.max(1,right-left)}" height="28" fill="${col}" ${this.tip(tip)}</rect>`;
   }
  }
  for(let i=0;i<=5;i++)svg+=`<text x="${L+pw*i/5}" y="${H-30}" text-anchor="middle">${this.number(min+(max-min)*i/5)}</text>`;
  return svg+`<text x="${L+pw/2}" y="${H-8}" text-anchor="middle">${this.escape(v.x_label)}</text></svg><p class="hint">白: 残存0% → 濃青: 残存100%。灰色: 対象EDなし。各行にホバーして割合とED数を確認できます。</p>`;
 }
 static bars(v){
  const W=850,L=330,R=100,H=Math.max(160,v.items.length*35+55),max=Math.max(1e-9,...v.items.map(p=>p.value??0));let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${this.escape(v.title)}">`;
  for(let i=0;i<=5;i++){const x=L+(W-L-R)*i/5;svg+=`<line x1="${x}" x2="${x}" y1="10" y2="${H-35}" stroke="#dbe5ed"/><text x="${x}" y="${H-20}" text-anchor="middle">${this.number(max*i/5)}</text>`;}
  v.items.forEach((p,i)=>{const y=15+i*35,bw=(W-L-R)*(p.value??0)/max,tip=`${p.label}\n${this.number(p.value)} ${v.unit||''}`;svg+=`<g ${this.tip(tip)}<text x="0" y="${y+14}">${this.escape(p.label.length>47?p.label.slice(0,44)+'…':p.label)}</text><rect x="${L}" y="${y}" width="${bw}" height="20" fill="#26849f"/><text x="${L+bw+6}" y="${y+14}">${this.number(p.value)}</text></g>`;});
  return svg+`<text x="${L}" y="${H-3}">${this.escape(v.unit||'')}</text></svg>`;
 }
 static table(root,v){
  const wrap=document.createElement('div');wrap.className='tablewrap';const table=document.createElement('table');const head=document.createElement('thead'),tr=document.createElement('tr');
  for(const col of v.columns){const th=document.createElement('th');th.textContent=col;tr.append(th);}head.append(tr);table.append(head);const body=document.createElement('tbody');table.append(body);wrap.append(table);root.append(wrap);
  let page=0;const controls=document.createElement('div');controls.className='actions table-pagination';root.append(controls);
  const draw=()=>{body.replaceChildren();for(const row of v.rows.slice(page*100,page*100+100)){const tr=document.createElement('tr');for(const x of row){const td=document.createElement('td');td.textContent=this.number(x);td.title=x===null?'未定義または未計測':String(x);tr.append(td);}body.append(tr);}controls.replaceChildren();for(const [label,delta,disabled] of [['前へ',-1,page===0],['次へ',1,(page+1)*100>=v.rows.length]]){const b=document.createElement('button');b.textContent=label;b.disabled=disabled;b.onclick=()=>{page+=delta;draw();};controls.append(b);}const note=document.createElement('span');note.textContent=`${v.rows.length? page*100+1:0}〜${Math.min(v.rows.length,page*100+100)} / ${v.rows.length} 行`;controls.append(note);};draw();
  const csv=document.createElement('button');csv.textContent='この表をCSV出力';csv.onclick=()=>{const quote=x=>'"'+String(x??'').replace(/"/g,'""')+'"';const text='\ufeff'+[v.columns,...v.rows].map(r=>r.map(quote).join(',')).join('\n');const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=v.title.replace(/[\\/:*?"<>|]/g,'_')+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};root.append(csv);
 }
 static render(root,v){
  if(v.type==='table'){this.table(root,v);return;}
  const fn={lines:'lines',stacked_area:'stackedArea',heatmap:'heatmap',survival:'survival',bars:'bars'}[v.type];if(!fn)return false;
  root.innerHTML=this[fn](v);root.classList.add('research-chart');
  if(v.type==='lines'){const label=document.createElement('label');label.className='check';const toggle=document.createElement('input');toggle.type='checkbox';toggle.onchange=()=>root.classList.toggle('show-point-labels',toggle.checked);label.append(toggle,document.createTextNode('数値ラベルを表示'));root.append(label);}
  this.tooltips(root);return true;
 }
 static tooltips(root){
  const tip=document.createElement('div');tip.className='analysis-tooltip';tip.hidden=true;root.append(tip);root.style.position='relative';
  const show=(e)=>{const target=e.target.closest('[data-tip]');if(!target){tip.hidden=true;return;}tip.textContent=target.dataset.tip;tip.hidden=false;const r=root.getBoundingClientRect(),p=target.getBoundingClientRect();tip.style.left=Math.max(0,Math.min(root.clientWidth-tip.offsetWidth,(e.clientX??p.x)-r.x+12))+'px';tip.style.top=Math.max(0,(e.clientY??p.y)-r.y-tip.offsetHeight-10)+'px';};
  root.addEventListener('pointermove',show);root.addEventListener('focusin',show);for(const evt of ['pointerleave','focusout'])root.addEventListener(evt,()=>tip.hidden=true);
 }
}
if(typeof module!=='undefined')module.exports=AnalysisCharts;
