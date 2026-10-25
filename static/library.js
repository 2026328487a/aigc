const libraryById = id => document.getElementById(id);
let libraryPage = 1, libraryRequest = 0;
async function loadLibrary(page = 1) {
  const request = ++libraryRequest;
  libraryById('library-error').hidden = true;
  libraryById('library-status').textContent = '正在读取本地作品…';
  libraryById('previous').disabled = libraryById('next').disabled = true;
  const params = new URLSearchParams({page,page_size:12,q:libraryById('search').value.trim()});
  if(libraryById('filter-engine').value) params.set('engine',libraryById('filter-engine').value);
  try {
    const data = await window.archiveUI.api(`/api/comfy/jobs?${params}`);
    if(request !== libraryRequest) return;
    libraryPage = data.page;
    window.archiveUI.renderJobs(libraryById('library-jobs'),data.jobs);
    libraryById('library-status').textContent = `共 ${data.total} 条任务 · 已保存的结果可直接预览`;
    libraryById('page-number').textContent = `第 ${data.page} / ${data.pages} 页`;
    libraryById('previous').disabled = data.page <= 1;
    libraryById('next').disabled = data.page >= data.pages;
  } catch(error) {
    if(request !== libraryRequest) return;
    libraryById('library-status').textContent = '读取失败，可点击“查找 / 刷新”重试。';
    libraryById('library-error').textContent = error.message;libraryById('library-error').hidden = false;
  }
}
libraryById('filters').onsubmit = event => {event.preventDefault();loadLibrary();};
libraryById('filter-engine').onchange = () => loadLibrary();
libraryById('previous').onclick = () => loadLibrary(libraryPage - 1);
libraryById('next').onclick = () => loadLibrary(libraryPage + 1);
loadLibrary();
