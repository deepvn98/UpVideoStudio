'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const token = $('meta[name="studio-token"]').content;
const {categories, languages} = JSON.parse($('meta[name="studio-catalog"]').content);
const state = {jobs: [], accounts: [], events: [], automation: {}, selected: new Set(), filter: 'all', view: 'queue', channel: ''};
const labels = {draft:'Bản nháp', queued:'Chờ tải lên', uploading:'Đang tải lên', finishing:'Hoàn thiện', paused:'Tạm dừng', done:'Đã tải lên', error:'Cần xử lý', warning:'Cần hoàn thiện'};
const privacy = {private:'Riêng tư', unlisted:'Không công khai', public:'Công khai'};
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = s => s ? new Date(s).toLocaleString('vi-VN',{hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit',year:'numeric'}) : '';
const bytes = n => n < 1048576 ? (n/1024).toFixed(0)+' KB' : n < 1073741824 ? (n/1048576).toFixed(1)+' MB' : (n/1073741824).toFixed(2)+' GB';
const account = id => state.accounts.find(a => a.id === id);
const active = j => ['queued','uploading','finishing'].includes(j.state);
const editable = j => !active(j) && !j.has_session && !j.video_id;
const selectedFrom = jobs => jobs.filter(j => state.selected.has(j.id));
const visibleSelection = () => selectedFrom(visible());
const draftSelection = () => visibleSelection().filter(j=>j.state==='draft' && editable(j) && (!state.channel || j.account===state.channel));
const removable = j => ['draft','error','paused','warning'].includes(j.state);
let refreshing = false, modalGeneration = 0;

// UI preferences only: never store credentials, form drafts or upload commands.
const UI_STORAGE_KEY = 'upvideo.ui.v1';
function normalizeUI(value) {
  const v = value && typeof value === 'object' ? value : {};
  return {
    selected: Array.isArray(v.selected) ? [...new Set(v.selected.filter(id=>typeof id==='string'))] : [],
    filter: ['all','draft','active','done','issues'].includes(v.filter) ? v.filter : 'all',
    view: ['queue','channels','history','guide'].includes(v.view) ? v.view : 'queue',
    channel: typeof v.channel==='string' ? v.channel : '',
    search: typeof v.search==='string' ? v.search.slice(0,500) : ''
  };
}
function saveUI() {
  const value = {selected:[...state.selected],filter:state.filter,view:state.view,
    channel:state.channel,search:$('#search').value};
  try { localStorage.setItem(UI_STORAGE_KEY,JSON.stringify(value)); } catch { /* Storage disabled: keep current tab usable. */ }
}
function restoreUI(raw) {
  try {
    const value=normalizeUI(JSON.parse(raw));
    state.selected=new Set(value.selected);state.filter=value.filter;state.channel=value.channel;
    $('#search').value=value.search;
    showView(value.view,false);
  } catch { /* Ignore damaged or unavailable browser preferences. */ }
}
function readUI() {
  try { const raw=localStorage.getItem(UI_STORAGE_KEY);if(raw!==null)restoreUI(raw); } catch {}
}

async function api(path, body) {
  let response;
  try {
    response = await fetch(path, {method:body === undefined ? 'GET':'POST',headers:{'Content-Type':'application/json','X-Studio-Token':token},body:body === undefined ? undefined : JSON.stringify(body),signal:AbortSignal.timeout(15000)});
  } catch (error) {
    if (error.name === 'TimeoutError' || error.name === 'AbortError') {
      throw new Error('Chương trình Python phản hồi quá lâu. Kiểm tra cửa sổ UpVideo Studio rồi thử lại. Thao tác chưa được tự động gửi lại.');
    }
    throw new Error('Không kết nối được với chương trình Python trên máy. Hãy mở lại Start.cmd hoặc UpVideoStudio.exe, giữ cửa sổ chương trình chạy và sử dụng tab mới được mở. Nếu chương trình vẫn chạy, kiểm tra tiện ích trình duyệt đang chặn kết nối localhost.');
  }
  if (response.status === 403) throw new Error('Phiên giao diện đã hết hiệu lực hoặc yêu cầu bị từ chối. Tải lại trang rồi thực hiện lại thao tác.');
  let data;
  try { data = await response.json(); }
  catch { throw new Error('Phản hồi từ máy chủ cục bộ không hợp lệ. Mở lại ứng dụng và dùng đúng tab UpVideo Studio.'); }
  if (!response.ok) throw new Error(data.error || 'Không kết nối được ứng dụng.');
  return data;
}
function toast(message, error=false) {
  if([...$('#toasts').children].some(el=>el.textContent===message))return;
  const el = document.createElement('div'); el.className='toast'+(error?' error':'');el.textContent=message;$('#toasts').append(el);setTimeout(()=>el.remove(), error?10000:6000);
}
async function task(path, body, title) {
  $('#busy-title').textContent=title;$('#busy').hidden=false;
  try {
    const result=await api(path,body);
    if (!result.task) return result;
    while(true) {
      await new Promise(r=>setTimeout(r,600));
      const status=await api('/api/task?id='+result.task);
      if(status.state==='error') throw new Error(status.error);
      if(status.state==='done') return status.result;
    }
  } finally { $('#busy').hidden=true; }
}
function modal(title, body) {
  modalGeneration++;$('#modal-title').textContent=title;$('#modal-body').innerHTML=body;
  if(!$('#modal').open) $('#modal').showModal();
  return modalGeneration;
}
function closeModal(){modalGeneration++;$('#modal').close();}
function errorInModal(error){let el=$('#modal-error');if(!el){el=document.createElement('div');el.id='modal-error';el.className='modal-error';$('#modal-body').append(el);}el.textContent=error.message;el.scrollIntoView({block:'nearest'});}
function options(list, value){return list.map(([id,name])=>`<option value="${esc(id)}" ${String(id)===String(value)?'selected':''}>${esc(name)}</option>`).join('');}
function channelOptions(value){return options(state.accounts.map(a=>[a.id,a.name+' · '+a.channel_id]),value);}
function showView(view,persist=true){state.view=view;$$('.view').forEach(el=>el.hidden=el.id!=='view-'+view);$$('.nav').forEach(el=>el.classList.toggle('active',el.dataset.view===view));$('#crumb').textContent={queue:'Hàng đợi',channels:'Kênh của tôi',history:'Nhật ký hoạt động',guide:'Hướng dẫn'}[view];render();if(persist)saveUI();}
function groupByChannel(jobs){const order=new Map(state.accounts.map((account,index)=>[account.id,index]));let next=order.size;for(const job of jobs)if(!order.has(job.account))order.set(job.account,next++);return [...jobs].sort((left,right)=>order.get(left.account)-order.get(right.account));}
function visible(){const jobs=state.jobs.filter(j=>{
  const q=$('#search').value.toLocaleLowerCase();
  if(q&&!j.title.toLocaleLowerCase().includes(q))return false;
  if(state.channel&&j.account!==state.channel)return false;
  return state.filter==='all'||state.filter==='active'&&active(j)||state.filter==='issues'&&['warning','error','paused'].includes(j.state)||j.state===state.filter;
});return state.channel?jobs:groupByChannel(jobs);}
function render(){
  $$('.tab').forEach(t=>t.classList.toggle('active',t.dataset.filter===state.filter));
  $('#nav-count').textContent=state.jobs.length;$('#total-count').textContent=state.jobs.length;
  $('#stat-pending').textContent=state.jobs.filter(j=>['draft','paused','queued'].includes(j.state)).length;
  $('#stat-active').textContent=state.jobs.filter(j=>['uploading','finishing'].includes(j.state)).length;
  $('#stat-done').textContent=state.jobs.filter(j=>j.state==='done').length;
  const errors=state.jobs.filter(j=>['error','warning'].includes(j.state)).length;$('#stat-error').textContent=errors;$('#error-caption').textContent=errors?'video cần kiểm tra':'mọi thứ đang ổn';
  const current=state.channel;
  const channelHTML='<option value="">Tất cả các kênh</option>'+options(state.accounts.map(a=>[a.id,a.name]),current);
  if($('#channel-filter').innerHTML!==channelHTML)$('#channel-filter').innerHTML=channelHTML;
  const rows=visible(), rowSelection=selectedFrom(rows);$('#empty').hidden=state.jobs.length>0;$('#no-results').hidden=!state.jobs.length||!!rows.length;
  $('#jobs').innerHTML=rows.map(j=>`<tr><td><input type="checkbox" data-select="${j.id}" ${state.selected.has(j.id)?'checked':''} aria-label="Chọn ${esc(j.title)}"></td><td><div class="video-cell"><span class="video-icon">▶</span><div><button class="video-name" data-edit="${j.id}" title="${esc(j.title)}">${esc(j.title)}</button><small class="video-sub">${bytes(j.size)} · ${esc(j.path.split(/[\\/]/).pop())}</small></div></div></td><td title="${esc(account(j.account)?.channel_id||'')}">${esc(account(j.account)?.name||'Chưa kết nối kênh')}</td><td>${j.publish_at?fmt(j.publish_at):privacy[j.privacy]}${j.publish_at?'<small class="video-sub">Giờ trên máy của bạn</small>':''}</td><td><span class="badge ${j.state}" title="${esc(j.error)}">${labels[j.state]}${j.state==='uploading'?' '+j.progress+'%':''}</span>${active(j)||j.state==='paused'?`<div class="progress"><i style="width:${Number(j.progress)||0}%"></i></div>`:''}</td><td><div class="row-actions">${active(j)?`<button class="icon-button" data-pause="${j.id}" aria-label="Tạm dừng ${esc(j.title)}">Ⅱ</button>`:j.state!=='done'?`<button class="icon-button" data-start="${j.id}" aria-label="Bắt đầu hoặc tiếp tục ${esc(j.title)}">▷</button>`:''}<button class="icon-button" data-edit="${j.id}" aria-label="Chi tiết ${esc(j.title)}">⋯</button></div></td></tr>`).join('');
  $('#table-summary').textContent=rows.length+' / '+state.jobs.length+' video';$('#selection-label').textContent=rowSelection.length?'Đã chọn '+rowSelection.length+' video':'Chưa chọn video';
  $('#select-all').checked=rows.length>0&&rows.every(j=>state.selected.has(j.id));$('#select-all').indeterminate=rows.some(j=>state.selected.has(j.id))&&!$('#select-all').checked;
  ['bulk-open','schedule-open','start-selected','remove-selected'].forEach(id=>$('#'+id).disabled=!rowSelection.length);
  $('#pause-all').disabled=!state.jobs.some(active);
  if(state.view==='channels')renderChannels();
  if(state.view==='history')$('#events').innerHTML=state.events.length?state.events.map(e=>`<div class="event ${esc(e.level)}"><time>${fmt(e.time)}</time><span>${esc(e.message)}</span></div>`).join(''):'<div class="no-results">Chưa có hoạt động. Kết nối kênh để bắt đầu.</div>';
}
function renderChannels(){
  $('#channels-grid').innerHTML=state.accounts.length?state.accounts.map(a=>`<article class="channel-card"><div class="avatar">${esc(a.name[0]?.toUpperCase())}</div><h3>${esc(a.name)}</h3><p>${esc(a.channel_id)}<br>Đã kết nối ${fmt(a.connected)}</p><button class="button small" data-channel="${a.id}">Xem hàng đợi</button><button class="button small danger" data-forget="${a.id}">Ngắt kết nối</button></article>`).join(''):'<article class="channel-card"><div class="avatar">＋</div><h3>Kết nối kênh đầu tiên</h3><p>Đăng nhập trên trang Google. UpVideo không yêu cầu mật khẩu của bạn.</p><button class="button primary" id="first-connect">Kết nối Google</button></article>';
}
async function refresh(){if(refreshing)return;refreshing=true;try{const data=await api('/api/state');Object.assign(state,data);state.selected=new Set([...state.selected].filter(id=>state.jobs.some(j=>j.id===id)));if(state.channel&&!state.accounts.some(a=>a.id===state.channel))state.channel='';$('#connection').innerHTML='<i></i> Đã kết nối';$('#offline-notice').hidden=true;render();}catch(e){$('#connection').textContent='Mất kết nối · mở lại ứng dụng';$('#offline-notice').hidden=false;$('#offline-message').textContent=e.message;}finally{refreshing=false;}}

function connect(){
  modal('Kết nối kênh YouTube',`<p class="helper">Chọn file OAuth Client JSON loại Desktop app. File được xử lý trên máy; trình duyệt chỉ mở trang đăng nhập chính thức của Google.</p><label class="upload-file">◇ Chọn thông tin ứng dụng Google<input type="file" id="client-file" accept=".json,application/json"></label><div class="info-box">Chọn đúng kênh hoặc Brand Account trong bước đăng nhập Google. Tên và ID kênh sẽ xuất hiện sau khi kết nối thành công.</div><p class="helper">Ứng dụng cần quyền quản lý YouTube để tải video và thêm vào playlist. Project chưa audit có thể bị giới hạn video riêng tư.</p><div class="modal-footer"><button class="button primary" id="connect-google">Tiếp tục với Google ↗</button></div>`);
  $('#connect-google').onclick=async()=>{try{const file=$('#client-file').files[0];if(!file)throw new Error('Hãy chọn file JSON trước.');if(file.size>100000)throw new Error('File JSON quá lớn.');const config=JSON.parse(await file.text());const data=await api('/api/connect',{config});modal('Tiếp tục trên Google',`<div class="info-box">Đăng nhập và cấp quyền trên Google. Khi hoàn tất, quay lại đây; danh sách kênh sẽ tự cập nhật.</div><div class="modal-footer"><a class="button primary" href="${esc(data.url)}" target="_blank" rel="noopener noreferrer">Mở trang đăng nhập Google ↗</a></div>`);showView('channels');}catch(e){errorInModal(e);}};
}
function importVideos(channel=''){
  if(!state.accounts.length){connect();return;}
  modal('Thêm video vào hàng đợi',`<div class="field"><label for="import-account">Kênh nhận video</label><select id="import-account">${channelOptions($('#channel-filter').value)}</select></div><div class="field"><label for="folder-path">Thư mục video</label><div class="inline"><input id="folder-path" placeholder="D:\\Videos\\Thang-09"><button class="button" id="pick-folder">Chọn thư mục</button></div><small>Quét tất cả video trong thư mục và thư mục con. Không di chuyển file gốc.</small></div><div class="info-box">Mỗi video được nhận diện bằng nội dung để tránh nhập trùng trên cùng kênh. File lớn có thể cần vài phút để kiểm tra. Nội dung mới được lưu thành bản nháp.</div><div class="modal-footer"><button class="button primary" id="do-import">Quét và thêm video</button></div>`);
  $('#do-import').textContent='Liên kết và thêm video';
  if(typeof channel==='string' && channel)$('#import-account').value=channel;
  const watchInfo=document.createElement('div');watchInfo.className='info-box';watchInfo.innerHTML='<label><input type="checkbox" id="watch-folder" checked> Tự cập nhật bản nháp khi thư mục thay đổi</label><p>Thêm, sửa hoặc xóa file sẽ cập nhật bản nháp. Không tự bắt đầu upload. Chỉ theo dõi khi chương trình đang chạy; mỗi kênh liên kết một thư mục.</p><p id="watch-current"></p><button class="button small" id="watch-stop" type="button">Dừng theo dõi thư mục hiện tại</button>';
  $('#do-import').parentElement.before(watchInfo);
  const updateWatch=()=>{const w=(state.watches||[]).find(w=>w.account===$('#import-account').value);$('#watch-current').textContent=w?'Đang theo dõi: '+w.path:'Chưa liên kết thư mục.';$('#watch-stop').hidden=!w;if(w)$('#folder-path').value=w.path;};
  $('#import-account').onchange=()=>{$('#folder-path').value='';updateWatch();};updateWatch();
  $('#watch-stop').onclick=async()=>{try{await api('/api/unwatch',{account:$('#import-account').value});await refresh();updateWatch();toast('Đã dừng theo dõi. Giữ nguyên bản nháp và file.');}catch(e){errorInModal(e);}};
  $('#pick-folder').onclick=async()=>{try{const result=await task('/api/pick',{},'Chọn thư mục trong cửa sổ Windows');if(result.path)$('#folder-path').value=result.path;}catch(e){errorInModal(e);}};
  $('#do-import').onclick=async()=>{try{const path=$('#folder-path').value.trim();if(!path)throw new Error('Chọn hoặc dán đường dẫn thư mục.');const data=await task('/api/import',{path,account:$('#import-account').value,watch:$('#watch-folder').checked},'Đang quét và kiểm tra video…');closeModal();await refresh();toast(`Đã thêm ${data.added} video · bỏ qua ${data.duplicates} video trùng.`);if(data.warnings.length)modal('Kết quả nhập video',`<div class="info-box">${data.warnings.length} video cần bổ sung hoặc kiểm tra nội dung.</div><div class="review-list">${data.warnings.map(w=>`<div class="review-item">${esc(w)}</div>`).join('')}</div>`);}catch(e){errorInModal(e);}};
}
function boolOptions(value){return options([['','Chọn câu trả lời'],['false','Không'],['true','Có']],value===null?'':String(value));}
function boolValue(id){const v=$(id).value;return v===''?null:v==='true';}
function localDateTime(value){if(!value)return '';const d=new Date(value);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
async function edit(jid){
  const j=state.jobs.find(j=>j.id===jid);if(!j)return;
  if(!editable(j)){
    modal('Chi tiết video',`<h3>${esc(j.title)}</h3><p class="helper">${esc(account(j.account)?.name||'Kênh chưa kết nối')} · ${bytes(j.size)} · ${labels[j.state]}</p><div class="info-box">${esc(j.error||'Nội dung đã được chốt khi bắt đầu upload.')}<br>Tiến trình: ${j.progress}%${j.publish_at?'<br>Lịch đăng: '+fmt(j.publish_at):''}</div>${j.video_id?`<a class="button" href="https://www.youtube.com/watch?v=${encodeURIComponent(j.video_id)}" target="_blank" rel="noopener noreferrer">Mở video ↗</a>`:''}<div class="modal-footer">${!active(j)&&j.state!=='done'?'<button class="button primary" id="retry-job">Tiếp tục / thử lại</button>':''}${j.has_session&&!j.video_id&&!active(j)?'<button class="button danger" id="reset-job">Tạo lại phiên upload</button>':''}</div>`);
    if($('#retry-job'))$('#retry-job').onclick=()=>startReview([j]);
    if($('#reset-job'))$('#reset-job').onclick=()=>{modal('Kiểm tra trước khi tạo phiên mới',`<div class="info-box">Phiên mới sẽ tải video lại từ đầu. Trước tiên, kiểm tra YouTube Studio và chắc chắn video này chưa được tạo trên kênh. Nếu đã có video, không tạo lại phiên.</div><label class="checkbox-label"><input type="checkbox" id="reset-confirm">Tôi đã kiểm tra kênh và xác nhận video chưa được tải lên.</label><div class="modal-footer"><button class="button danger" id="confirm-reset">Tạo lại phiên</button></div>`);$('#confirm-reset').onclick=async()=>{try{if(!$('#reset-confirm').checked)throw new Error('Cần kiểm tra kênh trước khi tạo lại phiên.');await api('/api/reset-session',{id:j.id,confirmed:true});closeModal();await refresh();toast('Đã tạo lại bản nháp. Kiểm tra lịch trước khi chạy.');}catch(e){errorInModal(e);}};};return;
  }
  const generation=modal('Chuẩn bị video',`<form id="edit-form"><div class="field"><label for="edit-title">Tiêu đề</label><input id="edit-title" value="${esc(j.title)}" maxlength="100" required><span id="title-count" class="count-hint">${j.title.length}/100</span></div><div class="field"><label for="edit-desc">Mô tả</label><textarea id="edit-desc" rows="5">${esc(j.description)}</textarea><small>Tối đa 5.000 byte UTF-8. Nội dung tiếng Việt có thể dùng nhiều byte hơn số ký tự.</small></div><div class="field"><label for="edit-tags">Tags</label><input id="edit-tags" value="${esc(j.tags.join(', '))}"><small>Phân cách bằng dấu phẩy.</small></div><div class="form-grid"><div class="field"><label for="edit-category">Danh mục</label><select id="edit-category">${options(categories,j.category)}</select></div><div class="field"><label for="edit-language">Ngôn ngữ nội dung</label><select id="edit-language">${options(languages,j.language)}</select></div><div class="field"><label for="edit-kids">Video dành cho trẻ em?</label><select id="edit-kids" required>${boolOptions(j.made_for_kids)}</select></div><div class="field"><label for="edit-ai">Nội dung tổng hợp cần khai báo?</label><select id="edit-ai" required>${boolOptions(j.synthetic)}</select></div><div class="field"><label for="edit-privacy">Hiển thị khi tải lên</label><select id="edit-privacy">${options(Object.entries(privacy),j.privacy)}</select></div><div class="field"><label for="edit-time">Lên lịch công khai (không bắt buộc)</label><input type="datetime-local" id="edit-time" value="${localDateTime(j.publish_at)}"><small>Giờ trên máy của bạn; cách hiện tại ít nhất 15 phút.</small></div></div><div class="field" style="margin-top:17px"><label for="edit-thumb">Đường dẫn thumbnail</label><input id="edit-thumb" value="${esc(j.thumbnail)}" placeholder="D:\\Videos\\video-01.jpg"><small>JPG hoặc PNG, không quá 2 MB. Có thể để trống.</small></div><div class="field"><label for="edit-playlists">Playlist (giữ Ctrl để chọn nhiều)</label><select multiple id="edit-playlists" disabled><option>Đang tải playlist…</option></select><small id="playlist-help">Đang kết nối Google…</small></div><div class="modal-footer"><button type="submit" class="button primary">Lưu thay đổi</button></div></form>`);
  if($('#edit-kids').value==='')$('#edit-kids').value='false';
  const playlistSelect=$('#edit-playlists'),playlistField=playlistSelect.closest('.field'),resizePlaylist=()=>playlistSelect.size=Math.min(7,Math.max(2,playlistSelect.options.length));resizePlaylist();new MutationObserver(resizePlaylist).observe(playlistSelect,{childList:true});playlistField.classList.add('playlist-field');playlistField.querySelector('label').textContent='Playlist của kênh '+(account(j.account)?.name||'hiện tại')+' · giữ Ctrl để chọn nhiều';
  $('#edit-title').oninput=e=>$('#title-count').textContent=e.target.value.length+'/100';
  $('#edit-time').onchange=()=>{if($('#edit-time').value)$('#edit-privacy').value='private';};
  let playlistLoaded=false;
  $('#edit-form').onsubmit=async e=>{e.preventDefault();try{const changes={title:$('#edit-title').value.trim(),description:$('#edit-desc').value,tags:$('#edit-tags').value.split(',').map(s=>s.trim()).filter(Boolean),category:$('#edit-category').value,language:$('#edit-language').value,made_for_kids:boolValue('#edit-kids'),synthetic:boolValue('#edit-ai'),privacy:$('#edit-privacy').value,publish_at:$('#edit-time').value?new Date($('#edit-time').value).toISOString():'',thumbnail:$('#edit-thumb').value.trim(),playlists:playlistLoaded?[...$('#edit-playlists').selectedOptions].map(o=>o.value):j.playlists};await api('/api/edit',{id:jid,changes});closeModal();await refresh();toast('Đã lưu nội dung video.');}catch(err){errorInModal(err);}};
  try{const result=await api('/api/playlists',{account:j.account});let data;while(true){await new Promise(r=>setTimeout(r,500));if(generation!==modalGeneration)return;const t=await api('/api/task?id='+result.task);if(t.state==='error')throw new Error(t.error);if(t.state==='done'){data=t.result;break;}}if(generation!==modalGeneration)return;$('#edit-playlists').innerHTML=data.map(p=>`<option value="${esc(p.id)}" ${j.playlists.includes(p.id)?'selected':''}>${esc(p.title)}</option>`).join('');$('#edit-playlists').disabled=false;playlistLoaded=true;$('#playlist-help').textContent=data.length?'Giữ Ctrl để chọn nhiều playlist.':'Kênh chưa có playlist.';}catch(e){if(generation!==modalGeneration)return;$('#edit-playlists').innerHTML='';$('#playlist-help').textContent=e.message+' Playlist đã lưu được giữ nguyên.';}
}
function bulk(){
  const jobs=draftSelection();
  if(!jobs.length){toast('Không có bản nháp phù hợp trong lựa chọn.',true);return;}
  const channel=state.channel;
  const saved=channel?(state.automation?.[channel]?.content||{}):{};
  const common=key=>jobs.every(j=>j[key]===jobs[0][key])?jobs[0][key]:null;
  const latest=key=>Object.prototype.hasOwnProperty.call(saved,key)?saved[key]:common(key);
  modal('Khai báo cho '+jobs.length+' video',`<div class="info-box">Các giá trị bạn chọn sẽ được áp dụng cho tất cả video đã chọn. Chọn “Giữ nguyên từng video” nếu không muốn đổi ngôn ngữ hoặc danh mục.</div><div class="form-grid"><div class="field"><label for="bulk-language">Ngôn ngữ (Language)</label><select id="bulk-language">${options([['__keep__','Giữ nguyên từng video'],...languages],latest('language')??'__keep__')}</select></div><div class="field"><label for="bulk-category">Danh mục (Category)</label><select id="bulk-category">${options([['__keep__','Giữ nguyên từng video'],...categories],latest('category')??'__keep__')}</select></div><div class="field"><label for="bulk-kids">Nội dung dành cho trẻ em?</label><select id="bulk-kids">${boolOptions(latest('made_for_kids'))}</select></div><div class="field"><label for="bulk-ai">Nội dung chỉnh sửa/tổng hợp cần khai báo?</label><select id="bulk-ai">${boolOptions(latest('synthetic'))}</select><small>Chọn theo nội dung thực tế và hướng dẫn khai báo trong YouTube Studio.</small></div></div><div class="modal-footer"><button class="button primary" id="bulk-save">Áp dụng cho ${jobs.length} video</button></div>`);
  if(!channel&&$('#bulk-language').value==='__keep__')$('#bulk-language').value='en-US';
  if($('#bulk-kids').value==='')$('#bulk-kids').value='false';
  const generation=modalGeneration;
  const note=document.createElement('p');note.className='helper';note.textContent=`Thiết lập này sẽ áp dụng cho toàn bộ bản nháp chưa bắt đầu của ${new Set(jobs.map(j=>j.account)).size} kênh đã chọn.`;$('#modal-body').prepend(note);
  let playlistLoaded=false,playlistDirty=false;
  if(channel){
    const section=document.createElement('div');section.className='field playlist-field';section.innerHTML=`<label>Playlist của kênh ${esc(account(channel)?.name||'hiện tại')}</label><div id="bulk-playlists">Đang tải playlist của kênh…</div><small>Chỉ thay đổi playlist khi bạn tick hoặc bỏ tick trong danh sách.</small>`;
    $('#bulk-save').parentElement.before(section);
    (async()=>{try{
      const result=await api('/api/playlists',{account:channel});
      while(generation===modalGeneration){
        await new Promise(r=>setTimeout(r,500));
        if(generation!==modalGeneration)return;
        const response=await api('/api/task?id='+result.task);
        if(generation!==modalGeneration)return;
        if(response.state==='error')throw new Error(response.error);
        if(response.state==='done'){
          const selectedPlaylists=Object.prototype.hasOwnProperty.call(saved,'playlists')?saved.playlists:null;
          $('#bulk-playlists').innerHTML=response.result.length?response.result.map(p=>`<label class="checkbox-label"><input type="checkbox" value="${esc(p.id)}" ${(selectedPlaylists?selectedPlaylists.includes(p.id):jobs.every(j=>j.playlists.includes(p.id)))?'checked':''}>${esc(p.title)}</label>`).join(''):'Kênh chưa có playlist.';
          playlistLoaded=true;
          $('#bulk-playlists').onchange=()=>{playlistDirty=true;};break;
        }
      }
    }catch(e){if(generation===modalGeneration)$('#bulk-playlists').textContent=e.message+' Danh sách đã gán được giữ nguyên.';}})();
  }
  $('#bulk-save').onclick=async()=>{
    try{
      const body={ids:jobs.map(j=>j.id),made_for_kids:boolValue('#bulk-kids'),synthetic:boolValue('#bulk-ai')};
      if($('#bulk-language').value!=='__keep__')body.language=$('#bulk-language').value;
      if($('#bulk-category').value!=='__keep__')body.category=$('#bulk-category').value;
      if(channel && playlistLoaded && playlistDirty){body.account=channel;body.playlists=$$('#bulk-playlists input:checked').map(el=>el.value);}
      const result=await api('/api/bulk',body);closeModal();await refresh();toast(`Đã đồng bộ khai báo cho ${result.count} video trong kênh.`);
    }catch(e){errorInModal(e);}
  };
}
function plan(){
  const chosen=draftSelection();
  if(!chosen.length)return;
  if(new Set(chosen.map(j=>j.account)).size!==1){toast('Chọn video chưa upload thuộc cùng một kênh.',true);return;}
  const aid=chosen[0].account;
  const jobs=state.jobs.filter(j=>j.account===aid&&j.state==='draft'&&editable(j));
  const saved=state.automation?.[aid]?.schedule||{};
  const today=localDateTime(new Date().toISOString()).slice(0,10);
  const date=saved.date||today;
  const slots=saved.slots||'08:00, 19:00';
  const interval=Number(saved.interval)||1;
  const offset=Number.isFinite(Number(saved.offset))?Number(saved.offset):-new Date().getTimezoneOffset();
  const sync=saved.sync!==false;
  const visibility=['private','unlisted','public','schedule'].includes(saved.visibility)?saved.visibility:'schedule';
  const visibilityOption=(value,title,description)=>`<label class="visibility-option"><input type="radio" name="schedule-visibility" value="${value}" ${visibility===value?'checked':''}><span><b>${title}</b><small>${description}</small></span></label>`;
  modal('Hiển thị và xếp lịch cho '+jobs.length+' video',`<div class="info-box">Kênh: <b>${esc(account(aid)?.name)}</b><br>Thiết lập này áp dụng cho toàn bộ bản nháp chưa bắt đầu của kênh và các video được thêm vào sau này.</div><fieldset class="visibility-panel"><legend>Lưu hoặc xuất bản</legend><p>Chọn ai có thể xem video.</p>${visibilityOption('private','Riêng tư','Chỉ bạn và những người bạn chọn mới xem được video.')}${visibilityOption('unlisted','Không công khai','Bất kỳ ai có đường liên kết đều có thể xem video.')}${visibilityOption('public','Công khai','Mọi người đều có thể xem video.')}<label class="premiere-option"><input type="checkbox" disabled><span>Đặt làm Công chiếu tức thì<small>YouTube Data API chưa hỗ trợ tạo Premiere; hãy bật trong YouTube Studio sau khi tải lên.</small></span></label>${visibilityOption('schedule','Lên lịch','Chọn ngày và giờ để video tự chuyển sang công khai.')}</fieldset><div id="schedule-fields" ${visibility==='schedule'?'':'hidden'}><div class="form-grid"><div class="field"><label for="schedule-date">Bắt đầu từ ngày</label><input type="date" id="schedule-date" value="${esc(date)}"></div><div class="field"><label for="schedule-slots">Khung giờ mỗi ngày</label><input id="schedule-slots" value="${esc(slots)}"></div><div class="field"><label for="schedule-interval">Lặp mỗi bao nhiêu ngày?</label><input type="number" id="schedule-interval" min="1" max="365" value="${esc(interval)}"><small>1 = hằng ngày; 2 = cách ngày.</small></div><div class="field"><label for="schedule-offset">Múi giờ cố định</label><select id="schedule-offset">${options(Array.from({length:105},(_,i)=>{const n=-720+i*15;return [n,'UTC'+(n>=0?'+':'-')+String(Math.floor(Math.abs(n)/60)).padStart(2,'0')+':'+String(Math.abs(n)%60).padStart(2,'0')];}),offset)}</select><small>Không tự đổi theo giờ mùa hè.</small></div></div><label class="checkbox-label schedule-sync"><input type="checkbox" id="sync-schedule" ${sync?'checked':''}>Đồng bộ lịch đang có trên YouTube trước khi xếp (có sử dụng quota API).</label></div><div class="modal-footer"><button class="button primary" id="schedule-save">${visibility==='schedule'?'Xếp lịch':'Áp dụng hiển thị'} toàn bộ kênh</button></div>`);
  const updateVisibility=()=>{const scheduled=$('input[name="schedule-visibility"]:checked').value==='schedule';$('#schedule-fields').hidden=!scheduled;$('#schedule-save').textContent=(scheduled?'Xếp lịch':'Áp dụng hiển thị')+' toàn bộ kênh';};
  $$('input[name="schedule-visibility"]').forEach(input=>input.onchange=updateVisibility);
  $('#schedule-save').onclick=async()=>{try{const selectedVisibility=$('input[name="schedule-visibility"]:checked').value;const result=await task('/api/schedule',{ids:chosen.map(j=>j.id),account:aid,visibility:selectedVisibility,date:$('#schedule-date').value,slots:$('#schedule-slots').value,interval:Number($('#schedule-interval').value),offset:Number($('#schedule-offset').value),sync:$('#sync-schedule').checked},selectedVisibility==='schedule'?'Đang kiểm tra và xếp lịch…':'Đang cập nhật chế độ hiển thị…');closeModal();await refresh();toast(selectedVisibility==='schedule'?`Đã xếp lại lịch ${result.count} video và lưu quy tắc cho kênh.`:`Đã cập nhật chế độ hiển thị cho ${result.count} video và lưu quy tắc cho kênh.`);}catch(e){errorInModal(e);}};
}
function startReview(jobs){jobs=jobs.filter(j=>!active(j)&&j.state!=='done');if(!jobs.length){toast('Không có video để bắt đầu.');return;}modal('Sẵn sàng tải '+jobs.length+' video?',`<p class="helper">Kiểm tra kênh và chế độ hiển thị. Video công khai có thể xuất hiện ngay sau khi xử lý xong; video có lịch sẽ được công khai theo giờ đã chọn.</p><div class="review-list">${jobs.map(j=>`<div class="review-item"><b>${esc(j.title)}</b><small>${esc(account(j.account)?.name||'Kênh chưa kết nối')} · ${j.publish_at?fmt(j.publish_at):privacy[j.privacy]}${j.video_id?' · chỉ hoàn thiện bước còn thiếu':''}</small></div>`).join('')}</div><div class="modal-footer"><button class="button" id="review-back">Quay lại</button><button class="button primary" id="confirm-start">▶ Bắt đầu tải lên</button></div>`);$('#review-back').onclick=closeModal;$('#confirm-start').onclick=async()=>{try{await api('/api/start',{ids:jobs.map(j=>j.id)});closeModal();await refresh();toast('Đã đưa video vào hàng đợi tải lên.');}catch(e){errorInModal(e);}};}

document.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;try{
  if(b.dataset.view)showView(b.dataset.view);
  if(b.dataset.filter){state.filter=b.dataset.filter;render();saveUI();}
  if(b.dataset.edit)await edit(b.dataset.edit);
  if(b.dataset.start)startReview(state.jobs.filter(j=>j.id===b.dataset.start));
  if(b.dataset.pause){await api('/api/pause',{ids:[b.dataset.pause]});toast('Đang tạm dừng sau phần dữ liệu hiện tại…');await refresh();}
  if(b.dataset.channel){state.channel=b.dataset.channel;showView('queue');}
  if(b.dataset.forget){const aid=b.dataset.forget;modal('Ngắt kết nối kênh?',`<p class="helper">Token trên máy sẽ bị xóa. Hàng đợi và lịch sử được giữ lại; kết nối lại bằng cùng OAuth client và kênh để tiếp tục.</p><div class="modal-footer"><button class="button danger" id="confirm-forget">Ngắt kết nối</button></div>`);$('#confirm-forget').onclick=async()=>{try{await api('/api/forget',{account:aid});closeModal();await refresh();}catch(err){errorInModal(err);}};}
  if(b.id==='first-connect')connect();
}catch(err){toast(err.message,true);}});
$('#jobs').addEventListener('change',e=>{if(e.target.dataset.select){e.target.checked?state.selected.add(e.target.dataset.select):state.selected.delete(e.target.dataset.select);render();saveUI();}});
$('#select-all').onchange=e=>{visible().forEach(j=>e.target.checked?state.selected.add(j.id):state.selected.delete(j.id));render();saveUI();};
$('#search').oninput=()=>{render();saveUI();};$('#channel-filter').onchange=()=>{state.channel=$('#channel-filter').value;render();saveUI();};
$('#import-open').onclick=importVideos;$('#empty-add').onclick=importVideos;$('#connect-open').onclick=connect;
$('#modal-close').onclick=closeModal;$('#modal').addEventListener('cancel',()=>modalGeneration++);
$('#bulk-open').onclick=bulk;$('#schedule-open').onclick=plan;$('#start-selected').onclick=()=>startReview(visibleSelection());
$('#remove-selected').onclick=async()=>{
  const jobs=visibleSelection();if(!jobs.length)return;
  if(jobs.some(j=>!removable(j))){toast('Không xóa video đang chờ tải lên, đang tải lên, hoàn thiện hoặc đã tải lên.',true);return;}
  const ids=jobs.map(j=>j.id);
  try{
    const preview=await api('/api/remove-preview',{ids});
    modal('Xóa '+jobs.length+' bản nháp?',`<p class="helper">Toàn bộ thư mục chứa video, ảnh và nội dung sẽ được chuyển ra ngoài thư mục liên kết. File không bị xóa vĩnh viễn; bản nháp sẽ biến mất khỏi hàng đợi và không được tự quét lại từ thư mục cũ.</p><div class="review-list">${preview.moved.map(p=>`<div class="review-item" style="overflow-wrap:anywhere"><strong>Từ:</strong> ${esc(p.source)}<br><strong>Đến:</strong> ${esc(p.destination)}</div>`).join('')}</div><div class="modal-footer"><button class="button danger" id="confirm-remove">Chuyển thư mục và xóa bản nháp</button></div>`);
    const notice=document.createElement('p');notice.className='helper';notice.textContent='Thao t\u00e1c n\u00e0y kh\u00f4ng x\u00f3a video ho\u1eb7c h\u1ee7y l\u1ecbch tr\u00ean YouTube.';$('#modal-body').prepend(notice);
    $('#confirm-remove').onclick=async()=>{const button=$('#confirm-remove');button.disabled=true;try{await api('/api/remove',{ids});jobs.forEach(j=>state.selected.delete(j.id));saveUI();closeModal();await refresh();toast('Đã chuyển thư mục và xóa bản nháp.');}catch(e){errorInModal(e);button.disabled=false;}};
  }catch(e){toast(e.message,true);}
};
$('#pause-all').onclick=async()=>{try{await api('/api/pause',{ids:state.jobs.filter(active).map(j=>j.id)});toast('Đang lưu và tạm dừng các lượt tải lên…');await refresh();}catch(e){toast(e.message,true);}};
$('#download-template').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['Title:\nTên video của bạn\n\nVideo Description:\nMô tả video\n\nTags:\ntừ khóa 1, từ khóa 2'],{type:'text/plain;charset=utf-8'}));a.download='info.txt';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
window.addEventListener('storage',event=>{if(event.key===UI_STORAGE_KEY)restoreUI(event.newValue);});
window.addEventListener('focus',()=>{readUI();refresh();});
document.addEventListener('visibilitychange',()=>{if(!document.hidden){readUI();refresh();}});
readUI();refresh();setInterval(refresh,2500);
