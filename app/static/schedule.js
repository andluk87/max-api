'use strict';
// Visual schedule builder; old cron expressions are preserved until explicitly changed.
(() => {
  const minuteSteps=[1,5,10,15,20,30], hourSteps=[1,2,3,4,6,8,12];
  const days=[['1','Пн'],['2','Вт'],['3','Ср'],['4','Чт'],['5','Пт'],['6','Сб'],['0','Вс']];
  const $=id=>document.getElementById(id);
  let original='', canonical='', legacy='';
  const number=(value,min,max)=>/^\d+$/.test(value) && Number(value)>=min && Number(value)<=max;
  function encode(s) {
    const m=String(Number(s.minute)),h=String(Number(s.hour));
    if(s.mode==='minutes') return (Number(s.everyminutes)===1?'*':'*/'+s.everyminutes)+' * * * *';
    if(s.mode==='hourly') return m+' '+(Number(s.everyhours)===1?'*':'*/'+s.everyhours)+' * * *';
    if(s.mode==='daily') return m+' '+h+' * * *';
    if(s.mode==='weekly') {
      if(!s.days.length) throw Error('Выберите хотя бы один день недели');
      return m+' '+h+' * * '+days.map(x=>x[0]).filter(x=>s.days.includes(x)).join(',');
    }
    if(s.mode==='monthly') return m+' '+h+' '+s.monthday+' * *';
    if(s.mode==='legacy') return s.legacy;
    throw Error('Выберите периодичность');
  }
  function decode(expression) {
    const parts=expression.trim().split(/\s+/); if(parts.length!==5) return null;
    const [m,h,d,mo,w]=parts, base={hour:'9',minute:'0',days:['1','2','3','4','5'],monthday:'1',everyminutes:'15',everyhours:'1'};
    if(mo!=='*') return null;
    if(h==='*' && d==='*' && w==='*' && (m==='*'||/^\*\/\d+$/.test(m))) {
      const n=m==='*'?1:Number(m.slice(2));
      return minuteSteps.includes(n)?{...base,mode:'minutes',everyminutes:String(n)}:null;
    }
    if(!number(m,0,59)) return null;
    base.minute=String(Number(m));
    if(d==='*' && w==='*' && (h==='*'||/^\*\/\d+$/.test(h))) {
      const n=h==='*'?1:Number(h.slice(2));
      return hourSteps.includes(n)?{...base,mode:'hourly',everyhours:String(n)}:null;
    }
    if(!number(h,0,23)) return null;
    base.hour=String(Number(h));
    if(d==='*' && w==='*') return {...base,mode:'daily'};
    if((number(d,1,31)||d.toUpperCase()==='L') && w==='*') return {...base,mode:'monthly',monthday:d.toUpperCase()==='L'?'L':String(Number(d))};
    if(d==='*') {
      const selected=new Set();
      for(const piece of w.split(',')) {
        const range=piece.split('-');
        if(range.length>2 || !range.every(x=>number(x,0,7))) return null;
        const a=Number(range[0]),b=Number(range[1] ?? range[0]); if(b<a) return null;
        for(let i=a;i<=b;i++)selected.add(String(i%7));
      }
      if(selected.size) return {...base,mode:'weekly',days:[...selected]};
    }
    return null;
  }
  function describeState(s) {
    const at=String(s.hour).padStart(2,'0')+':'+String(s.minute).padStart(2,'0');
    if(s.mode==='minutes') return Number(s.everyminutes)===1?'Каждую минуту':'Каждые '+s.everyminutes+' мин.';
    if(s.mode==='hourly') return 'Каждые '+s.everyhours+' ч., на '+s.minute+'-й минуте';
    if(s.mode==='daily') return 'Ежедневно в '+at;
    if(s.mode==='weekly') return days.filter(x=>s.days.includes(x[0])).map(x=>x[1]).join(', ')+' в '+at;
    if(s.mode==='monthly') return (s.monthday==='L'?'В последний день месяца':s.monthday+'-го числа каждого месяца')+' в '+at;
    return 'Сложное расписание: '+s.legacy;
  }
  function describe(cron) {const decoded=decode(cron);return decoded?describeState(decoded):(cron?'Сложное расписание: '+cron:'Не задано');}
  function fields() {
    return {mode:$('schedulekind').value,hour:$('schedulehour').value,minute:$('scheduleminute').value,
      everyminutes:$('everyminutes').value,everyhours:$('everyhours').value,monthday:$('monthday').value,
      days:[...document.querySelectorAll('#weekdays input:checked')].map(x=>x.value),legacy};
  }
  function read() {const expression=encode(fields());return original && expression===canonical?original:expression;}
  function update() {
    const s=fields();
    $('minutesgroup').hidden=s.mode!=='minutes'; $('hourstepgroup').hidden=s.mode!=='hourly';
    $('timegroup').hidden=['minutes','legacy'].includes(s.mode); $('hourgroup').hidden=s.mode==='hourly';
    $('weekgroup').hidden=s.mode!=='weekly'; $('monthgroup').hidden=s.mode!=='monthly';
    $('legacygroup').hidden=s.mode!=='legacy'; $('legacyvalue').textContent=legacy;
    try {encode(s);$('schedulesummary').textContent=describeState(s)+' · '+$('timezone').selectedOptions[0].textContent;}
    catch(e){$('schedulesummary').textContent=e.message;}
    $('cronpreview').textContent='';
  }
  function load(expression='') {
    original=expression; legacy=expression;
    const s=expression?decode(expression):{mode:'daily',hour:'9',minute:'0',days:['1','2','3','4','5'],everyminutes:'15',everyhours:'1',monthday:'1'};
    $('legacyoption').hidden=Boolean(s);$('schedulekind').value=s?s.mode:'legacy';
    if(s) {
      for(const [key,id] of [['hour','schedulehour'],['minute','scheduleminute'],['everyminutes','everyminutes'],['everyhours','everyhours'],['monthday','monthday']]) $(id).value=s[key];
      document.querySelectorAll('#weekdays input').forEach(x=>{x.checked=s.days.includes(x.value);});
    }
    canonical=encode(fields());update();
  }
  function init() {
    for(let i=0;i<24;i++)$('schedulehour').add(new Option(String(i).padStart(2,'0'),String(i)));
    for(let i=0;i<60;i++)$('scheduleminute').add(new Option(String(i).padStart(2,'0'),String(i)));
    minuteSteps.forEach(n=>$('everyminutes').add(new Option(n+' мин.',String(n))));
    hourSteps.forEach(n=>$('everyhours').add(new Option(n+' ч.',String(n))));
    for(let i=1;i<=31;i++)$('monthday').add(new Option(String(i),String(i)));
    $('monthday').add(new Option('Последний день','L'));
    days.forEach(([value,name])=>{
      const label=document.createElement('label'),input=document.createElement('input'),span=document.createElement('span');
      input.type='checkbox';input.value=value;span.textContent=name;label.append(input,span);$('weekdays').append(label);
      input.addEventListener('change',update);
    });
    ['schedulekind','schedulehour','scheduleminute','everyminutes','everyhours','monthday','timezone'].forEach(id=>$(id).addEventListener('change',update));
    load();
  }
  globalThis.ScheduleBuilder={init,load,read,update,encode,decode,describe};
})();
