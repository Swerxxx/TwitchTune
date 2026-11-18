const api=globalThis.browser||chrome;
async function send(kind){const r=await api.runtime.sendMessage({kind});if(!r?.ok)throw Error(r?.error||'Нет связи');return r;}
async function refresh(){try{const r=await send('state');document.querySelector('#status').textContent=r.last;const c=r.app?.counts||{};document.querySelector('#counts').textContent='Ждут оценки: '+(c.awaiting_rating||0)+' · Ждут Twitch: '+(c.browser_verify||0)+' · Выполнено: '+(c.fulfilled||0);}catch(e){document.querySelector('#status').textContent=e.message;}}
for(const [id,kind] of [['setup','calibrate'],['start','start'],['stop','stop'],['retry','retry']])document.getElementById(id).onclick=async()=>{try{await send(kind);if(kind==='calibrate')window.close();else await refresh();}catch(e){document.getElementById('status').textContent=e.message;}};
refresh();
