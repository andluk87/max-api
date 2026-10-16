'use strict';
(()=>{
  const root=document.documentElement,key='max-ui-appearance-v1';
  const palettes=['iris','ocean','forest','rose'];const modes=['light','dark','auto'];
  let preferences={palette:'iris',mode:'auto'};
  try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved){if(palettes.includes(saved.palette))preferences.palette=saved.palette;if(modes.includes(saved.mode))preferences.mode=saved.mode;}}catch{}
  const media=window.matchMedia?window.matchMedia('(prefers-color-scheme: dark)'):null;
  function apply(){
    root.dataset.palette=preferences.palette;
    root.dataset.bsTheme=preferences.mode==='auto'?(media?.matches?'dark':'light'):preferences.mode;
    document.querySelectorAll('[data-appearance]').forEach(el=>el.value=preferences[el.dataset.appearance]);
    const label=document.getElementById('appearance-state');if(label)label.textContent=root.dataset.bsTheme==='dark'?'Ночной режим':'Дневной режим';
  }
  function save(){try{localStorage.setItem(key,JSON.stringify(preferences));}catch{}apply();}
  apply();media?.addEventListener?.('change',()=>{if(preferences.mode==='auto')apply();});
  window.addEventListener('storage',event=>{if(event.key!==key)return;try{const value=JSON.parse(event.newValue);if(value&&palettes.includes(value.palette)&&modes.includes(value.mode)){preferences=value;apply();}}catch{}});
  function decorate(parent){
    if(!(parent instanceof Element)&&parent!==document)return;
    const nodes=[...(parent instanceof Element?[parent]:[]),...parent.querySelectorAll('button,input,select,textarea,table')];
    nodes.forEach(el=>{
      if(el.matches('button')){el.classList.add('btn');}
      else if(el.matches('select'))el.classList.add('form-select');
      else if(el.matches('textarea,input:not([type=checkbox]):not([type=radio]):not([type=range])'))el.classList.add('form-control');
      else if(el.matches('input[type=checkbox],input[type=radio]'))el.classList.add('form-check-input');
      else if(el.matches('input[type=range]'))el.classList.add('form-range');
      else if(el.matches('table'))el.classList.add('table');
    });
  }
  document.addEventListener('DOMContentLoaded',()=>{
    decorate(document);apply();
    document.querySelectorAll('[data-appearance]').forEach(el=>el.addEventListener('change',()=>{const prop=el.dataset.appearance,values=prop==='mode'?modes:palettes;if(values.includes(el.value)){preferences[prop]=el.value;save();}}));
    new MutationObserver(records=>records.forEach(r=>r.addedNodes.forEach(decorate))).observe(document.body,{childList:true,subtree:true});
  },{once:true});
})();
