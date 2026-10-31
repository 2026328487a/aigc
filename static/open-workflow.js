const workflowById = id => document.getElementById(id);
const workflowTemplates = {'qwen-text':['qwen21','文字生成图片'],'qwen-edit':['qwen21','参考图编辑'],'h3-image':['h3','首帧生成视频'],'h3-reference':['h3','参考图生成视频'],music:['music3','描述与歌词生成音乐']};
for(const [name,label] of [
  ['h3-lightx','LightX2V 首帧视频'],['h3-continuation','两段连续镜头'],
  ['h3-digital-human','录音驱动数字人'],['h3-vocal-human','提取人声并驱动数字人'],
  ['h3-character-swap','动作迁移与人物替换'],['h3-head-swap','视频换头'],
  ['h3-anime','真人转动漫'],['h3-motion','稳定身体动作'],['h3-sharpen','视频细节增强'],
  ['h3-latent-upscale','潜空间放大与二次采样'],['h3-face-refine','跟踪修脸与表情保留'],
  ['h3-union-control','Union 2.0 边缘动作控制'],['h3-effects','子弹时间特效'],['h3-refmod','可复用人物参考素材'],
  ['h3-refmod-reuse','载入已保存的人物参考']
]) workflowTemplates[name]=['h3',label];
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
function arrangeApiGraph(app, workflow) {
  const graph = app.rootGraph;
  const nodes = graph._nodes || [...graph.nodes];
  const nodeMap = new Map(nodes.map(node => [String(node.id),node]));
  const depths = new Map();
  function depth(id, visiting = new Set()) {
    if(depths.has(id)) return depths.get(id);
    if(visiting.has(id)) return 0;
    const next = new Set(visiting);next.add(id);
    const dependencies = (nodeMap.get(id)?.inputs || []).map(input => {
      if(input.link == null) return null;
      const link = graph.links.get?.(input.link) || graph.links[input.link];
      return link && nodeMap.has(String(link.origin_id)) ? String(link.origin_id) : null;
    }).filter(id => id != null);
    const value = dependencies.length ? Math.max(...dependencies.map(id => depth(id, next))) + 1 : 0;
    depths.set(id,value);return value;
  }
  const columns = new Map();
  for(const node of nodes) {
    const column = depth(String(node.id));
    if(!columns.has(column)) columns.set(column,[]);
    columns.get(column).push(node);
  }
  let x = 0;
  for(const [,nodes] of [...columns].sort((a,b) => a[0]-b[0])) {
    const sourceTypes = ['AIGCLoadH3Reference','LoadImage','LoadAudio','VHS_LoadVideo'];
    nodes.sort((a,b) => {
      const aType=a.type, bType=b.type;
      const aIndex=sourceTypes.indexOf(aType),bIndex=sourceTypes.indexOf(bType);
      return (aIndex < 0 ? 100 : aIndex) - (bIndex < 0 ? 100 : bIndex);
    });
    let y = 0;
    for(const node of nodes) {node.pos = [x,y];y += Math.max(150,node.size?.[1] || 150) + 60;}
    x += Math.max(340,...nodes.map(node => node.size?.[0] || 340)) + 100;
  }
  app.graph.setDirtyCanvas(true,true);
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
      if(candidate?.isGraphReady && candidate.vueAppReady && candidate.positionConversion && !candidate.configuringGraph &&
         typeof candidate.loadApiJson === 'function' && typeof candidate.loadGraphData === 'function') {app = candidate;break;}
      await workflowSleep(250);
    }
    if(!app) throw new Error('ComfyUI 画布加载超时，请检查连接后重试。');
    if(data.format === 'native') await app.loadGraphData(data.workflow,true,true,data.filename);
    else {
      await app.loadApiJson(data.workflow,data.filename);
      const expected = Object.values(data.workflow).map(node => node.class_type).sort();
      const actual = (app.rootGraph._nodes || [...app.rootGraph.nodes]).map(node => node.type).sort();
      if(JSON.stringify(expected) !== JSON.stringify(actual)) throw new Error('画布节点与模板不一致，请重试加载。');
      arrangeApiGraph(app,data.workflow);
    }
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
