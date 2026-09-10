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
