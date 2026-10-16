'use strict';
(() => {
  function safeURL(value) {try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:null;}catch{return null;}}
  function render(text, elements=[]) {
    text=String(text || '');
    const root=document.createElement('div');root.className='incomingtext';
    const ranges=elements.map(e=>({...e,start:Number(e.from_ ?? e.from ?? 0),end:Number(e.from_ ?? e.from ?? 0)+Number(e.length || 0)}))
      .filter(e=>Number.isInteger(e.start)&&Number.isInteger(e.end)&&e.start>=0&&e.end>e.start&&e.end<=text.length);
    const points=[...new Set([0,text.length,...ranges.flatMap(e=>[e.start,e.end])])].sort((a,b)=>a-b);
    const tags={STRONG:'strong',BOLD:'strong',EMPHASIZED:'em',ITALIC:'em',UNDERLINE:'u',STRIKETHROUGH:'s',MONOSPACED:'code',CODE:'code',HEADING:'strong',QUOTE:'span',SPOILER:'span'};
    for(let i=0;i<points.length-1;i++){
      const a=points[i],b=points[i+1];let node=document.createTextNode(text.slice(a,b));
      for(const e of ranges.filter(e=>e.start<=a&&e.end>=b)){
        const type=String(e.type).toUpperCase();let parent;
        if(type==='LINK'){
          const href=safeURL(e.attributes?.url || text.slice(e.start,e.end));if(!href)continue;
          parent=document.createElement('a');parent.href=href;parent.target='_blank';parent.rel='noopener noreferrer';
        }else if(tags[type]){parent=document.createElement(tags[type]);parent.className='entity-'+type.toLowerCase();
          if(type==='SPOILER'){parent.tabIndex=0;parent.setAttribute('role','button');parent.setAttribute('aria-label','Показать скрытый текст');const reveal=()=>parent.classList.toggle('revealed');parent.addEventListener('click',reveal);parent.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();reveal();}});}
        }else continue;
        parent.append(node);node=parent;
      }
      root.append(node);
    }
    return root;
  }
  function photos(attachments) {
    const box=document.createElement('div');box.className='incomingphotos';
    attachments.forEach(a=>{
      if(String(a.type || a._type).toUpperCase()!=='PHOTO')return;
      const url=safeURL(a.base_url || a.baseUrl || a.url);
      const preview=a.preview_data || a.previewData;
      const fallback=preview?.encoding==='base64' && /^[A-Za-z0-9+/=]+$/.test(preview.data || '')?'data:image/jpeg;base64,'+preview.data:null;
      const figure=document.createElement('figure'),img=document.createElement('img'),caption=document.createElement('figcaption');
      img.alt='Фото из сообщения';img.loading='lazy';img.referrerPolicy='no-referrer';
      caption.textContent='';
      img.addEventListener('error',()=>{if(fallback && img.src!==fallback){img.src=fallback;caption.textContent='Предпросмотр фотографии';}else{img.hidden=true;caption.textContent='Фото недоступно. Обновите историю, чтобы получить свежую ссылку.';}});
      if(url){img.src=url;const link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Открыть фото';figure.append(link);}
      else if(fallback){img.src=fallback;caption.textContent='Предпросмотр фотографии';}
      else{img.hidden=true;caption.textContent='Фото недоступно';}
      figure.prepend(img);figure.append(caption);box.append(figure);
    });return box;
  }
  globalThis.RichMessage={render,photos,safeURL};
})();
