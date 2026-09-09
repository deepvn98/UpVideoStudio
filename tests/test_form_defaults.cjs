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
  assert.match(source,/\$\('#bulk-language'\)\.value='en-US'/);
  assert.doesNotMatch(source,/\$\('#edit-language'\)\.value='en-US'/);
});
