const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const apiSource = source.slice(source.indexOf('async function api('), source.indexOf('\nfunction toast('));
function client(fetch) {
  return vm.runInNewContext(apiSource + '\napi', {fetch, token:'test-token', AbortSignal});
}
test('disconnected Python produces actionable message and does not replay POST', async () => {
  let calls=0;
  const api=client(async()=>{calls++;throw new TypeError('Failed to fetch');});
  await assert.rejects(api('/api/connect',{config:{}}), /Start\.cmd/);
  assert.equal(calls,1);
});
test('expired interface session asks to reload',async()=>{
  await assert.rejects(client(async()=>({status:403}))('/api/state'), /Tải lại trang/);
});
test('timeout does not silently retry an action',async()=>{
  const error=new Error();error.name='TimeoutError';
  await assert.rejects(client(async()=>{throw error;})('/api/start',{ids:['one']}), /chưa được tự động gửi lại/);
});
test('valid Google connect response reaches the UI',async()=>{
  const result=await client(async()=>({status:200,ok:true,json:async()=>({url:'https://accounts.google.com/o/oauth2/v2/auth'})}))('/api/connect',{config:{}});
  assert.match(result.url,/accounts.google.com/);
});
test('server validation error is preserved',async()=>{
  await assert.rejects(client(async()=>({status:400,ok:false,json:async()=>({error:'Desktop app required'})}))('/api/connect',{}), /Desktop app required/);
});
