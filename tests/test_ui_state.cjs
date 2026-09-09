const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const helpers=source.slice(source.indexOf('const UI_STORAGE_KEY'),source.indexOf('async function api('));
function tab(storage){
  const search={value:''};
  const state={selected:new Set(),filter:'all',view:'queue',channel:''};
  const context={state,$:()=>search,localStorage:storage,showView:(view,persist)=>{state.view=view;assert.equal(persist,false);}};
  vm.createContext(context);vm.runInContext(helpers,context);
  return {state,search,save:()=>vm.runInContext('saveUI()',context),read:()=>vm.runInContext('readUI()',context),restore:raw=>{context.raw=raw;vm.runInContext('restoreUI(raw)',context);}};
}
test('reopening a tab restores selection, filters, channel, search and view',()=>{
  let saved=null;
  const storage={getItem:()=>saved,setItem:(k,v)=>{saved=v;}};
  const first=tab(storage);first.state.selected.add('video-1');first.state.filter='active';first.state.channel='channel-a';first.state.view='history';first.search.value='Serengeti';first.save();
  const reopened=tab(storage);reopened.read();
  assert.deepEqual([...reopened.state.selected],['video-1']);
  assert.equal(reopened.state.filter,'active');assert.equal(reopened.state.channel,'channel-a');
  assert.equal(reopened.state.view,'history');assert.equal(reopened.search.value,'Serengeti');
});
test('receiving changes in another tab updates UI without writing back',()=>{
  let writes=0;
  const second=tab({setItem:()=>writes++,getItem:()=>null});
  second.restore(JSON.stringify({selected:['one','two'],filter:'draft'}));
  assert.deepEqual([...second.state.selected],['one','two']);assert.equal(second.state.filter,'draft');assert.equal(writes,0);
  second.restore(JSON.stringify({selected:[],filter:'all'}));assert.equal(second.state.selected.size,0);
});
test('corrupt or blocked storage does not break the page',()=>{
  const current=tab({getItem:()=>{throw Error('blocked');},setItem:()=>{throw Error('blocked');}});
  assert.doesNotThrow(()=>{current.read();current.save();current.restore('{broken');});
  current.restore(JSON.stringify({selected:['valid',42,'valid'],filter:'invalid',view:'invalid'}));
  assert.deepEqual([...current.state.selected],['valid']);assert.equal(current.state.view,'queue');
});
test('saved UI never includes account credentials or upload payloads',()=>{
  let saved;
  const current=tab({getItem:()=>null,setItem:(k,v)=>{saved=JSON.parse(v);}});
  current.state.accounts=[{token:'secret'}];current.state.jobs=[{session:'private'}];current.save();
  assert.deepEqual(Object.keys(saved).sort(),['channel','filter','search','selected','view']);
});
