const byId = id => document.getElementById(id);
let acting=false, connected=false;
function error(message) {byId('chat-error').textContent=message;byId('chat-error').hidden=!message;}
async function api(path,options={}) {
  const response=await fetch(path,{...options,headers:{'X-AIGC-Client':'local'}});
  const data=await response.json();
  if(!response.ok) throw new Error(typeof data.detail==='string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
async function refresh() {
  try {
    const session=(await api('/api/status')).chat;
    const ready=session.state==='ready',changing=['starting','stopping'].includes(session.state);
    byId('chat-connect').hidden=ready;
    byId('chat-connect').disabled=acting || changing;
    byId('chat-disconnect').hidden=!ready;
    byId('chat-disconnect').disabled=acting || changing;
    byId('chat-open').hidden=!ready;
    byId('chat-status').textContent=changing ? '正在连接或释放聊天服务…' : ready ? (session.busy ? 'Qwen 正在回复' : '已连接 · 闲置 15 分钟自动释放') : session.cleaned===true ? '已断开，聊天服务与模型已释放。再次连接可继续使用原界面。' : '点击“连接并打开”，使用原有聊天界面。';
    const frame=byId('chat-frame');
    if(ready && !connected) {frame.src='/qwen/';frame.hidden=false;}
    if(!ready && connected) {frame.src='about:blank';frame.hidden=true;}
    connected=ready;
    if(session.error) error(session.error);
  } catch(e) {error(e.message);}
}
async function action(path) {
  acting=true;error('');byId('chat-connect').disabled=true;byId('chat-disconnect').disabled=true;
  try {await api(path,{method:'POST'});} catch(e) {error(e.message);} finally {acting=false;await refresh();}
}
byId('chat-connect').onclick=()=>action('/api/chat/connect');
byId('chat-disconnect').onclick=()=>action('/api/chat/disconnect');
refresh();setInterval(refresh,2000);
