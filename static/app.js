const $ = (id) => document.getElementById(id);
const requestedEngine = new URLSearchParams(location.search).get('engine');
let engine = requestedEngine === 'voxcpm2' ? 'voxcpm2' : 'qwen21', mode = engine === 'voxcpm2' ? 'speech' : 'text', files = [], selectedJob, allJobs = [], server = {}, busy = false, shuttingDown = false;
let activeJob = null, jobsRevision = 0;
const deletingJobs = new Set();
try {
  const saved = sessionStorage.getItem('aigc.selectedJob');
  if (saved !== null) {
    const value = JSON.parse(saved);
    if (value === null || (typeof value === 'string' && /^[0-9a-f]{32}$/.test(value))) selectedJob = value;
  }
} catch { /* Preview selection still works when browser storage is unavailable. */ }
const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
const states = {completed:'已完成', failed:'失败', cancelled:'已取消', interrupted:'已中断', running:'生成中', queued:'等待中'};
function showError(message) { $('error').textContent = message; $('error').hidden = !message; }
function selectJob(jobId) {
  selectedJob = jobId;
  try { sessionStorage.setItem('aigc.selectedJob', JSON.stringify(jobId)); } catch { /* Optional persistence. */ }
}
function clearMedia() {
  for (const media of [$('result-video'), $('result-audio')]) {
    media.pause();
    if (media.hasAttribute('src')) { media.removeAttribute('src'); media.load(); }
  }
  $('result-image').removeAttribute('src'); $('working-image').removeAttribute('src');
  $('download').removeAttribute('href');
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'X-AIGC-Client':'local', ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
function renderFiles() {
  $('image-previews').replaceChildren();
  files.forEach((file, index) => {
    const item = document.createElement('div'); item.className = 'preview-item';
    const image = document.createElement('img'); image.src = URL.createObjectURL(file); image.alt = file.name;
    image.onload = () => URL.revokeObjectURL(image.src);
    const label = document.createElement('span'); label.textContent = mode === 'image' ? (index === 0 ? '首帧' : '尾帧') : `Picture ${index + 1}`;
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label', `移除图片 ${index + 1}`);
    remove.onclick = () => { files.splice(index, 1); renderFiles(); };
    item.append(image, label, remove); $('image-previews').append(item);
  });
}
function acceptFiles(incoming) {
  const maximum = mode === 'image' ? 2 : 9;
  if (files.length + incoming.length > maximum) { showError(`此模式最多上传 ${maximum} 张图片。可以先移除已选图片。`); return; }
  if ([...incoming].some(f => f.size > 32 * 1024 * 1024)) { showError('每张图片不能超过 32 MB。'); return; }
  files.push(...incoming); renderFiles(); showError('');
}
$('images').onchange = (event) => { acceptFiles(event.target.files); event.target.value = ''; };
for (const event of ['dragenter','dragover']) $('drop-zone').addEventListener(event, e => {e.preventDefault(); $('drop-zone').classList.add('dragging');});
for (const event of ['dragleave','drop']) $('drop-zone').addEventListener(event, e => {e.preventDefault(); $('drop-zone').classList.remove('dragging');});
$('drop-zone').addEventListener('drop', e => acceptFiles(e.dataTransfer.files));
function configureMode() {
  const qwen = engine === 'qwen21', music = engine === 'music3', tts = engine === 'voxcpm2';
  $('upload-fields').hidden = !['image','reference','edit'].includes(mode);
  $('audio-upload-fields').hidden = mode !== 'clone';
  if (mode !== 'clone') {$('reference-player').pause(); $('reference-source').pause();}
  updateReferenceControls();
  $('lyrics-field').hidden = !music; $('voice-fields').hidden = !tts;
  $('size-field').hidden = music || tts;
  $('seconds-field').hidden = qwen || tts; $('duration-hint').hidden = qwen || tts;
  $('image-hint').textContent = mode === 'edit' ? '支持 1–9 张参考图；输出比例跟随第 1 张' : mode === 'image' ? '第 1 张为首帧，第 2 张为可选尾帧' : '支持 1–9 张参考图，按上传顺序编号';
  $('prompt-label').textContent = music ? '音乐描述' : tts ? '待朗读文字' : '画面描述';
  $('prompt').maxLength = tts ? 2000 : 12000;
  $('prompt-hint').textContent = music ? '描述曲风、乐器、人声与情绪。建议先生成短片段，再尝试完整歌曲。' : tts ? '直接填写要说的内容。长内容建议按自然段分别生成。' : mode === 'edit' ? '描述要修改的内容；画面尺寸控制大致像素总量，输出保持首张参考图比例。' : mode === 'text' ? '描述主体、构图、光线与要呈现的文字。默认 1024 × 1024、25 步。' : mode === 'reference' ? '用 <Picture 1>、<Picture 2> 指定对应参考图的主体。' : '动作越具体，越方便比较不同参数的效果。';
  document.querySelectorAll('[data-mode]').forEach(b => {b.classList.toggle('selected',b.dataset.mode===mode); b.setAttribute('aria-pressed',String(b.dataset.mode===mode));});
  renderFiles(); durationHint();
}
function configureEngine() {
  engine = $('engine').value;
  mode = {qwen21:'text', h3:'image', music3:'music', voxcpm2:'speech'}[engine];
  const qwen = engine === 'qwen21', music = engine === 'music3', tts = engine === 'voxcpm2';
  const modes = {qwen21:[['text','文生图'],['edit','图片编辑']], h3:[['image','图生视频'],['reference','参考图生视频']], music3:[['music','音乐生成']], voxcpm2:[['speech','文字转语音'],['clone','声音克隆']]}[engine];
  document.querySelectorAll('[data-mode]').forEach((b,i) => {b.hidden=!modes[i]; b.dataset.mode=modes[i]?.[0]||''; b.textContent=modes[i]?.[1]||'';});
  const sizes = qwen ? [['1024,1024','1024 × 1024 · 默认'],['512,512','512 × 512 · 较快'],['1536,1024','1536 × 1024 · 横幅'],['1024,1536','1024 × 1536 · 竖幅'],['custom','自定义']] : [['768,448','768 × 448 · 默认'],['512,320','512 × 320 · 较快'],['864,480','864 × 480'],['448,768','448 × 768 · 竖屏'],['custom','自定义']];
  $('size').replaceChildren(...sizes.map(([value,label]) => new Option(label,value)));
  $('custom-size').hidden=true;
  $('steps').value=qwen?25:music?30:tts?10:8;
  $('seconds').max=music?300:15; $('seconds').value=music?30:2;
  $('seconds-label').textContent=music?'最长时长（秒）':'目标时长（秒）';
  $('width').max=$('height').max=qwen?2048:1536;
  $('width').value=qwen?1024:768; $('height').value=qwen?1024:448;
  $('model-title').textContent={qwen21:'QWEN IMAGE 2.1',h3:'MINIMAX H3',music3:'MINIMAX MUSIC 3',voxcpm2:'VOXCPM2'}[engine];
  $('prompt').placeholder=music?'例如：温暖的民谣，轻柔女声，指弹吉他与钢琴，副歌逐渐明亮。':tts?'例如：你好呀，我今日想同你分享一个几有趣嘅故事。':qwen?'例如：一只橘猫坐在窗边的木桌上，午后的柔和阳光，桌上有一杯茶。':'描述主体动作、镜头运动、光线与声音。';
  configureMode();
}
let referenceURL, sourceURL, selectedReference = null, extractionController = null, extractionRevision = 0, rangeEdited = false;
function updateReferenceControls() {
  const extracting = !!extractionController;
  $('reference-range').disabled = mode !== 'clone' || !$('reference-media').files[0] || extracting;
  $('reference-extract').textContent = extracting ? '正在提取…' : '提取并用作参考录音';
  $('generate').disabled = server.state !== 'ready' || server.engine !== engine || busy || (mode === 'clone' && (!selectedReference || extracting));
}
function setReference(file, description = '', downloadable = false) {
  selectedReference = file;
  const player = $('reference-player'); player.pause();
  if (referenceURL) URL.revokeObjectURL(referenceURL);
  referenceURL = null; player.removeAttribute('src'); player.hidden = !file;
  $('reference-download').hidden = !file || !downloadable; $('reference-download').removeAttribute('href');
  $('reference-info').hidden = !file; $('reference-info').textContent = description || file?.name || '';
  if (file) {
    referenceURL = URL.createObjectURL(file); player.src = referenceURL;
    if (downloadable) {$('reference-download').href = referenceURL; $('reference-download').download = file.name;}
  }
  player.load(); updateReferenceControls();
}
function invalidateExtraction() {
  extractionRevision++;
  extractionController?.abort(); extractionController = null;
  $('reference-extract-status').textContent = '';
  $('reference-extract-error').hidden = true;
  updateReferenceControls();
}
function clearReferenceSource() {
  $('reference-source').pause(); $('reference-source').removeAttribute('src'); $('reference-source').load();
  $('reference-source').hidden = true;
  if (sourceURL) URL.revokeObjectURL(sourceURL);
  sourceURL = null; $('reference-source-info').textContent = '';
}
$('reference-audio').onchange = () => {
  invalidateExtraction(); clearReferenceSource(); $('reference-media').value = '';
  const file = $('reference-audio').files[0];
  if (file?.size > 32 * 1024 * 1024) {setReference(null); showError('参考录音上限 32 MB。较长录音可使用下方裁剪工具。'); return;}
  setReference(file || null); showError('');
};
$('reference-media').onchange = () => {
  invalidateExtraction(); clearReferenceSource(); $('reference-audio').value = ''; setReference(null);
  const file = $('reference-media').files[0];
  if (!file) return;
  if (file.size > 128 * 1024 * 1024) {
    $('reference-media').value = ''; updateReferenceControls();
    $('reference-extract-error').textContent = '源视频或录音上限 128 MB，请先截取较短的文件。'; $('reference-extract-error').hidden = false; return;
  }
  rangeEdited = false; $('reference-start').value = '0'; $('reference-end').value = '15';
  const player = $('reference-source');
  sourceURL = URL.createObjectURL(file); const url = sourceURL;
  player.onloadedmetadata = () => {
    if (sourceURL !== url) return;
    if (Number.isFinite(player.duration)) {
      const duration = Math.floor(player.duration * 100) / 100;
      if (!rangeEdited) $('reference-end').value = String(duration <= 30 ? duration : 15);
      $('reference-source-info').textContent = `${file.name} · ${duration} 秒。可播放定位，再选择片段。`;
    }
  };
  player.onerror = () => {if (sourceURL === url) $('reference-source-info').textContent = '浏览器无法预览此格式，仍可手动填写起止时间并尝试提取。';};
  player.src = url; player.hidden = false; player.load();
  $('reference-source-info').textContent = file.name;
  updateReferenceControls();
};
function referenceRangeChanged() {rangeEdited = true; invalidateExtraction(); setReference(null);}
for (const id of ['reference-start', 'reference-end']) $(id).oninput = referenceRangeChanged;
$('reference-trim').onchange = referenceRangeChanged;
$('reference-use-position').onclick = () => {
  const player = $('reference-source'), start = Math.floor(player.currentTime * 100) / 100;
  $('reference-start').value = String(start);
  $('reference-end').value = String(Math.floor(Math.min(start + 15, Number.isFinite(player.duration) ? player.duration : start + 15) * 100) / 100);
  referenceRangeChanged();
};
$('reference-extract').onclick = async () => {
  const file = $('reference-media').files[0]; if (!file) return;
  invalidateExtraction(); setReference(null);
  const revision = extractionRevision;
  try {
    const start = Number($('reference-start').value), end = Number($('reference-end').value);
    if (!$('reference-start').value || !$('reference-end').value || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end - start < 3 || end - start > 30) throw new Error('请选择 3–30 秒的片段，结束时间必须晚于开始时间。');
    extractionController = new AbortController(); updateReferenceControls();
    $('reference-extract-status').textContent = '正在本机提取音轨…';
    $('reference-source').pause();
    const form = new FormData(); form.append('media', file); form.append('start', start); form.append('end', end); form.append('trim_silence', $('reference-trim').checked);
    const response = await fetch('/api/reference-audio/extract', {method:'POST', headers:{'X-AIGC-Client':'local'}, body:form, signal:extractionController.signal});
    if (!response.ok) {
      const data = await response.json();
      throw new Error(response.status === 404 ? '请重启本地工作台以启用音轨提取，再刷新页面。' : typeof data.detail === 'string' ? data.detail : '提取失败，请检查文件与起止时间。');
    }
    const blob = await response.blob(); if (revision !== extractionRevision) return;
    const name = file.name.replace(/\.[^.]+$/, '') + '_reference.wav';
    const duration = Number(response.headers.get('X-Audio-Duration'));
    setReference(new File([blob], name, {type:'audio/wav'}), `${name} · ${duration.toFixed(2)} 秒 · 已用作参考录音`, true);
    const removed = Number(response.headers.get('X-Trimmed-Start')) + Number(response.headers.get('X-Trimmed-End'));
    $('reference-extract-status').textContent = `提取完成${removed > 0 ? `，已裁去首尾 ${removed.toFixed(2)} 秒静音` : ''}。请在下方试听，也可以下载保存。`;
    showError('');
  } catch (error) {
    if (revision !== extractionRevision || error.name === 'AbortError') return;
    $('reference-extract-status').textContent = '';
    $('reference-extract-error').textContent = error.message; $('reference-extract-error').hidden = false;
  } finally {
    if (revision === extractionRevision) {extractionController = null; updateReferenceControls();}
  }
};
$('engine').onchange=() => {configureEngine(); refresh();};
document.querySelectorAll('[data-mode]').forEach(button => button.onclick = () => {
  if (button.dataset.mode === mode) return;
  if (button.dataset.mode === 'image' && files.length > 2) {showError('请先保留不超过 2 张图片，再切换到图生视频。'); return;}
  mode = button.dataset.mode;
  document.querySelectorAll('[data-mode]').forEach(b => {b.classList.toggle('selected',b===button); b.setAttribute('aria-pressed',String(b===button));});
  $('steps').value = engine === 'qwen21' ? 25 : engine === 'voxcpm2' ? 10 : engine === 'music3' ? 30 : mode === 'image' ? 8 : 4;
  configureMode();
});
$('size').onchange = () => {$('custom-size').hidden = $('size').value !== 'custom';};
$('random-seed').onclick = () => {$('seed').value = crypto.getRandomValues(new Uint32Array(1))[0];};
function durationHint() {if(engine==='music3'){$('duration-hint').textContent='时长为生成上限；歌曲可能提前结束。输出保存为无损 WAV。';return;} const target = Math.max(5, Math.round(Number($('seconds').value)*24)); const frames = target+(5-target%17+17)%17; $('duration-hint').textContent = `实际时长约 ${(frames/24).toFixed(2)} 秒 · ${frames} 帧 · 24 fps`;}
$('seconds').oninput = durationHint; durationHint();
async function action(fn) {try {showError(''); await fn(); await refresh();} catch (error) {showError(error.message);}}
$('connect').onclick = () => action(async () => {$('connect').disabled=true; await api(`/api/connect?engine=${engine}`,{method:'POST'});});
$('disconnect').onclick = () => action(async () => {$('disconnect').disabled=true; await api('/api/disconnect',{method:'POST'});});
$('cancel').onclick = () => action(async () => {$('cancel').disabled=true; await api(`/api/jobs/${selectedJob}/cancel`,{method:'POST'});});
$('clear-preview').onclick = () => {selectJob(null); renderJob(null); renderHistory();};
$('shutdown').onclick = async () => {
  try {
    $('shutdown').disabled=true;
    await api('/api/shutdown',{method:'POST'}); shuttingDown=true;
    $('server-state').textContent='工作台已关闭'; $('status-dot').className='dot';
    $('generate').disabled=true; $('connect').disabled=true; $('disconnect').hidden=true;
    showError('工作台已关闭，可以关闭此页面。下次双击 start.cmd 重新打开。');
  } catch(error) {showError(error.message); $('shutdown').disabled=false;}
};
$('generate-form').onsubmit = (event) => {
  event.preventDefault();
  action(async () => {
    if (['image','reference','edit'].includes(mode) && !files.length) throw new Error('请先选择至少一张参考图片。');
    const [width,height] = $('size').value === 'custom' ? [Number($('width').value),Number($('height').value)] : $('size').value.split(',').map(Number);
    const settings = {engine,mode,prompt:$('prompt').value,width,height,seconds:Number($('seconds').value),steps:Number($('steps').value),seed:Number($('seed').value),lyrics:$('lyrics').value,language:$('language').value,reference_text:mode==='clone'?$('reference-text').value:'',voice_style:$('voice-style').value};
    const form = new FormData(); form.append('settings',JSON.stringify(settings)); if(['image','reference','edit'].includes(mode))files.forEach(file => form.append('images',file));
    if(mode==='clone'){if(extractionController)throw new Error('请等待音轨提取完成。'); const reference=selectedReference; if(!reference)throw new Error('请上传参考录音，或先从视频提取。'); if(reference.size>32*1024*1024)throw new Error('参考录音上限 32 MB。'); form.append('reference_audio',reference);}
    $('generate').disabled=true;
    const job = await api('/api/jobs',{method:'POST',body:form}); selectJob(job.id);
  });
};
function renderJob(job) {
  $('empty-result').hidden=!!job; $('job-panel').hidden=!job;
  $('clear-preview').hidden=!job || !terminal.has(job.state);
  if (!job) {clearMedia(); return;}
  const complete=job.state==='completed', working=!terminal.has(job.state);
  const picture=job.settings.engine==='qwen21', sound=['music3','voxcpm2'].includes(job.settings.engine);
  $('result-video').hidden=!complete||picture||sound; $('result-image').hidden=!complete||!picture; $('audio-result').hidden=!complete||!sound; $('working-preview').hidden=complete;
  $('audio-title').textContent=job.settings.engine==='music3'?'音乐已生成':'语音已生成';
  const resultURL=`/api/jobs/${job.id}/files/${job.result_file||(picture?'result.png':sound?'result.wav':'result.mp4')}`;
  const media=picture?$('result-image'):sound?$('result-audio'):$('result-video');
  if(complete && media.getAttribute('src')!==resultURL)media.src=resultURL;
  if(!complete||picture||sound){const video=$('result-video'); video.pause(); if(video.hasAttribute('src')){video.removeAttribute('src'); video.load();}}
  if(!complete||!picture)$('result-image').removeAttribute('src');
  if(!complete||!sound){const audio=$('result-audio');audio.pause();if(audio.hasAttribute('src')){audio.removeAttribute('src');audio.load();}}
  $('working-image').hidden=!job.images.length;
  if (!complete && job.images.length) $('working-image').src=`/api/jobs/${job.id}/files/${job.images[0]}`;
  else $('working-image').removeAttribute('src');
  document.querySelector('.working-caption').textContent=job.stage;
  $('job-stage').textContent=job.stage; $('job-elapsed').textContent=`${Math.floor(job.elapsed/60)} 分 ${Math.floor(job.elapsed%60)} 秒`;
  $('job-progress').value=job.progress; $('job-progress').hidden=!working;
  const dimensions=job.image||job.video||job.settings;
  $('job-details').textContent=sound?`${job.audio?`${job.audio.duration} 秒 · ${job.audio.sample_rate/1000} kHz · ${job.audio.channels===2?'立体声':'单声道'} · `:''}${job.settings.steps} 步 · 种子 ${job.settings.seed}`:`${dimensions.width} × ${dimensions.height}${picture?'':` · ${job.effective_seconds} 秒`} · ${job.settings.steps} 步 · 种子 ${job.settings.seed}`;
  $('job-error').hidden=!job.error; $('job-error').textContent=job.error || '';
  $('cleanup-status').textContent=job.remote_cleaned ? '✓ 本次远端文件已清理' : (terminal.has(job.state) ? (job.cleanup_finished ? '远端清理未确认，请查看连接诊断' : '正在清理本次远端文件…') : '完成后下载并清理远端文件');
  $('download').hidden=!complete; $('download').href=resultURL+'?download=true'; $('download').textContent=picture?'下载图片 ↓':sound?'下载音频 ↓':'下载视频 ↓';
  $('cancel').hidden=!working; $('cancel').disabled=false;
}
let historyKey='';
async function deleteHistoryJob(job) {
  if (deletingJobs.has(job.id)) return;
  if (!window.confirm('删除这条历史记录及其本地文件？\n\n生成图片／视频／音频、参考素材和参数文件将一并删除，无法撤销。浏览器另存的副本不受影响。')) return;
  deletingJobs.add(job.id); jobsRevision++; renderHistory();
  if (selectedJob === job.id) clearMedia();
  try {
    showError('');
    await api(`/api/jobs/${job.id}`, {method:'DELETE'});
    allJobs = allJobs.filter(item => item.id !== job.id);
    if (selectedJob === job.id) selectJob(null);
  } catch (error) {showError(error.message);}
  finally {
    deletingJobs.delete(job.id); jobsRevision++;
    renderJob(allJobs.find(item => item.id === selectedJob)); renderHistory();
    await refresh();
  }
}
function renderHistory() {
  $('history-count').textContent=`${allJobs.length} 个实验`; $('history-empty').hidden=allJobs.length>0;
  const key=allJobs.map(j=>j.id+j.state).join(',')+selectedJob+activeJob+[...deletingJobs].join(',');
  if(key===historyKey)return; historyKey=key; $('history-list').replaceChildren();
  allJobs.forEach(job=>{
    const item=document.createElement('div'); item.className='history-item'+(selectedJob===job.id?' active':'');
    item.dataset.jobId=job.id;
    const open=document.createElement('button'); open.type='button'; open.className='history-open';
    open.setAttribute('aria-label', `查看记录：${job.settings.prompt}`);
    const image=document.createElement('img');
    const thumbnail=job.state==='completed' && job.settings.engine==='qwen21'?'result.png':job.images[0];
    image.src=thumbnail?`/api/jobs/${job.id}/files/${thumbnail}`:'/static/icon.svg'; image.alt='实验预览'; image.loading='lazy';
    const text=document.createElement('div'); text.className='history-text';
    const title=document.createElement('strong'); title.textContent=job.settings.prompt;
    const note=document.createElement('small'); note.textContent=`${new Date(job.created).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})} · ${['music3','voxcpm2'].includes(job.settings.engine)?(job.settings.engine==='music3'?'音乐':'语音')+(job.audio?` · ${job.audio.duration} 秒`:''):`${job.settings.width} × ${job.settings.height}`}`;
    text.append(title,note); const state=document.createElement('span'); state.className='history-state'; state.textContent=states[job.state]||job.state;
    open.append(image,text,state); open.onclick=()=>{selectJob(job.id); renderJob(job); renderHistory();};
    const remove=document.createElement('button'); remove.type='button'; remove.className='button history-delete';
    remove.textContent=deletingJobs.has(job.id)?'删除中…':'删除';
    remove.setAttribute('aria-label', `删除记录及文件：${job.settings.prompt}`);
    remove.disabled=!terminal.has(job.state)||activeJob===job.id||deletingJobs.has(job.id);
    open.disabled=deletingJobs.has(job.id);
    remove.title=remove.disabled?'任务运行或清理期间不能删除':'删除这条记录及其本地文件';
    remove.onclick=()=>deleteHistoryJob(job);
    item.append(open,remove); $('history-list').append(item);
  });
}
let polling=false;
async function refresh() {
  if(polling||shuttingDown)return; polling=true;
  const revision=jobsRevision;
  try {
    const [status,jobs]=await Promise.all([api('/api/status'),api('/api/jobs')]);
    if(revision!==jobsRevision)return;
    server=status.session; allJobs=jobs; activeJob=status.active_job; busy=!!activeJob;
    const labels={disconnected:'未连接',starting:'正在启动',ready:'已就绪',stopping:'正在回收',error:'连接失败'};
    const matching=server.engine===engine;
    $('server-state').textContent=`DGX · ${labels[server.state] || server.state}`;
    $('status-dot').className='dot '+server.state;
    $('connect').hidden=server.state==='ready'&&matching; $('connect').disabled=busy||['starting','stopping'].includes(server.state);
    $('connect').textContent=server.state==='starting'?'正在启动…':server.state==='ready'&&!matching?'切换连接':'连接 DGX';
    $('engine').disabled=busy||['starting','stopping'].includes(server.state);
    $('engine-hint').textContent=server.state==='ready'?(matching?'当前模型已连接。':'点击“切换连接”启动所选模型。'):'选择模型后连接 DGX。';
    $('disconnect').hidden=['disconnected','error'].includes(server.state); $('disconnect').disabled=server.state==='stopping';
    $('disconnect').textContent=busy?'取消并断开':'断开';
    updateReferenceControls();
    $('generate').replaceChildren(document.createTextNode(busy?'正在生成，请稍候':server.state==='ready'&&matching?'开始生成':'连接所选模型后开始生成'));
    const arrow=document.createElement('span'); arrow.textContent='↗'; $('generate').append(arrow);
    $('connection-log').textContent=[server.authenticated?'接口认证已验证。':'',server.error,...server.logs].filter(Boolean).join('\n')||'等待连接。';
    if(server.error && server.state==='error')showError(server.error);
    if(selectedJob===undefined)selectJob(activeJob||allJobs[0]?.id||null);
    if(selectedJob && !allJobs.some(j=>j.id===selectedJob))selectJob(null);
    renderJob(deletingJobs.has(selectedJob)?null:allJobs.find(j=>j.id===selectedJob)); renderHistory();
  } catch(error) {$('server-state').textContent='本地服务连接中断'; $('generate').disabled=true; showError(error.message);}
  finally {polling=false;}
}
$('engine').value = engine;
configureEngine(); refresh(); setInterval(refresh,2000);
