const byId = id => document.getElementById(id);
const environments = {qwen21:'Qwen Image 2.1',h3:'MiniMax H3',music3:'MiniMax Music 3',voxcpm2:'VoxCPM2'};
const templates = {qwen21:[['qwen-text','文字生成图片'],['qwen-edit','参考图编辑']],h3:[['h3-image','首帧生成视频'],['h3-reference','参考图生成视频']],music3:[['music','描述与歌词生成音乐']]};
const states = {queued:'排队中',downloading:'正在保存到本机',download_error:'归档失败，等待重试',saved:'已保存到本机',failed:'任务失败',cancelled:'已取消',interrupted:'已中断'};
let acting = false, stopped = false;
async function api(path, options = {}) {
  const response = await fetch(path,{...options,headers:{'X-AIGC-Client':'local',...options.headers}});
  const data = await response.json();
  if(!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
function error(message) {byId('error').textContent=message;byId('error').hidden=!message;}
function renderTemplates() {
  byId('templates').replaceChildren();
  for(const [name,label] of templates[byId('engine').value] || []) {
    const link=document.createElement('a');link.href=`/api/comfy/templates/${name}`;link.textContent=label;link.download=`${name}.api.json`;byId('templates').append(link);
  }
}
function renderJobs(jobs) {
  byId('jobs').replaceChildren();
  if(!jobs.length) {const empty=document.createElement('p');empty.textContent='暂无 ComfyUI 任务。';byId('jobs').append(empty);return;}
  for(const job of jobs.slice(0,8)) {
    const row=document.createElement('div');row.className='job';
    const title=document.createElement('div');title.className='job-title';
    const name=document.createElement('span');name.textContent=`${environments[job.engine] || job.engine} · ${new Date(job.created).toLocaleString()}`;
    const state=document.createElement('span');state.textContent=states[job.state] || job.state;title.append(name,state);row.append(title);
    const links=document.createElement('div');links.className='job-links';
    const workflow=document.createElement('a');workflow.href=`/api/comfy/jobs/${job.id}/files/workflow-api.json`;workflow.textContent='下载工作流';links.append(workflow);
    for(const [index,file] of (job.files || []).entries()) {const link=document.createElement('a');link.href=`/api/comfy/jobs/${job.id}/files/${encodeURIComponent(file.local_name)}`;link.textContent=`结果 ${index+1} · ${file.filename}`;links.append(link);}
    row.append(links);
    if(job.error) {const text=document.createElement('p');text.textContent=job.error;row.append(text);}
    byId('jobs').append(row);
  }
}
async function refresh() {
  if(stopped) return;
  try {
    const data=await api('/api/status'), session=data.session;
    const ready=session.state==='ready', changing=['starting','stopping'].includes(session.state);
    if((ready || changing) && templates[session.engine]) byId('engine').value=session.engine;
    byId('engine').disabled=acting || changing || ready || data.comfy.pending;
    byId('connect').disabled=acting || changing || ready || !!data.active_job || data.comfy.pending;
    byId('connect').hidden=ready;
    byId('open-comfy').hidden=!ready || session.engine==='voxcpm2';
    byId('disconnect').hidden=!ready;byId('disconnect').disabled=acting;
    byId('status').textContent=ready ? `${environments[session.engine]} 已就绪${data.comfy.pending ? ' · 正在执行或归档任务' : ' · 空闲 10 分钟后自动回收'}` : session.state==='starting' ? '正在启动服务器实例…' : session.state==='stopping' ? '正在保存并清理会话…' : session.cleaned===true ? '已断开，服务器会话文件已清理。' : '尚未连接服务器。';
    if(session.error) error(session.error);
    byId('logs').textContent=session.logs.join('\n');
    renderTemplates();renderJobs(data.comfy.jobs);
  } catch(e) {error(e.message);}
}
async function action(path) {
  acting=true;error('');byId('connect').disabled=true;byId('disconnect').disabled=true;
  try {await api(path,{method:'POST'});} catch(e) {error(e.message);} finally {acting=false;await refresh();}
}
byId('engine').onchange=renderTemplates;
byId('connect').onclick=()=>action(`/api/connect?engine=${byId('engine').value}`);
byId('disconnect').onclick=()=>action('/api/disconnect');
byId('shutdown').onclick=async()=>{try {byId('shutdown').disabled=true;await api('/api/shutdown',{method:'POST'});stopped=true;byId('status').textContent='本地管理器已关闭。';byId('connect').disabled=true;byId('open-comfy').hidden=true;byId('disconnect').hidden=true;}catch(e){error(e.message);byId('shutdown').disabled=false;}};
renderTemplates();refresh();setInterval(refresh,2000);
