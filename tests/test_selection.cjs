const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
function helpers(jobs, channel='') {
  const context = {state:{jobs, channel, selected:new Set(jobs.map(j=>j.id))},visible:()=>jobs};
  const names = ['active','editable','selectedFrom','visibleSelection','draftSelection','removable'];
  vm.runInNewContext(names.map(name=>source.split('\n').find(line=>line.startsWith('const '+name+' ='))).join('\n')+
    '\nthis.drafts = draftSelection; this.visibleSelected = visibleSelection; this.canRemove = removable;', context);
  return context;
}
test('selection count is scoped to rows currently visible', ()=>{
  const jobs=[{id:'visible'},{id:'hidden-a'},{id:'hidden-b'}];
  const h=helpers(jobs);
  h.visible=()=>[jobs[0]];
  h.state.selected=new Set(['hidden-a','hidden-b']);
  assert.equal(h.visibleSelected().length,0);
  h.state.selected.add('visible');
  assert.deepEqual(Array.from(h.visibleSelected(),j=>j.id),['visible']);
});
test('mixed selection targets only drafts', ()=>{
  const jobs = ['draft','paused','done','error','queued'].map((state,id)=>({id,state,account:'a'}));
  const h=helpers(jobs);
  assert.deepEqual(Array.from(h.drafts(), j=>j.state), ['draft']);
});
test('specific channel limits drafts; all channels allows bulk metadata', ()=>{
  const jobs=[{id:1,state:'draft',account:'a'},{id:2,state:'draft',account:'b'}];
  assert.equal(helpers(jobs).drafts().length,2);
  assert.deepEqual(Array.from(helpers(jobs,'a').drafts(),j=>j.account),['a']);
});
test('all-channel queue groups videos by channel and preserves order inside each group', ()=>{
  const jobs=[
    {id:'a1',account:'a'},{id:'b1',account:'b'},
    {id:'b2',account:'b'},{id:'a2',account:'a'}
  ];
  const context={state:{accounts:[{id:'a'},{id:'b'}]}};
  const helper=source.split('\n').find(line=>line.startsWith('function groupByChannel('));
  vm.runInNewContext(helper+'\nthis.grouped=groupByChannel;',context);
  assert.deepEqual(Array.from(context.grouped(jobs),job=>job.id),['a1','a2','b1','b2']);
  assert.deepEqual(jobs.map(job=>job.id),['a1','b1','b2','a2']);
});
test('removal follows queue state regardless of saved video ID', ()=>{
  const h=helpers([]);
  for(const state of ['paused','error']) assert.equal(h.canRemove({state,has_session:true,video_id:''}),true);
  for(const state of ['draft','paused','error','warning']) assert.equal(h.canRemove({state,video_id:'uploaded'}),true);
  for(const state of ['done','queued','uploading','finishing']) assert.equal(h.canRemove({state,video_id:''}),false);
  assert.equal(h.canRemove({state:'uploading'}),false);
});
