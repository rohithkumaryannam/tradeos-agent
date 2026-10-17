// Terminal presentation reuses the existing websocket, risk editor and trade journal.
const iconPaths = {
 home:'M3 10 12 3l9 7v11h-6v-7H9v7H3Z',search:'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
 chart:'M4 21V11h3v10 M10 21V4h3v17 M16 21V8h3v13',bot:'M8 3h8 M12 3v4 M4 8h16v12H4Z M8 12v3 M16 12v3 M1 11v5 M23 11v5',
 nodes:'M6 5 18 12 6 19 M6 5v14 M8 5a2 2 0 1 1-4 0 2 2 0 0 1 4 0 M20 12a2 2 0 1 1-4 0 2 2 0 0 1 4 0 M8 19a2 2 0 1 1-4 0 2 2 0 0 1 4 0',
 flask:'M8 3h8 M10 3v7L4 20h16l-6-10V3 M7 15h10',history:'M3 12a9 9 0 1 0 2-6 M3 3v5h5 M12 7v5l4 2',briefcase:'M3 7h18v14H3Z M8 7V3h8v4 M3 11l9 4 9-4 M12 13v4',
 orders:'M5 4h14v16H5Z M8 8h8 M8 12h8 M8 16h5',shield:'M12 2 3 6v7c0 4 9 9 9 9s9-5 9-9V6Z M8 12l3 3 5-6',sparkles:'m12 2 2 7 7 3-7 2-2 8-2-8-8-2 8-3Z M21 2v4 M19 4h4',
 book:'M4 3h16v18H4Z M8 3v18 M11 7h6 M11 11h6',heart:'M12 21 3 12C-3 4 8-1 12 6c4-7 15-2 9 6Z',settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1Z M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
 activity:'M2 12h4l3-8 5 16 3-8h5',bell:'M5 17h14l-2-4V8a5 5 0 0 0-10 0v5Z M10 21h4',chevron:'m7 10 5 5 5-5',user:'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M4 21v-3a8 8 0 0 1 16 0v3Z',
 camera:'M3 7h4l2-3h6l2 3h4v14H3Z M16 13a4 4 0 1 1-8 0 4 4 0 0 1 8 0',expand:'M3 9V3h6 M15 3h6v6 M21 15v6h-6 M9 21H3v-6',crosshair:'M12 1v6 M12 17v6 M1 12h6 M17 12h6 M18 12a6 6 0 1 1-12 0 6 6 0 0 1 12 0',
 trend:'M4 20 20 4 M6 20a2 2 0 1 1-4 0 2 2 0 0 1 4 0 M22 4a2 2 0 1 1-4 0 2 2 0 0 1 4 0',minus:'M3 12h18',trash:'M3 6h18 M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7',refresh:'M20 7a9 9 0 1 0 1 8 M20 2v6h-6'
};
function icon(name) { return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${iconPaths[name] || iconPaths.chart}"/></svg>`; }
document.querySelectorAll('[data-icon]').forEach(el => { el.outerHTML = icon(el.dataset.icon); });
function openDialog(id) {
 document.querySelectorAll('dialog[open]').forEach(d => d.close());
 $(id).showModal();
 if (id === 'searchDialog') { $('searchInput').value = ''; searchWorkspace(''); $('searchInput').focus(); }
}
document.querySelectorAll('[data-open]').forEach(b => b.onclick = () => openDialog(b.dataset.open));
document.querySelectorAll('[data-close]').forEach(b => b.onclick = () => b.closest('dialog').close());
document.querySelectorAll('[data-risk]').forEach(b => b.onclick = () => { document.querySelectorAll('dialog[open]').forEach(d=>d.close()); $('riskControlsBtn').click(); });
document.querySelectorAll('dialog').forEach(d => d.addEventListener('click', e => { if(e.target===d) { const r=d.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close(); } }));
document.querySelectorAll('[data-focus]').forEach(b => b.onclick = () => { const p=$(b.dataset.focus); p.scrollIntoView({block:'center',behavior:'smooth'}); p.classList.add('highlight-panel'); setTimeout(()=>p.classList.remove('highlight-panel'),1800); });
$('botStatusBtn').onclick = () => openDialog('botsDialog');
$('modeBtn').onclick = () => openDialog('settingsDialog');
$('executionBtn').onclick = () => openDialog('settingsDialog');
$('switchMode').onclick = () => openDialog('settingsDialog');
$('searchBtn').onclick = () => openDialog('searchDialog');
document.addEventListener('keydown', e => {if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();openDialog('searchDialog');}});
function searchWorkspace(query) {
 const entries=[...(last?.symbols||[]).map(s=>({label:s,kind:'Market',symbol:s})),...Object.entries(STRAT_SHORT).map(([s,label])=>({label,kind:'Strategy',dialog:'strategiesDialog'})),
 {label:'Risk controls',kind:'Settings',risk:true},{label:'Trade journal',kind:'History',dialog:'journalDialog'},{label:'Bot controls',kind:'Settings',dialog:'botsDialog'},{label:'System health',kind:'Status',dialog:'healthDialog'}];
 $('searchResults').innerHTML=entries.filter(e=>e.label.toLowerCase().includes(query.toLowerCase())).map(e=>`<button class="search-result" data-symbol="${esc(e.symbol||'')}" data-dialog="${e.dialog||''}" data-risk-search="${e.risk||''}">${esc(e.label)}<small>${e.kind}</small></button>`).join('')||'<p>No matches.</p>';
 $('searchResults').querySelectorAll('button').forEach(b=>b.onclick=()=>{ if(b.dataset.symbol){selectMarket(b.dataset.symbol);$('searchDialog').close();}else if(b.dataset.riskSearch){$('searchDialog').close();$('riskControlsBtn').click();}else openDialog(b.dataset.dialog); });
}
$('searchInput').oninput=e=>searchWorkspace(e.target.value);
let chartCache=null, chartRequest=0, chartBusy=false, chartTF=null, visibleBars=100, showIndicators=true, chartHover=null, drawMode='crosshair', drawingStart=null;
const drawings=new Map();
function selectMarket(symbol) { activeSymbol=symbol; chartCache=null; chartHover=null; if(last)render(last); refreshChart(); }
$('symbolSelect').onchange=e=>selectMarket(e.target.value);
$('tradeSymbol').onchange=e=>selectMarket(e.target.value);
function setTimeframe(tf) { chartTF=tf; chartCache=null; chartHover=null; $('chartResolution').value=tf; render(last); refreshChart(); }
$('chartResolution').onchange=e=>setTimeframe(e.target.value);
$('timeframes').querySelectorAll('button').forEach(b=>b.onclick=()=>setTimeframe(b.dataset.tf));
async function refreshChart() {
 if(!last||!activeSymbol) return;
 const tf=chartTF||last.resolution, symbol=activeSymbol, sequence=++chartRequest;
 if(tf===last.resolution){chartCache=null;chartBusy=false;render(last);return;}
 chartBusy=true; $('chartNotice').hidden=false; $('chartNotice').textContent='Loading '+symbol+' · '+tf+'…';
 try {
  const r=await fetch(`/api/chart?symbol=${encodeURIComponent(symbol)}&resolution=${encodeURIComponent(tf)}`),d=await r.json();
  if(sequence!==chartRequest)return;
  if(!r.ok)throw new Error(d.error||'Chart unavailable');
  chartCache={symbol,tf,chart:d.chart}; $('chartNotice').hidden=true;
 }catch(e){if(sequence===chartRequest){chartCache=null;$('chartNotice').hidden=false;$('chartNotice').textContent=e.message;}}
 finally{if(sequence===chartRequest){chartBusy=false;render(last);}}
}
setInterval(()=>{if(chartTF&&last&&chartTF!==last.resolution&&!chartBusy&&!document.hidden)refreshChart();},30000);
function chartData() { const tf=chartTF||last?.resolution; if(tf===last?.resolution)return (last?.markets?.[activeSymbol]?.chart||[]).slice(-visibleBars); return chartCache?.symbol===activeSymbol&&chartCache?.tf===tf ? chartCache.chart.slice(-visibleBars):[]; }
renderMarketsStrip = function(s) {
 $('marketsStrip').innerHTML=(s.symbols||[]).map(symbol=>`<button class="${symbol===activeSymbol?'active':''}" data-market="${esc(symbol)}" aria-pressed="${symbol===activeSymbol}">${esc(symbol)}</button>`).join('');
 $('marketsStrip').querySelectorAll('button').forEach(b=>b.onclick=()=>selectMarket(b.dataset.market));
};
let performanceDays=0;
$('performanceRange').querySelectorAll('button').forEach(b=>b.onclick=()=>{performanceDays=Number(b.dataset.days);$('performanceRange').querySelectorAll('button').forEach(x=>x.classList.toggle('selected',x===b));if(last)render(last);});
function summarize(trades){
 const wins=trades.filter(t=>t.pnl>0), losses=trades.filter(t=>t.pnl<0), sum=ts=>ts.reduce((n,t)=>n+t.pnl,0), w=sum(wins),l=sum(losses),rs=trades.filter(t=>Number.isFinite(t.r));
 return {trades:trades.length,wins:wins.length,losses:losses.length,win_rate:trades.length?+(wins.length/trades.length*100).toFixed(1):null,total_pnl:sum(trades),profit_factor:l?w/-l:null,avg_win:wins.length?w/wins.length:null,avg_loss:losses.length?l/losses.length:null,avg_r:rs.length?+(rs.reduce((n,t)=>n+t.r,0)/rs.length).toFixed(2):null,best:trades.length?Math.max(...trades.map(t=>t.pnl)):null,worst:trades.length?Math.min(...trades.map(t=>t.pnl)):null};
}
renderAnalytics = function(a){
 const trades=(last?.trades||[]).filter(t=>!performanceDays||Date.parse(t.exit_ts)>=Date.now()-performanceDays*86400000);
 if(performanceDays)a=summarize(trades); a=a||{};
 $('perfSub').textContent=`(${a.trades||0} trades${performanceDays?' · '+performanceDays+'D':''})`;
 const tiles=[['Net P&L',a.trades?signed(a.total_pnl):'—',a.total_pnl>=0?'up':'down','net-tile'],['Win Rate',a.win_rate==null?'—':fmt(a.win_rate,1)+'%',a.win_rate>=50?'up':'down'],['Profit Factor',fmt(a.profit_factor),a.profit_factor>=1?'up':'down'],['W / L',`${a.wins||0} / ${a.losses||0}`,''],['Avg Win',a.avg_win==null?'—':signed(a.avg_win),'up'],['Avg Loss',a.avg_loss==null?'—':signed(a.avg_loss),'down'],['Best / Worst',a.trades?`${signed(a.best)} / ${signed(a.worst)}`:'—','']];
 $('perfGrid').innerHTML=tiles.map(([name,value,cl,extra])=>`<div class="perf ${extra||''}"><div class="l">${name}</div><div class="n ${cl}">${value}</div>${extra?'<canvas id="performanceSpark" height="50" aria-label="Cumulative realized trade profit"></canvas>':''}</div>`).join('');
 let cumulative=0; spark('performanceSpark',[0,...trades.map(t=>cumulative+=t.pnl)],a.total_pnl>=0?'#00d99b':'#ff365d');
};
renderFeed = function(feed){
 const filtered=(feed||[]).filter(e=>!$('feedFilter').value||e.event===$('feedFilter').value);
 const html=items=>items.map(e=>`<div title="${esc(e.detail)}"><span class="ts">${esc((e.ts||'').slice(11,19))}</span><span class="tag ${esc(e.event)}">${esc(e.event.toUpperCase())}</span><span class="detail">${esc(e.detail)}</span></div>`).join('')||'<p class="mut">No matching events.</p>';
 $('feed').innerHTML=html(filtered);$('fullFeed').innerHTML=html(feed||[]);
};
$('feedFilter').onchange=()=>renderFeed(last?.feed);
function terminalCanvas(id){const cv=$(id),dpr=devicePixelRatio||1,w=cv.clientWidth,h=parseFloat(getComputedStyle(cv).height)||Number(cv.getAttribute('height'))||100;cv.width=Math.round(w*dpr);cv.height=Math.round(h*dpr);const ctx=cv.getContext('2d');ctx.scale(dpr,dpr);return{cv,ctx,w,h};}
function spark(id,values,color='#2382ff'){
 if(!$(id))return;const {ctx,w,h}=terminalCanvas(id);const vals=values.filter(Number.isFinite);if(vals.length<2)return;
 const lo=Math.min(...vals),hi=Math.max(...vals),rng=hi-lo||1,y=v=>h-4-(v-lo)/rng*(h-8),x=i=>i/(vals.length-1)*w;
 ctx.beginPath();vals.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.strokeStyle=color;ctx.lineWidth=1;ctx.stroke();
 ctx.lineTo(w,h);ctx.lineTo(0,h);ctx.closePath();const grad=ctx.createLinearGradient(0,0,0,h);grad.addColorStop(0,color+'45');grad.addColorStop(1,color+'00');ctx.fillStyle=grad;ctx.fill();
}
let chartGeometry=null;
drawPriceChart = function(){
 const {ctx,w,h}=terminalCanvas('priceChart'),data=chartData(),right=63,plotW=w-right,top=50,volumeH=32,bottom=h-volumeH-9;
 ctx.font='9px Inter, sans-serif';ctx.strokeStyle='#1a2a3d66';ctx.fillStyle='#829bbd';ctx.lineWidth=.6;
 if(!data.length){ctx.textAlign='center';ctx.fillText(chartBusy?'Loading market candles…':'Market data unavailable',w/2,h/2);chartGeometry=null;return;}
 const prices=data.flatMap(c=>[c.l,c.h,...(showIndicators?[c.ema_trend]:[])].filter(Number.isFinite));
 const low=Math.min(...prices),high=Math.max(...prices),buffer=(high-low)*.08||1,lo=low-buffer,hi=high+buffer,y=p=>top+(hi-p)/(hi-lo)*(bottom-top),step=plotW/data.length,x=i=>i*step+step/2;
 chartGeometry={x,y,data,plotW,top,bottom,lo,hi,step};
 for(let i=0;i<=5;i++){const yy=top+i*(bottom-top)/5;ctx.beginPath();ctx.moveTo(0,yy);ctx.lineTo(w,yy);ctx.stroke();ctx.fillText(fmt(hi-i*(hi-lo)/5,1),plotW+7,yy+3);}
 for(let i=0;i<10;i++){const xx=i*plotW/10;ctx.beginPath();ctx.moveTo(xx,0);ctx.lineTo(xx,h);ctx.stroke();}
 const maxVol=Math.max(...data.map(c=>c.volume||0),1);
 data.forEach((c,i)=>{const color=c.c>=c.o?'#00c896':'#f3385b';ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x(i),y(c.h));ctx.lineTo(x(i),y(c.l));ctx.stroke();ctx.fillRect(x(i)-step*.33,Math.min(y(c.o),y(c.c)),Math.max(1,step*.66),Math.max(1,Math.abs(y(c.o)-y(c.c))));ctx.globalAlpha=.55;const vh=(c.volume||0)/maxVol*volumeH;ctx.fillRect(x(i)-step*.33,h-vh-3,Math.max(1,step*.66),vh);ctx.globalAlpha=1;});
 if(showIndicators)for(const [key,color]of[['ema_fast','#18a1ff'],['ema_slow','#edb220'],['ema_trend','#9c54ff']]){ctx.beginPath();let began=false;data.forEach((c,i)=>{if(c[key]==null)return;if(began)ctx.lineTo(x(i),y(c[key]));else{ctx.moveTo(x(i),y(c[key]));began=true;}});ctx.strokeStyle=color;ctx.lineWidth=1;ctx.stroke();}
 function line(price,color,label,dash=[3,3]){if(price==null||price<lo||price>hi)return;const yy=y(price);ctx.setLineDash(dash);ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(0,yy);ctx.lineTo(plotW,yy);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=color;ctx.fillRect(plotW,yy-7,right,14);ctx.fillStyle='#fff';ctx.font='9px monospace';ctx.fillText(label,plotW+4,yy+3);}
 const latest=data.at(-1);line(latest.c,latest.c>=latest.o?'#009d87':'#d93959',fmt(latest.c,1),[1,2]);
 const position=last?.markets?.[activeSymbol]?.position;if(position){line(position.sl,'#c72b53','SL '+fmt(position.sl,1));line(position.tp,'#02936e','TP '+fmt(position.tp,1));}
 const key=activeSymbol+':'+(chartTF||last?.resolution),ds=drawings.get(key)||[];
 const xAt=t=>(t-data[0].t)/(latest.t-data[0].t||1)*(plotW-step)+step/2;
 ctx.save();ctx.beginPath();ctx.rect(0,0,plotW,h);ctx.clip();ctx.strokeStyle='#4b9fff';ctx.lineWidth=1.5;
 for(const d of ds){ctx.beginPath();if(d.type==='horizontal'){ctx.moveTo(0,y(d.a.p));ctx.lineTo(plotW,y(d.a.p));}else{ctx.moveTo(xAt(d.a.t),y(d.a.p));ctx.lineTo(xAt(d.b.t),y(d.b.p));}ctx.stroke();}
 ctx.restore();
 if(chartHover){const index=Math.max(0,Math.min(data.length-1,Math.floor(chartHover.x/step))),xx=x(index),yy=chartHover.y;ctx.strokeStyle='#94a9c0';ctx.lineWidth=.6;ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(xx,0);ctx.lineTo(xx,h);ctx.moveTo(0,yy);ctx.lineTo(plotW,yy);ctx.stroke();ctx.setLineDash([]);setOHLC(data[index]);}else setOHLC(latest);
 const indices=[0,.2,.4,.6,.8,1].map(q=>Math.floor(q*(data.length-1)));$('chartTimes').innerHTML=indices.map(i=>`<span>${new Date(data[i].t*1000).toISOString().slice(11,16)}</span>`).join('');
};
function setOHLC(c){if(!c)return;$('ohlc').textContent=`O ${fmt(c.o,1)}   H ${fmt(c.h,1)}   L ${fmt(c.l,1)}   C ${fmt(c.c,1)}   ${signed(c.c-c.o)}`;$('ohlc').className='mono '+(c.c>=c.o?'up':'down');$('indicatorValues').innerHTML=showIndicators?`<span>EMA 9 close <b style="color:#18a1ff">${fmt(c.ema_fast,1)}</b></span><span>EMA 20 close <b style="color:#edb220">${fmt(c.ema_slow,1)}</b></span><span>EMA 200 close <b style="color:#9c54ff">${fmt(c.ema_trend,1)}</b></span>`:'';$('rsiValue').textContent=fmt(c.rsi);}
drawRSI=function(){const {ctx,w,h}=terminalCanvas('rsi'),data=chartData(),pw=w-63,y=v=>5+(100-v)/100*(h-10);ctx.fillStyle='#32134c44';ctx.fillRect(0,y(70),pw,y(30)-y(70));for(const n of[30,50,70]){ctx.setLineDash([4,4]);ctx.strokeStyle='#65448677';ctx.beginPath();ctx.moveTo(0,y(n));ctx.lineTo(pw,y(n));ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#a7a4bb';ctx.font='8px monospace';ctx.fillText(n.toFixed(0),pw+9,y(n)+3);}ctx.beginPath();let start=false;data.forEach((c,i)=>{if(c.rsi==null)return;const x=(i+.5)*pw/data.length;if(start)ctx.lineTo(x,y(c.rsi));else{ctx.moveTo(x,y(c.rsi));start=true;}});ctx.strokeStyle='#bb4bdf';ctx.lineWidth=1;ctx.stroke();};
drawEquity=function(curve){const {ctx,w,h}=terminalCanvas('eq');if(!curve||curve.length<2){ctx.fillStyle='#6f88a6';ctx.font='10px Inter';ctx.textAlign='center';ctx.fillText('Awaiting recorded equity history',w/2,h/2);return;}const vals=curve.map(c=>c.equity),lo=Math.min(...vals),hi=Math.max(...vals),rng=hi-lo||1,pw=w-45,y=v=>h-10-(v-lo)/rng*(h-20);ctx.font='8px monospace';for(let i=0;i<4;i++){const yy=10+i*(h-20)/3;ctx.strokeStyle='#16283c';ctx.beginPath();ctx.moveTo(0,yy);ctx.lineTo(pw,yy);ctx.stroke();ctx.fillStyle='#8ca4c5';ctx.fillText(fmt(hi-i*rng/3,0),pw+4,yy+3);}ctx.beginPath();vals.forEach((v,i)=>i?ctx.lineTo(i/(vals.length-1)*pw,y(v)):ctx.moveTo(0,y(v)));ctx.strokeStyle='#2382ff';ctx.lineWidth=1.25;ctx.stroke();ctx.lineTo(pw,h);ctx.lineTo(0,h);ctx.closePath();const g=ctx.createLinearGradient(0,0,0,h);g.addColorStop(0,'#1456e57a');g.addColorStop(1,'#1441aa08');ctx.fillStyle=g;ctx.fill();};
$('priceChart').addEventListener('mousemove',e=>{const r=e.target.getBoundingClientRect();chartHover={x:e.clientX-r.left,y:e.clientY-r.top};drawPriceChart();});
$('priceChart').addEventListener('mouseleave',()=>{chartHover=null;drawPriceChart();});
$('priceChart').addEventListener('wheel',e=>{e.preventDefault();visibleBars=Math.max(25,Math.min(160,visibleBars+(e.deltaY>0?10:-10)));drawPriceChart();drawRSI();},{passive:false});
for(const [id,mode] of [['crosshairTool','crosshair'],['trendTool','trend'],['horizontalTool','horizontal']])$(id).onclick=()=>{drawMode=mode;drawingStart=null;['crosshairTool','trendTool','horizontalTool'].forEach(x=>$(x).classList.toggle('selected',x===id));};
$('priceChart').addEventListener('click',e=>{if(!chartGeometry||drawMode==='crosshair')return;const g=chartGeometry,r=e.target.getBoundingClientRect(),xx=e.clientX-r.left,yy=e.clientY-r.top;if(xx>g.plotW)return;const point={t:g.data[0].t+xx/g.plotW*(g.data.at(-1).t-g.data[0].t),p:g.hi-(yy-g.top)/(g.bottom-g.top)*(g.hi-g.lo)},key=activeSymbol+':'+(chartTF||last.resolution),ds=drawings.get(key)||[];if(drawMode==='horizontal')ds.push({type:'horizontal',a:point});else if(!drawingStart){drawingStart=point;notify('Select the end of the trend line.');return;}else{ds.push({type:'trend',a:drawingStart,b:point});drawingStart=null;}drawings.set(key,ds);drawPriceChart();});
$('clearDrawings').onclick=()=>{drawings.delete(activeSymbol+':'+(chartTF||last.resolution));drawingStart=null;drawPriceChart();};
$('resetChart').onclick=()=>{visibleBars=100;drawPriceChart();drawRSI();};
$('indicatorsBtn').onclick=()=>{showIndicators=!showIndicators;$('indicatorsBtn').classList.toggle('selected',showIndicators);drawPriceChart();};
$('fullChart').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('chartPanel').requestFullscreen();}catch(e){notify('Fullscreen is unavailable in this browser.',true);}};
document.addEventListener('fullscreenchange',()=>{if(last)render(last);});
$('saveChart').onclick=()=>{const a=document.createElement('a');a.download=`${activeSymbol}-${chartTF||last?.resolution}.png`;a.href=$('priceChart').toDataURL();a.click();};
let orderBusy=false;
async function loadOrders(){if(orderBusy)return;orderBusy=true;try{const r=await fetch('/api/orders'),d=await r.json();if(!r.ok)throw new Error(d.error||'Orders unavailable');$('orderCount').textContent=`(${d.orders.length})`;$('ordersEmpty').hidden=!!d.orders.length;$('ordersEmpty').innerHTML=icon('orders')+`<strong>No open orders</strong><span>${d.status==='simulated'?'Paper fills execute immediately':'Your exchange orders will appear here'}</span>`;$('openOrders').innerHTML=d.orders.map(o=>`<tr><td>${esc(o.symbol)}</td><td class="${o.side==='buy'?'up':'down'}">${esc((o.side||'').toUpperCase())}</td><td>${esc((o.type||'').replaceAll('_',' '))}</td><td>${o.price?fmt(Number(o.price),1):'Market'}</td><td>${esc(o.size)}</td><td>${esc(o.state)}</td></tr>`).join('');}catch(e){$('orderCount').textContent='—';$('openOrders').innerHTML='';$('ordersEmpty').hidden=false;$('ordersEmpty').innerHTML=icon('orders')+'<strong>Order state unavailable</strong><span>'+esc(e.message)+'</span>';}finally{orderBusy=false;}}
$('refreshOrders').onclick=loadOrders;setInterval(()=>{if(!document.hidden)loadOrders();},30000);loadOrders();
let tradeSide='short',reviewTicket=null;
document.querySelectorAll('[data-side]').forEach(b=>b.onclick=()=>{tradeSide=b.dataset.side;document.querySelectorAll('[data-side]').forEach(x=>x.classList.toggle('selected',x===b));updateTicket();});
$('tradeSize').oninput=updateTicket;
function ticketIssue(){if(!last)return'Waiting for live state';if(!wsOpen||Date.now()-lastFrame>5000)return'Connection stale; wait for live state';if(last.entries_paused)return'New entries are paused';const m=last.markets?.[activeSymbol];if(m?.position)return`${activeSymbol} already has an open position`;if(!last.entry_risk_ok)return last.entry_risk_reason||'Risk controls block new entries';if(m?.data_status!=='fresh')return m?.data_status||'Waiting for market data';const same=Object.values(last.markets||{}).filter(m=>m.position?.side===tradeSide).length;if(same>=last.risk.max_same_direction_positions)return'Same-direction position limit reached';return'';}
function updateTicket(){
 if(!last)return;
 const m=last.markets?.[activeSymbol],pending=m?.pending;
 $('tradeNotional').textContent='Risk-sized at confirmation';
 $('tradeProtection').textContent=`${last.risk.stop_loss_pct}% / ${last.risk.take_profit_pct}% default`;
 $('tradeLeverage').textContent=last.risk.max_leverage+'× · Edit';
 $('tradeBlock').textContent=pending?`${pending.action}: ${pending.reason}`:'No pending strategy signal. Use Manual execution to review strategy entries here.';
 $('placeOrder').textContent=pending?'Confirm strategy signal':'Awaiting strategy signal';
 $('placeOrder').disabled=!pending||!wsOpen||Date.now()-lastFrame>5000||actionBusy;
 $('tradeSize').disabled=true;
 document.querySelectorAll('[data-side]').forEach(b=>b.disabled=true);
}
$('quickTradeForm').onsubmit=e=>{e.preventDefault();if(last?.markets?.[activeSymbol]?.pending)post('/api/signal/confirm'+qs(activeSymbol));else notify('No strategy signal is awaiting confirmation.');};
document.querySelectorAll('[data-ai-topic]').forEach(b=>b.onclick=()=>{$('aiPrompt').textContent=b.dataset.aiTopic+' · '+(activeSymbol||'Market');$('aiBtn').click();});
const originalRender=render;
render=function(s){
 if(!s)return;if(!chartTF)chartTF=s.resolution;
 originalRender(s);
 const markets=s.markets||{},m=markets[activeSymbol]||{},p=s.portfolio||{},r=s.risk||{},symbols=s.symbols||[],chart=m.chart||[],positions=Object.entries(markets).filter(([,v])=>v.position);
 for(const id of['symbolSelect','tradeSymbol']){const el=$(id);if(el.dataset.symbols!==symbols.join(',')){el.innerHTML=symbols.map(x=>`<option>${esc(x)}</option>`).join('');el.dataset.symbols=symbols.join(',');}el.value=activeSymbol;}
 $('chartResolution').value=chartTF;document.querySelectorAll('[data-tf]').forEach(b=>b.classList.toggle('selected',b.dataset.tf===chartTF));
 $('modeBtn').textContent=s.mode==='DELTA_DEMO'?'Delta Demo':'Paper';$('executionBtn').textContent=s.auto?'Auto':'Manual';$('botCount').textContent=symbols.length;
 $('walletMode').textContent=$('modeBtn').textContent;$('walletConnection').textContent=s.mode==='PAPER'?'Simulated account':s.account_status==='live'?'Connected':'Connection unavailable';$('walletEquity').textContent=s.equity==null?'—':'$'+fmt(s.equity);
 const usd=(s.account_balances||[]).find(b=>['USD','USDT'].includes(b.asset));$('walletAvailable').textContent=usd?.available!=null?'$'+fmt(usd.available):'—';const margin=(s.exchange_positions||[]).map(p=>p.margin);$('walletMargin').textContent=s.mode==='DELTA_DEMO'&&margin.length&&margin.every(Number.isFinite)?'$'+fmt(margin.reduce((a,b)=>a+b,0)):'—';
 $('switchMode').textContent=s.mode==='DELTA_DEMO'?'Switch to Paper':'Switch to Delta Demo';$('heroSymbol').textContent=activeSymbol;$('heroPrice').textContent=fmt(m.price,1);
 const first=chart[0],latest=chart.at(-1),change=first&&latest?latest.c-first.c:null;$('heroChange').textContent=change==null?'Waiting for candles':`${signed(change)}  ${signed(change/first.c*100)}%`;$('heroChange').title='Change across the loaded strategy candle window';$('heroChange').className='stat-note '+(change>=0?'up':'down');
 const coin=activeSymbol?.startsWith('ETH')?'eth':activeSymbol?.startsWith('SOL')?'sol':'btc';$('heroCoin').className='coin '+coin;$('heroCoin').textContent=coin==='btc'?'₿':coin==='eth'?'Ξ':'◎';
 spark('priceSpark',chart.map(c=>c.c),change>=0?'#00d99b':'#ff365d');spark('equitySpark',(s.equity_curve||[]).map(c=>c.equity));let day=0;spark('pnlSpark',[0,...(s.trades||[]).filter(t=>t.exit_ts.slice(0,10)===s.ts.slice(0,10)).map(t=>day+=t.pnl)],s.realized_today>=0?'#00d99b':'#ff365d');
 $('slotHeadline').textContent=p.known===false?'—':`${p.positions} / ${r.max_open_positions}`;$('slotsNote').textContent=p.known===false?'Position state unknown':p.positions>=r.max_open_positions?'Max positions reached':'Slots available';$('slotsNote').className='stat-note '+(p.positions>=r.max_open_positions?'down':'up');$('slotProgress').value=100*p.positions/r.max_open_positions;$('exposureProgress').value=Math.min(100,p.leverage/r.max_leverage*100);
 $('positionsCount').textContent=p.known===false?'—':`(${positions.length})`;
 $('positionList').innerHTML=positions.map(([sym,v])=>{const pos=v.position,c=sym.startsWith('BTC')?'btc':sym.startsWith('ETH')?'eth':'sol';return`<article class="position-item"><div class="position-title"><span class="coin ${c}">${c==='btc'?'₿':c==='eth'?'Ξ':'◎'}</span><b>${esc(sym)}</b><span class="badge ${pos.side==='long'?'up':''}">${esc(pos.side.toUpperCase())}</span><span>${fmt(pos.size,0)}</span><span class="pnl ${pos.unrealized>=0?'up':'down'}">${signed(pos.unrealized)}</span></div><div class="position-numbers"><div><span>Entry</span><b>${fmt(pos.entry,1)}</b></div><div><span>Mark</span><b>${fmt(v.price,1)}</b></div><div><span>SL</span><b class="down">${fmt(pos.sl,1)}</b></div><div><span>TP</span><b class="up">${fmt(pos.tp,1)}</b></div></div><div class="position-protection"><span class="${pos.protection==='exchange'?'up':'warn'}">${pos.protection==='exchange'?'◆ SL/TP on exchange':pos.protection==='local'?'Local SL/TP protection':'Protection unverified'}</span><button data-position="${esc(sym)}">Manage</button></div></article>`;}).join('')||`<div class="empty-state"><strong>${p.known===false?'Positions unavailable':'No open positions'}</strong><span>${p.known===false?'Waiting for account access':'Qualifying trades will appear here'}</span></div>`;
 $('positionList').querySelectorAll('button').forEach(b=>b.onclick=()=>{selectMarket(b.dataset.position);openDialog('positionDialog');});
 $('recentTrades').innerHTML=[...(s.trades||[])].reverse().slice(0,6).map(t=>`<tr><td>${esc(t.exit_ts.slice(11,19))}</td><td>${esc(t.symbol)}</td><td class="${t.side==='long'?'up':'down'}">${esc(t.side.toUpperCase())}</td><td>${fmt(t.exit,1)}</td><td>${t.size}</td><td class="${t.pnl>=0?'up':'down'}">${signed(t.pnl)}</td></tr>`).join('')||'<tr><td colspan="6">No completed trades yet.</td></tr>';
 const active=new Set(s.active_strategies||[]);$('strategyCards').innerHTML=Object.keys(r.strategy_risk_pct||{}).map(name=>{const a=s.analytics?.by_strategy?.[name];return`<article class="strategy-mini"><header>${esc(STRAT_SHORT[name]||name)} <span class="active-label">${active.has(name)?'Active':'Inactive'}</span></header><p>${a?.trades?`${a.win_rate}% win rate · <span class="${a.total_pnl>=0?'up':'down'}">${signed(a.total_pnl)}</span>`:'No trades yet'}</p><button data-strategy-view="${esc(name)}">View →</button></article>`;}).join('');$('strategyCards').querySelectorAll('button').forEach(b=>b.onclick=()=>openDialog('strategiesDialog'));
 $('strategyTimeframe').textContent=s.resolution+' signals';$('chartTitle').textContent=activeSymbol+' PERPETUAL';$('chartSource').textContent='· '+chartTF+' · Delta';$('chartStatus').textContent=chartTF===s.resolution?(m.data_status==='fresh'?'Fresh candles':m.data_status):'Chart refreshes every 30s';$('chartClock').textContent=new Date().toISOString().slice(11,19)+' UTC';
 $('botDetails').textContent=`${s.strategy} · ${s.resolution} signals · ${symbols.join(', ')}. ${s.entries_paused?'Entries paused.':s.entry_risk_reason||'Entry risk budgets available.'}`;
 $('marketList').innerHTML=symbols.map(sym=>`<button class="market-result" data-select-market="${esc(sym)}">${esc(sym)}<small>${fmt(markets[sym]?.price,1)} · ${esc(markets[sym]?.data_status||'Unknown')}</small></button>`).join('');$('marketList').querySelectorAll('button').forEach(b=>b.onclick=()=>{selectMarket(b.dataset.selectMarket);$('marketsDialog').close();});
 $('analyticsDetails').innerHTML=`<dl>${[['Completed trades',s.analytics?.trades||0],['Net P&L',signed(s.analytics?.total_pnl)],['Win rate',fmt(s.analytics?.win_rate,1)+'%'],['Profit factor',fmt(s.analytics?.profit_factor)],['Average R',fmt(s.analytics?.avg_r)],['Portfolio stop risk',fmt(p.risk_pct)+'%'],['Gross exposure','$'+fmt(p.notional)]].map(([key,value])=>`<div class="row"><dt>${key}</dt><dd>${value}</dd></div>`).join('')}</dl>`;
 $('rCapOk').title=s.entry_risk_reason||'';$('rCapOk').textContent=s.entries_paused?'ENTRIES PAUSED':s.entry_risk_ok?'ENTRY BUDGET AVAILABLE':'NEW ENTRIES BLOCKED';$('rCapOk').className='risk-status '+(s.entry_risk_ok&&!s.entries_paused?'up':'down');updateTicket();
};
fetch('/api/state').then(r=>r.json()).then(s=>{last=s;render(s);}).catch(()=>notify('Waiting for dashboard connection',true));
