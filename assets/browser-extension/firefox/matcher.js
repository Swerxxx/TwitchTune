/* Pure identity matching: no fuzzy text, positional matching or bulk actions. */
(function(root){
  function chooseOrder(task,cards){
    if(!task || typeof task.id!== 'string' || !Array.isArray(cards))return {ok:false,reason:'Некорректное задание'};
    const matches=cards.filter(c=>Array.isArray(c.ids)&&c.ids.includes(task.id));
    if(matches.length!==1)return {ok:false,reason:matches.length?'Несколько карточек с одним ID':'На странице не найден точный ID заказа'};
    const card=matches[0];
    if(new Set(card.ids).size!==1)return {ok:false,reason:'Карточка содержит несколько ID; нельзя определить отдельный заказ'};
    if(card.viewer && task.viewer && card.viewer.toLowerCase()!==task.viewer.toLowerCase())return {ok:false,reason:'Не совпадает зритель'};
    if(card.reward && task.rewardTitle && card.reward!==task.rewardTitle)return {ok:false,reason:'Не совпадает награда'};
    if(card.input && task.input && card.input!==task.input)return {ok:false,reason:'Не совпадает текст заказа'};
    return {ok:true,index:cards.indexOf(card)};
  }
  const api={chooseOrder};
  if(typeof module!=='undefined')module.exports=api;
  root.TwitchTuneMatcher=api;
})(globalThis);
