import test from 'node:test';
import assert from 'node:assert/strict';
import { todayTotals, localDay, todayLabel, timeKeyForWork } from '../src/today.ts';
import { workItems } from '../src/model.ts';
import type { Entry, Snapshot } from '../src/model.ts';

const now = new Date(2026,9,30,14).getTime();
const stamp = (hour: number, minute=0) => new Date(2026,9,30,hour,minute).toISOString();
const entry = (id: string|number, start=stamp(9), stop: string|null=stamp(10), extra: Partial<Entry> = {}): Entry => ({
  id, workspace_id:1, project_id:6, task_id:31, description:'Work', start, stop, duration:1, ...extra,
});
const snapshot = (...entries: Entry[]): Snapshot => ({
  state:{tracking:false,entry_id:null,start_time:null,workspace_id:1,project_id:null,task_id:null,description:''},
  projects:[{id:6,workspace_id:1,name:'Studio'},{id:7,workspace_id:1,name:'Research'}],
  tasks:[{id:31,workspace_id:1,project_id:6,name:'Navigation'}],entries,
  project_context:{account:'fixture',workspace_id:1}, task_capability:'supported',pending_count:0,warning:'',
  today_date:localDay(now),today_fetched_at:now/1000,
});

test('task totals combine different descriptions while activities and projects remain separate', () => {
  const s=snapshot(entry(1),entry(2,stamp(10),stamp(10,30),{description:'Other detail'}),
    entry(3,stamp(11),stamp(11,20),{task_id:null,description:'Navigation'}),
    entry(4,stamp(12),stamp(12,10),{project_id:7,task_id:null,description:'Navigation'}));
  const totals=todayTotals(s,now);
  assert.equal(totals.seconds,7200);
  assert.deepEqual([...totals.groups.values()].map(g=>g.seconds),[5400,1200,600]);
});
test('the running interval contributes once even when a cloud copy exists', () => {
  const s=snapshot(entry(1),entry(2,stamp(13),null));
  Object.assign(s.state,{tracking:true,entry_id:2,start_time:stamp(13),project_id:6,task_id:31,description:'Live'});
  const totals=todayTotals(s,now);
  assert.equal(totals.seconds,7200);
  assert.equal([...totals.groups.values()][0].rows.length,2);
  assert.equal(totals.incomplete,false);
});
test('pending edits override their remote ID even when the start and assignment change', () => {
  const s=snapshot(entry(1),entry('pending:one',stamp(10),stamp(10,30),{
    _offline:true,_remote_entry_id:1,project_id:7,task_id:null,description:'Corrected',
  }));
  const totals=todayTotals(s,now);
  assert.equal(totals.seconds,1800);assert.equal(totals.groups.size,1);
  assert.equal([...totals.groups.values()][0].project_id,7);
});
test('a confirmed pending create cannot double count its one matching cloud interval', () => {
  const s=snapshot(entry(1),entry('pending:one',stamp(9),stamp(10),{_offline:true}));
  assert.equal(todayTotals(s,now).seconds,3600);
});
test('two real cloud IDs with identical timestamps are never collapsed', () => {
  assert.equal(todayTotals(snapshot(entry(1),entry(2)),now).seconds,7200);
});
test('an offline timer with a reused start does not erase a completed interval', () => {
  const s=snapshot(entry(1));
  Object.assign(s.state,{tracking:true,start_time:stamp(9),project_id:6,task_id:31,description:'Work'});
  assert.equal(todayTotals(s,now).seconds,21600);
});
test('overnight intervals and live work count only the part after local midnight', () => {
  const yesterday=new Date(2026,9,29,23,30).toISOString();
  const s=snapshot(entry(1,yesterday,stamp(0,30)));
  Object.assign(s.state,{tracking:true,start_time:yesterday,project_id:6,task_id:31,description:'Live'});
  assert.equal(todayTotals(s,new Date(2026,9,30,1).getTime()).seconds,5400);
});
test('foreign workspaces and old days do not inflate today', () => {
  const yesterday=new Date(2026,9,29,12).toISOString();
  const s=snapshot(entry(1),entry(2,stamp(9),stamp(10),{workspace_id:2}),entry(3,yesterday,yesterday));
  assert.equal(todayTotals(s,now).seconds,3600);
});
test('an unmatched open pending interval is incomplete, never an ever-growing total', () => {
  const s=snapshot(entry('pending:old',stamp(9),null,{_offline:true,duration:-1}));
  const totals=todayTotals(s,now);
  assert.equal(totals.seconds,0);assert.equal(totals.incomplete,true);
  assert.equal(todayLabel(totals,0),'—');
});
test('partial cache and rollover never masquerade as a loaded zero', () => {
  const s=snapshot(entry(1));s.today_fetched_at=null;
  assert.equal(todayLabel(todayTotals(s,now),3600),'≈1h');
  assert.equal(todayLabel(todayTotals(s,now),0),'—');
  assert.equal(todayTotals(snapshot(),new Date(2026,9,31,1).getTime()).loaded,false);
});
test('canonical server activity keys join spelling variants without mixing tasks', () => {
  const s=snapshot(entry(1,stamp(9),stamp(10),{task_id:null,description:'BUILD',_activity_key:'canonical'}),
    entry(2,stamp(11),stamp(12),{task_id:null,description:' build ',_activity_key:'canonical'}));
  const totals=todayTotals(s,now);
  assert.equal(totals.groups.size,1);
  assert.equal(totals.groups.get(timeKeyForWork(s,{key:'activity',project_id:6,task_id:null,description:'Build',activity_key:'canonical',label:'Build',recent:1}))?.seconds,7200);
});
test('DST days use local midnights rather than a fixed 24-hour subtraction', () => {
  const old=process.env.TZ;process.env.TZ='Europe/Bucharest';
  try {
    for (const [month,day,hours] of [[2,29,23],[9,25,25]]) {
      const start=new Date(2026,month,day).getTime(),end=new Date(2026,month,day+1).getTime();
      assert.equal((end-start)/3600000,hours);
      const s=snapshot(entry(1,new Date(start).toISOString(),new Date(end).toISOString()));
      assert.equal(todayTotals(s,end-1).seconds,hours*3600-1);
    }
  } finally { if(old===undefined)delete process.env.TZ;else process.env.TZ=old; }
});
test('canonical spelling variants occupy one work row and show the daily total once', () => {
  const s=snapshot();
  s.activities=[{id:6,name:'Studio',last_used:2,activities:[
    {key:'canonical',description:'Build',last_used:1,count:1},
    {key:'canonical',description:'BUILD',last_used:2,count:1},
  ]}];
  const recent=workItems(s).filter(w=>!w.task);
  assert.equal(recent.length,1);assert.equal(recent[0].description,'BUILD');
});
test('legacy completed duration-only entries count without pretending to be running', () => {
  const totals=todayTotals(snapshot(entry(1,stamp(9),null,{duration:3600})),now);
  assert.equal(totals.seconds,3600);assert.equal(totals.incomplete,false);
});
