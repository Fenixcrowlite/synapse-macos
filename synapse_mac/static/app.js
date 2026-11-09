const $=id=>document.getElementById(id);
let settings={}, secret='';
const toast=s=>{const n=$('toast');n.textContent=s;n.style.display='block';setTimeout(()=>n.style.display='none',3500)};
async function req(path,method='GET',data){const o={method,credentials:'same-origin',headers:{}};if(data!==undefined){o.headers['Content-Type']='application/json';o.body=JSON.stringify(data)}const r=await fetch(path,o);const x=await r.json();if(!r.ok)throw new Error(x.error||`HTTP ${r.status}`);return x}
async function refresh(){const x=await req('/api/status');settings=x.settings;$('loginPanel').hidden=true;$('dashboard').hidden=false;$('logout').hidden=false;$('indicator').textContent=x.stopped?'STOP active':'Online · local';$('state').textContent=x.stopped?'STOP active':'Running';$('mode').textContent=settings.control_enabled?'Control enabled':'Read only';$('toolCount').textContent=x.tools.length;$('stopStatus').textContent=x.stopped?'Emergency STOP: ACTIVE':'Emergency STOP: inactive';$('control').checked=settings.control_enabled;$('shell').checked=settings.shell_enabled;$('gui').checked=settings.gui_enabled;$('roots').value=settings.allowed_roots.join('\n');$('timeout').value=settings.max_command_seconds;$('chatModel').value=settings.llm_model||'llama3.2:3b';$('endpoint').value=location.protocol==='https:'?location.origin+'/mcp':x.mcp_url;$('toolList').replaceChildren(...x.tools.map(t=>{const z=document.createElement('span');z.textContent=t.name;return z}))}
async function guarded(fn){try{await fn()}catch(e){toast('Error: '+e.message)}}
$('loginBtn').onclick=()=>guarded(async()=>{await req('/api/login','POST',{token:$('loginToken').value});$('loginToken').value='';await refresh()});
$('loginToken').addEventListener('keydown',e=>{if(e.key==='Enter')$('loginBtn').click()});
$('refresh').onclick=()=>guarded(refresh);
$('logout').onclick=()=>guarded(async()=>{await req('/api/logout','POST',{});location.reload()});
$('save').onclick=()=>guarded(async()=>{const edit={control_enabled:$('control').checked,shell_enabled:$('shell').checked,gui_enabled:$('gui').checked,allowed_roots:$('roots').value.split('\n').map(s=>s.trim()).filter(Boolean),max_command_seconds:Number($('timeout').value)};if((edit.control_enabled||edit.shell_enabled||edit.gui_enabled)&&!window.confirm('Enable computer control capabilities for all clients possessing the MCP token?'))return;await req('/api/config','POST',edit);toast('Settings saved');await refresh()});
$('emergency').onclick=()=>guarded(async()=>{await req('/api/stop','POST',{});toast('Emergency STOP is active');await refresh()});
$('resume').onclick=()=>guarded(async()=>{if(!confirm('Clear the emergency STOP flag? Other control settings remain as configured.'))return;await req('/api/resume','POST',{});await refresh()});
$('revealToken').onclick=()=>guarded(async()=>{secret=(await req('/api/mcp-token')).token;$('mcpToken').type='text';$('mcpToken').value=secret;toast('Token revealed; keep it private')});
$('rotateToken').onclick=()=>guarded(async()=>{if(!confirm('Rotate token? All existing MCP clients will lose access.'))return;secret=(await req('/api/rotate-mcp','POST',{})).token;$('mcpToken').type='text';$('mcpToken').value=secret;toast('MCP token rotated')});
$('copySnippet').onclick=()=>guarded(async()=>{if(!secret)secret=(await req('/api/mcp-token')).token;const snippet=JSON.stringify({mcpServers:{'synapse-mac':{type:'streamable-http',url:$('endpoint').value,headers:{Authorization:'Bearer '+secret}}}},null,2);await navigator.clipboard.writeText(snippet);toast('Client configuration copied; treat as a secret')});
document.querySelectorAll('.diagnostic').forEach(el=>el.onclick=()=>guarded(async()=>{const x=await req('/api/test-tool','POST',{name:el.dataset.name,arguments:{}});$('result').textContent=JSON.stringify(x.result,null,2)}));
refresh().catch(()=>{});
async function channelRefresh(){
 const x=await req('/api/channels');
 $('remoteMode').textContent=x.remote_access_enabled ? 'Remote access enabled' : 'Remote access disabled';
 const count=['main','backup'].filter(n=>x[n].running).length;
 $('remoteNote').textContent=x.remote_access_enabled ? (count===2?'Both HTTPS channels active':`${count}/2 HTTPS channels active. Check credentials or tunnel configuration.`) : 'Network access blocked by the local access switch.';
 for(const n of ['main','backup']){
  const c=x[n], status=$(`${n}Status`), error=$(`${n}Error`),link=$(`${n}Url`),input=$(`${n}UrlInput`);
  status.textContent=c.running?'● Online':'○ Offline';
  status.style.color=c.running?'#86efac':'#fbbf24';
  error.textContent=c.installed?(c.last_error||'Installed'):('CLI missing. '+c.setup);
  link.textContent=c.url?c.url:'No registered public URL';
  if(c.url){link.href=c.url;link.hidden=false}else{link.removeAttribute('href');link.hidden=true}
  if(document.activeElement!==input)input.value=c.url||'';
 }
}
document.querySelectorAll('.channel-start').forEach(b=>b.onclick=()=>guarded(async()=>{
 const n=b.dataset.channel;
 if(!confirm('Enable public HTTPS reachability for '+n+'? Keep the admin and MCP tokens secret.'))return;
 b.disabled=true;try{await req('/api/channel/'+n+'/start','POST',{});await channelRefresh();toast('Start requested. Tunnel status may take a moment to appear.')}finally{b.disabled=false}
}));
document.querySelectorAll('.channel-stop').forEach(b=>b.onclick=()=>guarded(async()=>{
 const n=b.dataset.channel;
 if(!confirm('Stop '+n+' network transport?'))return;
 await req('/api/channel/'+n+'/stop','POST',{});await channelRefresh()
}));
document.querySelectorAll('.channel-save').forEach(b=>b.onclick=()=>guarded(async()=>{
 const n=b.dataset.channel;
 await req('/api/channel/'+n+'/set-url','POST',{url:$(`${n}UrlInput`).value});
 await channelRefresh();toast('Public hostname registered')
}));
const originalRefresh=refresh;
refresh=async()=>{await originalRefresh();await channelRefresh()};

$('copyFailover').onclick=()=>guarded(async()=>{
  if(!secret)secret=(await req('/api/mcp-token')).token;
  const ch=await req('/api/channels');
  const data={main:ch.main.url,backup:ch.backup.url,mcp_token:secret};
  await navigator.clipboard.writeText(JSON.stringify(data,null,2));
  toast('Failover client config copied (contains your private token)');
});

$('startBoth').onclick=()=>guarded(async()=>{
 if(!confirm('Open both authenticated HTTPS tunnels to the public Internet? Anyone with the correct admin/MCP secrets can access your Mac.'))return;
 $('startBoth').disabled=true;
 try{
  const x=await req('/api/remote/start','POST',{});
  await channelRefresh();
  if(!x.both_online){
   const errors=['main','backup'].filter(n=>!x.channels[n].running).map(n=>n+': '+(x.channels[n].error||'Not ready')).join('; ');
   toast('Remote channels need setup: '+errors);
  }else toast('Both authenticated remote channels are online');
 }finally{$('startBoth').disabled=false}
});
$('stopBoth').onclick=()=>guarded(async()=>{
 if(!confirm('Block ALL remote access immediately? This does not stop local Web UI.'))return;
 await req('/api/remote/stop','POST',{});
 toast('Remote access blocked');
 await channelRefresh();
});

// Local AI chat is intentionally separate from ChatGPT Free. All remote users
// authenticate to this Mac and confirm any model-proposed tool actions.
let chatHistory=[], pendingAction=null, chatBusy=false;
function addBubble(kind,text){
 const div=document.createElement('div');div.className='chat-bubble '+kind;
 div.textContent=text; $('chatTranscript').append(div);$('chatTranscript').scrollTop=$('chatTranscript').scrollHeight;
}
function setPending(item){
 pendingAction=item||null; $('chatProposal').hidden=!pendingAction;
 if(pendingAction){ $('chatProposalBody').textContent=JSON.stringify({tool:pendingAction.name,arguments:pendingAction.arguments},null,2); }
}
async function sendChat(){
 if(chatBusy||pendingAction)return;
 const text=$('chatInput').value.trim();if(!text)return;
 chatBusy=true;$('chatAsk').disabled=true;$('chatInput').value='';
 addBubble('user',text);
 chatHistory.push({role:'user',content:text});
 chatHistory=chatHistory.slice(-12);
 try{
   const out=await req('/api/chat','POST',{messages:chatHistory});
   addBubble('assistant',out.message);
   chatHistory.push({role:'assistant',content:out.message});
   setPending(out.confirmation||null);
 }catch(e){
   addBubble('system','Chat error: '+e.message);
   chatHistory.pop();
 }finally{ chatBusy=false;$('chatAsk').disabled=false; }
}
$('chatAsk').onclick=()=>sendChat();
$('chatInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();sendChat();}});
$('chatApprove').onclick=()=>guarded(async()=>{
 if(!pendingAction)return;
 const action=pendingAction;
 if(!confirm('Execute '+action.name+' on this Mac with these exact arguments? Review the JSON above first.'))return;
 setPending(null);
 const out=await req('/api/chat/execute','POST',{id:action.id});
 const rendered=out.ok ? JSON.stringify(out.result,null,2) : ('Denied or failed: '+out.error);
 addBubble('system',action.name+': '+rendered.slice(0,6000));
 chatHistory.push({role:'assistant',content:'Tool result for '+action.name+': '+rendered.slice(0,3000)});
 chatHistory=chatHistory.slice(-12);
});
$('chatDeny').onclick=()=>guarded(async()=>{await req('/api/chat/reject','POST',{});setPending(null);addBubble('system','Action rejected. Nothing was executed.');});
$('chatClear').onclick=()=>guarded(async()=>{await req('/api/chat/reject','POST',{});setPending(null);chatHistory=[];$('chatTranscript').replaceChildren();addBubble('system','Chat cleared.');});
$('chatModelSave').onclick=()=>guarded(async()=>{
 await req('/api/config','POST',{llm_model:$('chatModel').value.trim()});
 toast('Local model updated');await refresh();
});
