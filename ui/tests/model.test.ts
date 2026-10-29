import test from 'node:test'
import assert from 'node:assert/strict'
import { afterOwnStop, commandFor, contextMatches, draftFor, localTime, running, workItems } from '../src/model.ts'
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

test('a cloud timer with a null description can render its current activity', () => {
  const data = sample()
  data.state.description = null
  data.state.task_id = null
  data.activities = [{id:6,name:'Studio',last_used:1,activities:[{description:'',last_used:1,count:1}]}]
  assert.equal(running(data,workItems(data).find(w => !w.task)! ),true)
})
test('stopping retains an unsaved start correction on the completed interval', () => {
  const data=sample(), result=sample()
  const draft=draftFor(data,'current')
  draft.start=localTime('2026-10-29T11:30:12Z')
  result.state.tracking=false
  result.stopped_entry={id:11,workspace_id:1,project_id:6,task_id:31,description:'Original',start:data.state.start_time!,stop:'2026-10-29T13:00:00Z',duration:3562}
  const saved=afterOwnStop(draft,result)
  assert.equal(saved.start,draft.start)
  assert.deepEqual(commandFor(saved).payload.changes,{start:'2026-10-29T11:30:12.000Z'})
})
test('an explicit stopped interval survives the Today filter across midnight', () => {
  const data=sample(), result=sample()
  result.state.tracking=false
  result.entries=[]
  result.stopped_entry={id:11,workspace_id:1,project_id:6,task_id:31,description:'Original',start:data.state.start_time!,stop:'2026-10-30T01:00:00Z',duration:46762}
  const draft=draftFor(data,'current')
  draft.description='Kept overnight'
  assert.equal(afterOwnStop(draft,result).mode,'entry')
})
test('older pending intervals require explicit full review and show exact seconds', () => {
  const data=sample()
  const entry={id:11,workspace_id:1,project_id:6,task_id:31,description:'Old',start:data.state.start_time!,stop:'2026-10-29T13:00:17Z',duration:3579,_needs_full_review:true}
  const draft=draftFor(data,'entry',undefined,entry)
  assert.equal(draft.start,localTime(entry.start))
  assert.equal(draft.stop,localTime(entry.stop))
  assert.equal(draft.start.length,19)
  assert.throws(()=>commandFor(draft),/approve/)
  draft.reviewed=true
  assert.equal(commandFor(draft).payload.reviewed_interval,true)
  assert.deepEqual(commandFor(draft).payload.changes,{})
})

test('editing a running Today row uses the live timer instead of its stale cache', () => {
  const data=sample()
  const stale={id:11,workspace_id:1,project_id:6,task_id:31,description:'Old cached text',start:'2026-10-29T11:00:00Z',stop:null,duration:-1}
  const draft=draftFor(data,'current',undefined,stale)
  draft.description='Corrected text only'
  assert.equal(draft.start,localTime(data.state.start_time))
  assert.equal(draft.entry,undefined)
  assert.deepEqual(commandFor(draft).payload.changes,{description:'Corrected text only'})
  assert.equal(draftFor(data,'entry',undefined,stale).start,localTime(stale.start))
})
