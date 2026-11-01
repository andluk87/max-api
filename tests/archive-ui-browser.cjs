// Isolated server only. Archive and generation responses are mocked; no paid calls.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const base=process.env.BASE_URL||'http://127.0.0.1:18089',key='browser-test-key-000000000000000000000';
  const headers={Authorization:'Bearer '+key};
  const state=await(await page.request.get(base+'/api/news',{headers})).json();
  state.settings.enabled=true;state.settings.provider='ollama';state.destinations=[{chat_id:'-79387375605133',name:'Тест',enabled:1,photos:0}];
  state.articles=[];state.deliveries=[{article_id:1,chat_id:'-79387375605133',status:'sent',mid:'old-id',created:1793500000}];
  const url='https://www.mk-chukotka.ru/social/2026/10/30/first.html';
  await page.route('**/api/news',route=>route.fulfill({json:state}));
  await page.route('**/api/news/archive?day=*',route=>route.fulfill({json:{day:'2026-10-30',articles:[{url,title:'Архивная статья',day:'2026-10-30',status:'published',id:1}]}}));
  await page.route('**/api/news/archive/import',route=>{
   assert.deepEqual(route.request().postDataJSON(),{day:'2026-10-30',url});
   state.articles.unshift({id:2,url,status:'draft',created:1793500000,data:{title:'Архивная статья',ai_title:'Новая ручная копия',summary:'Пересказ для проверки.',source:'МК на Чукотке',manual_only:true,original_url:url,hashtags:['#Погода']}});
   return route.fulfill({json:{id:2,status:'draft'}});
  });
  await page.goto(base);await page.locator('#key').fill(key);await page.locator('#unlock').click();await page.locator('#workspace').waitFor();
  await page.locator('#tab-news').click();await page.locator('[data-news-view=archive]').click();await page.locator('#news-archive-date').fill('2026-10-30');await page.locator('#news-archive-search').click();
  await page.locator('.news-archive-item').waitFor();assert.equal(await page.locator('.news-archive-item strong').textContent(),'Архивная статья');
  await page.getByRole('button',{name:'Новый черновик и пересказ',exact:true}).click();
  await page.locator('.news-preview>strong').waitFor();assert.equal(await page.locator('.news-preview>strong').textContent(),'Новая ручная копия');
  assert.equal(await page.locator('.news-preview-source a').getAttribute('href'),url);
  assert.equal(await page.locator('#news-detail').getByRole('button',{name:'Опубликовать',exact:true}).isEnabled(),true);
  assert.equal(await page.locator('#news-detail input[type=checkbox]').isChecked(),true);
  assert.equal(await page.locator('#news-detail input[type=checkbox]').isDisabled(),false);
  assert.equal(await page.locator('#news-filter').inputValue(),'all');
  assert.deepEqual(errors,[]);console.log('Archive UI: date search, published source, separate manual draft and repeat publication target OK');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
