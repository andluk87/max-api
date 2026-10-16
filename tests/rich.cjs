const {JSDOM}=require(process.env.JSDOM_MODULE || 'jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const dom=new JSDOM('',{runScripts:'outside-only'}),w=dom.window;
w.eval(fs.readFileSync(path.join(__dirname,'../app/static/rich.js'),'utf8'));
const R=w.RichMessage;
const node=R.render('👋 bold link <script>',[
{type:'STRONG',from_:3,length:4},{type:'UNDERLINE',from:3,length:4},
{type:'LINK',from_:8,length:4,attributes:{url:'https://example.com'}},
{type:'LINK',from_:13,length:8,attributes:{url:'javascript:alert(1)'}}]);
assert.equal(node.textContent,'👋 bold link <script>');assert.equal(node.querySelector('strong').textContent,'bold');
assert.equal(node.querySelector('a').href,'https://example.com/');assert.equal(node.querySelectorAll('a').length,1);assert.equal(node.querySelector('script'),null);
for(const type of ['EMPHASIZED','STRIKETHROUGH','CODE','MONOSPACED','QUOTE','HEADING','SPOILER'])assert.ok(R.render('text',[{type,from_:0,length:4}]).children.length);
const photos=R.photos([{type:'PHOTO',base_url:'https://example.com/image.jpg'},{type:'PHOTO',preview_data:{encoding:'base64',data:'YWJj'}},{type:'VIDEO'}]);
assert.equal(photos.querySelectorAll('img').length,2);assert.equal(photos.querySelector('img').referrerPolicy,'no-referrer');
assert.equal(R.safeURL('javascript:alert(1)'),null);
assert.equal(R.render('text',[{type:'STRONG',from_:-1,length:100}]).textContent,'text');
w.close();console.log('Rich text UTF-16, nesting, links, XSS, photos and fallback passed');
