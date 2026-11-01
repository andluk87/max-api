// Isolated server only. News and send responses are mocked; no real MAX posts.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const base=process.env.BASE_URL||'http://127.0.0.1:18089',key='browser-test-key-000000000000000000000';
  const state=await(await page.request.get(base+'/api/news',{headers:{Authorization:'Bearer '+key}})).json();
  state.settings.enabled=true;state.settings.photos_allowed=true;
  state.destinations=[{chat_id:'123',name:'Тест',enabled:1,photos:1}];
  state.articles=[{id:1,url:'https://example.org/news.html',status:'draft',created:1793500000,data:{ai_title:'Заголовок',title:'Оригинал',summary:'Краткий пересказ',photo_author:'Автор фото',source:'МК на Чукотке',image:'https://example.org/photo.jpg',hashtags:['#Россия']}}];
  state.deliveries=[];
  await page.route('https://example.org/photo.jpg',r=>r.fulfill({body:fs.readFileSync(path.join(__dirname,'fixtures/photo.png')),contentType:'image/png'}));
  await page.route('**/api/news',r=>r.fulfill({json:state}));
  await page.route('**/api/news/article/1',r=>r.fulfill({json:{ok:true}}));
  await page.route('**/api/news/article/1/publish',r=>{state.articles[0].status='queued';state.deliveries=[{article_id:1,chat_id:'123',status:'queued',created:1793500000}];return r.fulfill({json:{ok:true}});});
  await page.route('**/api/news/article/1/reject',r=>{state.articles[0].status='rejected';state.deliveries[0].status='cancelled';return r.fulfill({json:{ok:true}});});
  await page.goto(base);await page.locator('#key').fill(key);await page.locator('#unlock').click();await page.locator('#workspace').waitFor();await page.locator('#tab-news').click();
  await page.locator('.news-photo-credit').waitFor();assert.equal(await page.locator('.news-photo-credit').textContent(),'📸 Фото: Автор фото');assert.ok((await page.locator('.news-preview-source').textContent()).startsWith('Подробности на сайте: '));
  const correctOrder=await page.locator('.news-preview').evaluate(n=>{const all=[...n.children];return all.indexOf(n.querySelector('img'))<all.indexOf(n.querySelector('.news-photo-credit'))&&all.indexOf(n.querySelector('.news-photo-credit'))<all.indexOf(n.querySelector('strong'));});assert.equal(correctOrder,true);
  await page.locator('#news-detail').getByRole('button',{name:'Опубликовать',exact:true}).click();await page.waitForFunction(()=>document.getElementById('news-feedback').textContent.includes('В очереди: 1'));
  assert.equal(await page.locator('#news-detail').getByRole('button',{name:'Опубликовать',exact:true}).isDisabled(),true);assert.equal(await page.locator('#news-detail').getByRole('button',{name:'Снять с очереди',exact:true}).isVisible(),true);
  await page.locator('#news-detail').getByRole('button',{name:'Снять с очереди',exact:true}).click();await page.waitForFunction(()=>document.getElementById('news-feedback').textContent.includes('отменены'));assert.equal(state.deliveries[0].status,'cancelled');assert.deepEqual(errors,[]);
  console.log('News queue UI: photo credit order, source label, queued feedback and cancellation OK');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
