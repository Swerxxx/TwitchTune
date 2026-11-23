(()=>{
const api=globalThis.browser||chrome;
const channel=/^\/popout\/([a-z0-9_]+)\/reward-queue\/?$/i.exec(location.pathname)?.[1].toLowerCase();
if(!channel)return;
const key='layout:'+channel;
const twitchCard='.reward-queue-body .redemption-list-item__body';
const twitchTransitionCard='.tw-transition-group > .tw-transition > .redemption-list-item__body';
const uuid=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
let config=null, active=false, picking=null;
api.storage.local.get(key).then(s=>config=s[key]||null);
const normalize=s=>String(s||'').replace(/\s+/g,' ').trim();
const visible=e=>!!(e&&e.isConnected&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
const label=e=>normalize(e?.getAttribute('aria-label')||e?.getAttribute('title')||e?.innerText);
const completeLabel=s=>/^(?:mark as complete|mark complete|complete|выполнено|выполнить|завершить|(?:отметить|пометить) как выполненн(?:ый|ую|ое))$/i.test(s);
const completionButtons=root=>[...root.querySelectorAll('button,[role="button"]')].filter(button=>visible(button)&&completeLabel(label(button)));
const canComplete=button=>visible(button)&&!button.disabled&&button.getAttribute('aria-disabled')!=='true'&&completeLabel(label(button));
function pathFrom(root,node){
 if(root===node)return ':scope';const parts=[];
 while(node&&node!==root){const parent=node.parentElement;if(!parent)throw Error('Нет родителя');
  const siblings=[...parent.children].filter(e=>e.tagName===node.tagName);
  parts.unshift(node.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(node)+1)+')');node=parent;
 }if(node!==root)throw Error('Поля находятся в разных карточках');return ':scope > '+parts.join(' > ');
}
function containerSelector(node){
 const attr=['data-test-selector','data-a-target'].find(a=>node.getAttribute(a));
 if(attr)return node.tagName.toLowerCase()+'['+attr+'="'+CSS.escape(node.getAttribute(attr))+'"]';
 if(node.matches('li,article,[role="listitem"]'))return node.tagName.toLowerCase()+(node.getAttribute('role')?'[role="listitem"]':'');
 if(node.matches(twitchCard))return twitchCard;
 return null;
}
function pageValue(object, name){
 return object==null?undefined:Object.getOwnPropertyDescriptor(object,name)?.value;
}
function historicalOrder(redemption, id){
 const reward=pageValue(redemption,'reward');
 const rewardId=pageValue(reward,'id')??pageValue(redemption,'rewardID')??pageValue(redemption,'rewardId')??pageValue(redemption,'reward_id');
 const input=pageValue(redemption,'userInput')??pageValue(redemption,'user_input');
 const status=pageValue(redemption,'status');
 if(typeof rewardId!=='string'||!uuid.test(rewardId)||typeof input!=='string'||!input.trim()||input.length>4000
   ||(status!=null&&(typeof status!=='string'||status.toUpperCase()!=='UNFULFILLED')))return null;
 const viewerId=pageValue(pageValue(redemption,'user'),'id')??pageValue(redemption,'user_id');
 return {id,rewardId,input,viewerId:typeof viewerId==='string'&&viewerId.length<=100?viewerId:''};
}
function readTwitchOrder(row, includeHistory){
 // Limit React inspection to this order and stop at the shared transition group.
 try{
  const element=row.wrappedJSObject||row;
  const list=row.parentElement.parentElement;
  const listElement=list.wrappedJSObject||list;
  const prop=Object.getOwnPropertyNames(element).find(name=>name.startsWith('__reactFiber$'));
  if(!prop)return {ids:[],history:[]};
  const boundary=pageValue(listElement,prop);
  if(!boundary)return {ids:[],history:[]};
  const otherBoundary=pageValue(boundary,'alternate');
  const ids=new Set(), keys=new Set(), history=new Map();
  let node=pageValue(element,prop);
  for(let depth=0;node&&depth<24;depth++,node=pageValue(node,'return')){
   if(node===boundary||node===otherBoundary)return {ids:[...(ids.size?ids:keys)],history:[...history.values()]};
   for(const version of [node,pageValue(node,'alternate')]){
    const props=pageValue(version,'memoizedProps');
    for(const redemption of [pageValue(props,'redemption'),pageValue(props,'rewardRedemption')]){
     const id=pageValue(redemption,'id');
     if(typeof id==='string'&&uuid.test(id)){
      ids.add(id);
      const order=includeHistory&&historicalOrder(redemption,id);
      if(order)history.set(JSON.stringify(order),order);
     }
    }
    const reactKey=pageValue(version,'key');
    if(typeof reactKey!=='string')continue;
    const id=reactKey.startsWith('.$')?reactKey.slice(2):reactKey;
    if(uuid.test(id))keys.add(id);
   }
  }
 }catch{/* Page objects may be unavailable or in a different React version. */}
 return {ids:[],history:[]};
}
function readIds(card){
 const ids=new Set(), confirmed=new Set();
 for(const e of [card,...card.querySelectorAll('[data-redemption-id],[data-reward-redemption-id],[data-id],[id]')]){
  for(const a of ['data-redemption-id','data-reward-redemption-id','data-id','id']){
   const value=e.getAttribute(a);if(!value)continue;
   // IDs are compared as complete values or explicit UUID components, never text.
   if(a.includes('redemption')){
    ids.add(value);
    if(uuid.test(value))confirmed.add(value);
   }
   if(a==='data-id')ids.add(value);
   for(const m of value.matchAll(/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/gi))ids.add(m[0]);
  }
 }return {ids:[...ids],confirmed};
}
function cards(includeHistory=false){
 if(!config)return [];
 return [...document.querySelectorAll(config.card)].filter(visible).flatMap(root=>{
  const find=selector=>selector===':scope'?root:selector?root.querySelector(selector):null;
  const viewer=find(config.viewer),reward=find(config.reward),input=find(config.input),button=find(config.button);
  if(config.checkStructure){
   const controls=completionButtons(root);
   if(controls.length!==1||controls[0]!==button||!visible(viewer)||!visible(input)||(config.reward&&!visible(reward)))return [];
  }
  let {ids,confirmed}=readIds(root);
  let history=[];
  if(!ids.length||includeHistory){
   const row=button?.closest(twitchCard);
   if(row?.matches(twitchTransitionCard)&&row.contains(viewer)&&row.contains(input)&&(!reward||row.contains(reward))){
    const data=readTwitchOrder(row,includeHistory);
    if(!ids.length){ids=data.ids;confirmed=new Set(ids);}
    history=data.history;
   }
  }
  const legacyId=ids.length===1&&confirmed.has(ids[0])?ids[0]:null;
  return [{root,button,ids,legacyId,history,viewer:normalize(viewer?.innerText),reward:normalize(reward?.innerText),input:normalize(input?.innerText)}];
 });
}
function historyOnPage(){
 const list=cards(true);
 const orders=list.flatMap(card=>{
  if(card.ids.length!==1||!card.viewer||card.viewer.length>200||!canComplete(card.button))return [];
  const [id]=card.ids;
  if(list.some(other=>other!==card&&other.ids.includes(id))||card.history.length!==1)return [];
  const [order]=card.history;
  return order.id===id&&normalize(order.input)===card.input?[{...order,viewer:card.viewer}]:[];
 });
 return {orders:orders.slice(0,100)};
}
function preflight(includeIds=false){
 if(!config)return {ready:false,reason:'Нужна однократная настройка страницы в расширении.'};
 try{const list=cards();if(!list.length)return {ready:false,reason:'Нет подходящих карточек: очередь пуста или страница изменилась.'};
 if(!list.some(c=>c.ids.length))return {ready:false,reason:'Twitch не показывает ID заказа в карточках. Автоподтверждение приостановлено; требуется настройка под эту страницу.'};
 const result={ready:true,count:list.length};
 if(includeIds){
  result.visibleIds=[...new Set(list.flatMap(card=>card.ids))].filter(id=>id.length<=128).slice(0,200);
  result.legacyIds=list.filter(card=>card.legacyId&&canComplete(card.button)&&result.visibleIds.includes(card.legacyId)
    &&!list.some(other=>other!==card&&other.ids.includes(card.legacyId))).map(card=>card.legacyId);
 }
 return result;}catch{return {ready:false,reason:'Разметка страницы изменилась. Настрой страницу заново.'};}
}
async function complete(task){
 if(active)return {outcome:'blocked',note:'Уже выполняется другое задание'};
 active=true;
 try{
  if(task.channel!==channel)throw Error('Открыт другой канал');
  let list=cards(),match=TwitchTuneMatcher.chooseOrder(task,list);
  if(!match.ok)return {outcome:'blocked',note:match.reason};
  let card=list[match.index];
  if(task.rewardId===''&&card.legacyId!==task.id)throw Error('Точный ID старой заявки больше не виден');
  if(!canComplete(card.button))throw Error('Кнопка выполнения не распознана');
  const permission=await api.runtime.sendMessage({kind:'check',task});
  if(!permission?.ok||!permission.allowed)throw Error('TwitchTune отозвал или просрочил задание');
  // Re-read after asynchronous round trip: Twitch may have virtualized/reused cards.
  list=cards();match=TwitchTuneMatcher.chooseOrder(task,list);
  if(!match.ok)throw Error('Карточка изменилась перед нажатием');
  card=list[match.index];
  if(task.rewardId===''&&card.legacyId!==task.id)throw Error('Точный ID старой заявки больше не виден');
  if(!canComplete(card.button))throw Error('Кнопка изменилась');
  card.button.click();
  return {outcome:'clicked',note:'Ожидание события FULFILLED от Twitch'};
 }catch(e){return {outcome:'blocked',note:e.message};}finally{active=false;}
}
function calibrate(){
 if(picking)return;
 const selected=[];let hover=null;
 const panel=document.createElement('div');panel.style.cssText='position:fixed;top:12px;left:12px;z-index:2147483647;padding:16px;background:#111;color:#d6f76a;border:2px solid #d6f76a;border-radius:8px;font:16px sans-serif;max-width:480px';
 const prompts=['Нажми на ник зрителя в карточке заказа.','Нажми на название награды в той же карточке.','Нажми на текст заявки (ссылку на трек).','Нажми на кнопку «Пометить как выполненное». В режиме настройки клик перехватывается.'];
 const hint=document.createElement('div');
 const skipReward=document.createElement('button');
 skipReward.type='button';skipReward.textContent='Название награды вне карточки';
 skipReward.style.cssText='display:block;margin-top:8px;padding:6px 10px;background:#d6f76a;color:#111;border-radius:4px;cursor:pointer';
 const show=text=>{
  hint.textContent='TwitchTune: '+text+'  Esc — отмена';
  skipReward.style.display=selected.length===1?'block':'none';
 };
 skipReward.addEventListener('click',()=>{
  if(selected.length!==1)return;
  selected.push(null);show(prompts[selected.length]);
 });
 panel.append(hint,skipReward);show(prompts[0]);document.documentElement.append(panel);
 function cleanup(){if(hover)hover.style.outline='';panel.remove();document.removeEventListener('click',click,true);document.removeEventListener('mousemove',move,true);document.removeEventListener('keydown',keyDown,true);picking=null;}
 function move(event){if(panel.contains(event.target))return;if(hover)hover.style.outline='';hover=event.target;hover.style.outline='2px solid #c6f52b';}
 function keyDown(event){if(event.key==='Escape'){event.preventDefault();cleanup();}}
 async function click(event){
  if(panel.contains(event.target))return;event.preventDefault();event.stopImmediatePropagation();
  if(selected.length===4)return;
  let node=event.target;
  if(selected.length===3){node=node.closest('button,[role="button"]');if(!node||!completeLabel(label(node))){show('Выбери кнопку «Пометить как выполненное» в карточке заказа.');return;}}
  selected.push(node);
  if(selected.length<4){show(prompts[selected.length]);return;}
  try{
   const [viewer,reward,input,button]=selected;
   const fields=selected.filter(Boolean);
   let root=viewer;while(root&&!fields.every(field=>root.contains(field)))root=root.parentElement;
   if(!root||root===document.body||root===document.documentElement)throw Error('Поля не принадлежат одной карточке');
   const controls=completionButtons(root);
   if(fields.includes(root)||controls.length!==1||controls[0]!==button){
    throw Error('Выбери поля одной карточки. Если название награды находится в боковой панели, нажми «Название награды вне карточки».');
   }
   const stableSelector=containerSelector(root);
   const buttonPath=pathFrom(root,button);
   const candidate={
    card:stableSelector||`${root.localName}:has(${buttonPath.slice(':scope '.length)})`,
    checkStructure:true,viewer:pathFrom(root,viewer),reward:reward?pathFrom(root,reward):null,
    input:pathFrom(root,input),button:buttonPath,
   };
   config=candidate;await api.storage.local.set({[key]:config});
   await api.runtime.sendMessage({kind:'calibrationSaved'});
   cleanup();
   const readiness=preflight();
   alert(readiness.ready?'Страница настроена. Включи автоподтверждение в окне расширения.':readiness.reason);
  }catch(e){cleanup();alert('Настройка не завершена: '+e.message);}
 }
 document.addEventListener('click',click,true);document.addEventListener('mousemove',move,true);document.addEventListener('keydown',keyDown,true);picking={cleanup};
}
api.runtime.onMessage.addListener((message,sender,respond)=>{
 if(sender.id!==api.runtime.id)return false;
 if(message.kind==='preflight'){respond(preflight(message.includeIds===true));return false;}
 if(message.kind==='history'){respond(historyOnPage());return false;}
 if(message.kind==='calibrate'){calibrate();respond({ok:true});return false;}
 if(message.kind==='complete'){complete(message.task).then(respond);return true;}
 return false;
});
})();
