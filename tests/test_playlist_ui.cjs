const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const app=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const styles=fs.readFileSync(path.join(__dirname,'../web/style.css'),'utf8');

test('bulk playlist UI has no replacement toggle and only saves after an item changes',()=>{
  assert.doesNotMatch(app,/bulk-change-playlists/);
  assert.match(app,/playlistDirty=true/);
  assert.match(app,/playlistLoaded && playlistDirty/);
});

test('playlist checkboxes keep their compact native size',()=>{
  assert.match(styles,/#bulk-playlists \.checkbox-label input\{width:14px!important;height:14px!important/);
  assert.match(styles,/#bulk-playlists\{[^}]*overflow-x:hidden/);
});

test('playlist IDs remain values but are not rendered as visible labels',()=>{
  assert.doesNotMatch(app,/esc\(p\.id\.slice\(-6\)\)/);
  assert.doesNotMatch(app,/esc\(p\.title\)\} · \$\{esc\(p\.id\)/);
  assert.match(app,/value="\$\{esc\(p\.id\)\}"/);
});
