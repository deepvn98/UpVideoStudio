const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const names=['esc','options','boolOptions'];
const helpers=names.map(name=>source.split('\n').find(line=>line.startsWith('const '+name+' =')||line.startsWith('function '+name+'('))).join('\n');
const context={};vm.runInNewContext(helpers+'\nthis.renderBoolean=boolOptions;',context);

test('only kids fields are defaulted to No while saved boolean answers remain unchanged',()=>{
  assert.match(context.renderBoolean(null),/<option value="" selected>Chọn câu trả lời<\/option>/);
  assert.match(context.renderBoolean(true),/<option value="true" selected>Có<\/option>/);
  assert.equal((source.match(/if\(\$\('#(?:edit|bulk)-kids'\)\.value===''/g)||[]).length,2);
  assert.doesNotMatch(source,/if\(\$\('#(?:edit|bulk)-ai'\)\.value===''/);
});

test('only the bulk declaration defaults to English (United States)',()=>{
  assert.match(source,/if\(!channel&&\$\('#bulk-language'\)\.value==='__keep__'\)\$\('#bulk-language'\)\.value='en-US'/);
  assert.doesNotMatch(source,/\$\('#edit-language'\)\.value='en-US'/);
});

test('channel declaration reopens with the latest saved content settings',()=>{
  assert.match(source,/state\.automation\?\.\[channel\]\?\.content/);
  assert.match(source,/hasOwnProperty\.call\(saved,key\)\?saved\[key\]:common\(key\)/);
  assert.match(source,/hasOwnProperty\.call\(saved,'playlists'\)/);
});

test('channel schedule reopens with the latest saved scheduling settings',()=>{
  assert.match(source,/state\.automation\?\.\[aid\]\?\.schedule/);
  for(const field of ['date','slots','interval','offset','sync','visibility'])assert.match(source,new RegExp(`saved\\.${field}`));
});

test('visibility offers every status supported by the YouTube video API',()=>{
  for(const value of ['private','unlisted','public','schedule'])assert.match(source,new RegExp(`visibilityOption\\('${value}'`));
  assert.match(source,/YouTube Data API chưa hỗ trợ tạo Premiere/);
});

test('each queue row can override or inherit the channel publishing mode',()=>{
  for(const value of ['inherit','private','unlisted','public','schedule'])assert.match(source,new RegExp(`\\['${value}',`));
  assert.match(source,/data-publishing=/);
  assert.match(source,/publishing_override/);
  assert.match(source,/api\('\/api\/visibility'/);
  assert.match(source,/id="overwrite-publishing"/);
});

test('publishing priority resolves inherited and per-video modes correctly',()=>{
  const line=source.split('\n').find(value=>value.startsWith('function publishingMode('));
  const scope={};vm.runInNewContext(line+'\nthis.mode=publishingMode;',scope);
  assert.equal(scope.mode({publishing_override:false,privacy:'unlisted',publish_at:''}),'inherit');
  assert.equal(scope.mode({publishing_override:true,privacy:'unlisted',publish_at:''}),'unlisted');
  assert.equal(scope.mode({publishing_override:true,privacy:'private',publish_at:'2035-01-01T00:00:00Z'}),'schedule');
});

test('content editor no longer silently changes publishing priority',()=>{
  const submit=source.split('\n').find(line=>line.includes("$('#edit-form').onsubmit"));
  assert.doesNotMatch(submit,/edit-privacy|edit-time|publish_at|privacy:/);
  assert.doesNotMatch(source,/id="edit-privacy"|id="edit-time"/);
});

test('background refresh does not close an active publishing dropdown',()=>{
  assert.match(source,/function publishingEditorActive\(\)/);
  assert.match(source,/if\(!publishingEditorActive\(\)\)render\(\)/);
  assert.match(source,/e\.target\.blur\(\);try\{await setPublishing/);
  assert.match(source,/addEventListener\('focusout'/);
});

test('per-video schedule always uses a 24-hour time field',()=>{
  assert.match(source,/id="video-publish-time" inputmode="numeric"/);
  assert.match(source,/Giờ công khai \(24 giờ\)/);
  assert.match(source,/Nhập theo định dạng HH:mm, từ 00:00 đến 23:59/);
  assert.match(source,/\(\?:\[01\]\\d\|2\[0-3\]\):\[0-5\]\\d/);
  assert.doesNotMatch(source,/id="video-publish-at"/);
});
