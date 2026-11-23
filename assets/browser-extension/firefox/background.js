const api=globalThis.browser||chrome;
const HOST='com.twitchtune.queue';
let nativePort=null, sequence=0, running=false, busy=false, last='Расширение выключено';
const waiting=new Map();
function status(text){last=text;api.storage.local.set({lastStatus:text}).catch(()=>{});}
function disconnect(){if(nativePort)nativePort.disconnect();nativePort=null;for(const p of waiting.values()){clearTimeout(p.timer);p.reject(new Error('Связь с TwitchTune потеряна'));}waiting.clear();}
function connect(){
 if(nativePort)return;
 nativePort=api.runtime.connectNative(HOST);
 nativePort.onMessage.addListener(m=>{const p=waiting.get(m.requestId);if(p){clearTimeout(p.timer);waiting.delete(m.requestId);m.ok?p.resolve(m):p.reject(new Error(m.error||'Действие недоступно'));}});
 nativePort.onDisconnect.addListener(()=>{const message=api.runtime.lastError?.message;nativePort=null;for(const p of waiting.values()){clearTimeout(p.timer);p.reject(new Error(message||'TwitchTune недоступен'));}waiting.clear();status('Нет связи с TwitchTune. Подключи расширение в настройках программы.');});
}
function native(message){return new Promise((resolve,reject)=>{try{connect();const id=++sequence;const timer=setTimeout(()=>{waiting.delete(id);reject(new Error('TwitchTune не ответил'));},12000);waiting.set(id,{resolve,reject,timer});nativePort.postMessage({...message,requestId:id});}catch(e){reject(e);}});}
function queueChannel(url){try{const u=new URL(url);if(u.protocol!=='https:'||u.hostname!=='www.twitch.tv')return null;return /^\/popout\/([a-z0-9_]+)\/reward-queue\/?$/i.exec(u.pathname)?.[1].toLowerCase()||null;}catch{return null;}}
async function queueTab(channel){
 const tabs=await api.tabs.query({url:'https://www.twitch.tv/popout/*/reward-queue*'});
 const matching=tabs.filter(t=>queueChannel(t.url)===channel);
 if(matching.length!==1)throw Error(matching.length?'Оставь одну вкладку очереди этого канала.':'Открой очередь заказов канала '+channel);
 return matching[0];
}
async function tick(){
 if(!running||busy)return;busy=true;
 try{
  const s=await native({action:'status'});
  const tab=await queueTab(s.channel);
  const ready=await api.tabs.sendMessage(tab.id,{kind:'preflight',includeIds:true}).catch(()=>null);
  if(!ready?.ready){status(ready?.reason||'Обнови вкладку очереди и настрой страницу в расширении.');return;}
  const response=await native({action:'claim',channel:s.channel,visibleIds:ready.visibleIds,legacyIds:ready.legacyIds});
  if(!response.task){status('Подключено: '+s.channel+'. Ждём прослушивания и сохранения оценки.');return;}
  const task=response.task;
  let outcome;
  try{outcome=await api.tabs.sendMessage(tab.id,{kind:'complete',task});}
  catch{outcome={outcome:'not_found',note:'Вкладка закрылась или обновилась'};}
  await native({action:'report',channel:s.channel,id:task.id,lease:task.lease,outcome:outcome.outcome,note:outcome.note||''});
  status(outcome.outcome==='clicked'?'Нажато «Выполнено». Ждём подтверждения Twitch в программе.':outcome.note||'Заказ не найден на странице.');
 }catch(e){status(e.message||'Нет связи с TwitchTune');}
 finally{busy=false;}
}
api.runtime.onMessage.addListener((message,sender,sendResponse)=>{
 // Only our extension popup and our restricted content script may issue commands.
 if(sender.id!==api.runtime.id)return false;
 const operation=async()=>{
  if(sender.tab){
    if(!queueChannel(sender.tab.url))throw new Error('Страница не поддерживается');
    if(message.kind==='check'){const t=message.task;return native({action:'check',channel:t.channel,id:t.id,lease:t.lease});}
    if(message.kind==='calibrationSaved'){status('Страница настроена. Автоматизация включается отдельно в окне расширения.');return {ok:true};}
    throw new Error('Недоступная команда страницы');
  }
  if(message.kind==='start'){running=true;await api.storage.local.set({enabled:true});tick();return {ok:true};}
  if(message.kind==='stop'){running=false;await api.storage.local.set({enabled:false});disconnect();status('Расширение выключено');return {ok:true};}
  if(message.kind==='state'){let app=null;try{app=await native({action:'status'});}catch(e){last=e.message;}return {ok:true,running,last,app};}
  if(message.kind==='retry'){return native({action:'retry'});}
  if(message.kind==='importHistory'){
   const {channel}=await native({action:'status'});
   const tab=await queueTab(channel);
   const page=await api.tabs.sendMessage(tab.id,{kind:'history'}).catch(()=>null);
   if(!page?.orders?.length)throw Error('На странице нет старых заказов с точным ID и данными награды.');
   let queued=0;
   for(let offset=0;offset<page.orders.length;offset+=10){
    const result=await native({action:'import_history',channel,orders:page.orders.slice(offset,offset+10)});
    queued+=result.queued;
   }
   return {ok:true,queued};
  }
  if(message.kind==='calibrate'){
    const tabs=await api.tabs.query({active:true,currentWindow:true});
    if(!tabs[0]||!queueChannel(tabs[0].url))throw new Error('Открой вкладку очереди Twitch и нажми снова.');
    running=false;await api.storage.local.set({enabled:false});
    return api.tabs.sendMessage(tabs[0].id,{kind:'calibrate'});
  }
  throw new Error('Неизвестная команда');
 };
 operation().then(sendResponse,e=>sendResponse({ok:false,error:e.message}));return true;
});
api.storage.local.get('enabled').then(s=>{running=!!s.enabled;if(running)tick();});
setInterval(tick,3000);
