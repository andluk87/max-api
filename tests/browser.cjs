// Isolated local server only: no MAX session. npm install --no-save playwright
// BASE_URL=http://127.0.0.1:18087 TEST_API_KEY=... node tests/browser.cjs
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1380,height:960}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const base=process.env.BASE_URL || 'http://127.0.0.1:18087';
  const key=process.env.TEST_API_KEY || 'browser-test-key-000000000000000000000';
  const headers={Authorization:'Bearer '+key};
  const check=async(predicate)=>{for(let n=0;n<80;n++){if(await predicate())return;await page.waitForTimeout(100);}throw Error('Timed out waiting for UI');};
  const read=async url=>(await page.request.get(base+url,{headers})).json();
  const unlock=async()=>{await page.locator('#key').fill(key);await page.locator('#unlock').click();await check(async()=>(await page.locator('#notice').textContent()).includes('Панель подключена'));};
  try {
    assert.equal((await read('/api/status')).state,'disconnected','Use an isolated server');
    await page.route('**/api/chats*',route=>{
      const first={id:'10',type:'CHAT',display_name:'Команда',title:'Команда',last_event_time:100};
      return route.fulfill({json:route.request().url().includes('marker=100')?
        {chats:[first,{id:'20',type:'DIALOG',display_name:'Иван',last_event_time:90}],complete:true,next_marker:null}:
        {chats:[first],complete:false,next_marker:'100'}});
    });
    await page.goto(base);await unlock();
    assert.equal(await page.locator('[role=tabpanel]:visible').count(),1);
    await page.locator('#tab-editor').click();await page.locator('#chats').click();
    await check(async()=>(await page.locator('#chatprogress').textContent()).includes('Загрузка завершена'));
    assert.equal(await page.locator('.chatrow').count(),2);
    await page.locator('.chatrow').filter({hasText:'Команда'}).locator('input').check();
    await page.getByRole('button',{name:'В избранное: Иван',exact:true}).click();
    await check(async()=>(await read('/api/favorite-chats')).chats.length===1);
    assert.match(await page.locator('#selectedcount').textContent(),/1/); // starring isn't selecting
    await page.locator('#tab-favorites').click();assert.equal(await page.locator('#panel-editor').isVisible(),false);
    await check(async()=>(await page.locator('#favoritelist').textContent()).includes('Иван'));
    await page.reload();await unlock(); // favorites survive page reload without fetching MAX chats
    await page.locator('#tab-favorites').click();await page.locator('#newfromfavorites').click();
    assert.equal(await page.locator('#panel-editor').isVisible(),true);
    assert.match(await page.locator('#selectedcount').textContent(),/1/);
    assert.equal(await page.locator('.chatrow input:checked').count(),1);
    await page.locator('#chats').click();await page.locator('#chatscope').selectOption('all');
    await check(async()=>await page.locator('.chatrow').count()===2);
    await page.locator('.chatrow').filter({hasText:'Команда'}).locator('input').check();
    const title='Tabs test '+Date.now();await page.locator('#title').fill(title);
    await page.locator('#text').fill('Привет');
    await page.locator('#text').evaluate(e=>{e.focus();e.setSelectionRange(0,e.value.length);});
    await page.locator('#fmtbold').click();await page.locator('#emojiwave').click();
    assert.equal(await page.locator('#text').inputValue(),'**Привет**👋');
    await page.locator('#photo').setInputFiles(path.join(__dirname,'fixtures/photo.png'));
    await page.locator('#interval').evaluate(e=>{e.value='30';e.dispatchEvent(new Event('input'));});
    await page.locator('#schedulekind').selectOption('weekly');
    await page.locator('#weekdays label').filter({hasText:'Вт'}).locator('input').uncheck();
    await page.locator('#weekdays label').filter({hasText:'Чт'}).locator('input').uncheck();
    await page.locator('#schedulehour').selectOption('9');await page.locator('#scheduleminute').selectOption('30');
    await page.locator('#timezone').selectOption('Asia/Anadyr');await page.locator('#cronenabled').check();
    await page.locator('#previewcron').click();
    await check(async()=>(await page.locator('#cronpreview').textContent()).includes('Asia/Anadyr'));
    assert.match(await page.locator('#cronpreview').textContent(),/09:30/);
    assert.equal(await page.locator('input#cron').count(),0);
    await page.locator('#savesaved').click();await check(async()=>(await page.locator('#notice').textContent()).includes('Сообщение сохранено'));
    const draft=(await read('/api/saved-messages')).messages.find(x=>x.title===title);
    assert.deepEqual(draft.chat_ids,['20','10']);assert.equal(draft.cron,'30 9 * * 1,3,5');
    assert.equal(draft.timezone,'Asia/Anadyr');assert.equal(draft.has_photo,true);
    await page.locator('#tab-saved').click();
    const row=page.locator('#savedrows tr').filter({hasText:title});await row.waitFor();
    assert.match(await row.locator('.nextrun').textContent(),/09:30/);
    assert.match(await row.locator('.nextrun').textContent(),/Asia\/Anadyr/);
    if(process.env.SCREENSHOT)await page.screenshot({path:process.env.SCREENSHOT,fullPage:true});
    await row.getByRole('button',{name:'Выключить',exact:true}).click();
    await check(async()=>!(await read('/api/saved-messages/'+draft.id)).enabled);
    await row.getByRole('button',{name:'Включить',exact:true}).click();
    await check(async()=>(await read('/api/saved-messages/'+draft.id)).enabled);
    await row.getByRole('button',{name:'Отправить',exact:true}).click();
    await check(async()=>await page.locator('#panel-queue').isVisible());
    const box=page.locator('details.run').filter({hasText:title});await box.locator('summary').click();
    await box.getByRole('button',{name:'Отменить оставшиеся'}).click();
    await check(async()=>(await read('/api/send-runs')).runs.find(x=>x.draft_id===draft.id).state==='cancelled');
    await page.locator('#tab-saved').click();await row.getByRole('button',{name:'Открыть',exact:true}).click();
    await check(async()=>(await page.locator('#notice').textContent()).includes('Сообщение открыто'));
    assert.equal(await page.locator('#schedulekind').inputValue(),'weekly');
    assert.equal(await page.locator('#timezone').inputValue(),'Asia/Anadyr');
    assert.equal(await page.locator('#weekdays input:checked').count(),3);
    await page.locator('#title').fill(title+' edited');await page.locator('#removephoto').click();
    await page.locator('#savesaved').click();await check(async()=>!(await read('/api/saved-messages/'+draft.id)).has_photo);
    assert.equal((await read('/api/saved-messages/'+draft.id)).next_run,draft.next_run);
    // Preserve a complex pre-existing schedule when only the message is edited.
    const legacy=(await (await page.request.post(base+'/api/saved-messages',{headers,multipart:{payload:JSON.stringify({title:'Legacy '+title,text:'old',chat_ids:['20'],cron:'0 9 1,15 * *',timezone:'Europe/Berlin',enabled:false})}})).json());
    await page.locator('#tab-saved').click();await page.locator('#refreshsaved').click();
    const legacyRow=page.locator('#savedrows tr').filter({hasText:'Legacy '+title});
    await legacyRow.getByRole('button',{name:'Открыть',exact:true}).click();
    await check(async()=>await page.locator('#legacygroup').isVisible());
    assert.equal(await page.locator('#timezone').inputValue(),'Europe/Berlin');
    await page.locator('#text').fill('changed');await page.locator('#savesaved').click();
    await check(async()=>(await read('/api/saved-messages/'+legacy.id)).text==='changed');
    assert.equal((await read('/api/saved-messages/'+legacy.id)).cron,'0 9 1,15 * *');
    await page.locator('#schedulekind').selectOption('monthly');await page.locator('#monthday').selectOption('L');
    await page.locator('#schedulehour').selectOption('10');await page.locator('#scheduleminute').selectOption('15');
    await page.locator('#savesaved').click();await check(async()=>(await read('/api/saved-messages/'+legacy.id)).cron==='15 10 L * *');
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.locator('#tab-saved').click();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    page.once('dialog',d=>d.accept());await row.getByRole('button',{name:'Удалить',exact:true}).click();
    await check(async()=>(await page.request.get(base+'/api/saved-messages/'+draft.id,{headers})).status()===404);
    await page.locator('#tab-favorites').click();await page.locator('#favoritelist').getByRole('button',{name:'Убрать'}).click();
    await check(async()=>(await read('/api/favorite-chats')).chats.length===0);
    assert.deepEqual((await read('/api/saved-messages/'+legacy.id)).chat_ids,['20']);
    await page.request.delete(base+'/api/saved-messages/'+legacy.id,{headers});
    assert.deepEqual(errors,[]);
    console.log('Browser: tabs, favorites persistence, visual schedules, Anadyr, saved launch times, legacy preservation, CRUD, queue, mobile passed');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
