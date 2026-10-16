'use strict';
const $ = id => document.getElementById(id);
let managedUser='admin',scopeEpoch=0,isAdministrator=false;
let key = '', timer, qrUrl, busy = false, activeTab = 'connect';
function notice(text) { $('notice').textContent = text; }
async function api(path, method='GET', body) {
  const epoch=scopeEpoch;
  const multipart = body instanceof FormData;
  let response = await fetch('/api/' + path, {method, headers: {
    Authorization: 'Bearer ' + key, ...(isAdministrator && managedUser!=='admin' && !path.startsWith('admin/')?{'X-User-ID':managedUser}:{}), ...(body && !multipart ? {'Content-Type':'application/json'} : {})
  }, ...(body ? {body: multipart ? body : JSON.stringify(body)} : {})});
  const bytes=await response.arrayBuffer();
  if(epoch!==scopeEpoch)throw Error('Аккаунт изменён; предыдущий запрос отменён');
  response=new Response(response.status===204?null:bytes,{status:response.status,headers:response.headers});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const requestId=response.headers.get('X-Request-ID');
    throw new Error((typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map(e => e.loc.join('.') + ': ' + e.msg).join('; ') : 'Ошибка HTTP ' + response.status)+(requestId?' · Запрос: '+requestId:''));
  }
  return response;
}
const tabNames=new Set(['connect','editor','saved','favorites','queue','events','relays','logs','admin']);
function tabFromAddress(){return location.hash.slice(1)||'connect';}
function showTab(name,historyMode='push') {
  if(!tabNames.has(name)||(name==='admin'&&!isAdministrator)){name='connect';historyMode='replace';}
  const fragment='#'+name;
  if(location.hash!==fragment){
    const url=new URL(location.href);url.hash=fragment;
    history[historyMode==='replace'?'replaceState':'pushState'](null,'',url);
  }
  activeTab=name;
  document.querySelectorAll('[data-tab]').forEach(button=>{
    const selected=button.dataset.tab===name;
    button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;
    $('panel-'+button.dataset.tab).hidden=!selected;
  });
  if(key) {
    const loader={saved:loadSaved,favorites:loadFavorites,queue:loadRuns,logs:()=>loadLogs(),events:loadInbox,relays:()=>RelayUI.load(),admin:()=>AdminUI.load()}[name];
    if(loader) loader().catch(e=>notice(e.message));
  }
}
window.addEventListener('hashchange',()=>{
  if($('workspace').hidden)return; // Keep a deep link until authentication succeeds.
  showTab(tabFromAddress(),'replace');
});
const tabs=[...document.querySelectorAll('[data-tab]')];
tabs.forEach((button,index)=>{
  button.addEventListener('click',()=>showTab(button.dataset.tab));
  button.addEventListener('keydown',e=>{
    let target;
    if(e.key==='ArrowRight')target=(index+1)%tabs.length;
    if(e.key==='ArrowLeft')target=(index-1+tabs.length)%tabs.length;
    if(e.key==='Home')target=0;if(e.key==='End')target=tabs.length-1;
    if(target!==undefined){e.preventDefault();tabs[target].focus();showTab(tabs[target].dataset.tab);}
  });
});
function uiButton(text, handler, className='secondary') {
  const button=document.createElement('button');button.type='button';button.className=className;button.textContent=text;
  button.addEventListener('click',async()=>{button.disabled=true;try{await handler();}catch(e){notice(e.message);}finally{button.disabled=false;}});
  return button;
}
const labels = {disconnected:'Отключён',connecting:'Подключение…',qr_pending:'Ожидает QR',password_required:'Нужен пароль 2FA',authenticating:'Проверка пароля…',connected:'Аккаунт подключён',reconnecting:'Переподключение…',error:'Ошибка входа'};
async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const state = await (await api('status')).json();
    $('status').textContent = labels[state.state] || state.state;
    $('passwordForm').hidden = state.state !== 'password_required';
    $('hint').textContent = state.password_hint || '';
    if (state.qr_available) {
      const blob = await (await api('auth/qr')).blob();
      if (qrUrl) URL.revokeObjectURL(qrUrl);
      qrUrl = URL.createObjectURL(blob);
      $('qr').src = qrUrl; $('qr').hidden = false; $('qrtext').hidden = true;
    } else {
      $('qr').hidden = true; $('qrtext').hidden = false;
      $('qrtext').textContent = state.state === 'connected' ? '✓ Сессия активна' : 'QR не активен';
    }
    if (state.error) notice('Вход не завершён: ' + state.error + '. Нажмите «Начать вход / повторить».');
  } finally { busy = false; }
}
function action(id, fn, event='click') {
  $(id).addEventListener(event, async e => {
    e.preventDefault();
    const button = e.currentTarget.tagName === 'BUTTON' ? e.currentTarget : e.currentTarget.querySelector('button[type=submit]') || e.currentTarget.querySelector('button');
    if (button) button.disabled = true;
    try { await fn(); } catch (err) { notice(err.message); }
    finally { if (button) button.disabled = false; }
  });
}
action('loginform', async () => {
  $('loginerror').textContent='';
  try {
  clearInterval(timer); key = $('key').value; $('key').value = '';
  const identity=await (await api('whoami')).json();
  isAdministrator=identity.role==='admin';$('tab-admin').hidden=!isAdministrator;
  $('accountlabel').textContent=(isAdministrator?'Администратор · ':'Пользователь · ')+identity.user.name;
  await refresh();
  $('loginview').hidden=true; $('workspace').hidden=false; document.body.classList.remove('locked');
  showTab(tabFromAddress(),'replace');
  await loadFavorites(); await loadSaved(); await loadRuns(); notice('Панель подключена');
  let tick=0;
  timer = setInterval(() => { refresh().catch(e => notice(e.message)); if(activeTab==='relays')RelayUI.load().catch(e=>notice(e.message)); if(activeTab==='queue')loadRuns().catch(e=>notice(e.message)); if(activeTab==='saved' && ++tick%4===0)loadSaved().catch(e=>notice(e.message)); }, 2500);
  } catch(e) { if($('workspace').hidden){key='';$('loginerror').textContent=e.message;$('key').focus();}else notice(e.message); }
}, 'submit');
action('start', async () => { await api('auth/start','POST'); await refresh(); });
action('stop', async () => { await api('auth/stop','POST'); await refresh(); });
action('end-session', async () => {
  const result = await (await api('auth/logout','POST')).json();
  await refresh();
  notice(result.remote_revoked ? 'Сессия MAX завершена. Для входа потребуется QR.' : 'Локальная сессия удалена. Отзыв устройства в MAX не подтверждён; завершите его в приложении MAX.');
});
action('passwordForm', async () => {
  const password = $('password').value; $('password').value = '';
  await api('auth/password','POST',{password}); await refresh();
}, 'submit');
const directory = new Map(), selectedChats = new Map(), favorites = new Map();
function allChats() {
  const result=new Map([...favorites].map(([id,c])=>[id,{id,type:c.type,display_name:c.title}]));
  directory.forEach((c,id)=>result.set(id,c));return result;
}
function visibleChats() {
  const query = $('chatsearch').value.toLocaleLowerCase();
  const type = $('chattype').value;
  return [...allChats().values()].filter(c => ($('chatscope').value!=='favorites' || favorites.has(String(c.id))) && (!type || c.type === type) &&
    [c.display_name, c.title, c.id, c.peer_id].filter(Boolean).join(' ').toLocaleLowerCase().includes(query))
    .sort((a,b) => (b.last_event_time || 0) - (a.last_event_time || 0));
}
function renderChats() {
  const types = {DIALOG:'Личное',CHAT:'Группа',CHANNEL:'Канал'};
  $('chatchoices').replaceChildren();
  visibleChats().forEach(c => {
    const id=String(c.id),row=document.createElement('div');row.className='chatrow';
    const label=document.createElement('label');label.className='chatselect';
    const input=document.createElement('input');input.type='checkbox';input.checked=selectedChats.has(id);
    const name=c.display_name || c.title || 'Чат';
    input.addEventListener('change',()=>{if(input.checked)selectedChats.set(id,name);else selectedChats.delete(id);renderChats();});
    const text=document.createElement('span');text.textContent='['+(types[c.type] || c.type)+'] '+name+' · '+id;
    label.append(input,text);
    const star=uiButton(favorites.has(id)?'★':'☆',async()=>{
      if(favorites.has(id)){await api('favorite-chats/'+id,'DELETE');favorites.delete(id);}
      else {const data=await (await api('favorite-chats/'+id,'PUT',{title:name,type:c.type || 'CHAT'})).json();favorites.set(id,data);}
      renderChats();renderFavorites();
    },'star');
    star.setAttribute('aria-label',(favorites.has(id)?'Убрать из избранного: ':'В избранное: ')+name);
    star.setAttribute('aria-pressed',String(favorites.has(id)));
    row.append(label,star);$('chatchoices').append(row);
    if (selectedChats.has(id)) selectedChats.set(id,name);
  });
  $('selectedcount').textContent = 'Выбрано чатов: ' + selectedChats.size;
  $('selectedchips').replaceChildren();
  selectedChats.forEach((name,id) => {
    const button = document.createElement('button'); button.type='button'; button.textContent=name+' · '+id+' ×';
    button.addEventListener('click', () => {selectedChats.delete(id); renderChats();});
    $('selectedchips').append(button);
  });
}
action('selectvisible', () => { visibleChats().forEach(c => selectedChats.set(String(c.id),c.display_name || c.title || 'Чат')); renderChats(); });
action('clearselection', () => { selectedChats.clear(); renderChats(); });
action('addchat', () => {
  const ids = $('chat').value.split(/[\s,;]+/).filter(Boolean);
  if (!ids.length || ids.some(id => !/^-?\d+$/.test(id))) throw new Error('Введите числовые ID чатов через запятую');
  ids.forEach(id => { const normalized = BigInt(id).toString(); selectedChats.set(normalized,allChats().get(normalized)?.display_name || 'Чат'); if(!allChats().has(normalized))directory.set(normalized,{id:normalized,type:'CHAT',display_name:'Чат'}); });
  $('chat').value=''; renderChats();
});
$('chatsearch').addEventListener('input', renderChats);
$('chattype').addEventListener('change', renderChats);
$('chatscope').addEventListener('change', renderChats);
action('selectfavorites',()=>{favorites.forEach((c,id)=>selectedChats.set(id,c.title));renderChats();});
async function loadChats() {
  let marker = null, pages = 0;
  const seen = new Set();
  const warnings = new Set();
  directory.clear(); renderChats();
  try {
    while (true) {
      $('chatprogress').textContent = 'Загрузка страницы ' + (pages + 1) + '… Найдено: ' + directory.size;
      const data = await (await api('chats' + (marker ? '?marker=' + encodeURIComponent(marker) : ''))).json();
      data.chats.forEach(c => directory.set(String(c.id), c));
      pages++; renderChats();
      if (data.warning) warnings.add(data.warning);
      if (data.complete) {
        const dialogs = [...directory.values()].filter(c => c.type === 'DIALOG').length;
        $('chatprogress').textContent = 'Загрузка завершена: ' + directory.size + ', личных: ' + dialogs;
        notice(warnings.size ? [...warnings].join(' ') : 'Все страницы чатов загружены');
        break;
      }
      if (!data.next_marker || seen.has(data.next_marker)) {
        $('chatprogress').textContent = 'Загружено: ' + directory.size + '. Полнота списка не подтверждена.';
        notice([...warnings].join(' ') || 'MAX повторил маркер страницы. Повторите загрузку.');
        break;
      }
      marker = data.next_marker; seen.add(marker);
    }
  } catch (err) {
    $('chatprogress').textContent = 'Загрузка прервана. Показано: ' + directory.size + '. Нажмите «Загрузить все чаты» для повтора.';
    throw err;
  }
}
action('chats',loadChats);
async function loadFavorites() {
  const data=await (await api('favorite-chats')).json();favorites.clear();
  data.chats.forEach(c=>favorites.set(String(c.chat_id),c));renderChats();renderFavorites();
}
function renderFavorites() {
  $('favoritelist').replaceChildren();$('favoriteempty').hidden=favorites.size>0;
  favorites.forEach((c,id)=>{
    const row=document.createElement('div');row.className='favoriteitem';
    const text=document.createElement('span');text.textContent='★ '+c.title+' · '+id;
    row.append(text,uiButton('Убрать',async()=>{await api('favorite-chats/'+id,'DELETE');favorites.delete(id);renderFavorites();renderChats();}));
    $('favoritelist').append(row);
  });
}
action('newfromfavorites',()=>{
  if(!favorites.size)throw Error('Сначала добавьте чаты в избранное');
  newDraft();favorites.forEach((c,id)=>selectedChats.set(id,c.title));renderChats();
});
function insertText(before, after='', placeholder='текст') {
  const input = $('text');
  const start = input.selectionStart, end = input.selectionEnd;
  const selected = placeholder === '' ? '' : (input.value.slice(start, end) || placeholder);
  const replacement = before + selected + after;
  if (input.value.length - (end - start) + replacement.length > 4000) {
    notice('Текст должен быть не длиннее 4000 символов'); return;
  }
  input.setRangeText(replacement, start, end, 'end'); input.focus();
}
[['fmtbold','**'],['fmtitalic','_'],['fmtunderline','__'],['fmtstrike','~~'],['fmtcode','`']]
  .forEach(([id,marker]) => action(id, () => insertText(marker,marker)));
action('fmtlink', () => insertText('[','](https://example.com)','название ссылки'));
action('fmtquote', () => {
  const input = $('text');
  const prefix = input.selectionStart > 0 && input.value[input.selectionStart - 1] !== '\n' ? '\n> ' : '> ';
  insertText(prefix, '', 'цитата');
});
[['emojismile','😀'],['emojiwave','👋'],['emojiheart','❤️'],['emojilike','👍'],['emojifire','🔥'],['emojicheck','✅'],['emojiparty','🎉']]
  .forEach(([id,emoji]) => action(id, () => insertText(emoji,'','')));
let importedPhoto=null;
let photoUrl, savedId = null, savedHasPhoto = false, photoAction = 'keep';
function clearPhoto() {
  importedPhoto=null;
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl = null; $('photo').value = ''; $('photobox').hidden = true;
  $('photopreview').removeAttribute('src'); $('photoname').textContent = '';
}
async function showSavedPhoto() {
  if (!savedId || !savedHasPhoto) return;
  const blob=await (await api('saved-messages/'+savedId+'/photo')).blob();
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl=URL.createObjectURL(blob); $('photopreview').src=photoUrl; $('photobox').hidden=false;
  $('photoname').textContent='Сохранённая картинка';
}
$('photo').addEventListener('change', async () => {
  importedPhoto=null;
  const file = $('photo').files[0];
  if (!file || file.size > 10 * 1024 * 1024) {
    clearPhoto(); photoAction=savedHasPhoto ? 'keep' : 'remove';
    if(file) notice('Картинка должна быть не больше 10 МиБ');
    try { await showSavedPhoto(); } catch(e) {notice(e.message);}
    return;
  }
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoAction='replace'; photoUrl = URL.createObjectURL(file); $('photopreview').src = photoUrl;
  $('photoname').textContent = file.name; $('photobox').hidden = false;
});
action('removephoto', () => {clearPhoto(); savedHasPhoto=false; photoAction='remove';});
$('interval').addEventListener('input', () => { $('intervalvalue').textContent=$('interval').value; });
function inZone(timestamp, timezone) {
  return new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'medium',timeZone:timezone}).format(new Date(timestamp*1000));
}
let savedMessages=[], savedGeneration=0;
async function loadSaved() {
  const generation=++savedGeneration,data=await (await api('saved-messages')).json();
  if(generation===savedGeneration){savedMessages=data.messages;renderSaved();}
  return data.messages;
}
function renderSaved() {
  const query=$('savedsearch').value.toLocaleLowerCase();
  const messages=savedMessages.filter(d=>d.title.toLocaleLowerCase().includes(query));
  $('savedrows').replaceChildren();$('savedempty').hidden=messages.length>0;
  $('savedempty').textContent=savedMessages.length?'Нет сообщений по этому запросу.':'Сохранённых сообщений пока нет.';
  messages.forEach(d=>{
    const row=document.createElement('tr');row.dataset.id=d.id;
    const title=document.createElement('td');const strong=document.createElement('strong');strong.textContent=d.title;
    const preview=document.createElement('small');preview.textContent=(d.has_photo?'▧ Картинка · ':'')+(d.text.slice(0,90) || 'Без подписи');title.append(strong,preview);
    const schedule=document.createElement('td');schedule.textContent=(d.enabled?'Включено · ':'Выключено · ')+ScheduleBuilder.describe(d.cron);
    const next=document.createElement('td');next.className='nextrun';
    next.textContent=d.enabled && d.next_run?inZone(d.next_run,d.timezone):'—';
    const zone=document.createElement('small');zone.textContent=d.timezone;next.append(zone);
    const count=document.createElement('td');count.textContent=String(d.chat_ids.length);
    const actions=document.createElement('td');actions.className='tableactions';
    actions.append(uiButton('Открыть',()=>openSaved(d.id)),uiButton('Отправить',async()=>{
      await api('saved-messages/'+d.id+'/send','POST');showTab('queue');notice('Сохранённое сообщение поставлено в очередь');
    }),uiButton(d.enabled?'Выключить':'Включить',async()=>{
      await api('saved-messages/'+d.id+'/schedule','PATCH',{enabled:!d.enabled});await loadSaved();
    }),uiButton('Удалить',()=>deleteSaved(d.id),'danger'));
    row.append(title,schedule,next,count,actions);$('savedrows').append(row);
  });
}
$('savedsearch').addEventListener('input',renderSaved);
function updateSavedInfo(d) {
  $('editinglabel').textContent='Редактируется: '+d.title;
  $('savedinfo').textContent=d.enabled ? 'Следующий запуск: '+inZone(d.next_run,d.timezone)+' ('+d.timezone+'). '+(d.schedule_note || '') : 'Расписание выключено';
}
function newDraft(switchTab=true) {
  savedId=null; savedHasPhoto=false; photoAction='keep'; clearPhoto();
  $('title').value=''; $('text').value=''; $('interval').value='5'; $('intervalvalue').textContent='5';
  $('cronenabled').checked=false; $('timezone').value='Asia/Anadyr';ScheduleBuilder.load();
  $('editinglabel').textContent='Новое сообщение'; $('savedinfo').textContent='Новое сообщение ещё не сохранено';
  $('cronpreview').textContent=''; selectedChats.clear();$('chatscope').value=favorites.size?'favorites':'all';renderChats();if(switchTab)showTab('editor');
}
action('newsaved',()=>newDraft());
action('neweditor',()=>newDraft());
action('refreshsaved', loadSaved);
async function openSaved(id) {
  const d=await (await api('saved-messages/'+id)).json();
  savedId=id; savedHasPhoto=d.has_photo; photoAction='keep'; clearPhoto();
  $('title').value=d.title; $('text').value=d.text; $('interval').value=String(d.interval_seconds);
  $('intervalvalue').textContent=String(d.interval_seconds);
  if(![...$('timezone').options].some(x=>x.value===d.timezone))$('timezone').add(new Option(d.timezone,d.timezone));
  $('timezone').value=d.timezone; $('cronenabled').checked=d.enabled;ScheduleBuilder.load(d.cron);$('cronpreview').textContent='';
  selectedChats.clear(); d.chat_ids.forEach(id=>selectedChats.set(id,allChats().get(id)?.display_name || 'Чат'));
  renderChats();updateSavedInfo(d);showTab('editor');
  await showSavedPhoto();
  notice('Сообщение открыто. Изменения применятся после сохранения.');
}
async function deleteSaved(id) {
  if (!window.confirm('Удалить сообщение, его расписание и отменить ещё не начатые отправки?')) return;
  await api('saved-messages/'+id,'DELETE'); if (savedId===id)newDraft(false);
  await loadSaved();await loadRuns();notice('Сообщение удалено');
}
async function saveDraft() {
  const file=$('photo').files[0] || importedPhoto;
  if (!$('text').value.trim() && !file && !savedHasPhoto) throw new Error('Введите текст или выберите картинку');
  const payload={title:$('title').value.trim(), text:$('text').value,
    chat_ids:[...selectedChats.keys()],interval_seconds:Number($('interval').value),
    cron:ScheduleBuilder.read(),timezone:$('timezone').value.trim(),enabled:$('cronenabled').checked,
    photo_action:file ? 'replace' : photoAction};
  if (!payload.title) throw new Error('Введите название сообщения');
  const form=new FormData(); form.append('payload',JSON.stringify(payload)); if (file) form.append('photo',file);
  const d=await (await api('saved-messages'+(savedId ? '/'+savedId : ''),savedId ? 'PUT' : 'POST',form)).json();
  savedId=d.id; savedHasPhoto=d.has_photo; photoAction='keep'; $('photo').value=''; importedPhoto=null;
  updateSavedInfo(d); await loadSaved(); return d;
}
let editorBusy=false;
async function editorAction(sendNow) {
  if (editorBusy) return;
  editorBusy=true; $('savesaved').disabled=true; $('sendbutton').disabled=true;
  try {
    if (sendNow && !selectedChats.size) throw new Error('Выберите хотя бы один чат');
    const d=await saveDraft();
    if (sendNow) {
      const run=await (await api('saved-messages/'+d.id+'/send','POST')).json();
      await loadRuns();showTab('queue'); notice('Отправка поставлена в очередь: '+run.deliveries.length+' чатов. Пауза '+run.interval_seconds+' сек.');
    } else notice('Сообщение сохранено'+(d.enabled ? ', расписание включено' : ''));
  } finally {editorBusy=false; $('savesaved').disabled=false; $('sendbutton').disabled=false;}
}
action('savesaved', () => editorAction(false));
action('send', () => editorAction(true), 'submit');
action('previewcron', async () => {
  const data=await (await api('schedule/preview','POST',{cron:ScheduleBuilder.read(),timezone:$('timezone').value.trim()})).json();
  $('cronpreview').textContent=data.upcoming.map(t=>inZone(t,data.timezone)).join(' · ')+' ('+data.timezone+')';
});
let runsBusy=false;
const sendStates={queued:'В очереди',running:'Отправляется',completed:'Завершено',partial:'Есть ошибки или неподтверждённые отправки',cancelled:'Отменено',pending:'Ожидает',sending:'Отправляется',sent:'Отправлено',failed:'Не отправлено',unknown:'Результат неизвестен'};
async function loadRuns() {
  if (runsBusy) return;
  runsBusy=true;
  try {
    const data=await (await api('send-runs')).json();
    $('queuestatus').textContent=data.scheduler_error ? 'Ошибка планировщика: '+data.scheduler_error :
      data.connected ? 'Очередь работает. Интервал отсчитывается после завершения предыдущей отправки.' : 'MAX не подключён: ручные отправки ожидают подключения, запуски cron пропускаются.';
    const opened=new Set([...$('runs').querySelectorAll('details[open]')].map(x=>x.dataset.id));
    $('runs').replaceChildren();
    data.runs.forEach(run=>{
      const box=document.createElement('details'); box.className='run'; box.dataset.id=run.id; box.open=opened.has(run.id);
      const summary=document.createElement('summary');
      summary.textContent=run.title+' · '+(sendStates[run.state] || run.state)+' · '+run.deliveries.filter(x=>x.state==='sent').length+'/'+run.deliveries.length+' · '+new Date(run.created*1000).toLocaleString();
      const list=document.createElement('ul');
      run.deliveries.forEach(item=>{const li=document.createElement('li');li.textContent=item.chat_id+' — '+(sendStates[item.state] || item.state)+(item.error ? ': '+item.error : '');list.append(li);});
      box.append(summary,list);
      if (['queued','running'].includes(run.state)) {
        const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='Отменить оставшиеся';
        button.addEventListener('click',async()=>{button.disabled=true;try{await api('send-runs/'+run.id+'/cancel','POST');await loadRuns();}catch(e){notice(e.message);button.disabled=false;}});
        box.append(button);
      }
      $('runs').append(box);
    });
  } finally {runsBusy=false;}
}
action('refreshruns',loadRuns);
let inboxGeneration=0, inboxBefore=null, inboxRows=[], inboxLive=false;
function inboxName(id) { const c=allChats().get(String(id));return c?.display_name || c?.title || 'Чат '+id; }
async function loadInbox() {
  const data=await (await api('inbox/chats')).json();
  inboxLive=(await (await api('status')).json()).state==='connected';
  if(inboxLive) await loadChats();
  const selected=$('inboxchat').value;
  const chats=new Map(data.chats.map(c=>[String(c.chat_id),c]));
  if(inboxLive)allChats().forEach((c,id)=>{if(!chats.has(id))chats.set(id,{chat_id:id});});
  $('inboxchat').replaceChildren(new Option('Выберите чат',''));
  [...chats.values()].sort((a,b)=>inboxName(a.chat_id).localeCompare(inboxName(b.chat_id),'ru')).forEach(c=>$('inboxchat').add(new Option(inboxName(c.chat_id),c.chat_id)));
  $('inboxchat').value=chats.has(selected)?selected:'';
  $('inboxstatus').textContent=(inboxLive?'История MAX. Чатов: ':'MAX отключён. Только сохранённые входящие. Чатов: ')+chats.size;
  await loadInboxMessages();
}
async function loadInboxMessages(append=false) {
  const id=$('inboxchat').value, generation=++inboxGeneration;
  $('moreevents').hidden=true;
  if(!append){inboxRows=[];$('events').replaceChildren();}
  if(!id){$('events').textContent='Выберите чат слева.';return;}
  const data=await (await api((inboxLive?'history/':'inbox/')+encodeURIComponent(id)+(append && inboxBefore?'?before='+inboxBefore:''))).json();
  if(generation!==inboxGeneration || id!==$('inboxchat').value)return;
  const seen=new Set(inboxRows.map(x=>String(x.message.id ?? x.update_id)));
  inboxRows.push(...data.messages.filter(x=>!seen.has(String(x.message.id ?? x.update_id))));inboxBefore=data.next_before;renderInbox(id);
  $('moreevents').hidden=!inboxBefore;
}
function renderInbox(id) {
  $('events').replaceChildren();
  if(!inboxRows.length){$('events').textContent=inboxLive?'MAX не вернул сообщений для этого чата.':'В этом чате нет сохранённых входящих сообщений.';return;}
  inboxRows.forEach(({message:m})=>{
    const card=document.createElement('article');card.className='incomingcard';
    const meta=document.createElement('small');
    const timestamp=Number(m.time || m.timestamp || 0);
    const date=timestamp?new Date(timestamp<1e12?timestamp*1000:timestamp):null;
    meta.textContent=inboxName(id)+(m.sender?' · Отправитель '+m.sender:'')+(date && !isNaN(date)?' · '+date.toLocaleString():'');
    const text=RichMessage.render(m.text,m.elements || []);
    card.append(meta,text);
    const attachments=m.attaches || m.attachments || [];
    card.append(RichMessage.photos(attachments));
    if(attachments.length){const note=document.createElement('small');note.textContent='Вложения: '+attachments.length+'. При импорте в редактор переносится первая фотография; пересылка копирует все фото.';card.append(note);}
    card.append(uiButton('Импортировать в сообщение',async()=>{
      if(($('title').value || $('text').value || $('photo').files.length || importedPhoto) && !window.confirm('Заменить содержимое редактора новым сообщением? Несохранённые изменения будут потеряны.'))return;
      const photos=attachments.filter(a=>String(a.type || a._type).toUpperCase()==='PHOTO');
      let file=null;
      if(photos.length){
        if(!m.id)throw Error('У сообщения нет ID для загрузки фото. Обновите историю.');
        const blob=await (await api('source/'+encodeURIComponent(id)+'/'+encodeURIComponent(m.id)+'/photo/0')).blob();
        file=new File([blob],'imported.'+({ 'image/png':'png','image/webp':'webp'}[blob.type] || 'jpg'),{type:blob.type});
      }
      newDraft();$('title').value=('Из '+inboxName(id)).slice(0,150);$('text').value=m.text || '';
      if(file){importedPhoto=file;photoAction='replace';photoUrl=URL.createObjectURL(file);$('photopreview').src=photoUrl;$('photoname').textContent=file.name;$('photobox').hidden=false;}

      notice('Создан новый черновик из входящего сообщения. Выберите получателей и сохраните.'+(photos.length>1?' Импортировано первое фото; редактор поддерживает одну картинку.':''));$('text').focus();
    }),uiButton('Автопересылка из чата',()=>RelayUI.fromChat(id)));$('events').append(card);
  });
}
action('updates',loadInbox);
action('moreevents',()=>loadInboxMessages(true));
$('inboxchat').addEventListener('change',()=>loadInboxMessages().catch(e=>notice(e.message)));

ScheduleBuilder.init();

RelayUI.init({api,notice,showTab,loadChats,allChats,uiButton});

action('logout',()=>location.reload());
async function manageAccount(uid,name){
  if(!isAdministrator)return;
  if(!confirm('Открыть аккаунт «'+name+'»? Несохранённые изменения редактора будут сброшены.'))return;
  managedUser=uid;scopeEpoch++;logRows=[];logBefore=null;$('logentries').replaceChildren();$('logstatus').textContent='';$('morelogs').hidden=true;
  directory.clear();selectedChats.clear();favorites.clear();savedMessages=[];savedGeneration++;
  inboxRows=[];inboxGeneration++;inboxBefore=null;inboxLive=false;
  clearPhoto();newDraft(false);renderSaved();renderFavorites();
  $('inboxchat').replaceChildren(new Option('Выберите чат',''));
  ['events','runs','relaylist','relayruns','adminaudit'].forEach(id=>$(id).replaceChildren());
  if(qrUrl)URL.revokeObjectURL(qrUrl);qrUrl=null;$('qr').hidden=true;$('password').value='';
  RelayUI.init({api,notice,showTab,loadChats,allChats,uiButton});
  $('accountlabel').textContent='Администратор управляет: '+name;
  showTab('connect');await loadFavorites();await loadSaved();await loadRuns();await refresh();
  notice('Открыт аккаунт: '+name);
}
AdminUI.init({api,notice,uiButton,manageAccount});

let logRows=[],logBefore=null;
async function loadLogs(append=false){
  const params=new URLSearchParams({limit:'100'});if($('loglevel').value)params.set('level',$('loglevel').value);if(append&&logBefore)params.set('before',logBefore);
  const result=await (await api('logs?'+params)).json();logRows=append?logRows.concat(result.logs):result.logs;logBefore=result.next_before;
  $('logentries').replaceChildren();
  logRows.forEach(row=>{const card=document.createElement('article');card.className='incomingcard';const title=document.createElement('strong');title.textContent=new Date(row.time).toLocaleString('ru-RU',{timeZone:'Asia/Anadyr'})+' · '+row.level+' · '+row.event;const details=document.createElement('pre');details.style.whiteSpace='pre-wrap';details.style.overflowWrap='anywhere';details.textContent=JSON.stringify(row,null,2);card.append(title,details);$('logentries').append(card);});
  $('logstatus').textContent=logRows.length?'Показано записей: '+logRows.length:'Записей по выбранному фильтру нет.';$('morelogs').hidden=!logBefore;
}
action('refreshlogs',()=>loadLogs());action('morelogs',()=>loadLogs(true));
$('loglevel').onchange=()=>loadLogs().catch(e=>notice(e.message));
$('exportlogs').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(logRows,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='max-logs-'+managedUser+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
