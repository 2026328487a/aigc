import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';

app.registerExtension({
  name: 'AIGC.H3ReferenceUpload',
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if(nodeData.name !== 'AIGCLoadH3Reference') return;
    const original = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function() {
      original?.apply(this, arguments);
      const node = this;
      this.addWidget('button', '上传参考素材', null, () => {
        const input = document.createElement('input');
        input.type = 'file';input.accept = '.safetensors';
        input.onchange = async () => {
          const file = input.files?.[0];
          if(!file) return;
          try {
            const body = new FormData();body.append('image',file);body.append('overwrite','false');
            const response = await api.fetchApi('/upload/image',{method:'POST',body});
            if(!response.ok) throw new Error(`上传失败 (${response.status})`);
            const uploaded = await response.json();
            const value = [uploaded.subfolder,uploaded.name].filter(Boolean).join('/');
            const widget = node.widgets.find(widget => widget.name === 'reference');
            if(!widget.options.values.includes(value)) widget.options.values.push(value);
            widget.value = value;node.setDirtyCanvas(true,true);
          } catch(error) {app.ui.dialog.show(error.message);}
        };
        input.click();
      });
    };
  }
});
