'use strict';
class ProfileSummary {
 static models(p,resolve){
  const found={policy:new Set(),mobility:new Set()};let offset=0;
  for(const group of p.groups||[]){
   const rules=(p.assignments||[]).filter(a=>(!a.groups?.length||a.groups.includes(group.name))&&(!a.tags?.length||a.tags.every(t=>(group.tags||[]).includes(t))));
   // ED-specific rules partition a group into special IDs and the common remainder.
   const special=[...new Set(rules.flatMap(a=>a.ids||[]))].filter(i=>i>=offset&&i<offset+group.count);
   const ids=special.slice();if(special.length<group.count){let id=offset;const taken=new Set(special);while(taken.has(id))id++;ids.push(id);}
   for(const id of ids)for(const category of ['policy','mobility']){let spec=group[category];for(const rule of rules)if((!rule.ids?.length||rule.ids.includes(id))&&rule[category])spec=rule[category];if(spec)found[category].add(resolve(spec,category).kind);}
   offset+=group.count;
  }
  return Object.fromEntries(Object.entries(found).map(([k,s])=>[k,[...s].sort()]));
 }
 static uniqueId(){return crypto.randomUUID?crypto.randomUUID().slice(0,8):Array.from(crypto.getRandomValues(new Uint8Array(4)),x=>x.toString(16).padStart(2,'0')).join('');}
 static name(p,resolve,date=new Date(),unique=ProfileSummary.uniqueId()){
  const kinds=this.models(p,resolve),safe=s=>s.replace(/[^\p{L}\p{N}_.-]/gu,'_');
  const short=a=>a.length===1?safe(a[0].split(':').pop()).slice(0,36):'mixed-'+a.length;
  const stamp=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Tokyo',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date),part=k=>stamp.find(x=>x.type===k).value;
  return `${(p.groups||[]).reduce((n,g)=>n+g.count,0)}-${short(kinds.policy)}-${short(kinds.mobility)}-${part('month')}${part('day')}${part('hour')}${part('minute')}-${unique}`;
 }
 static flatten(value,schema,path='',resolve=s=>s){
  const rows=[];
  function visit(v,s,p,label){
   s=resolve(s||{});if(s.anyOf)s={...resolve(s.anyOf.find(x=>x.type!=='null')||{}),...s};
   if(v===null||typeof v!=='object'){rows.push([label,p,v===null?'未指定（継承・既定値）':typeof v==='boolean'?(v?'有効':'無効'):v]);return;}
   if(!Object.keys(v).length){rows.push([label,p,Array.isArray(v)?'[]':'{}']);return;}
   if(Array.isArray(v)&&v.every(x=>x===null||typeof x!=='object')){rows.push([label,p,JSON.stringify(v)]);return;}
   for(const [k,x]of Object.entries(v)){const cs=resolve(s.properties?.[k]||s.items||{});visit(x,cs,p?p+'.'+k:k,cs.title||k);}
  }
  visit(value,schema,path,schema.title||path);return rows;
 }
}
if(typeof module!=='undefined')module.exports=ProfileSummary;
