'use strict';

// A framing parser independent of TCP chunks and Japanese UTF-8 byte boundaries.
class AIEventParser{
 constructor(onEvent){this.buffer='';this.onEvent=onEvent;}
 push(text){this.buffer+=text;if(this.buffer.length>2100000)throw Error('AIストリームが大きすぎます。');let match;while((match=/\r?\n\r?\n/.exec(this.buffer))){const block=this.buffer.slice(0,match.index);this.buffer=this.buffer.slice(match.index+match[0].length);const data=block.split(/\r?\n/).filter(x=>x.startsWith('data:')).map(x=>x.slice(5).replace(/^ /,'')).join('\n');if(data)this.onEvent(JSON.parse(data));}}
 finish(){if(this.buffer.trim())throw Error('AIの応答が途中で途切れました。');}
}
async function aiStream(body,onEvent,signal){
 const token=sessionStorage.getItem('lorasim-token')||'';
 let response;
 try{response=await fetch('/api/consultant/stream',{method:'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},body:JSON.stringify(body),signal});}
 catch(e){if(e.name==='AbortError')throw e;throw Error('APIサーバーに接続できません。ネットワークを確認してください。');}
 if(!response.ok){const d=await response.json().catch(()=>null);const e=Error(d?.detail||`AI相談に失敗しました（HTTP ${response.status}）。`);e.diagnostics=d?.diagnostics;throw e;}
 if(!response.headers.get('content-type')?.includes('text/event-stream'))throw Error('APIがストリームを返しませんでした。APIと画面のバージョンを確認してください。');
 let terminal=false,failed=null;const parser=new AIEventParser(e=>{if(e.type==='done')terminal=true;if(e.type==='error'){terminal=true;failed=Error(e.message);}onEvent(e);});
 const reader=response.body.getReader(),decoder=new TextDecoder();
 try{for(;;){const{done,value}=await reader.read();if(done)break;parser.push(decoder.decode(value,{stream:true}));}parser.push(decoder.decode());parser.finish();}
 finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
 if(failed)throw failed;
 if(!terminal)throw Error('AIの回答が完了前に途切れました。表示中の回答は途中です。ネットワークやプロキシの設定を確認してください。');
}

class AISettingsDialog{
 constructor({api,el}){
  Object.assign(this,{api,el});const field=(label,input,hint)=>el('label',{},label,input,el('small',{class:'hint'},hint));
  this.url=el('input',{type:'url',required:true,'aria-label':'OpenAI接続先URL',placeholder:'https://api.openai.com/v1'});
  this.model=el('input',{required:true,maxlength:200,'aria-label':'OpenAIモデル名',placeholder:'利用可能なモデルIDを入力'});
  this.type=el('select',{'aria-label':'OpenAI API形式'},el('option',{value:'responses'},'Responses API'),el('option',{value:'chat_completions'},'Chat Completions API'));
  this.key=el('input',{type:'password',autocomplete:'new-password',maxlength:4096,'aria-label':'OpenAI APIキー',placeholder:'未変更なら空欄'});
  this.clearKey=el('input',{type:'checkbox'});
  this.tokens=el('input',{type:'number',min:1,max:128000,required:true,'aria-label':'AI出力トークン上限'});
  this.timeout=el('input',{type:'number',min:10,max:600,required:true,'aria-label':'AI待機時間'});
  this.status=el('p',{class:'hint',role:'status'});this.error=el('div',{role:'alert'});
  this.save=el('button',{type:'submit',class:'primary'},'AI設定を保存');
  this.reset=el('button',{type:'button',onclick:()=>this.restore()},'環境変数の設定に戻す');
  this.form=el('form',{},el('div',{class:'sectiontitle'},el('h2',{},'OpenAI API接続設定'),el('button',{type:'button',onclick:()=>this.dialog.close(),'aria-label':'AI設定を閉じる'},'閉じる')),this.status,
   el('div',{class:'ai-settings-grid'},field('接続先エンドポイント',this.url,'ベースURLまたは /responses・/chat/completions までのURL。API形式は下で指定します。'),field('API形式',this.type,'OpenAIへの新規設定ではResponses APIを選べます。既存の環境変数設定は維持します。'),field('モデル名',this.model,'APIプロジェクトで利用できるモデルID。ChatGPTの表示名とは異なる場合があります。'),field('API Key',this.key,'サーバーに保存します。空欄は現在のキーを維持し、保存後に値は表示しません。'),field('出力トークン上限',this.tokens,'推論用トークンも含む上限。回答が空になる・途中で切れる場合に調整します。'),field('待機時間 [秒]',this.timeout,'推論・回答生成を含む全体の待機上限。10〜600秒。')),
   el('label',{class:'check'},this.clearKey,'保存済みキーを削除（環境変数のキーも使用しない）'),
   el('p',{class:'hint'},'保存だけでは外部APIに送信しません。以後の相談から適用され、APIの再起動は不要です。サーバーのデータ領域へ保存するため、Docker再作成後も維持されます。'),this.error,el('div',{class:'actions'},this.save,this.reset));
  this.dialog=el('dialog',{class:'ai-settings-dialog'},this.form);document.body.append(this.dialog);
  this.form.onsubmit=e=>{e.preventDefault();this.store();};this.dialog.addEventListener('close',()=>{this.key.value='';});
 }
 fill(c){this.config=c;this.url.value=c.base_url||'https://api.openai.com/v1';this.model.value=c.model||'';this.type.value=c.source==='environment'&&!c.model?'responses':c.api_type||'responses';this.tokens.value=c.model?(c.max_tokens||8000):8000;this.timeout.value=c.timeout_s||180;this.key.value='';this.clearKey.checked=false;this.status.textContent=`設定元：${c.source==='gui'?'GUI保存値':'環境変数'} ／ キー：${c.key_configured?'設定済み（非表示）':'未設定'}。${c.hint||''}`;}
 async open(){this.error.replaceChildren();this.dialog.showModal();this.save.disabled=true;try{this.fill(await this.api('/api/consultant/config'));}catch(e){planningErrors(this.error,e,this.el);}finally{this.save.disabled=false;}}
 async store(){if(!this.form.reportValidity())return;this.save.disabled=true;this.reset.disabled=true;this.error.replaceChildren();try{const c=await this.api('/api/consultant/config','PUT',{base_url:this.url.value,model:this.model.value.trim(),api_type:this.type.value,api_key:this.key.value||null,clear_key:this.clearKey.checked,max_tokens:Number(this.tokens.value),timeout_s:Number(this.timeout.value)});this.fill(c);this.status.textContent='保存しました。'+this.status.textContent;window.dispatchEvent(new CustomEvent('ai-config-changed',{detail:c}));}catch(e){planningErrors(this.error,e,this.el);}finally{this.key.value='';this.save.disabled=false;this.reset.disabled=false;}}
 async restore(){this.save.disabled=this.reset.disabled=true;this.error.replaceChildren();try{const c=await this.api('/api/consultant/config','DELETE');this.fill(c);window.dispatchEvent(new CustomEvent('ai-config-changed',{detail:c}));}catch(e){planningErrors(this.error,e,this.el);}finally{this.save.disabled=this.reset.disabled=false;}}
}

class AIConversation{
 constructor({root,api,el,defaultQuestion=''}){
  Object.assign(this,{root,api,el});this.history=[];this.serial=0;this.context=null;this.busy=false;
  this.status=el('p',{class:'hint',role:'status'},'相談データを用意してください。');this.error=el('div',{role:'alert'});
  this.log=el('div',{class:'ai-conversation',role:'log','aria-label':'AIとの会話','aria-live':'polite'});
  this.question=el('textarea',{rows:3,maxlength:6000,'aria-label':'AIへの質問'},defaultQuestion);
  this.send=el('button',{type:'button',class:'primary',onclick:()=>this.ask()},'AIに相談する');this.send.disabled=true;
  this.stop=el('button',{type:'button',onclick:()=>this.controller?.abort()},'回答を停止');this.stop.hidden=true;
  this.clear=el('button',{type:'button',onclick:()=>this.newChat()},'新しい会話');
  this.export=el('button',{type:'button',onclick:()=>planningDownload({source:this.exportSource,history:this.transcript||[],context:this.exportContext},'ai-consultation.json')},'会話を保存');
  root.append(this.status,this.error,this.log,el('label',{},'質問・続けて質問',this.question),el('div',{class:'actions'},this.send,this.stop,this.clear,this.export,el('button',{type:'button',onclick:()=>window.aiSettings?.open()},'OpenAI接続設定')));
  window.addEventListener('ai-config-changed',e=>{this.config=e.detail;this.update();});
 }
 update(){this.send.disabled=this.busy||!this.context||!this.config?.configured;this.question.disabled=this.busy;this.clear.disabled=this.busy;this.export.disabled=this.busy;this.stop.hidden=!this.busy;
  if(!this.busy)this.status.textContent=!this.context?'相談データを用意してください。':!this.config?.configured?'AI接続設定が必要です。'+(this.config?.hint||''):`送信先：${this.config.base_url} ／ ${this.config.model} ／ ${this.config.api_type} · ${this.source||''}`;
 }
 async refreshConfig(){try{this.config=await this.api('/api/consultant/config');this.update();}catch(e){planningErrors(this.error,e,this.el);}}
 invalidate(message='対象・分析条件が変わりました。データを更新してから相談してください。'){
  ++this.serial;this.controller?.abort();this.context=null;this.analysisId=null;this.history=[];this.update();this.status.textContent=message;
 }
 setContext(context,{config,analysisId=null,source=''}){this.invalidate();this.context=context;this.config=config;this.analysisId=analysisId;this.source=source;this.newChat();}
 newChat(){this.history=[];this.transcript=[];this.exportContext=this.context;this.exportSource=this.source;this.log.replaceChildren();this.error.replaceChildren();this.update();}
 async ask(){if(this.busy||!this.context||!this.question.value.trim())return;
  const serial=this.serial,question=this.question.value.trim(),el=this.el;this.busy=true;this.controller=new AbortController();this.error.replaceChildren();this.update();
  const request=el('article',{class:'ai-message user'},el('strong',{},'あなた'),el('p',{},question)),text=el('div',{class:'ai-answer-text'}),note=el('p',{class:'hint'}),reply=el('article',{class:'ai-message assistant'},el('strong',{},'AIコンサルタント'),text,note);
  this.log.append(request,reply);this.log.scrollTop=this.log.scrollHeight;reply.scrollIntoView({block:'nearest'});this.status.textContent='接続・生成を待っています…';let answer='',finished=null;
  try{
   await aiStream({...(!this.analysisId?{context:this.context}:{analysis_id:this.analysisId}),question,history:this.history},e=>{
    if(serial!==this.serial)return;
    if(e.type==='start')this.status.textContent=`${e.model} が考察中です…`;
    if(e.type==='delta'){answer+=e.text;const follow=this.log.scrollHeight-this.log.scrollTop-this.log.clientHeight<100;text.textContent=answer;if(follow)this.log.scrollTop=this.log.scrollHeight;this.status.textContent='回答を受信中…';}
    if(e.type==='done'){finished=e;note.textContent=[`モデル：${e.model}`,e.warning,e.usage?`使用トークン：${e.usage.total_tokens??'—'}`:''].filter(Boolean).join(' ／ ');}
   },this.controller.signal);
   if(serial!==this.serial)return;
   this.history.push({role:'user',content:question},{role:'assistant',content:answer});this.question.value='';
   this.transcript.push({question,answer,status:finished?.warning?'incomplete':'completed',model:finished?.model,usage:finished?.usage});
  }catch(e){
   note.textContent=e.name==='AbortError'?'停止しました。この回答は途中です。':(answer?'受信が中断しました。この回答は途中です。':'回答を取得できませんでした。');
   if(serial===this.serial){if(e.name!=='AbortError')planningErrors(this.error,e,el);this.transcript.push({question,answer,status:'interrupted'});}
  }finally{this.busy=false;this.controller=null;this.update();}
 }
}

class AnalysisConsultant{
 constructor({root,api,el}){
  Object.assign(this,{root,api,el});this.serial=0;this.snapshot=null;
  this.summary=el('p',{class:'hint'},'実験を分析すると、その集計結果を相談できます。');this.preview=el('pre',{class:'consultant-context',tabindex:0});
  this.prepare=el('button',{type:'button',onclick:()=>this.load()},'分析結果を相談データにする');this.prepare.disabled=true;
  this.error=el('div',{role:'alert'});const chat=el('div');
  root.append(el('div',{class:'sectiontitle'},el('div',{},el('span',{class:'eyebrow'},'AI RESEARCH CONSULTANT'),el('h2',{},'分析結果をAIと考察')),el('button',{type:'button',onclick:()=>window.aiSettings?.open()},'OpenAI接続設定')),
   el('p',{class:'hint'},'表示中の分析結果・集計範囲・保存プロファイルを根拠に考察します。相談ボタンを押したときだけ外部APIへ送信します。実験・設定は自動変更しません。'),this.summary,this.prepare,this.error,
   el('details',{},el('summary',{},'AIに送る集計データを確認'),this.preview),chat);
  this.chat=new AIConversation({root:chat,api,el,defaultQuestion:'各アルゴリズムのPRR、干渉・GW送信中・感度未満のトレードオフと収束を考察してください。実験条件の違い、結論を支持する数値、不確実な点、次に検証すべき実験を示してください。'});
 }
 setAnalysis(data){++this.serial;this.chat.invalidate();this.snapshot=data?.consultant_snapshot;this.prepare.disabled=!this.snapshot?.id;this.preview.textContent='';this.error.replaceChildren();
  this.summary.textContent=this.snapshot?.id?`${data.runs.length} Run・${data.modules.length} モジュールの集計結果。長い配列の標本化：${this.snapshot.sampled_arrays}箇所。データを確認して相談してください。`:this.snapshot?.error||'先に実験を分析してください。';
 }
 invalidate(){++this.serial;this.prepare.disabled=true;this.snapshot=null;this.chat.invalidate();this.summary.textContent='分析条件が変更されました。「選択した実験を分析」で更新してください。';this.preview.textContent='';}
 async load(){if(!this.snapshot?.id)return;const serial=++this.serial;this.prepare.disabled=true;this.error.replaceChildren();try{const d=await this.api('/api/consultant/analysis/'+this.snapshot.id);if(serial!==this.serial)return;this.preview.textContent=JSON.stringify(d.context,null,2);this.chat.setContext(d.context,{config:d.connection,analysisId:this.snapshot.id,source:`分析 ${this.snapshot.id.slice(0,8)} · ${d.context.runs.length} Run`});this.summary.textContent='相談データを準備しました。質問を入力して「AIに相談する」を押してください。';}catch(e){planningErrors(this.error,e,this.el);}finally{if(serial===this.serial)this.prepare.disabled=false;}}
}
if(typeof module!=='undefined')module.exports={AIEventParser};
