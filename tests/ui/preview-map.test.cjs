const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('lorasim/static/preview-map.js','utf8');
const PreviewMap=vm.runInNewContext(source+'\nPreviewMap');

for(const [width,height,area] of [[1200,300,[2000,2000]],[320,240,[4000,1000]],[800,450,[1000,5000]]]){
 test(`equal metric scale within ${width} x ${height}, area ${area}`,()=>{
  const g=PreviewMap.project(width,height,area);
  assert.ok(g.width<=width+1e-9&&g.height<=height+1e-9);
  assert.ok(Math.abs((g.x(100)-g.x(0))-(g.y(0)-g.y(100)))<1e-9);
  assert.ok(Math.abs((g.x(area[0])-g.x(0))/(g.y(0)-g.y(area[1]))-area[0]/area[1])<1e-9);
 });
}

for(const ratio of [1,2,3])test(`drawing and ED picking agree at pixel ratio ${ratio}`,()=>{
 const arcs=[],transforms=[];
 const context=new Proxy({arc:(...args)=>arcs.push(args),setTransform:(...args)=>transforms.push(args)}, {get:(o,k)=>o[k]||(()=>{})});
 const canvas={style:{},getContext:()=>context,getBoundingClientRect(){return {left:25,top:75,width:parseFloat(this.style.width),height:parseFloat(this.style.height)};}};
 let bounds={width:1200,height:300};
 const view=new PreviewMap(canvas,{getBoundingClientRect:()=>bounds});
 const devices=[{id:0,xyz:[0,0,0]},{id:1,xyz:[1000,1000,0],path:[{time_s:10,xyz:[500,200,0]}]}];
 const data={area_m:[2000,2000],devices,gateways:[]};
 for(const size of [[1200,300],[320,260]]){
  bounds={width:size[0],height:size[1]};arcs.length=0;
  view.draw(data,{at:10,pixelRatio:ratio});
  assert.equal(canvas.width,Math.round(parseFloat(canvas.style.width)*ratio));
  assert.equal(canvas.height,Math.round(parseFloat(canvas.style.height)*ratio));
  assert.equal(arcs.length,2);
  assert.equal(view.pick(25+arcs[1][0],75+arcs[1][1],devices).id,1);
  assert.equal(view.pick(-100,-100,devices),null);
  assert.equal(arcs[1][2],3.5); // CSS radius stays constant on resize/Retina.
 }
 assert.equal(transforms.length,2);
});
