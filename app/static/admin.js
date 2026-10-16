'use strict';
(()=>{
 let env;
 const $=x=>document.getElementById(x);
 function showKey(key){$('newuserkey').value=key;$('newkeybox').hidden=false;$('newuserkey').focus();$('newuserkey').select();}
 const statsLabels={messages_received:'Получено сообщений',saved_messages:'Сохранённых сообщений',favorites:'Избранных чатов',schedules_enabled:'Включённых расписаний',relay_rules:'Правил пересылки',relays_enabled:'Включённых пересылок',api_requests:'Запросов API',api_errors:'Ошибок API',disk_bytes:'Объём данных, байт',direct_sent:'Прямых отправок API',direct_unknown:'Прямых отправок с неизвестным результатом'};
 const states={skipped_repost:'Перепост пропущен',skipped_duplicate:'Дубль пропущен',pending:'Ожидает',sending:'Отправляется',sent:'Отправлено',unknown:'Неизвестный результат',failed:'Ошибка',cancelled:'Отменено'};
 async function load(){
  const data=await (await env.api('admin/users')).json();$('adminusers').replaceChildren();
  $('admintotals').textContent='Пользователей: '+(data.users.length-1)+' · Активных: '+data.users.filter(u=>u.id!=='admin'&&u.enabled).length+' · Время работы: '+Math.floor(data.uptime_seconds/60)+' мин.';
  const total=document.createElement('p');total.textContent='Всего: сообщений '+data.totals.messages_received+' · Сохранённых '+data.totals.saved_messages+' · Расписаний '+data.totals.schedules_enabled+' · Включённых пересылок '+data.totals.relays_enabled+' · Запросов API '+data.totals.api_requests+' · Ошибок '+data.totals.api_errors+' · Данных '+data.totals.disk_bytes+' байт';$('adminusers').append(total);
  const daily=document.createElement('details'),label=document.createElement('summary');label.textContent='Запросы и ошибки по дням (UTC)';daily.append(label);data.daily.forEach(day=>{const p=document.createElement('p');p.textContent=day.day+' · Запросов '+day.requests+' · Ошибок '+day.errors;daily.append(p);});$('adminusers').append(daily);
  data.users.forEach(user=>{
   const card=document.createElement('article');card.className='incomingcard';const heading=document.createElement('h3');heading.textContent=user.name+' · '+(user.enabled?'Доступ разрешён':'Заблокирован');
   const info=document.createElement('p');info.textContent='MAX: '+user.max_state+' · Последний запрос: '+(user.last_seen?new Date(user.last_seen*1000).toLocaleString():'—');card.append(heading,info);
   const list=document.createElement('dl');list.className='adminstats';Object.entries(statsLabels).forEach(([key,label])=>{const term=document.createElement('dt'),value=document.createElement('dd');term.textContent=label;value.textContent=user.stats[key] || 0;list.append(term,value);});card.append(list);
   for(const [key,label] of [['campaign_deliveries','Отправки сохранённых сообщений'],['relay_deliveries','Автопересылки']]){const p=document.createElement('p');p.textContent=label+': '+(Object.entries(user.stats[key] || {}).map(([state,count])=>(states[state] || state)+' '+count).join(' · ') || 'нет');card.append(p);}
   card.append(env.uiButton('Открыть панель',()=>env.manageAccount(user.id,user.name)));
   if(user.id!=='admin')card.append(env.uiButton('Переименовать',async()=>{const name=prompt('Имя пользователя',user.name);if(!name?.trim())return;await env.api('admin/users/'+user.id,'PATCH',{name:name.trim()});await load();}),env.uiButton('Заменить ключ',async()=>{if(!confirm('Отозвать прежний ключ пользователя и создать новый?'))return;const r=await (await env.api('admin/users/'+user.id+'/key','POST')).json();showKey(r.api_key);await load();}),env.uiButton(user.enabled?'Заблокировать':'Разблокировать',async()=>{await env.api('admin/users/'+user.id+'/enabled','PATCH',{enabled:!user.enabled});await load();env.notice('Доступ пользователя изменён');}),env.uiButton('Удалить пользователя',async()=>{if(!confirm('Удалить пользователя «'+user.name+'» и отозвать ключ? Фоновые задачи будут остановлены, данные останутся в отдельной папке на сервере.'))return;await env.api('admin/users/'+user.id,'DELETE');await load();},'danger'));
   $('adminusers').append(card);
  });
  const actions={create:'Создание пользователя',rename:'Изменение имени',rotate_key:'Замена ключа',enable:'Разблокировка',disable:'Блокировка',delete:'Удаление'};
  $('adminaudit').replaceChildren();data.audit.forEach(row=>{const p=document.createElement('p');p.textContent=new Date(row.created*1000).toLocaleString()+' · '+(actions[row.action] || row.action)+' · '+(data.users.find(u=>u.id===row.uid)?.name || 'Удалённый пользователь');$('adminaudit').append(p);});
 }
 function init(args){env=args;$('hideuserkey').onclick=()=>{$('newuserkey').value='';$('newkeybox').hidden=true;};$('adminrefresh').onclick=()=>load().catch(e=>env.notice(e.message));
 $('admincreate').onsubmit=async e=>{e.preventDefault();const button=e.currentTarget.querySelector('button');button.disabled=true;try{const data=await (await env.api('admin/users','POST',{name:$('adminname').value.trim()})).json();$('adminname').value='';showKey(data.api_key);await load();}catch(err){env.notice(err.message);}finally{button.disabled=false;}};
 }
 globalThis.AdminUI={init,load};
})();
