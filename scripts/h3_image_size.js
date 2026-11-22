import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';

export function imageGeometry(originalWidth, originalHeight, values) {
  const { mode, longest_edge = 512, scale = 1, width = 512, height = 320 } = values;
  if(originalWidth <= 0 || originalHeight <= 0) throw new Error('图片宽高必须大于 0。');
  let factor;
  if(mode === '最长边') factor = longest_edge / Math.max(originalWidth, originalHeight);
  else if(mode === '缩放倍数') factor = scale;
  else if(mode === '手动宽高') {
    if(![width,height].every(side => side >= 32 && side <= 1536 && side % 32 === 0))
      throw new Error('手动宽高必须是 32 的倍数，范围为 32–1536。');
    factor = Math.min(width / originalWidth, height / originalHeight);
  } else throw new Error('请选择最长边、缩放倍数或手动宽高。');
  if(!Number.isFinite(factor) || factor <= 0) throw new Error('缩放值必须是大于 0 的有限数值。');
  const contentWidth = Math.max(1,Math.floor(originalWidth * factor + 0.5));
  const contentHeight = Math.max(1,Math.floor(originalHeight * factor + 0.5));
  const canvasWidth = mode === '手动宽高' ? width : Math.ceil(contentWidth / 32) * 32;
  const canvasHeight = mode === '手动宽高' ? height : Math.ceil(contentHeight / 32) * 32;
  if(Math.max(canvasWidth,canvasHeight) > 1536)
    throw new Error(`目标画布 ${canvasWidth}×${canvasHeight} 超过 1536，请降低最长边或缩放倍数。`);
  return { contentWidth, contentHeight, width: canvasWidth, height: canvasHeight,
    padded: contentWidth !== canvasWidth || contentHeight !== canvasHeight };
}

function sourceImageUrl(node) {
  const source = node.getInputNode(0);
  const value = source?.widgets?.find(widget => widget.name === 'image')?.value;
  if(source?.type !== 'LoadImage' || !value) return null;
  const match = String(value).match(/^(.*?)(?:\s+\[(input|output|temp)\])?$/);
  const path = match[1].replaceAll('\\','/');
  const split = path.lastIndexOf('/');
  return api.apiURL('/view?' + new URLSearchParams({
    filename: path.slice(split + 1), subfolder: path.slice(0,Math.max(0,split)), type: match[2] || 'input',
  }));
}

function loadDimensions(url) {
  return new Promise((resolve,reject) => {
    const picture = new Image();
    picture.onload = () => resolve([picture.naturalWidth,picture.naturalHeight]);
    picture.onerror = () => reject(new Error('上传首帧图片后显示尺寸。'));
    picture.src = url;
  });
}

export async function refreshSizePreview(node) {
  const state = node.aigcSizePreview;
  if(!state) return;
  const version = ++state.version;
  const url = sourceImageUrl(node);
  if(!url) {
    state.element.textContent = '连接首帧图片后显示尺寸。';
    return;
  }
  try {
    let dimensions = state.url === url ? state.dimensions : null;
    if(!dimensions) {
      state.element.textContent = '正在读取图片尺寸…';
      dimensions = await loadDimensions(url);
      if(version !== state.version) return;
      state.url = url;state.dimensions = dimensions;
    }
    const values = Object.fromEntries(node.widgets.map(widget => [widget.name,widget.value]));
    const size = imageGeometry(...dimensions,values);
    state.element.textContent = `原图 ${dimensions[0]}×${dimensions[1]}\n` +
      `等比缩放 ${size.contentWidth}×${size.contentHeight}\n` +
      `生成画布 ${size.width}×${size.height} · ${size.padded ? '边缘补齐' : '无补边'}`;
  } catch(error) {
    if(version === state.version) state.element.textContent = error.message;
  }
  node.setDirtyCanvas(true,true);
}

function wrapAfter(nodeType, name, after) {
  const original = nodeType.prototype[name];
  nodeType.prototype[name] = function(...args) {
    const result = original?.apply(this,args);
    after.call(this,...args);
    return result;
  };
}

app.registerExtension({
  name: 'AIGC.H3ImageSize',
  async beforeRegisterNodeDef(nodeType,nodeData) {
    if(nodeData.name === 'LoadImage') {
      wrapAfter(nodeType,'onNodeCreated',function() {
        const widget = this.widgets.find(widget => widget.name === 'image');
        if(!widget) return;
        const original = widget.callback;
        const source = this;
        widget.callback = function(...args) {
          const result = original?.apply(this,args);
          for(const linkId of source.outputs?.[0]?.links || []) {
            const link = source.graph?.links?.get?.(linkId) || source.graph?.links?.[linkId];
            const target = source.graph?.getNodeById(link?.target_id);
            if(target?.type === 'AIGCH3ImageResize') {
              target.aigcSizePreview.url = null;
              queueMicrotask(() => refreshSizePreview(target));
            }
          }
          return result;
        };
      });
      return;
    }
    if(nodeData.name !== 'AIGCH3ImageResize') return;
    wrapAfter(nodeType,'onNodeCreated',function() {
      const element = document.createElement('div');
      element.style.cssText = 'white-space:pre-line;font:13px/1.6 sans-serif;padding:4px 8px;color:var(--input-text,#ddd);pointer-events:none';
      element.textContent = '连接首帧图片后显示尺寸。';
      this.aigcSizePreview = { element, version: 0, url: null, dimensions: null };
      const labels = { mode:'尺寸模式', longest_edge:'目标最长边', scale:'缩放倍数', width:'手动宽度', height:'手动高度' };
      for(const widget of this.widgets) {
        if(!labels[widget.name]) continue;
        widget.label = labels[widget.name];
        const original = widget.callback;
        const node = this;
        widget.callback = function(...args) {
          const result = original?.apply(this,args);
          refreshSizePreview(node);
          return result;
        };
      }
      const preview = this.addDOMWidget('size_preview','AIGCH3SizePreview',element,{ serialize:false });
      preview.computeSize = () => [this.size[0],76];
      this.setSize([430,Math.max(this.size[1],280)]);
    });
    for(const name of ['onAdded','onConfigure','onConnectionsChange'])
      wrapAfter(nodeType,name,function() { queueMicrotask(() => refreshSizePreview(this)); });
    wrapAfter(nodeType,'onExecuted',function(message) {
      if(message.text?.[0]) this.aigcSizePreview.element.textContent = message.text[0];
    });
    wrapAfter(nodeType,'onRemoved',function() { ++this.aigcSizePreview.version; });
  },
});
