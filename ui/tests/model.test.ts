import test from 'node:test'
import assert from 'node:assert/strict'
import { afterOwnStop, commandFor, contextMatches, draftFor, localTime, running, workItems, workGroups, isCurrentEntry, rebaseSyncedDraft, samePending } from '../src/model.ts'
import type { Snapshot } from '../src/model.ts'

const sample = (): Snapshot => ({
  state: {tracking: true, entry_id: 11, start_time: '2026-10-29T12:00:38+00:00', workspace_id: 1, project_id: 6, task_id: 31, description: 'Original'},
  projects: [{id: 6, workspace_id: 1, name: 'Studio', active: true}, {id: 7, workspace_id: 1, name: 'Research', active: true}],
  tasks: [{id: 31, workspace_id: 1, project_id: 6, name: 'Saved task', active: true}],
  entries: [], activities: [], project_context: {account: 'fixture', workspace_id: 1}, task_capability: 'supported', pending_count: 0, warning: '',
})
test('the tree groups by project ID and keeps empty projects available', () => {
  const data = sample()
  data.projects[1].name = 'Studio'
  data.tasks.push({...data.tasks[0],id:32,project_id:7})
  data.projects.push({id:8,workspace_id:1,name:'Empty project'})
  data.activities = [{id:null,name:'No project',last_used:1,activities:[{description:'Admin',last_used:1,count:1}]}]
  const groups = workGroups(data)
  assert.deepEqual(groups.map(g=>[g.id,g.works.map(w=>w.key)]), [
    [6,['task:31']], [7,['task:32']], [8,[]], [null,['activity:null:Admin']],
  ])
})
test('tree search keeps the parent and reveals matches without mixing projects', () => {
  const groups = workGroups(sample(),false,'saved','6')
  assert.equal(groups.length,1)
  assert.equal(groups[0].name,'Studio')
  assert.deepEqual(groups[0].works.map(w=>w.key),['task:31'])
  assert.equal(workGroups(sample(),false,'saved','7').length,0)
})
test('the running interval appears even before its activity is cached', () => {
  const data=sample()
  data.state.task_id=null
  const work=workGroups(data)[0].works.find(w=>w.key==='current')!
  assert.ok(running(data,work))
  assert.equal(draftFor(data,'current',work).snapshot.state.entry_id,11)
  assert.ok(workGroups(data,true).every(g=>g.works.every(w=>!!w.task)))
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

const pendingFixture = () => {
  const data=sample()
  data.state.entry_id=null
  const pending={id:'pending:one',workspace_id:1,project_id:6,task_id:31,description:'Original',start:data.state.start_time!,stop:null,duration:-1,_offline:true,_pending_ids:['one'],_pending_kind:'open'}
  data.current_pending=pending
  data.entries=[pending]
  return {data,pending}
}
test('a running pending Today row opens the current timer editor', () => {
  const {data,pending}=pendingFixture()
  assert.equal(isCurrentEntry(data,pending),true)
  assert.equal(isCurrentEntry(data,{...pending,_pending_ids:['different']}),false)
})
test('an offline running draft follows its exact sync confirmation and keeps all edits', () => {
  const {data,pending}=pendingFixture(), next=sample()
  const draft=draftFor(data,'current')
  draft.description='Typed while offline'
  draft.start=localTime('2026-10-29T11:30:00Z')
  const entry={...pending,id:11,_offline:false}
  next.draft_resolution={source:pending,entry,context:data.project_context}
  const resolved=rebaseSyncedDraft(draft,next)
  assert.equal(resolved.description,draft.description)
  assert.equal(resolved.start,draft.start)
  assert.equal((commandFor(resolved).payload.expected as Snapshot['state']).entry_id,11)
  assert.deepEqual(commandFor(resolved).payload.changes,{description:draft.description,start:'2026-10-29T11:30:00.000Z'})
})
test('a completed pending draft follows sync without copying or cancelling its text', () => {
  const {data,pending}=pendingFixture(), next=sample()
  const completed={...pending,stop:'2026-10-29T13:00:00Z',duration:3562,_pending_kind:'create'}
  const draft=draftFor(data,'entry',undefined,completed)
  draft.description='Edited before sync'
  const entry={...completed,id:22,_offline:false}
  next.draft_resolution={source:completed,entry,context:data.project_context}
  const resolved=rebaseSyncedDraft(draft,next)
  assert.equal(resolved.description,draft.description)
  assert.equal((commandFor(resolved).payload.entry as {id:number}).id,22)
  assert.deepEqual(commandFor(resolved).payload.changes,{description:draft.description})
})
test('sync confirmation cannot redirect a draft to another account or interval', () => {
  const {data,pending}=pendingFixture(), next=sample()
  const draft=draftFor(data,'current'), entry={...pending,id:11,_offline:false}
  next.draft_resolution={source:{...pending,_pending_ids:['other']},entry,context:data.project_context}
  assert.equal(rebaseSyncedDraft(draft,next),draft)
  next.draft_resolution.source=pending
  next.state.entry_id=99
  assert.equal(rebaseSyncedDraft(draft,next),draft)
  next.state.entry_id=11
  next.project_context.account='other-account'
  assert.equal(rebaseSyncedDraft(draft,next),draft)
})
test('Escape-style hiding and subsequent sync leave a pending project correction intact', () => {
  const {data,pending}=pendingFixture(), next=sample()
  const draft=draftFor(data,'current')
  draft.project='7'; draft.task='none'
  next.draft_resolution={source:pending,entry:{...pending,id:11,_offline:false},context:data.project_context}
  const resolved=rebaseSyncedDraft(draft,next)
  assert.equal(resolved.project,'7')
  assert.equal(resolved.task,'none')
  assert.deepEqual(commandFor(resolved).payload.changes,{project_id:7,task_id:null})
})
test('Stop can retain a pending Today draft even if sync finishes before its reply', () => {
  const {data,pending}=pendingFixture(), next=sample()
  const draft=draftFor(data,'current')
  draft.description='Kept through Stop and sync'
  const entry={...pending,id:11,_offline:false,stop:'2026-10-29T13:00:00Z',duration:3562}
  next.state.tracking=false
  next.entries=[]
  next.stopped_entry={...entry,id:pending.id,_offline:true}
  next.draft_resolution={source:pending,entry,context:data.project_context}
  const stopped=afterOwnStop(draft,next)
  assert.equal(stopped.mode,'entry')
  assert.equal(stopped.description,draft.description)
  assert.equal((commandFor(stopped).payload.entry as {id:number}).id,11)
})
test('queue identity comparisons do not confuse different ID arrays', () => {
  const {pending}=pendingFixture()
  assert.equal(samePending({...pending,_pending_ids:['a:b','c']},{...pending,_pending_ids:['a','b:c']}),false)
})

test('a New timer draft keeps its proposed work through current-timer sync', () => {
  const {data,pending}=pendingFixture(), next=sample()
  const draft=draftFor(data,'new')
  draft.description='Next interval description'; draft.project='7'; draft.task='none'
  next.draft_resolution={source:pending,entry:{...pending,id:11,_offline:false},context:data.project_context}
  const resolved=rebaseSyncedDraft(draft,next)
  assert.equal(resolved.description,draft.description)
  assert.equal(resolved.project,'7')
  assert.equal(resolved.task,'none')
  assert.equal((commandFor(resolved).payload.expected as Snapshot['state']).entry_id,11)
  assert.deepEqual(commandFor(resolved).payload.changes,{description:draft.description,project_id:7,task_id:null})
})

test('duration-only entry drafts show their derived end and preserve the original selector', () => {
  const s=sample(); const entry={id:42,workspace_id:1,project_id:6,task_id:null,description:'Closed',start:'2026-10-29T09:00:00Z',stop:null,duration:3600};
  const draft=draftFor(s,'entry',undefined,entry);
  assert.equal(draft.stop,localTime('2026-10-29T10:00:00Z'));
  draft.start=localTime('2026-10-29T09:05:00Z');
  assert.deepEqual(commandFor(draft).payload.changes,{start:'2026-10-29T09:05:00.000Z'});
  assert.deepEqual(commandFor(draft).payload.entry,entry);
});
