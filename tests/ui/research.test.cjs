const {test}=require('node:test');
const assert=require('node:assert/strict');
const Charts=require('../../lorasim/static/analysis-charts.js');
const Profile=require('../../lorasim/static/profile-summary.js');
const fs=require('node:fs');

test('continuous cumulative area, grids and per-endpoint hover are present',()=>{
 const svg=Charts.stackedArea({title:'収束',unit:'t',labels:[100,200,500],items:[{Received:1},{Received:.7,Interference:.3},{Received:.8,Interference:.2}]});
 assert.equal((svg.match(/<polygon/g)||[]).length,2);
 assert.ok(svg.includes('1〜500回の累積')&&svg.includes('80%'));
 assert.equal((svg.match(/fill="transparent"/g)||[]).length,3);
 assert.ok(!/NaN|Infinity/.test(svg));
});
test('lines show gaps, ranges, sample counts and safely escaped labels',()=>{
 const svg=Charts.lines({title:'<script>',x_label:'ED数',y_label:'PRR',y_max:1,series:[{label:'a < b',points:[{x:5,y:.8,n:2,low:.7,high:.9},{x:10,y:null},{x:20,y:.6,n:1}]}]});
 assert.ok(!svg.includes('<script>')&&svg.includes('&lt;script&gt;')&&svg.includes('標本数: 2'));
 assert.ok(svg.includes('Run範囲: 0.7 – 0.9')&&!/NaN|Infinity/.test(svg));
 assert.ok(svg.includes('class="point-label"'));
 const empty=Charts.lines({title:'empty',series:[{label:'none',points:[]}]});assert.ok(empty.includes('対象データがありません'));
});
test('spatial map reports counts and uses equal distance scales',()=>{
 const svg=Charts.heatmap({title:'SF',grid_size:2,area_m:[2000,1000],mode:'sf',cells:Array.from({length:4},()=>({value:7,n:4,counts:[4,0,0,0,0,0]})),gateways:[]});
 assert.ok(svg.includes('width="650" height="325"')&&svg.includes('SF7: 4 (100%)'));
});
test('profile name uses effective assignments, Japan clock and explicit unique suffix',()=>{
 const p={groups:[{name:'g',count:500,policy:{kind:'fixed'},mobility:{kind:'stationary'}}],assignments:[{groups:['g'],policy:{kind:'sema'}},{ids:[999],mobility:{kind:'ignored'}}]};
 assert.equal(Profile.name(p,x=>x,new Date('2026-10-20T01:34:00Z'),'abcd1234'),'500-sema-stationary-10201034-abcd1234');
 p.assignments.push({ids:[3],mobility:{kind:'waypoints'}});
 assert.match(Profile.name(p,x=>x,new Date('2026-10-20T01:34:00Z'),'abcd1234'),/500-sema-mixed-2/);
});
test('summary flatten preserves nested values, booleans, null and leaf labels',()=>{
 assert.deepEqual(Profile.flatten({a:4,b:[1,2],c:null,d:{e:false}},{properties:{a:{title:'駆動電圧'}}}),[
  ['駆動電圧','a',4],['b','b','[1,2]'],['c','c','未指定（継承・既定値）'],['e','d.e','無効']]);
});
test('three preflight panels and dedicated chart assets are wired',()=>{
 const html=fs.readFileSync('lorasim/static/index.html','utf8'),app=fs.readFileSync('lorasim/static/app.js','utf8');
 for(const tab of ['layout','calculator','abstract'])assert.ok(html.includes(`id="${tab}Tab"`)&&html.includes(`id="${tab}Tool"`));
 assert.ok(app.includes("$('layoutTool').hidden=tab!=='layout'"));
 assert.ok(html.indexOf('/static/analysis-charts.js')<html.indexOf('/static/app.js'));
});
test('SEMA survival separates overlapping algorithms into labelled rows',()=>{
 const svg=Charts.survival({title:'SEMA',x_label:'時刻',series:[{label:'静止 A / 移動 C',points:[{x:0,y:1,n:2},{x:10,y:.5,n:2}]},{label:'静止 B / 移動 A',points:[{x:0,y:1,n:2},{x:10,y:0,n:2}]}]});
 assert.ok(svg.includes('静止 A / 移動 C')&&svg.includes('残存割合: 50%')&&svg.includes('対象ED: 2'));
 assert.ok(!/NaN|Infinity/.test(svg));
});
test('generated profile names fit the stored-profile name limit for custom classes',()=>{
 const p={groups:[{count:500,policy:{kind:'custom:'+ 'P'.repeat(100)},mobility:{kind:'custom:'+ 'M'.repeat(100)}}]};
 assert.ok(Profile.name(p,x=>x,new Date('2026-10-20T01:34:00Z'),'abcd1234').length<=120);
});
