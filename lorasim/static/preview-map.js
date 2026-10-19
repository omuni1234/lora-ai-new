'use strict';

// World coordinates and picking use one isotropic transform in CSS pixels.
class PreviewMap {
 constructor(canvas, viewport) {
  this.canvas=canvas;this.viewport=viewport;this.geometry=null;
 }
 static project(width,height,area) {
  const [a,b]=area,L=58,R=28,T=20,B=36;
  const scale=Math.min(Math.max(1,width-L-R)/a,Math.max(1,height-T-B)/b);
  return {width:L+a*scale+R,height:T+b*scale+B,scale,L,R,T,B,
   x:v=>L+v*scale,y:v=>T+(b-v)*scale};
 }
 draw(data,{at=0,paths=true,traffic=true,waypoints=true,pixelRatio=window.devicePixelRatio||1}={}) {
  const bounds=this.viewport.getBoundingClientRect();
  if(!data||bounds.width<=0||bounds.height<=0)return;
  const g=PreviewMap.project(bounds.width,bounds.height,data.area_m),c=this.canvas,ctx=c.getContext('2d');
  this.geometry=g;
  c.style.width=g.width+'px';c.style.height=g.height+'px';
  // Match the backing store to the CSS dimensions, including Retina displays.
  c.width=Math.max(1,Math.round(g.width*pixelRatio));c.height=Math.max(1,Math.round(g.height*pixelRatio));
  ctx.setTransform(c.width/g.width,0,0,c.height/g.height,0,0);
  const {width:w,height:h,x,y,L,R,T,B}=g,[a,b]=data.area_m;
  ctx.clearRect(0,0,w,h);ctx.fillStyle='#f5f9fc';ctx.fillRect(0,0,w,h);
  ctx.font='11px sans-serif';ctx.strokeStyle='#dce6ee';ctx.fillStyle='#647f94';
  for(let i=0;i<=4;i++){
   const xx=x(a*i/4),yy=y(b*i/4);
   ctx.beginPath();ctx.moveTo(xx,T);ctx.lineTo(xx,h-B);ctx.moveTo(L,yy);ctx.lineTo(w-R,yy);ctx.stroke();
   ctx.textAlign='center';ctx.fillText((a*i/4).toLocaleString('ja-JP',{maximumFractionDigits:1}),xx,h-B+17);
   ctx.textAlign='right';ctx.fillText((b*i/4).toLocaleString('ja-JP',{maximumFractionDigits:1}),L-8,yy+4);
  }
  ctx.textAlign='right';ctx.fillText('x [m]',w-2,h-2);ctx.textAlign='left';ctx.fillText('y [m]',5,12);
  if(waypoints){ctx.fillStyle='#a3abb7';for(const p of data.waypoints||[])ctx.fillRect(x(p[0])-1.5,y(p[1])-1.5,3,3);}
  for(const d of data.devices){
   const path=d.path||[];
   if(paths&&path.length){ctx.strokeStyle='#8da9c566';ctx.beginPath();path.forEach((p,i)=>i?ctx.lineTo(x(p.xyz[0]),y(p.xyz[1])):ctx.moveTo(x(p.xyz[0]),y(p.xyz[1])));ctx.stroke();}
   const pos=path.findLast(p=>p.time_s<=at)?.xyz||d.xyz;d.display_xyz=pos;
   ctx.fillStyle=d.warnings?.length?'#93a4b4':'#2b86ab';ctx.beginPath();ctx.arc(x(pos[0]),y(pos[1]),3.5,0,Math.PI*2);ctx.fill();
  }
  if(traffic)for(const r of data.transmissions||[]){ctx.fillStyle='#9875bd80';ctx.beginPath();ctx.arc(x(r.x_m),y(r.y_m),2,0,Math.PI*2);ctx.fill();}
  for(const gw of data.gateways){
   const xx=x(gw.position[0]),yy=y(gw.position[1]);ctx.fillStyle='#e4a04a25';ctx.beginPath();ctx.arc(xx,yy,15,0,Math.PI*2);ctx.fill();
   ctx.fillStyle='#d88e31';ctx.beginPath();ctx.moveTo(xx,yy-7);ctx.lineTo(xx+6,yy+5);ctx.lineTo(xx-6,yy+5);ctx.closePath();ctx.fill();
   ctx.fillStyle='#936327';ctx.fillText(gw.id,xx+10,yy+4);
  }
 }
 pick(clientX,clientY,devices) {
  if(!this.geometry)return null;
  const rect=this.canvas.getBoundingClientRect(),g=this.geometry;
  if(!rect.width||!rect.height)return null;
  const xx=(clientX-rect.left)*g.width/rect.width,yy=(clientY-rect.top)*g.height/rect.height;
  let nearest=null,distance=12**2;
  for(const d of devices){const p=d.display_xyz||d.xyz,delta=(g.x(p[0])-xx)**2+(g.y(p[1])-yy)**2;if(delta<distance){distance=delta;nearest=d;}}
  return nearest;
 }
}
