const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../static/open-workflow.js'), 'utf8');
const elements = new Map();
const context = vm.createContext({
  URLSearchParams,
  location: { search: '' },
  document: { getElementById: id => {
    if (!elements.has(id)) elements.set(id, { removeAttribute() {} });
    return elements.get(id);
  } },
});
vm.runInContext(source, context);
const { workflowForFrame, isComfyReady, restoreAutogrowLinks, verifyApiGraph } = vm.runInContext(
  '({ workflowForFrame, isComfyReady, restoreAutogrowLinks, verifyApiGraph })', context,
);

for (const [name, nodeCount] of [
  ['h3-hmnsfw-i2v', 17], ['h3-hmnsfw-turbo8', 19],
  ['h3-hmnsfw-lightx4', 19], ['h3-hmnsfw-turbo4-768p', 19],
]) test(`${name} one-click opening requests its registered native canvas`, async () => {
  const requested = [];
  const workflow = JSON.parse(fs.readFileSync(path.join(__dirname,
    `../docs/h3-extended/workflows/${name}.workflow.json`), 'utf8'));
  context.location.search = `?template=${name}`;
  context.fetch = async url => {
    requested.push(url);
    return { ok: true, json: async () => workflow };
  };
  try {
    const data = await vm.runInContext('getWorkflow()', context);
    assert.deepEqual(requested, [`/api/comfy/templates/${name}?download=false&format=native`]);
    assert.equal(data.engine, 'h3');
    assert.equal(data.format, 'native');
    assert.equal(data.filename, `${name}.workflow.json`);
    assert.equal(data.workflow.nodes.length, nodeCount);
  } finally {
    context.location.search = '';
    delete context.fetch;
  }
});

test('API links use the iframe Array constructor required by ComfyUI', () => {
  const frame = vm.createContext({});
  const frameWindow = vm.runInContext('({ JSON, Array })', frame);
  const workflow = { '1': { class_type: 'BasicGuider', inputs: { model: ['2', 0] } } };
  assert.equal(workflow['1'].inputs.model instanceof frameWindow.Array, false);
  const imported = workflowForFrame(frameWindow, workflow);
  assert.equal(imported['1'].inputs.model instanceof frameWindow.Array, true);
  assert.equal(JSON.stringify(imported), JSON.stringify(workflow));
  imported['1'].inputs.model[0] = 'changed';
  assert.equal(workflow['1'].inputs.model[0], '2');
});

test('native workflow node and link arrays also belong to the iframe', () => {
  const frameWindow = vm.runInNewContext('({ JSON, Array })');
  const imported = workflowForFrame(frameWindow, { nodes: [], links: [[1, 1, 0, 2, 0, 'MODEL']] });
  assert.equal(imported.nodes instanceof frameWindow.Array, true);
  assert.equal(imported.links[0] instanceof frameWindow.Array, true);
});

test('graph setup alone is insufficient while startup restoration is running', () => {
  const app = {
    isGraphReady: true, vueAppReady: true, positionConversion: {}, configuringGraph: false,
    extensionManager: { spinner: true }, loadApiJson() {}, loadGraphData() {},
  };
  const document = { getElementById: () => null };
  assert.equal(isComfyReady(app, document), false);
  app.extensionManager.spinner = false;
  assert.equal(isComfyReady(app, { getElementById: () => ({}) }), false);
  assert.equal(isComfyReady(app, document), true);
  app.configuringGraph = true;
  assert.equal(isComfyReady(app, document), false);
});

function graphFixture() {
  const workflow = {
    '1': { class_type: 'UNETLoader', inputs: { unet_name: 'example.safetensors' } },
    '2': { class_type: 'BasicGuider', inputs: { model: ['1', 0] } },
  };
  const link = { origin_id: 1, origin_slot: 0, target_id: 2, target_slot: 0 };
  const nodes = [
    { id: 1, type: 'UNETLoader', inputs: [] },
    { id: '2', type: 'BasicGuider', inputs: [{ name: 'model', link: 7 }] },
  ];
  return { workflow, link, nodes, app: { rootGraph: { _nodes: nodes, links: new Map([[7, link]]) } } };
}

test('connected nodes pass with both numeric and string node IDs', () => {
  const { app, workflow } = graphFixture();
  assert.deepEqual({ ...verifyApiGraph(app, workflow) }, { nodes: 2, links: 1 });
  app.rootGraph.links = { 7: app.rootGraph.links.get(7) };
  assert.equal(verifyApiGraph(app, workflow).links, 1);
});

test('disconnected nodes fail even when all node types are present', () => {
  const { app, workflow, nodes } = graphFixture();
  nodes[1].inputs[0].link = null;
  assert.throws(() => verifyApiGraph(app, workflow), /model 输入未正确连接/);
});

test('wrong source, output slot or target slot cannot pass the connection check', () => {
  for (const [field, value] of [['origin_id', 2], ['origin_slot', 1], ['target_id', 1], ['target_slot', 1]]) {
    const { app, workflow, link } = graphFixture();
    link[field] = value;
    assert.throws(() => verifyApiGraph(app, workflow), /未正确连接/);
  }
});

test('a restored graph with an extra or substituted node is rejected', () => {
  const { app, workflow, nodes } = graphFixture();
  nodes.push({ id: 3, type: 'LoadImage' });
  assert.throws(() => verifyApiGraph(app, workflow), /节点数量/);
  nodes.pop();
  nodes[1].type = 'DifferentNode';
  assert.throws(() => verifyApiGraph(app, workflow), /实际为 DifferentNode/);
});

test('legacy numbered reference sockets are restored from the declared autogrow group', () => {
  const { app, workflow, nodes } = graphFixture();
  const name = 'refs_image.ref_image_1';
  workflow['2'].inputs = { [name]: ['1', 0] };
  nodes[1].inputs = [{ name: 'refs_image.ref_image_0', link: null }];
  nodes[1].comfyDynamic = { autogrow: {
    refs_image: { prefix: 'ref_image_', max: 16, inputSpecs: [{ type: 'IMAGE' }] },
  } };
  nodes[1].addInput = (name, type) => nodes[1].inputs.push({ name, type, link: null });
  nodes[0].connect = (slot, target, inputIndex) => {
    target.inputs[inputIndex].link = 8;
    app.rootGraph.links.set(8, { origin_id: 1, origin_slot: slot, target_id: '2', target_slot: inputIndex });
  };
  restoreAutogrowLinks(app, workflow);
  assert.equal(nodes[1].inputs[1].name, name);
  assert.equal(verifyApiGraph(app, workflow).links, 1);
  restoreAutogrowLinks(app, workflow);
  assert.equal(nodes[1].inputs.length, 2);
});

test('undeclared or out-of-range reference sockets are not synthesized', () => {
  for (const name of ['other.ref_image_1', 'refs_image.ref_image_16', 'refs_image.ref_image_']) {
    const { app, workflow, nodes } = graphFixture();
    workflow['2'].inputs = { [name]: ['1', 0] };
    nodes[1].comfyDynamic = { autogrow: {
      refs_image: { prefix: 'ref_image_', max: 16, inputSpecs: [{ type: 'IMAGE' }] },
    } };
    nodes[1].addInput = () => assert.fail('Invalid socket should not be created');
    restoreAutogrowLinks(app, workflow);
    assert.throws(() => verifyApiGraph(app, workflow), /未正确连接/);
  }
});
