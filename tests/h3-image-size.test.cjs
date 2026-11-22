const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const pictures = [];
const extensions = [];
const context = vm.createContext({
  URLSearchParams, queueMicrotask,
  app: { registerExtension(extension) { extensions.push(extension); } },
  api: { apiURL: url => url },
  Image: class { constructor() { pictures.push(this); } },
});
const source = fs.readFileSync(path.join(__dirname,'../scripts/h3_image_size.js'),'utf8');
vm.runInContext(source.replace(/^import .*;\n/gm,'').replace(/^export /gm,''),context);
const { imageGeometry, refreshSizePreview } = vm.runInContext('({imageGeometry,refreshSizePreview})',context);

test('browser previews agree with expected content and canvas geometry', () => {
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname,'h3-image-size-cases.json'),'utf8'));
  for(const item of cases) {
    const size = imageGeometry(...item.original,item.values);
    assert.deepEqual([size.contentWidth,size.contentHeight],item.content,item.name);
    assert.deepEqual([size.width,size.height],item.canvas,item.name);
  }
});

test('invalid sizes display an error instead of an unusable preview', () => {
  for(const values of [{mode:'缩放倍数',scale:0},{mode:'缩放倍数',scale:NaN},
    {mode:'缩放倍数',scale:8},{mode:'手动宽高',width:513},{mode:'missing'}])
    assert.throws(() => imageGeometry(1920,1080,values));
});

test('image changes during decoding cannot restore stale dimensions', async () => {
  let filename = 'first.png';
  const node = {
    aigcSizePreview: { element:{}, version:0, url:null },
    getInputNode() { return { type:'LoadImage',widgets:[{name:'image',value:filename}] }; },
    widgets:[{name:'mode',value:'最长边'},{name:'longest_edge',value:512}],
    setDirtyCanvas() {},
  };
  const first = refreshSizePreview(node);
  const oldPicture = pictures.at(-1);
  filename = 'folder/portrait.png [input]';
  const second = refreshSizePreview(node);
  const newPicture = pictures.at(-1);
  assert.match(newPicture.src,/filename=portrait.png&subfolder=folder&type=input/);
  Object.assign(newPicture,{ naturalWidth:1080,naturalHeight:1920 });newPicture.onload();
  await second;
  Object.assign(oldPicture,{ naturalWidth:1920,naturalHeight:1080 });oldPicture.onload();
  await first;
  assert.match(node.aigcSizePreview.element.textContent,/原图 1080×1920/);
  assert.match(node.aigcSizePreview.element.textContent,/生成画布 288×512/);
  const count = pictures.length;
  node.widgets[1].value = 768;
  await refreshSizePreview(node);
  assert.equal(pictures.length,count);
  assert.match(node.aigcSizePreview.element.textContent,/生成画布 448×768 · 边缘补齐/);
});
