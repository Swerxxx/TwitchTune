/* Offline synthetic DOM, not a claim of compatibility with Twitch's live markup. */
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
 try{
 const page=await browser.newPage();
 await page.route('**/*',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:`<ul style="margin-left:520px"><li data-test-selector="order" data-redemption-id="exact"><span class="viewer">Viewer</span><span class="reward">Музыка</span><p>https://youtu.be/a</p><button id="finish" onclick="window.clicks=(window.clicks||0)+1">Пометить как выполненное</button><button id="reject" onclick="window.clicks=(window.clicks||0)+1">Отклонить</button></li></ul>`}));
 await page.goto('https://www.twitch.tv/popout/streamer/reward-queue');
 await page.evaluate(()=>{
  window.listeners=[];window.allowed=true;
  window.alert=message=>{window.calibrationMessage=message;};
  window.chrome={runtime:{id:'extension',onMessage:{addListener:f=>window.listeners.push(f)},sendMessage:async()=>({ok:true,allowed:window.allowed})},storage:{local:{get:async()=>({}),set:async()=>{}}}};
 });
 for(const script of ['matcher.js','content.js'])await page.addScriptTag({content:fs.readFileSync(path.join(__dirname,'../assets/browser-extension/chromium',script),'utf8')});
 const invoke=message=>page.evaluate(message=>new Promise(resolve=>{window.listeners[0](message,{id:'extension'},resolve)}),message);
 const task={id:'exact',channel:'streamer',viewer:'viewer',rewardTitle:'Музыка',input:'https://youtu.be/a',lease:'x'.repeat(48)};
 assert.equal((await invoke({kind:'calibrate'})).ok,true);
 for(const selector of ['.viewer','.reward','li > p','#reject'])await page.locator(selector).click();
 assert.equal((await invoke({kind:'preflight'})).ready,false);
 await page.locator('#finish').click();
 assert.equal((await invoke({kind:'preflight'})).ready,true);
 assert.match(await page.evaluate(()=>window.calibrationMessage),/^Страница настроена\./);
 assert.equal(await page.evaluate(()=>window.clicks||0),0);
 assert.equal((await invoke({kind:'complete',task:{...task,id:'wrong'}})).outcome,'blocked');
 assert.equal(await page.evaluate(()=>window.clicks||0),0);
 await page.evaluate(()=>window.allowed=false);
 assert.equal((await invoke({kind:'complete',task})).outcome,'blocked');
 assert.equal(await page.evaluate(()=>window.clicks||0),0);
 await page.evaluate(()=>window.allowed=true);
 assert.equal((await invoke({kind:'complete',task})).outcome,'clicked');
 assert.equal(await page.evaluate(()=>window.clicks),1);
 await page.locator('#finish').evaluate(e=>e.textContent='Пометить всё как выполненное');
 assert.equal((await invoke({kind:'complete',task})).outcome,'blocked');
 assert.equal(await page.evaluate(()=>window.clicks),1);
 await page.locator('li').evaluate(e=>e.removeAttribute('data-redemption-id'));
 assert.equal((await invoke({kind:'preflight'})).ready,false);

 await page.locator('ul').evaluate(list=>{
  list.outerHTML=`<main style="margin-left:560px">
   <aside>Музыка</aside>
   <div class="first"><span>Viewer</span><p data-redemption-id="exact">https://youtu.be/a</p>
    <button onclick="window.clicks++">Пометить как выполненное</button></div>
   <div class="second"><span>Other</span><p data-redemption-id="other">https://youtu.be/b</p>
    <button onclick="window.otherClicks=(window.otherClicks||0)+1">Пометить как выполненное</button></div>
  </main>`;
 });
 assert.equal((await invoke({kind:'calibrate'})).ok,true);
 for(const selector of ['.first span','aside','.first p','.first button'])await page.locator(selector).click();
 assert.match(await page.evaluate(()=>window.calibrationMessage),/Выбери поля одной карточки/);
 assert.equal(await page.evaluate(()=>window.clicks),1);

 assert.equal((await invoke({kind:'calibrate'})).ok,true);
 await page.locator('.first span').click();
 await page.getByRole('button',{name:'Название награды вне карточки'}).click();
 for(const selector of ['.first p','.first button'])await page.locator(selector).click();
 assert.match(await page.evaluate(()=>window.calibrationMessage),/^Страница настроена\./);
 assert.deepEqual(await invoke({kind:'preflight'}),{ready:true,count:2});
 assert.equal(await page.evaluate(()=>window.clicks),1);
 assert.equal((await invoke({kind:'complete',task})).outcome,'clicked');
 assert.equal(await page.evaluate(()=>window.clicks),2);
 assert.equal(await page.evaluate(()=>window.otherClicks||0),0);
 await page.locator('.first p').evaluate(e=>e.removeAttribute('data-redemption-id'));
 assert.equal((await invoke({kind:'complete',task})).outcome,'blocked');
 assert.equal(await page.evaluate(()=>window.clicks),2);
 await page.locator('.second p').evaluate(e=>e.removeAttribute('data-redemption-id'));
 assert.match((await invoke({kind:'preflight'})).reason,/Twitch не показывает ID заказа/);

 const firstId='d708a1be-62f5-4281-9bfa-bae32b51aee2';
 const secondId='560153e6-b301-4f0e-a8b1-6b94e3cf86d2';
 const unrelatedId='c0614123-cddf-4dd3-b985-0a50ada92692';
 await page.evaluate(({firstId,secondId,unrelatedId})=>{
  const group=document.createElement('div');group.className='tw-transition-group';
  const main=document.querySelector('main');main.classList.add('reward-queue-body');main.append(group);
  const boundary={key:unrelatedId};group.__reactFiber$fixture=boundary;
  for(const [index,card] of [...main.querySelectorAll(':scope > div:not(.tw-transition-group)')].entries()){
   card.classList.add('redemption-list-item__body');
   const wrapper=document.createElement('div');wrapper.className='tw-transition';wrapper.append(card);group.append(wrapper);
   const component=index===0
    ?{memoizedProps:{redemption:{id:firstId}},return:boundary}
    :{key:'.$'+secondId,return:boundary};
   card.__reactFiber$fixture={return:component};
  }
 },{firstId,secondId,unrelatedId});
 assert.deepEqual(await invoke({kind:'preflight'}),{ready:true,count:2});
 assert.deepEqual((await invoke({kind:'preflight',includeIds:true})).legacyIds,[firstId,secondId]);
 await page.locator('.first').evaluate((card,id)=>{
  card.fixtureFiber=card.__reactFiber$fixture;
  delete card.__reactFiber$fixture;
  card.id=id;
 },firstId);
 const scan=await invoke({kind:'preflight',includeIds:true});
 assert.ok(scan.visibleIds.includes(firstId));
 assert.deepEqual(scan.legacyIds,[secondId]);
 assert.equal((await invoke({kind:'complete',task:{...task,id:firstId,rewardId:''}})).outcome,'blocked');
 await page.locator('.first').evaluate(card=>{
  card.removeAttribute('id');
  card.__reactFiber$fixture=card.fixtureFiber;
  delete card.fixtureFiber;
 });
 assert.equal((await invoke({kind:'complete',task:{...task,id:unrelatedId}})).outcome,'blocked');
 assert.equal((await invoke({kind:'complete',task:{...task,id:firstId,rewardId:''}})).outcome,'clicked');
 assert.equal(await page.evaluate(()=>window.clicks),3);
 assert.equal((await invoke({kind:'complete',task:{...task,id:secondId,viewer:'other',input:'https://youtu.be/b'}})).outcome,'clicked');
 assert.equal(await page.evaluate(()=>window.otherClicks),1);
 await page.locator('.first').evaluate((card,id)=>{
  card.__reactFiber$fixture.return.alternate={memoizedProps:{redemption:{id}}};
 },secondId);
 assert.equal((await invoke({kind:'complete',task:{...task,id:firstId}})).outcome,'blocked');
 assert.equal(await page.evaluate(()=>window.clicks),3);
 console.log('Synthetic DOM: calibration, row-scoped Twitch IDs, exact clicks and guards for conflicting IDs, missing IDs and bulk actions passed');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
