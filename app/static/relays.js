'use strict';
(()=>{
  let env,id=null,selected=new Set();
  const $=x=>document.getElementById(x),name=c=>'['+({CHAT:'Группа',DIALOG:'Личное',CHANNEL:'Канал'}[c.type] || 'Чат')+'] '+(c.display_name || c.title || c.id);
  function options(){
    $('relaycopyoptions').hidden=$('relaymode').value!=='copy';
    $('relaylinkoptions').hidden=!$('relayaddsource').checked;
  }
  function selectedNames(){
    $('relaycount').textContent='Выбрано чатов: '+selected.size;
    $('relayselectednames').textContent=[...selected].map(k=>name(env.allChats().get(k)||{id:k})).join(' · ');
  }
  function render(){
    const dest=$('relaydestination').value,chats=env.allChats();
    $('relaydestination').replaceChildren(new Option('Выберите чат',''));
    const queryDest=$('relaydestsearch').value.toLocaleLowerCase();
    const filters={CHAT:'relaydestgroups',CHANNEL:'relaydestchannels',DIALOG:'relaydestpersonal'};
    chats.forEach((c,k)=>{if(filters[c.type] && $(filters[c.type]).checked && name(c).toLocaleLowerCase().includes(queryDest))$('relaydestination').add(new Option(name(c),k));});
    if(dest && ![...$('relaydestination').options].some(o=>o.value===dest))$('relaydestination').add(new Option('Выбрано: '+name(chats.get(dest)||{id:dest}),dest));
    $('relaydestination').value=dest;
    const all=new Map(chats);selected.forEach(k=>{if(!all.has(k))all.set(k,{id:k,type:'CHAT'});});
    $('relaysources').replaceChildren();const query=$('relaysearch').value.toLocaleLowerCase();
    all.forEach((c,k)=>{
      if(query&&!name(c).toLocaleLowerCase().includes(query))return;
      const label=document.createElement('label');label.className='checkline';const check=document.createElement('input');check.type='checkbox';check.checked=selected.has(k);
      check.onchange=()=>{if(check.checked)selected.add(k);else selected.delete(k);selectedNames();};
      const text=document.createElement('span');text.textContent=name(c);label.append(check,text);$('relaysources').append(label);
    });selectedNames();
  }
  function reset(){id=null;selected.clear();$('relaytitle').value='';$('relaydestination').value='';$('relayinterval').value='5';$('relayenabled').checked=false;$('relaysearch').value='';$('relaydestsearch').value='';$('relaymode').value='copy';$('relayaddsource').checked=true;$('relaylinktarget').value='chat';['relaydestgroups','relaydestchannels','relaydestpersonal'].forEach(k=>$(k).checked=true);options();render();}
  async function load(){
    const data=await (await env.api('relays')).json();if(!$('relaysources').children.length && env.allChats().size)render();$('relaylist').replaceChildren();
    $('relaystatus').textContent=data.error || (data.connected?'MAX подключён. Включённые правила отслеживают новые сообщения.':'MAX отключён. Новые сообщения поступят после подключения; очередь сохранена.');
    if(!data.relays.length)$('relaylist').textContent='Правил пока нет.';
    data.relays.forEach(rule=>{
      const box=document.createElement('article');box.className='incomingcard';const title=document.createElement('p');title.textContent=rule.title+' · '+(rule.mode==='forward'?'Переслать':'Создать новое')+' · '+(rule.enabled?'Включено':'Выключено')+' · '+rule.source_chat_ids.length+' чатов → '+(env.allChats().get(rule.destination)?.display_name || rule.destination);
      box.append(title,env.uiButton('Открыть',()=>{id=rule.id;selected=new Set(rule.source_chat_ids);$('relaytitle').value=rule.title;$('relaymode').value=rule.mode||'copy';$('relayaddsource').checked=rule.add_source!==false;$('relaylinktarget').value=rule.link_target||'message';options();
        if(![...$('relaydestination').options].some(o=>o.value===rule.destination))$('relaydestination').add(new Option('Чат '+rule.destination,rule.destination));
        $('relaydestination').value=rule.destination;$('relayinterval').value=rule.interval;$('relayenabled').checked=rule.enabled;$('relaysearch').value='';render();}),env.uiButton(rule.enabled?'Выключить':'Включить',async()=>{
        await env.api('relays/'+rule.id+'/enabled','PATCH',{enabled:!rule.enabled});if(id===rule.id)$('relayenabled').checked=!rule.enabled;await load();env.notice(rule.enabled?'Правило выключено. Ожидающие отправки отменены.':'Правило включено. Пересылаются только новые сообщения.');
      }),env.uiButton('Удалить',async()=>{if(!confirm('Удалить правило и отменить ожидающие отправки?'))return;await env.api('relays/'+rule.id,'DELETE');if(id===rule.id)reset();await load();},'danger'));$('relaylist').append(box);
    });
  }
  async function loadJobs(){
    const data=await (await env.api('relays?include_jobs=true&limit=100')).json();
    $('relayruns').replaceChildren();
    const states={skipped_repost:'Перепост пропущен',skipped_duplicate:'Дубль пропущен',pending:'В очереди',sending:'Отправляется',sent:'Отправлено',failed:'Ошибка',unknown:'Результат неизвестен',cancelled:'Отменено'};
    if(!data.jobs.length)$('relayruns').textContent='Новых сообщений для пересылки пока нет.';
    data.jobs.forEach(job=>{const p=document.createElement('p');p.textContent=job.title+' · '+(env.allChats().get(job.chat_id)?.display_name || 'Исходный чат')+' · '+(states[job.state] || job.state)+(job.error?' · '+job.error:'');$('relayruns').append(p);});
  }
  function init(args){env=args;$('relayruns').textContent='Нажмите кнопку, чтобы посмотреть результаты.';$('relaymode').onchange=options;$('relayaddsource').onchange=options;$('relaydestsearch').oninput=render;['relaydestgroups','relaydestchannels','relaydestpersonal'].forEach(k=>$(k).onchange=render);$('relayloadjobs').onclick=async()=>{const b=$('relayloadjobs');b.disabled=true;try{await loadJobs();}catch(e){env.notice(e.message);}finally{b.disabled=false;}};$('relaynew').onclick=reset;$('relaysearch').oninput=render;
    $('relayrefresh').onclick=()=>load().catch(e=>env.notice(e.message));
    $('relaychats').onclick=async()=>{try{await env.loadChats();render();}catch(e){env.notice(e.message);}};
    $('relayform').onsubmit=async e=>{e.preventDefault();const button=e.submitter || e.currentTarget.querySelector('[type=submit]');button.disabled=true;try{
      if(!selected.size)throw Error('Выберите хотя бы один исходный чат');
      const body={mode:$('relaymode').value,add_source:$('relayaddsource').checked,link_target:$('relaylinktarget').value,title:$('relaytitle').value.trim(),destination:$('relaydestination').value,interval:Number($('relayinterval').value),source_chat_ids:[...selected],enabled:$('relayenabled').checked};
      const rule=await (await env.api('relays'+(id?'/'+id:''),id?'PUT':'POST',body)).json();id=rule.id;await load();env.notice(rule.enabled?'Правило сохранено и включено. Ожидаются новые сообщения.':'Правило сохранено и выключено');
    }catch(e){env.notice(e.message);}finally{button.disabled=false;}};reset();
  }
  function fromChat(chatId){selected.add(String(chatId));env.showTab('relays');render();env.notice('Чат добавлен в источники. Выберите назначение, сохраните и включите правило.');}
  globalThis.RelayUI={init,load,fromChat};
})();
