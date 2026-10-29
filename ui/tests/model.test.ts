import test from 'node:test'
import assert from 'node:assert/strict'
import { afterOwnStop, commandFor, contextMatches, draftFor, localTime, workItems } from '../src/model.ts'
import type { Snapshot } from '../src/model.ts'

const sample = (): Snapshot => ({
  state: {tracking: true, entry_id: 11, start_time: '2026-10-29T12:00:38+00:00', workspace_id: 1, project_id: 6, task_id: 31, description: 'Original'},
  projects: [{id: 6, workspace_id: 1, name: 'Studio', active: true}, {id: 7, workspace_id: 1, name: 'Research', active: true}],
  tasks: [{id: 31, workspace_id: 1, project_id: 6, name: 'Saved task', active: true}],
  entries: [], activities: [], project_context: {account: 'fixture', workspace_id: 1}, task_capability: 'supported', pending_count: 0, warning: '',
})
test('description edits keep the exact interval, task and start seconds', () => {
  const data = sample()
  const draft = draftFor(data, 'current')
  draft.description = 'Corrected'
  const command = commandFor(draft)
  assert.equal(command.action, 'edit-current')
  assert.deepEqual(command.payload.changes, {description: 'Corrected'})
  assert.deepEqual(command.payload.expected, data.state)
})
test('changing a project explicitly clears the old task', () => {
  const draft = draftFor(sample(), 'current')
  draft.project = '7'
  draft.task = 'none'
  assert.deepEqual(commandFor(draft).payload.changes, {project_id: 7, task_id: null})
})
test('polling cannot replace a frozen draft target or form values', () => {
  const data = sample()
  const draft = draftFor(data, 'new')
  draft.description = 'New work'
  data.state.entry_id = 22
  assert.equal((commandFor(draft).payload.expected as Snapshot['state']).entry_id, 11)
  assert.equal(draft.description, 'New work')
})
test('an archived task can keep its assignment during a description correction', () => {
  const data = sample()
  data.tasks[0].active = false
  const draft = draftFor(data, 'current')
  draft.description = 'Corrected'
  assert.deepEqual(commandFor(draft).payload.changes, {description: 'Corrected'})
})
test('creating reusable task metadata never starts an interval', () => {
  const draft = draftFor(sample(), 'new-task')
  draft.description = 'Reusable'
  draft.project = '6'
  assert.deepEqual(commandFor(draft), {action: 'task-create', payload: {name: 'Reusable', project_id: 6, context: draft.snapshot.project_context}})
})
test('tasks from a different workspace do not enter the work list', () => {
  const data = sample()
  data.tasks.push({...data.tasks[0], id: 32, workspace_id: 2, name: 'Other account task'})
  assert.deepEqual(workItems(data).map(row => row.label), ['Saved task'])
})
test('a cached task in an archived project cannot be offered as new work', () => {
  const data=sample()
  data.projects[0].active=false
  assert.deepEqual(workItems(data),[])
  assert.deepEqual(workItems(data,true),[])
})
test('account comparison does not depend on JSON property order', () => {
  const first = sample(), second = sample()
  second.project_context = {workspace_id: 1, account: 'fixture'}
  assert.equal(contextMatches(first, second), true)
  second.project_context.account = 'changed'
  assert.equal(contextMatches(first, second), false)
})
test('invalid legacy timestamps are displayable without crashing the form', () => {
  assert.equal(localTime('broken'), '')
})
test('an explicit Stop keeps edits attached to that completed interval', () => {
  const original = sample(), result = sample()
  result.state.tracking = false
  result.entries = [{id:11,workspace_id:1,project_id:6,task_id:31,description:'Original',start:original.state.start_time!,stop:'2026-10-29T13:00:00Z',duration:3562}]
  const draft = draftFor(original,'current')
  draft.description = 'Draft kept after stopping'
  const completed = afterOwnStop(draft,result)
  assert.equal(completed.mode,'entry')
  assert.equal(completed.description,draft.description)
  const command = commandFor(completed)
  assert.equal(command.action,'edit-entry')
  assert.equal((command.payload.entry as {id:number}).id,11)
  assert.deepEqual(command.payload.changes,{description:draft.description})
})
test('a stopped draft never follows another account or an ambiguous interval', () => {
  const draft = draftFor(sample(),'current'), result = sample()
  result.state.tracking = false
  result.project_context.account = 'other'
  assert.equal(afterOwnStop(draft,result),draft)
  result.project_context.account = 'fixture'
  assert.equal(afterOwnStop(draft,result),draft)
})
