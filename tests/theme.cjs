// npm jsdom@26; JSDOM_MODULE=/path/to/jsdom node tests/theme.cjs
const {JSDOM}=require(process.env.JSDOM_MODULE||'jsdom'),fs=require('fs'),path=require('path'),assert=require('assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../app/static/index.html'),'utf8');const script=fs.readFileSync(path.join(__dirname,'../app/static/theme.js'),'utf8');
const dom=new JSDOM(html,{url:'http://localhost',runScripts:'outside-only'});const w=dom.window;let listener;
const media={matches:true,addEventListener:(name,fn)=>listener=fn};w.matchMedia=()=>media;
w.localStorage.setItem('max-ui-appearance-v1',JSON.stringify({palette:'rose',mode:'auto'}));w.eval(script);w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
assert.equal(w.document.documentElement.dataset.bsTheme,'dark');assert.equal(w.document.documentElement.dataset.palette,'rose');
media.matches=false;listener();assert.equal(w.document.documentElement.dataset.bsTheme,'light');
for(const palette of ['iris','ocean','forest','rose'])for(const mode of ['light','dark']){
 for(const [id,value] of [['ui-palette',palette],['ui-mode',mode]]){const el=w.document.getElementById(id);el.value=value;el.dispatchEvent(new w.Event('change'));}
 assert.equal(w.document.documentElement.dataset.bsTheme,mode);assert.equal(w.document.documentElement.dataset.palette,palette);
 assert.deepEqual(JSON.parse(w.localStorage.getItem('max-ui-appearance-v1')),{palette,mode});
}
media.matches=false;listener();assert.equal(w.document.documentElement.dataset.bsTheme,'dark');
w.dispatchEvent(new w.StorageEvent('storage',{key:'max-ui-appearance-v1',newValue:'{"palette":"ocean","mode":"light"}'}));assert.equal(w.document.documentElement.dataset.palette,'ocean');
w.close();console.log('Appearance: 8 combinations, restored choice, auto system changes, cross-tab sync passed');
