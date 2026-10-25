/* Shared local archive cards and media viewer. */
(() => {
  const environments = {qwen21:'Qwen Image 2.1',h3:'MiniMax H3',music3:'MiniMax Music 3'};
  const states = {queued:'排队中',downloading:'正在保存到本机',download_error:'归档失败，等待重试',saved:'已保存到本机',failed:'任务失败',cancelled:'已取消',interrupted:'已中断'};
  const api = async (path, options = {}) => {
    const response = await fetch(path,{...options,headers:{'X-AIGC-Client':'local',...options.headers}});
    const data = await response.json();
    if(!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
    return data;
  };
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if(className) node.className = className;
    if(text) node.textContent = text;
    return node;
  };
  const fileUrl = (job, name) => `/api/comfy/jobs/${job.id}/files/${encodeURIComponent(name)}`;
  const title = job => `${environments[job.engine] || job.engine} · ${new Date(job.created).toLocaleString()}`;
  function media(job, file) {
    const extension = file.local_name.split('.').pop().toLowerCase();
    const kind = /^(png|jpe?g|webp|gif)$/.test(extension) ? 'img' : /^(mp4|webm|mov)$/.test(extension) ? 'video' : /^(wav|mp3|ogg|flac|m4a)$/.test(extension) ? 'audio' : null;
    if(!kind) return element('p','note','此格式暂不支持浏览器预览，请在本地文件夹中打开。');
    const node = element(kind,'archive-media');
    node.src = fileUrl(job,file.local_name);
    if(kind === 'img') {node.alt = file.filename;node.loading = 'lazy';}
    else {node.controls = true;node.preload = 'metadata';if(kind === 'video') node.playsInline = true;}
    node.addEventListener('error',() => {
      if(node.parentElement && !node.parentElement.querySelector('.media-error')) {
        node.after(element('p','note media-error','预览无法加载，请在本地文件夹中打开。'));
      }
    });
    return node;
  }
  function release(container) {
    for(const node of container.querySelectorAll('video,audio')) {node.pause();node.removeAttribute('src');node.load();}
  }
  const dialog = element('dialog','archive-dialog');
  dialog.setAttribute('aria-label','本地作品预览');
  const heading = element('div','viewer-heading');
  const caption = element('h2');
  const close = element('button','secondary','关闭预览');
  close.type = 'button';close.onclick = () => dialog.close();
  heading.append(caption,close);
  const contents = element('div','viewer-contents');
  dialog.append(heading,contents);
  document.body.append(dialog);
  dialog.addEventListener('close',() => {release(contents);contents.replaceChildren();});
  dialog.addEventListener('click',event => {if(event.target === dialog) dialog.close();});
  function preview(job) {
    release(contents);contents.replaceChildren();caption.textContent = title(job);
    for(const [index,file] of (job.files || []).entries()) {
      const figure = element('figure','archive-result');
      figure.append(media(job,file));
      const label = element('figcaption','',`结果 ${index+1} · ${file.local_name}`);
      figure.append(label);contents.append(figure);
    }
    dialog.showModal();
  }
  function card(job) {
    const row = element('article','job');row.dataset.jobId = job.id;
    const heading = element('div','job-title');
    heading.append(element('span','',title(job)),element('span','job-state',states[job.state] || job.state));
    row.append(heading);
    const files = job.files || [];
    if(files.length) {
      const figure = element('figure','archive-result');figure.append(media(job,files[0]));
      figure.append(element('figcaption','',files[0].local_name));row.append(figure);
    }
    const links = element('div','job-links');
    if(files.length) {const button = element('button','secondary preview-open',`查看全部结果（${files.length}）`);button.type = 'button';button.onclick = () => preview(job);links.append(button);}
    if(job.has_workflow) {
      const open = element('a','button workflow-open','在 ComfyUI 中打开');
      open.href = `/open-workflow?job=${encodeURIComponent(job.id)}`;open.target = '_blank';open.rel = 'noopener';
      links.append(open);
    }
    row.append(links);
    if(job.error) row.append(element('p','',job.error));
    return row;
  }
  function renderJobs(container, jobs) {
    const signature = JSON.stringify(jobs);
    if(container.dataset.signature === signature) return;
    container.dataset.signature = signature;release(container);container.replaceChildren();
    if(!jobs.length) {container.append(element('p','note','暂无符合条件的 ComfyUI 任务。'));return;}
    container.append(...jobs.map(card));
  }
  window.archiveUI = {api,environments,renderJobs};
})();
