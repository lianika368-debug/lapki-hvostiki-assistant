() => {
  // Gradio 6.30 has hard-coded English loader/scroll labels. Translate only UI nodes.
  const labels = {'Scroll down':'К новым сообщениям','Loading response':'Ассистент готовит ответ',
    'Loading content':'Обработка сообщения','common.complete':'Готово','Clear chat':'Начать заново'};
  function localize(root) {
    root.querySelectorAll('[aria-label],[title]').forEach(el => {
      ['aria-label','title'].forEach(attr => {
        const value=el.getAttribute(attr);
        if(labels[value]) el.setAttribute(attr,labels[value]);
      });
    });
    root.querySelectorAll('.sr-only').forEach(el=>{
      if(labels[el.textContent]) el.textContent=labels[el.textContent];
    });
    root.querySelectorAll('.progress-text').forEach(el=>{
      const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
      let node;
      while((node=walker.nextNode())){
        if(node.nodeValue.includes('processing |')) node.nodeValue=node.nodeValue.replace('processing |','Обработка |');
        if(node.nodeValue.includes('queue:')) node.nodeValue=node.nodeValue.replace('queue:','Очередь:');
      }
    });
  }
  const observer=new MutationObserver(()=>localize(document));
  observer.observe(document.body,{subtree:true,childList:true});
  localize(document);
  // Keep the newest answer visible after an intentional send. Reading older history
  // otherwise uses Gradio's native autoscroll + its labelled jump-to-bottom button.
  let followNext=false, botsAtSend=0;
  const follow=()=>{followNext=true;botsAtSend=document.querySelectorAll('#salon-chat .message.bot').length;};
  document.addEventListener('click',e=>{
    if(e.target.closest('#composer button,#quick-questions button,#context-actions button,#review-panel button')) follow();
  });
  document.addEventListener('keydown',e=>{
    if(e.target.closest('#composer textarea')&&e.key==='Enter'&&!e.shiftKey) follow();
  });
  const chatObserver=new MutationObserver(()=>{
    if(!followNext)return;
    const messages=document.querySelectorAll('#salon-chat .message.bot');
    if(messages.length){
      const last=messages[messages.length-1];
      if(messages.length>botsAtSend){
        const pane=document.querySelector('#salon-chat .bubble-wrap,#salon-chat .panel-wrap');
        if(pane)pane.scrollTop=pane.scrollHeight;
        followNext=false;
      }
    }
  });
  document.addEventListener('wheel',e=>{if(e.target.closest('#salon-chat'))followNext=false;},{passive:true});
  document.addEventListener('touchstart',e=>{if(e.target.closest('#salon-chat'))followNext=false;},{passive:true});
  const attach=()=>{const chat=document.querySelector('#salon-chat');if(chat)chatObserver.observe(chat,{childList:true,subtree:true});};
  attach();
  // Reveal a newly opened form or review once; ordinary chat updates don't move the page.
  let lastBookingPanel=null, bookingFrame=null;
  const revealBookingPanel=()=>{
    bookingFrame=null;
    const form=document.querySelector('#booking-form');
    const review=document.querySelector('#review-panel');
    const panel=form?.getClientRects().length?form:review?.getClientRects().length?review:null;
    const panelKey=panel?.id||null;
    if(panel&&panelKey!==lastBookingPanel){
      panel.scrollIntoView({block:'start',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    }
    lastBookingPanel=panelKey;
  };
  const bookingObserver=new MutationObserver(()=>{
    if(bookingFrame===null) bookingFrame=requestAnimationFrame(revealBookingPanel);
  });
  bookingObserver.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class','style']});
  if(window.visualViewport){
    const keyboard=()=>document.documentElement.style.setProperty('--keyboard-offset',
      Math.max(0,window.innerHeight-window.visualViewport.height-window.visualViewport.offsetTop)+'px');
    window.visualViewport.addEventListener('resize',keyboard);
    window.visualViewport.addEventListener('scroll',keyboard);
    keyboard();
  }
}
