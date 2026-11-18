const api=globalThis.browser||chrome;
async function send(kind){const r=await api.runtime.sendMessage({kind});if(!r?.ok)throw Error(r?.error||'Нет связи');return r;}
async function refresh(){try{const r=await send('state');document.querySelector('#status').textContent=r.last;const c=r.app?.counts||{};document.querySelector('#counts').textContent='Ждут оценки: '+(c.awaiting_rating||0)+' · Ждут Twitch: '+(c.browser_verify||0)+' · Выполнено: '+(c.fulfilled||0);}catch(e){document.querySelector('#status').textContent=e.message;}}
for(const [id,kind] of [['setup','calibrate'],['import','importHistory'],['start','start'],['stop','stop'],['retry','retry']]){
 const button=document.getElementById(id);
 button.onclick=async()=>{
  button.disabled=true;
  try{
   const result=await send(kind);
   if(kind==='calibrate')window.close();
   else if(kind==='importHistory')document.getElementById('status').textContent='Отправлено в TwitchTune: '+result.queued;
   else await refresh();
  }catch(e){document.getElementById('status').textContent=e.message;}
  finally{button.disabled=false;}
 };
}
refresh();
