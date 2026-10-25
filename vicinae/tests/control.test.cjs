const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');
const { transformSync } = require('esbuild');
const filename = path.resolve(__dirname, '../src/control.ts');
const compiled = new Module(filename, module);
compiled.filename = filename;
compiled.paths = module.paths;
compiled._compile(transformSync(fs.readFileSync(filename, 'utf8'), { loader: 'ts', format: 'cjs' }).code, filename);
const { control, elapsed } = compiled.exports;

test('descriptions and paths are passed as literal arguments without a shell', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'timer cli '));
  try {
    const cli = path.join(dir, 'timer command');
    fs.writeFileSync(cli, '#!/usr/bin/env node\nconsole.log(JSON.stringify({ok:true,result:{args:process.argv.slice(2)}}));\n', { mode: 0o700 });
    const description = 'literal $(touch SHOULD_NOT_EXIST) "quotes"; & end';
    const result = await control(cli, 'start', { changes: { description } });
    assert.deepEqual(result.args, ['control', 'start', JSON.stringify({ changes: { description } })]);
    assert.equal(fs.existsSync('SHOULD_NOT_EXIST'), false);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('a structured backend error is shown even when the CLI exits nonzero', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'timer-error-'));
  try {
    const cli = path.join(dir, 'timer');
    fs.writeFileSync(cli, '#!/usr/bin/env node\nconsole.log(JSON.stringify({ok:false,error:"Tray is not running"}));process.exitCode=2;\n', { mode: 0o700 });
    await assert.rejects(control(cli, 'snapshot'), /Tray is not running/);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('elapsed handles completed entries and invalid dates', () => {
  assert.equal(elapsed('2026-10-25T10:00:00Z', '2026-10-25T11:02:03Z'), '1:02:03');
  assert.equal(elapsed('invalid'), '0:00:00');
});

test('a user edit waits for status polling instead of being dropped', async () => {
  const { queueCommands } = compiled.exports;
  const order = [];
  let finishPoll;
  const poll = new Promise(resolve => { finishPoll = resolve; });
  const send = queueCommands(async action => {
    order.push(action);
    if (action === 'snapshot') await poll;
    return { action };
  });
  const first = send('snapshot', {});
  const edit = send('edit-current', {});
  assert.equal(await send('snapshot', {}), undefined);
  finishPoll();
  await first;
  assert.deepEqual(await edit, { action: 'edit-current' });
  assert.deepEqual(order, ['snapshot', 'edit-current']);
});

test('historical entry forms offer projects from that entry workspace', () => {
  const { projectChoices } = compiled.exports;
  const snapshot = { state: { workspace_id: 1, project_id: 7 }, projects: [
    {id:7, workspace_id:1, active:true, name:'Current'},
    {id:8, workspace_id:2, active:true, name:'Other'},
    {id:9, workspace_id:2, active:false, name:'Archived'},
  ]};
  assert.deepEqual(projectChoices(snapshot, {workspace_id:2, project_id:9}).map(p => p.id), [8,9]);
});

test('editing a pending description preserves a project that was not supplied', () => {
  const { timerChanges } = compiled.exports;
  assert.deepEqual(timerChanges({description:'Before'}, {description:'After', project:'none'}), {description:'After'});
});

test('form edits send an explicitly changed project but omit unchanged values', () => {
  const { timerChanges } = compiled.exports;
  const entry = {description:'Work', project_id:7};
  assert.deepEqual(timerChanges(entry, {description:'Work', project:'7'}), {});
  assert.deepEqual(timerChanges(entry, {description:'Work', project:'none'}), {project_id:null});
  assert.deepEqual(timerChanges(entry, {description:'Work', project:'8'}), {project_id:'8'});
  assert.deepEqual(timerChanges(entry, {description:'Renamed'}), {description:'Renamed'});
});

test('new projects preserve the displayed account context without changing the timer', () => {
  const { projectCommand } = compiled.exports;
  const context = {workspace_id:12, account:'account-fingerprint'};
  const command = projectCommand(context, '  Client work  ', '#AABBCC');
  assert.deepEqual(command, {action:'project-create', payload:{context, name:'Client work', color:'#aabbcc'}});
  context.workspace_id = 99;
  assert.equal(command.payload.context.workspace_id, 12);
  assert.equal('changes' in command.payload, false);
});

test('project updates retain the original metadata for stale-edit protection', () => {
  const { projectCommand } = compiled.exports;
  const project = {id:7, workspace_id:12, name:'Before', color:'#112233', active:true};
  const command = projectCommand({workspace_id:12, account:'account-fingerprint'}, 'After', '#445566', project);
  assert.deepEqual(command.payload.project, {id:7, workspace_id:12, name:'Before', color:'#112233'});
  assert.equal(command.action, 'project-update');
  assert.equal(command.payload.name, 'After');
  project.name = 'External edit';
  assert.equal(command.payload.project.name, 'Before');
});

test('invalid project fields are rejected before a mutation command is constructed', () => {
  const { projectCommand } = compiled.exports;
  const context = {workspace_id:12, account:'account-fingerprint'};
  assert.throws(() => projectCommand(context, '   ', '#123456'), /name/i);
  assert.throws(() => projectCommand(context, 'x'.repeat(201), '#123456'), /200/);
  for (const color of ['red', '#abc', '#12345g', '123456']) {
    assert.throws(() => projectCommand(context, 'Valid', color), /#RRGGBB/);
  }
});
