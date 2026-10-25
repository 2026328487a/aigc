const workflowById = id => document.getElementById(id);
const workflowTemplates = {'qwen-text':['qwen21','文字生成图片'],'qwen-edit':['qwen21','参考图编辑'],'h3-image':['h3','首帧生成视频'],'h3-reference':['h3','参考图生成视频'],music:['music3','描述与歌词生成音乐']};
const workflowEnvironments = {qwen21:'Qwen Image 2.1',h3:'MiniMax H3',music3:'MiniMax Music 3'};
const workflowSleep = ms => new Promise(resolve => setTimeout(resolve,ms));
async function workflowApi(path, options = {}) {
  const response = await fetch(path,{...options,headers:{'X-AIGC-Client':'local',...options.headers}});
  const data = await response.json();
  if(!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
async function getWorkflow() {
  const params = new URLSearchParams(location.search);
  const template = params.get('template'), job = params.get('job');
  if((!template && !job) || (template && job)) throw new Error('请从模板或本地作品库选择一个工作流。');
  if(job) {
    if(!/^[0-9a-f]{32}$/.test(job)) throw new Error('任务编号无效。');
    const data = await workflowApi(`/api/comfy/jobs/${job}/workflow`);
    return {...data,label:'历史工作流'};
  }
  const entry = workflowTemplates[template];
  if(!entry) throw new Error('此工作流模板不存在。');
  return {engine:entry[0],label:entry[1],format:'api',filename:`${template}.api.json`,workflow:await workflowApi(`/api/comfy/templates/${template}?download=false`)};
}
async function ensureEnvironment(engine) {
  if(!workflowEnvironments[engine]) throw new Error('此工作流的模型环境暂不支持 ComfyUI。');
  let data = await workflowApi('/api/status');
  const activeStates = ['ready','starting','stopping'];
  if(activeStates.includes(data.session.state) && data.session.engine !== engine) {
    throw new Error(`当前正在使用 ${workflowEnvironments[data.session.engine] || data.session.engine}。请在连接管理页切换到 ${workflowEnvironments[engine]} 后重试。`);
  }
  if(data.session.state === 'stopping') throw new Error('会话正在清理，请完成后重试。');
  if(data.session.state !== 'ready' && data.session.state !== 'starting') {
    workflowById('workflow-status').textContent = `正在启动 ${workflowEnvironments[engine]}…`;
    await workflowApi(`/api/connect?engine=${engine}`,{method:'POST'});
  }
  const deadline = Date.now() + 300000;
  while(Date.now() < deadline) {
    data = await workflowApi('/api/status');
    if(data.session.engine !== engine) throw new Error('模型环境已切换，请重新打开工作流。');
    if(data.session.state === 'ready') return;
    if(data.session.state === 'error') throw new Error(data.session.error || '服务器启动失败。');
    if(data.session.state === 'stopping') throw new Error('会话正在清理，请完成后重试。');
    workflowById('workflow-status').textContent = `正在等待 ${workflowEnvironments[engine]} 就绪…`;
    await workflowSleep(1500);
  }
  throw new Error('服务器启动超时，请查看连接管理页的诊断信息后重试。');
}
async function openWorkflow() {
  const frame = workflowById('comfy-frame');
  workflowById('workflow-error').hidden = true;workflowById('retry-workflow').hidden = true;
  frame.hidden = true;frame.removeAttribute('src');
  try {
    const data = await getWorkflow();
    await ensureEnvironment(data.engine);
    workflowById('workflow-status').textContent = `正在打开 ${data.label} 的 ComfyUI 画布…`;
    frame.src = '/comfy/';frame.hidden = false;
    const deadline = Date.now() + 90000;
    let app;
    while(Date.now() < deadline) {
      const candidate = frame.contentWindow?.app;
      if(candidate?.graph && typeof candidate.loadApiJson === 'function' && typeof candidate.loadGraphData === 'function') {app = candidate;break;}
      await workflowSleep(250);
    }
    if(!app) throw new Error('ComfyUI 画布加载超时，请检查连接后重试。');
    if(data.format === 'native') await app.loadGraphData(data.workflow,true,true,data.filename);
    else await app.loadApiJson(data.workflow,data.filename);
    frame.contentWindow.dispatchEvent(new Event('resize'));
    workflowById('workflow-status').textContent = `${data.label}已加载 · 请检查提示词和输入素材，再手动点击运行。`;
    document.title = `${data.label} · AIGC`;
  } catch(error) {
    workflowById('workflow-status').textContent = '工作流尚未加载。';
    workflowById('workflow-error').textContent = error.message;workflowById('workflow-error').hidden = false;
    workflowById('retry-workflow').hidden = false;
  }
}
workflowById('retry-workflow').onclick = openWorkflow;
openWorkflow();
