// Isolated server only. The paid AI test endpoint is mocked.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const base=process.env.BASE_URL||'http://127.0.0.1:18089';
  const key='browser-test-key-000000000000000000000',headers={Authorization:'Bearer '+key};
  await page.goto(base);await page.locator('#key').fill(key);await page.locator('#unlock').click();await page.locator('#workspace').waitFor();
  await page.locator('#tab-news').click();await page.locator('[data-news-view=settings]').click();
  const provider=page.locator('#news-field-provider');await provider.waitFor();
  assert.equal(await provider.inputValue(),'ollama');
  assert.equal(await page.locator('#news-field-yandex_folder_id').isVisible(),false);
  await provider.selectOption('yandex');
  assert.equal(await page.locator('#news-field-ollama').isVisible(),false);
  assert.equal(await page.locator('#news-field-yandex_folder_id').isVisible(),true);
  await page.locator('#news-field-yandex_folder_id').fill('test-folder');
  assert.equal(await page.locator('#news-field-yandex_model').inputValue(),'aliceai-llm');
  const secret=page.locator('.news-cloud-settings input[type=password]');await secret.fill('browser-dummy-private-key');
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/news/yandex-key')&&r.request().method()==='PUT'),page.getByRole('button',{name:'Сохранить ключ',exact:true}).click()]);
  await page.waitForFunction(()=>document.querySelector('.news-cloud-settings input[type=password]').value==='');
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/news/settings')&&r.request().method()==='PUT'),page.locator('#news-settings button[type=submit]').click()]);
  const state=await(await page.request.get(base+'/api/news',{headers})).json();
  assert.equal(state.settings.provider,'yandex');assert.equal(state.settings.yandex_folder_id,'test-folder');assert.equal(state.yandex_key_configured,true);
  assert.equal(JSON.stringify(state).includes('browser-dummy-private-key'),false);
  await page.route('**/api/news/ai',route=>route.fulfill({json:{provider:'yandex',models:['aliceai-llm'],message:'Яндекс отвечает. Тестовая генерация выполнена успешно.'}}));
  await page.getByRole('button',{name:'Проверить ИИ',exact:true}).click();await page.waitForFunction(()=>document.getElementById('news-feedback').textContent.includes('Яндекс отвечает'));
  await provider.selectOption('ollama');assert.equal(await secret.isVisible(),false);assert.equal(await page.locator('#news-field-model').isVisible(),true);
  await provider.selectOption('yandex');
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/news/yandex-key')&&r.request().method()==='DELETE'),page.getByRole('button',{name:'Удалить ключ',exact:true}).click()]);
  assert.equal((await(await page.request.get(base+'/api/news',{headers})).json()).yandex_key_configured,false);
  assert.deepEqual(errors,[]);console.log('Yandex UI: provider switch, credentials, hidden key and API test feedback OK');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
