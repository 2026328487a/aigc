const byId = id => document.getElementById(id);
const environments = {qwen21:'Qwen Image 2.1',h3:'MiniMax H3',music3:'MiniMax Music 3',voxcpm2:'VoxCPM2'};
const templates = {qwen21:[['qwen-text','文字生成图片'],['qwen-edit','参考图编辑']],h3:[['h3-image','首帧生成视频'],['h3-reference','参考图生成视频']],music3:[['music','描述与歌词生成音乐']]};
templates.h3.push(
  ['h3-lightx','LightX2V 首帧视频'],['h3-continuation','两段连续镜头'],
  ['h3-hmnsfw-i2v','HMNSFW LoRA · 首帧视频'],
  ['h3-digital-human','录音驱动数字人'],['h3-vocal-human','提取人声并驱动数字人'],
  ['h3-character-swap','动作迁移与人物替换'],['h3-clothing-change','视频服装替换 · 完整流程'],['h3-head-swap','视频换头'],
  ['h3-anime','真人转动漫'],['h3-motion','稳定身体动作'],['h3-sharpen','视频细节增强'],
  ['h3-latent-upscale','潜空间放大与二次采样'],['h3-face-refine','跟踪修脸与表情保留'],
  ['h3-union-control','Union 2.0 边缘动作控制'],['h3-effects','子弹时间特效'],['h3-refmod','可复用人物参考素材'],
  ['h3-refmod-reuse','载入已保存的人物参考']
);
let acting = false, stopped = false;
async function api(path, options = {}) {
  const response = await fetch(path,{...options,headers:{'X-AIGC-Client':'local',...options.headers}});
  const data = await response.json();
  if(!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
function error(message) {byId('error').textContent=message;byId('error').hidden=!message;}
function renderTemplates() {
  const selected=byId('engine').value, entries=templates[selected] || [];
  byId('template-heading').textContent=`${environments[selected]} 工作流（${entries.length} 个）`;
  byId('templates').dataset.engine=selected;
  try {localStorage.setItem('aigc.workbench.engine',selected);} catch {}
  byId('templates').replaceChildren();
  for(const [name,label] of entries) {
    const card=document.createElement('div');card.className='template-card';
    const title=document.createElement('span');title.textContent=label;
    const open=document.createElement('a');open.href=`/open-workflow?template=${name}`;open.textContent='一键打开';open.target='_blank';open.rel='noopener';
    const native=['h3-clothing-change','h3-hmnsfw-i2v'].includes(name);
    const download=document.createElement('a');download.href=`/api/comfy/templates/${name}${native?'?format=native':''}`;download.textContent='下载模板';download.download=`${name}.${native?'workflow':'api'}.json`;
    card.append(title,open,download);byId('templates').append(card);
  }
}
function renderJobs(jobs) {
  window.archiveUI.renderJobs(byId('jobs'),jobs.slice(0,8));
}
async function refresh() {
  if(stopped) return;
  try {
    const data=await api('/api/status'), session=data.session;
    byId('chat-summary').textContent=data.chat.busy ? 'Qwen 正在回复，完成后可切换到图片、视频或音乐。' : data.chat.state==='ready' ? '聊天已连接 · 闲置 15 分钟后自动释放。' : '进入后点击“连接并打开”。';
    const ready=session.state==='ready', changing=['starting','stopping'].includes(session.state);
    if((ready || changing) && templates[session.engine]) byId('engine').value=session.engine;
    byId('engine').disabled=acting || changing || ready || data.comfy.pending;
    byId('connect').disabled=acting || changing || ready || !!data.active_job || data.comfy.pending || data.chat.busy;
    byId('connect').hidden=ready;
    byId('open-comfy').hidden=!ready || session.engine==='voxcpm2';
    byId('disconnect').hidden=!ready;byId('disconnect').disabled=acting;
    byId('status').textContent=ready ? `${environments[session.engine]} 已就绪${data.comfy.pending ? ' · 正在执行或归档任务' : ' · 空闲 10 分钟后自动回收'}` : session.state==='starting' ? '正在启动服务器实例…' : session.state==='stopping' ? '正在保存并清理会话…' : session.cleaned===true ? '已断开，服务器会话文件已清理。' : '尚未连接服务器。';
    if(session.error) error(session.error);
    byId('logs').textContent=session.logs.join('\n');
    const selected=byId('engine').value;
    if(byId('templates').dataset.engine!==selected) renderTemplates();
    byId('all-jobs').textContent=`查看全部作品（${data.comfy.total ?? data.comfy.jobs.length}）→`;
    renderJobs(data.comfy.jobs);
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
let savedEnvironment;
try {savedEnvironment=localStorage.getItem('aigc.workbench.engine');} catch {}
const requestedEnvironment=new URLSearchParams(location.search).get('engine');
const initialEnvironment=[requestedEnvironment,savedEnvironment].find(value=>Object.hasOwn(templates,value));
if(initialEnvironment) byId('engine').value=initialEnvironment;
renderTemplates();refresh();setInterval(refresh,2000);
