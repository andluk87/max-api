'use strict';
const search=document.querySelector('#search');
search.addEventListener('input',()=>{
 const words=search.value.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);let n=0;
 document.querySelectorAll('section.doc').forEach(section=>{const text=section.textContent.toLocaleLowerCase();section.hidden=!words.every(w=>text.includes(w));if(!section.hidden)n++;});
 document.querySelector('#search-status').textContent=words.length?`Найдено разделов: ${n}. Очистите поиск, чтобы показать всё.`:'';
});
document.querySelectorAll('aside a').forEach(a=>a.addEventListener('click',()=>{search.value='';search.dispatchEvent(new Event('input'));}));
document.querySelectorAll('pre').forEach(pre=>{
 const button=document.createElement('button');button.textContent='Копировать';button.type='button';
 button.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(pre.querySelector('code').textContent);button.textContent='Скопировано';}catch{button.textContent='Выделите код вручную';}setTimeout(()=>button.textContent='Копировать',2000);});pre.append(button);
});
document.querySelector('#print').onclick=()=>window.print();
