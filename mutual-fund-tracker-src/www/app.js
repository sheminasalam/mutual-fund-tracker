let funds=[];let portfolio=null;let profiles=[];let activeProfileId=localStorage.getItem('mft_active_profile')||'';let windowAllProfiles=false;let importPreview=null;window.__importMapping={};window.__sipMapping={};window.__sipReconciliationMapping={};let sortKey=localStorage.getItem('mft_sort_key')||'fund_name';let sortDir=localStorage.getItem('mft_sort_dir')||'asc';let appSettings={refresh_interval_seconds:7200,nifty_poll_interval_seconds:60,last_nav_refresh:null,nav_date:null};let refreshPollTimer=null;let pendingTxnQuote=null;let activePortfolioTab=localStorage.getItem('mft_portfolio_tab')||'table';let filtersOpen=localStorage.getItem('mft_filters_open')==='1';let fundFilters={search:'',sip:'all',returns:'all',plan:'all',option:'all',category:'all',parameter:'',relation:'>',value:''};
const money = v => v==null ? '—' : '₹'+Number(v).toLocaleString('en-IN',{maximumFractionDigits:0});
const num = (v,d=2) => v==null ? '—' : Number(v).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});
const pct = v => v==null ? '—' : num(v,2)+'%';
function formatDate(value){
 const s=String(value??'').trim();
 if(!s||s==='—'||s==='NAV unavailable'||s==='None')return s||'—';
 const m=s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
 if(m)return `${m[3]}/${m[2]}/${m[1]}`;
 const d=new Date(s);
 if(Number.isNaN(d.getTime()))return s;
 return d.toLocaleDateString('en-GB',{day:'2-digit',month:'2-digit',year:'numeric'});
}
function formatDateTime(value){
 const d=new Date(value);
 if(Number.isNaN(d.getTime()))return String(value??'—');
 return `${d.toLocaleDateString('en-GB',{day:'2-digit',month:'2-digit',year:'numeric'})} ${d.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}`;
}
function splitFundName(name){
 const s=String(name??'').trim();
 if(!s)return {main:'',sub:''};
 // Split at the first plan delimiter, allowing inconsistent spacing around the hyphen.
 // Prefer common plan keywords so internal hyphens in a fund name do not become the split point.
 const planMatch=s.match(/\s*-\s*(?=(?:direct|regular|growth|idcw|dividend)\b)/i);
 const i=planMatch?planMatch.index:-1;
 if(i<0){
   const fallback=s.search(/\s*-\s*/);
   if(fallback<0)return {main:s,sub:''};
   const delim=s.slice(fallback).match(/^\s*-\s*/)?.[0]||'-';
   return {main:s.slice(0,fallback).trim(),sub:s.slice(fallback+delim.length).trim()};
 }
 const delim=planMatch[0];
 return {main:s.slice(0,i).trim(),sub:s.slice(i+delim.length).trim()};
}
function tone(v){if(v==null)return 'value-na'; if(Number(v)>0)return 'value-pos'; if(Number(v)<0)return 'value-neg'; return 'value-neutral'}
function cell(v,format){return `<td class="${tone(v)} num">${format(v)}</td>`}
function escapeHtml(s){return String(s??'').replace(/[&<>'"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[m]))}
function getTheme(){return document.documentElement.dataset.theme==='dark'?'dark':'light'}
function applyTheme(theme){const dark=theme==='dark';document.documentElement.dataset.theme=dark?'dark':'light';try{localStorage.setItem('mft_theme',dark?'dark':'light')}catch(e){}const btn=document.getElementById('themeBtn');if(btn){btn.textContent=dark?'☀':'☾';btn.title=dark?'Switch to light mode':'Enable dark mode';btn.setAttribute('aria-label',dark?'Switch to light mode':'Enable dark mode');btn.setAttribute('aria-pressed',dark?'true':'false')}}
function toggleTheme(){applyTheme(getTheme()==='dark'?'light':'dark')}
window.toggleTheme=toggleTheme;
async function api(url,opts){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opts});const j=await r.json();if(!r.ok)throw new Error(j.error||'Request failed');return j}
const FUND_FILTER_PARAMETERS={
  sip_amount:{label:'SIP Amount',key:'sip_amount'},
  sip_day:{label:'SIP Day',key:'sip_day'},
  day_change:{label:'Day Change',key:'day_change'},
  day_pct:{label:'Day %',key:'day_pct'},
  month_change:{label:'Month Change',key:'month_change'},
  month_pct:{label:'Month %',key:'month_pct'},
  value:{label:'Total Value',key:'value'},
  year_change:{label:'Year Returns',key:'year_change'},
  invested:{label:'Invested',key:'invested'},
  profit:{label:'Total Profit',key:'profit'},
  profit_pct:{label:'Profit %',key:'profit_pct'},
  xirr:{label:'XIRR %',key:'xirr'}
};
function classificationValues(key){return [...new Set((funds||[]).map(f=>String(f?.[key]||'').trim()).filter(v=>v && v!=='Other'))].sort((a,b)=>a.localeCompare(b));}
function refreshClassificationFilterOptions(){
 const el=document.getElementById('categoryFilter');
 if(!el)return;
 const current=fundFilters.category||'all';
 const values=classificationValues('scheme_category');
 el.innerHTML='<option value="all">All categories</option>'+values.map(v=>`<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');
 el.value=values.includes(current)?current:'all';
}
function sanitizeMetricValueInput(el){
 if(!el)return '';
 const raw=String(el.value??'');
 // Allow an in-progress leading minus and decimal point while keeping the eventual value numeric.
 let cleaned=raw.replace(/[^0-9.\-]/g,'');
 const negative=cleaned.startsWith('-');
 cleaned=(negative?'':'')+cleaned.replace(/-/g,'');
 const parts=cleaned.split('.');
 if(parts.length>2)cleaned=parts[0]+'.'+parts.slice(1).join('');
 if(negative && !cleaned.startsWith('-'))cleaned='-'+cleaned;
 el.value=cleaned;
 return cleaned;
}
function handleMetricValueInput(){
 sanitizeMetricValueInput(document.getElementById('metricValue'));
 onFundFilterChange();
}
function currentFundFilters(){
 const search=(document.getElementById('filterBox')?.value||'').trim().toLowerCase();
 const sip=document.getElementById('sipFilter')?.value||fundFilters.sip||'all';
 const returns=document.getElementById('returnsFilter')?.value||fundFilters.returns||'all';
 const plan=document.getElementById('planFilter')?.value||fundFilters.plan||'all';
 const option=document.getElementById('optionFilter')?.value||fundFilters.option||'all';
 const category=document.getElementById('categoryFilter')?.value||fundFilters.category||'all';
 const parameter=document.getElementById('metricFilter')?.value||fundFilters.parameter||'';
 const relation=document.getElementById('metricRelation')?.value||fundFilters.relation||'>';
 const value=(document.getElementById('metricValue')?.value??fundFilters.value??'').trim();
 fundFilters={search,sip,returns,plan,option,category,parameter,relation,value};
 return fundFilters;
}
function hasActiveFundFilters(){const f=currentFundFilters();return Boolean(f.search)||f.sip!=='all'||f.returns!=='all'||f.plan!=='all'||f.option!=='all'||f.category!=='all'||(f.parameter && f.value!=='')}
function compareMetric(actual, relation, target){
 if(!Number.isFinite(actual)||!Number.isFinite(target)) return false;
 const eps=1e-9;
 if(relation==='>') return actual>target;
 if(relation==='>=') return actual>=target;
 if(relation==='=') return Math.abs(actual-target)<=eps;
 if(relation==='<=' ) return actual<=target;
 if(relation==='<') return actual<target;
 return false;
}
function getFilteredFunds(){
 const f=currentFundFilters();
 const metric=FUND_FILTER_PARAMETERS[f.parameter];
 const metricTarget=f.value!==''?Number(f.value):NaN;
 const visible=(funds||[]).filter(row=>{
   const name=String(row.fund_name||'').toLowerCase();
   const scheme=String(row.scheme_name||'').toLowerCase();
   if(f.search && !name.includes(f.search) && !scheme.includes(f.search)) return false;
   if(f.sip==='active' && !(row.sip_enabled===true && row.sip_status!=='inactive')) return false;
   if(f.sip==='inactive' && row.sip_status!=='inactive') return false;
   if(f.sip==='executed' && row.sip_execution_status!=='executed') return false;
   if(f.sip==='not_executed' && row.sip_execution_status!=='not_executed') return false;
   if(f.sip==='none' && (row.sip_enabled===true || row.sip_status==='inactive')) return false;
   const profit=Number(row.profit);
   if(f.returns==='profit' && !(Number.isFinite(profit)&&profit>0)) return false;
   if(f.returns==='loss' && !(Number.isFinite(profit)&&profit<0)) return false;
   if(f.returns==='nodata' && Number.isFinite(profit)) return false;
   if(f.plan!=='all' && row.plan_type!==f.plan) return false;
   if(f.option!=='all' && row.option_type!==f.option) return false;
   if(f.category!=='all' && row.scheme_category!==f.category) return false;
   if(metric && f.value!==''){
     const actual=Number(row[metric.key]);
     if(!compareMetric(actual,f.relation,metricTarget)) return false;
   }
   return true;
 });
 visible.sort((a,b)=>compareFunds(a,b,sortKey)*(sortDir==='desc'?-1:1));
 return visible;
}
function getVisibleFundsForExport(){return getFilteredFunds();}
function aggregateRows(rows){
 const total={value:0,invested:0,profit:0,day_change:0,month_change:0,year_change:0};
 for(const r of rows){for(const k of Object.keys(total)){const v=Number(r?.[k]);if(Number.isFinite(v))total[k]+=v;}}
 total.profit_pct=total.invested?total.profit/total.invested*100:null;
 total.day_pct=(total.value-total.day_change)!==0?total.day_change/(total.value-total.day_change)*100:null;
 total.month_pct=(total.value-total.month_change)!==0?total.month_change/(total.value-total.month_change)*100:null;
 total.xirr=null;
 return total;
}
function updateFundFilterUi(){
 const panel=document.getElementById('filterPanel'); if(panel)panel.classList.toggle('hidden',!filtersOpen);
 const btn=document.getElementById('filterToggleBtn');if(btn){btn.textContent=filtersOpen?'Hide Filters':'Filters';btn.setAttribute('aria-expanded',filtersOpen?'true':'false');}
 refreshClassificationFilterOptions();
 const s=document.getElementById('sipFilter');if(s)s.value=fundFilters.sip||'all';
 const r=document.getElementById('returnsFilter');if(r)r.value=fundFilters.returns||'all';
 const pl=document.getElementById('planFilter');if(pl)pl.value=fundFilters.plan||'all';
 const op=document.getElementById('optionFilter');if(op)op.value=fundFilters.option||'all';
 const cat=document.getElementById('categoryFilter');if(cat)cat.value=fundFilters.category||'all';
 const p=document.getElementById('metricFilter');if(p)p.value=fundFilters.parameter||'';
 const rel=document.getElementById('metricRelation');if(rel)rel.value=fundFilters.relation||'>';
 const val=document.getElementById('metricValue');if(val)val.value=fundFilters.value??'';
}
function clearFundFilters(){fundFilters={search:'',sip:'all',returns:'all',plan:'all',option:'all',category:'all',parameter:'',relation:'>',value:''};const f=document.getElementById('filterBox');if(f)f.value='';const ids=[['sipFilter','all'],['returnsFilter','all'],['planFilter','all'],['optionFilter','all'],['categoryFilter','all'],['metricFilter',''],['metricRelation','>'],['metricValue','']];for(const [id,v] of ids){const el=document.getElementById(id);if(el)el.value=v;}render();}
function setPortfolioTab(tab){activePortfolioTab=tab==='graphs'?'graphs':'table';localStorage.setItem('mft_portfolio_tab',activePortfolioTab);document.getElementById('portfolioTableTab')?.classList.toggle('is-active',activePortfolioTab==='table');document.getElementById('portfolioGraphsTab')?.classList.toggle('is-active',activePortfolioTab==='graphs');document.getElementById('portfolioTablePanel')?.classList.toggle('hidden',activePortfolioTab!=='table');document.getElementById('portfolioGraphsPanel')?.classList.toggle('hidden',activePortfolioTab!=='graphs');ensurePersistentTableScrollbar();if(activePortfolioTab==='graphs')renderGraphs();}
window.setPortfolioTab=setPortfolioTab;
function graphLabel(name){const sp=splitFundName(name||'');return sp.sub?`${sp.main} — ${sp.sub}`:sp.main;}
let graphSelectedFunds=[];let graphPeriod=localStorage.getItem('mft_graph_period')||'1y';let graphJobPoll=null;
const GRAPH_PARAMETERS={total_value:'Total Value',total_invested:'Total Invested',day_change:'Day Change',profit_pct:'Profit %'};
function renderGraphFundPicker(){
 const host=document.getElementById('graphFundPicker');if(!host)return;
 const selected=new Set(graphSelectedFunds.map(Number));
 host.innerHTML=(funds||[]).map(f=>`<label class="graph-fund-option"><input type="checkbox" value="${f.id}" ${selected.has(Number(f.id))?'checked':''}><span>${escapeHtml(graphLabel(f.fund_name))}</span></label>`).join('');
 host.querySelectorAll('input').forEach(cb=>cb.addEventListener('change',()=>{graphSelectedFunds=[...host.querySelectorAll('input:checked')].map(x=>Number(x.value));updateGraphFundCount();}));
 updateGraphFundCount();
}
function updateGraphFundCount(){const el=document.getElementById('graphFundCount');if(el)el.textContent=`${graphSelectedFunds.length} fund${graphSelectedFunds.length===1?'':'s'} selected`;}
function selectedGraphParameters(){return [...document.querySelectorAll('#graphParameterPicker input:checked')].map(x=>x.value);}
function setGraphPeriod(period){graphPeriod=period;localStorage.setItem('mft_graph_period',period);document.querySelectorAll('#graphPeriodPicker button').forEach(b=>b.classList.toggle('is-active',b.dataset.period===period));}
function renderGraphs(){
 renderGraphFundPicker();
 setGraphPeriod(graphPeriod);
 if(!graphSelectedFunds.length && funds.length){graphSelectedFunds=funds.map(f=>Number(f.id));renderGraphFundPicker();}
 updateGraphFundCount();
 const grid=document.getElementById('historicalGraphGrid');if(grid && !grid.dataset.ready) grid.innerHTML='<div class="graph-empty">Choose the funds and parameters, then select Build Graph.</div>';
}
function graphColor(index){return ['#2aa5e8','#8b5cf6','#0b7a0b','#e1183b','#d97706','#0891b2','#be185d','#64748b'][index%8];}
function graphParamUnit(p){return p==='profit_pct'?'%':'INR';}
function graphParamLabel(p){return GRAPH_PARAMETERS[p]||p;}
function graphStatText(param,value){return param==='profit_pct'?pct(value):money(value);}
function renderGraphStats(stats,params){
 const list=(params||[]).map(param=>({param,s:stats?.[param]})).filter(x=>x.s);
 if(!list.length) return '';
 const panels=[];
 for(let i=0;i<list.length;i+=2) panels.push(list.slice(i,i+2));
 return `<div class="graph-stats-grid" aria-label="Historical statistics">${panels.map((panel)=>`<div class="graph-stats"><div class="graph-stat-head"><span>Parameter</span><span>Maximum</span><span>Median</span><span>Minimum</span></div>${panel.map(({param,s})=>`<div class="graph-stat-row"><span class="graph-stat-param">${escapeHtml(graphParamLabel(param))}</span><span>${graphStatText(param,s.max)}<small>${s.max_date?formatDate(s.max_date):''}</small></span><span>${graphStatText(param,s.median)}</span><span>${graphStatText(param,s.min)}<small>${s.min_date?formatDate(s.min_date):''}</small></span></div>`).join('')}</div>`).join('')}</div>`;
}
function renderHistoricalGraphs(dataset){
 const host=document.getElementById('historicalGraphGrid');if(!host)return;
 const byParam={};for(const s of dataset.series||[])(byParam[s.parameter]??=[]).push(s);
 const allParams=(dataset.parameters||[]).filter(p=>byParam[p]);
 const singleFund=Number(dataset.holding_count||0)===1;
 const groups=singleFund&&allParams.length>1?[allParams]:allParams.map(p=>[p]);
 if(!groups.length){host.innerHTML='<div class="graph-empty">No historical graph data is available for the selected funds.</div>';return;}
 host.innerHTML=groups.map((params,gi)=>`<section class="historical-graph-card"><div class="graph-card-head"><h3>${params.map(graphParamLabel).join(' · ')}</h3><span class="graph-unit">${params.length===1?graphParamUnit(params[0]):(params.includes('profit_pct')&&params.some(p=>p!=='profit_pct')?'INR / %':graphParamUnit(params[0]))}</span></div>${singleFund?renderGraphStats(dataset.statistics,params):''}<div class="graph-viewport historical-graph-viewport"><div id="graphCanvas${gi}" class="historical-svg-host"></div></div></section>`).join('');
 groups.forEach((params,gi)=>renderGraphGroup(`graphCanvas${gi}`,params,byParam));
}
function renderGraphGroup(hostId,params,byParam){
 const host=document.getElementById(hostId);if(!host)return;const series=[];params.forEach(p=>(byParam[p]||[]).forEach(s=>series.push({...s,parameter:p})));
 const allDates=[...new Set(series.flatMap(s=>(s.points||[]).map(p=>p.date)))].sort();
 if(!allDates.length){host.innerHTML='<div class="graph-empty">No data</div>';return;}
 const w=1100,h=380,left=68,right=params.includes('profit_pct')&&params.some(p=>p!=='profit_pct')?78:22,top=28,bottom=44,plotW=w-left-right,plotH=h-top-bottom;
 const moneyParams=params.filter(p=>p!=='profit_pct'),pctParams=params.filter(p=>p==='profit_pct');
 const axisParams=(moneyParams.length?moneyParams:pctParams);
 const axisVals=axisParams.flatMap(param=>series.filter(s=>s.parameter===param).flatMap(s=>(s.points||[]).map(p=>Number(p[param])).filter(Number.isFinite)));
 const rightVals=pctParams.flatMap(param=>series.filter(s=>s.parameter===param).flatMap(s=>(s.points||[]).map(p=>Number(p[param])).filter(Number.isFinite)));
 const minV=axisVals.length?Math.min(...axisVals):0,maxV=axisVals.length?Math.max(...axisVals):1;const span=(maxV-minV)||1;
 const minR=rightVals.length?Math.min(...rightVals):0,maxR=rightVals.length?Math.max(...rightVals):1;const spanR=(maxR-minR)||1;
 const xFor=i=>left+(i/(Math.max(1,allDates.length-1)))*plotW;
 const yFor=v=>top+(1-((v-minV)/span))*plotH;
 const yForRight=v=>top+(1-((v-minR)/spanR))*plotH;
 const dateIdx=new Map(allDates.map((d,i)=>[d,i]));
 const valuesByDate=new Map();
 for(const d of allDates){const row={};for(const ss of series){const pt=(ss.points||[]).find(p=>p.date===d);row[`${ss.holding_id}_${ss.parameter}`]=pt&&Number.isFinite(Number(pt[ss.parameter]))?Number(pt[ss.parameter]):null;}valuesByDate.set(d,row);}
 let svg=`<svg class="mft-history-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="Historical ${escapeHtml(params.map(graphParamLabel).join(', '))}" preserveAspectRatio="none">`;
 for(let i=0;i<5;i++){const y=top+i*plotH/4;const val=maxV-i*span/4;svg+=`<line x1="${left}" y1="${y}" x2="${w-right}" y2="${y}" class="graph-grid-line"/><text x="${left-8}" y="${y+4}" text-anchor="end" class="graph-axis-label">${moneyParams.length?money(val):pct(val)}</text>`;}
 if(pctParams.length&&moneyParams.length){for(let i=0;i<5;i++){const y=top+i*plotH/4;const val=maxR-i*spanR/4;svg+=`<text x="${w-right+8}" y="${y+4}" text-anchor="start" class="graph-axis-label graph-axis-label-right">${pct(val)}</text>`;}}
 [0,0.25,0.5,0.75,1].forEach(t=>{const idx=Math.round(t*(allDates.length-1));const x=xFor(idx);svg+=`<text x="${x}" y="${h-14}" text-anchor="middle" class="graph-axis-label">${formatDate(allDates[idx])}</text>`});
 series.forEach((s,si)=>{const color=graphColor(si);let path='';let first=true;(s.points||[]).forEach(p=>{const val=Number(p[s.parameter]);if(!Number.isFinite(val))return;const i=dateIdx.get(p.date);const x=xFor(i),y=s.parameter==='profit_pct'&&moneyParams.length?yForRight(val):yFor(val);path+=(first?`M ${x} ${y}`:` L ${x} ${y}`);first=false;});if(path)svg+=`<path d="${path}" fill="none" stroke="${color}" stroke-width="2.2" vector-effect="non-scaling-stroke"/>`;});
 svg+=`<line x1="${left}" y1="${top}" x2="${left}" y2="${h-bottom}" class="graph-crosshair" visibility="hidden"/>`;
 if(pctParams.length&&moneyParams.length)svg+=`<line x1="${w-right}" y1="${top}" x2="${w-right}" y2="${h-bottom}" class="graph-axis-line-right"/>`;
 series.forEach((s,si)=>{const color=graphColor(si);svg+=`<circle class="graph-crosshair-point" data-series-index="${si}" r="5.5" fill="var(--card)" stroke="${color}" stroke-width="2.4" visibility="hidden"/>`;});
 svg+=`</svg>`;
 const legend=series.map((s,i)=>`<span class="history-legend-item"><i style="background:${graphColor(i)}"></i>${escapeHtml(graphLabel(s.fund_name))} · ${graphParamLabel(s.parameter)}</span>`).join('');
 host.innerHTML=`<div class="graph-chart-shell">${svg}<div class="graph-hover-tooltip hidden"></div></div><div class="history-legend">${legend}</div>`;
 const chart=host.querySelector('.mft-history-svg'),shell=host.querySelector('.graph-chart-shell'),tooltip=host.querySelector('.graph-hover-tooltip'),crosshair=chart.querySelector('.graph-crosshair'),points=[...chart.querySelectorAll('.graph-crosshair-point')];
 if(!chart||!shell||!tooltip)return;
 const hide=()=>{tooltip.classList.add('hidden');crosshair.setAttribute('visibility','hidden');points.forEach(point=>point.setAttribute('visibility','hidden'));};
 const showAt=(event)=>{
   const rect=chart.getBoundingClientRect();if(rect.width<=0)return hide();
   const ratio=Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width));
   const idx=Math.max(0,Math.min(allDates.length-1,Math.round(ratio*(allDates.length-1))));
   const date=allDates[idx],x=xFor(idx),row=valuesByDate.get(date)||{};
   crosshair.setAttribute('x1',x);crosshair.setAttribute('x2',x);crosshair.setAttribute('visibility','visible');
   points.forEach((point,si)=>{const ss=series[si],val=row[`${ss.holding_id}_${ss.parameter}`];if(val==null||!Number.isFinite(Number(val))){point.setAttribute('visibility','hidden');return;}point.setAttribute('cx',x);point.setAttribute('cy',ss.parameter==='profit_pct'&&moneyParams.length?yForRight(Number(val)):yFor(Number(val)));point.setAttribute('visibility','visible');});
   const uniqueHoldingIds=[...new Set(series.map(ss=>ss.holding_id))];const lines=series.map((ss,i)=>{const val=row[`${ss.holding_id}_${ss.parameter}`];const label=(uniqueHoldingIds.length===1&&params.length>1)?graphParamLabel(ss.parameter):`${graphLabel(ss.fund_name)} · ${graphParamLabel(ss.parameter)}`;return `<div class="graph-tooltip-row"><span><i style="background:${graphColor(i)}"></i>${escapeHtml(label)}</span><strong>${val==null?'—':graphStatText(ss.parameter,val)}</strong></div>`;}).join('');
   tooltip.innerHTML=`<div class="graph-tooltip-date">${formatDate(date)}</div>${lines}`;
   tooltip.classList.remove('hidden');
   const tw=tooltip.offsetWidth||220;const shellW=shell.clientWidth||w;const crosshairXpx=(x/w)*shellW;const roomRight=shellW-crosshairXpx;const leftPos=roomRight>=tw+24?Math.min(shellW-tw-6,crosshairXpx+18):Math.max(6,crosshairXpx-tw-18);tooltip.style.left=`${leftPos}px`;
 };
 chart.addEventListener('mousemove',showAt);chart.addEventListener('mouseleave',hide);chart.addEventListener('touchstart',showAt,{passive:true});chart.addEventListener('touchmove',showAt,{passive:true});chart.addEventListener('touchend',hide,{passive:true});
}

async function buildGraphs(){
 const ids=graphSelectedFunds.length?graphSelectedFunds:(funds||[]).map(f=>Number(f.id));
 const params=selectedGraphParameters();if(!ids.length){exportToast('Select at least one fund');return;}if(!params.length){exportToast('Select at least one parameter');return;}
 const loading=document.getElementById('graphLoading');const status=document.getElementById('graphStatus');const warnings=document.getElementById('graphWarnings');const grid=document.getElementById('historicalGraphGrid');
 loading?.classList.remove('hidden');warnings?.classList.add('hidden');grid&&(grid.innerHTML='');if(status)status.textContent='Preparing historical graph data…';
 try{
  const started=await api('./api/graphs/start',{method:'POST',body:JSON.stringify({profile_id:activeProfileId==='all'?'all':Number(activeProfileId),holding_ids:ids,parameters:params,period:graphPeriod})});
  if(graphJobPoll)clearInterval(graphJobPoll);graphJobPoll=setInterval(async()=>{
   try{const job=await api('./api/graphs/status/'+started.job_id);if(status)status.textContent=job.message||'Preparing…';if(job.status==='completed'){clearInterval(graphJobPoll);graphJobPoll=null;loading?.classList.add('hidden');if(job.result?.warnings?.length){warnings.innerHTML=job.result.warnings.map(w=>`<div>${escapeHtml(w)}</div>`).join('');warnings.classList.remove('hidden');}renderHistoricalGraphs(job.result||{});grid.dataset.ready='1';if(status)status.textContent='Historical graph ready.';}else if(job.status==='failed'){clearInterval(graphJobPoll);graphJobPoll=null;loading?.classList.add('hidden');if(status)status.textContent='Graph preparation failed';grid.innerHTML=`<div class="error-box"><strong>Unable to build graph</strong><p>${escapeHtml(job.error||'Unknown error')}</p></div>`;}}catch(e){clearInterval(graphJobPoll);graphJobPoll=null;loading?.classList.add('hidden');if(status)status.textContent=e.message;}}
  ,350);
 }catch(e){loading?.classList.add('hidden');if(status)status.textContent=e.message;grid.innerHTML=`<div class="error-box"><strong>Unable to build graph</strong><p>${escapeHtml(e.message)}</p></div>`;}
}
function exportToast(message){
 const old=document.querySelector('.export-toast'); if(old)old.remove();
 const el=document.createElement('div');el.className='export-toast';el.textContent=message;document.body.appendChild(el);
 setTimeout(()=>el.remove(),2500);
}
function canvasText(ctx,text,x,y,maxWidth){
 const str=String(text??''); if(ctx.measureText(str).width<=maxWidth){ctx.fillText(str,x,y);return;}
 let out=''; for(const ch of str){if(ctx.measureText(out+ch+'…').width>maxWidth)break;out+=ch;} ctx.fillText(out+'…',x,y);
}
function drawExportCell(ctx,text,x,y,w,h,opts={}){
 ctx.fillStyle=opts.bg||'#ffffff';ctx.fillRect(x,y,w,h);
 ctx.strokeStyle='#aab0b7';ctx.lineWidth=1;ctx.strokeRect(x,y,w,h);
 ctx.fillStyle=opts.color||'#111827';ctx.font=opts.font||'13px Arial';ctx.textAlign=opts.align||'center';
 ctx.textBaseline='middle';canvasText(ctx,text,opts.align==='left'?x+7:x+w/2,y+h/2,w-(opts.align==='left'?14:8));
}
function exportPortfolioPng(){
 if(!portfolio){exportToast('Portfolio is not loaded yet.');return;}
 const rows=getVisibleFundsForExport(); const t=portfolio.total||{};
 const title=profiles.find(p=>String(p.id)===String(activeProfileId))?.name || (activeProfileId==='all'?'All Investors':'Mutual Fund Tracker');
 const cols=[
  ['Fund Name',250],['SIP Amount',105],['SIP Date',80],['Day',105],['Day %',80],['Month',105],['Month %',80],['Total Value',120],['Year',120],['Invested',120],['Profit',120],['Profit %',85],['XIRR %',85]
 ];
 const rowH=30, headerH=48, top=128, bottom=52, width=cols.reduce((a,c)=>a+c[1],0)+48;
 const height=top+headerH+rowH*(rows.length+1)+bottom;
 const canvas=document.createElement('canvas');canvas.width=width*2;canvas.height=height*2;canvas.style.width=width+'px';canvas.style.height=height+'px';
 const ctx=canvas.getContext('2d');ctx.scale(2,2);ctx.fillStyle='#ffffff';ctx.fillRect(0,0,width,height);
 // Header
 ctx.fillStyle='#101828';ctx.font='700 22px Arial';ctx.textAlign='left';ctx.textBaseline='alphabetic';ctx.fillText('Mutual Fund Tracker',24,32);
 ctx.font='700 15px Arial';ctx.fillStyle='#344054';ctx.fillText(title,24,58);
 ctx.font='12px Arial';ctx.fillStyle='#667085';
 const navDate=appSettings.nav_date||rows.reduce((min,r)=>r.nav_date&&(min==null||r.nav_date<min)?r.nav_date:min,null);
 const last=appSettings.last_nav_refresh?formatDateTime(appSettings.last_nav_refresh):'';
 ctx.fillText(`NAV date: ${formatDate(navDate)||'—'}    Last refreshed: ${last||'—'}`,24,80);
 ctx.font='700 13px Arial';ctx.fillStyle='#111827';
 ctx.fillText(`Value: ${money(t.value)}   Invested: ${money(t.invested)}   Profit: ${money(t.profit)} (${pct(t.profit_pct)})   XIRR: ${pct(t.xirr)}`,24,103);
 // Table header
 let x=24,y=top;
 cols.forEach(([label,w])=>{drawExportCell(ctx,label,x,y,w,headerH,{bg:'#f3f4f6',font:'700 12px Arial'});x+=w;});
 y+=headerH;
 const toneBg=v=>Number(v)>0?'#0b7a0b':Number(v)<0?'#e1183b':'#ffffff';
 const toneColor=v=>Number(v)!==0?'#ffffff':'#222222';
 const fmtDay=v=>money(v), fmtPct=v=>pct(v);
 rows.forEach(f=>{
   x=24;
   const vals=[f.fund_name||'',f.sip_enabled?money(f.sip_amount):'0',f.sip_enabled?(f.sip_day||'—'):'-',f.day_change, f.day_pct,f.month_change,f.month_pct,f.value,f.year_change,f.invested,f.profit,f.profit_pct,f.xirr];
   vals.forEach((v,i)=>{
     let text=v;
     if([3,5,7,8,9,10].includes(i))text=fmtDay(v);
     if([4,6,11,12].includes(i))text=fmtPct(v);
     const positive=[3,4,5,6,8,10,11,12].includes(i);
     const bg=positive?toneBg(v):'#ffffff'; const color=positive?toneColor(v):'#222222';
     drawExportCell(ctx,text,x,y,cols[i][1],rowH,{bg,color,align:i===0?'left':'center',font:i===0?'700 12px Arial':'12px Arial'});x+=cols[i][1];
   }); y+=rowH;
 });
 x=24;const totals=[`Totals`,'','',t.day_change,'',t.month_change,'',t.value,t.year_change,t.invested,t.profit,t.profit_pct,t.xirr];
 vals=totals; vals.forEach((v,i)=>{let text=v;if([3,5,7,8,9,10].includes(i))text=money(v);if([11,12].includes(i))text=pct(v);if(i===4||i===6)text='';const positive=[3,5,8,10,11,12].includes(i);drawExportCell(ctx,text,x,y,cols[i][1],rowH,{bg:positive?toneBg(v):'#f3f4f6',color:positive?toneColor(v):'#111827',align:i===0?'left':'center',font:'700 12px Arial'});x+=cols[i][1];});
 // footer
 ctx.fillStyle='#667085';ctx.font='11px Arial';ctx.textAlign='left';ctx.fillText(`Exported ${formatDateTime(new Date().toISOString())} · ${rows.length} funds`,24,height-20);
 const link=document.createElement('a');link.download=`mutual-fund-analysis-${(title||'portfolio').replace(/[^a-z0-9]+/gi,'-').replace(/^-|-$/g,'').toLowerCase()||'portfolio'}.png`;link.href=canvas.toDataURL('image/png');link.click();
 exportToast('PNG exported successfully.');
}
window.exportPortfolioPng=exportPortfolioPng;
async function load(){try{profiles=await api('./api/profiles');if(!profiles.length)throw new Error('No investor profiles configured');if(activeProfileId!=='all'&&!profiles.some(p=>String(p.id)===String(activeProfileId))){activeProfileId=String(profiles[0].id);localStorage.setItem('mft_active_profile',activeProfileId);}renderProfileSelector();const query=activeProfileId==='all'?'all':activeProfileId;portfolio=await api('./api/portfolio?profile_id='+encodeURIComponent(query));funds=portfolio.rows||[];appSettings=await api('./api/settings');render();renderInvestorCard();renderRefreshInfo();}catch(e){document.getElementById('fundRows').innerHTML=`<tr><td colspan="14"><div class="error">${escapeHtml(e.message)}</div></td></tr>`}}
function renderRefreshInfo(){
 const el=document.getElementById('navRefreshInfo'); if(!el)return;
 const dateEl=el.querySelector('.refresh-date'), timeEl=el.querySelector('.refresh-time'), intervalEl=el.querySelector('.refresh-interval');
 const last=appSettings.last_nav_refresh;
 const mins=Math.round((appSettings.refresh_interval_seconds||7200)/60);
 if(last){
   const d=new Date(last);
   if(dateEl) dateEl.textContent=`Last NAV refresh: ${formatDate(last.slice(0,10))}`;
   if(timeEl) timeEl.textContent=`Time ${d.toLocaleTimeString('en-GB', {hour:'numeric',minute:'2-digit',second:'2-digit'})}`;
 }else{
   if(dateEl) dateEl.textContent='Last NAV refresh: —';
   if(timeEl) timeEl.textContent='';
 }
 if(intervalEl) intervalEl.textContent=`Every ${mins} min`;
}
function renderRefreshProgress(state){const wrap=document.getElementById('navProgressWrap');const bar=document.getElementById('navProgressBar');const msg=document.getElementById('navProgressMsg');if(!wrap||!bar||!msg)return;const active=state&&state.status==='running';wrap.classList.toggle('hidden',!active);bar.style.width=`${Math.max(0,Math.min(100,Number(state?.progress||0)))}%`;msg.textContent=state?.message||'Updating NAVs…';}
async function pollRefresh(){if(refreshPollTimer)clearInterval(refreshPollTimer);const check=async()=>{try{const s=await api('./api/refresh/status');renderRefreshProgress(s);if(s.status==='completed'||s.status==='error'){clearInterval(refreshPollTimer);refreshPollTimer=null;const btn=document.getElementById('refreshBtn');if(btn)btn.disabled=false;await load();if(s.status==='error')alert('NAV refresh failed: '+(s.error||s.message||'Unknown error'));}}catch(e){}};await check();refreshPollTimer=setInterval(check,700);}

function renderProfileSelector(){const sel=document.getElementById('profileSelect');if(!sel)return;sel.innerHTML=`<option value="all">All investors</option>`+profiles.map(p=>`<option value="${p.id}">${escapeHtml(p.name)} · PAN ${escapeHtml(p.pan||'—')} · ${p.fund_count||0} funds</option>`).join('');sel.value=activeProfileId==='all'?'all':String(activeProfileId);sel.onchange=()=>{activeProfileId=sel.value;localStorage.setItem('mft_active_profile',activeProfileId);load()};}
function renderInvestorCard(){
 const card=document.getElementById('investorCard');
 if(!card)return;
 if(activeProfileId==='all'){
   card.innerHTML=`<div class="investor-table-card"><div class="eyebrow">INVESTOR PROFILES</div><table class="detail-table"><thead><tr><th>Name</th><th>PAN</th><th>Email IDs</th><th>Phone</th><th>Funds</th><th></th></tr></thead><tbody>${profiles.map(p=>`<tr><td><strong>${escapeHtml(p.name)}</strong></td><td>${escapeHtml(p.pan||'—')}</td><td>${escapeHtml((p.emails||[]).join(', ')||'—')}</td><td>${escapeHtml(p.phone||'—')}</td><td class="num">${p.fund_count||0}</td><td><button class="tiny-btn" onclick="editSpecificInvestor(${p.id})">Edit</button><button class="tiny-btn danger" onclick="deleteInvestor(${p.id})">Delete</button></td></tr>`).join('')}</tbody></table></div>`;
   return;
 }
 const p=profiles.find(x=>String(x.id)===String(activeProfileId));
 if(!p)return;
 const a=p.address||{};
 const addr=[a.line1,a.line2,a.city,a.state,a.postal_code,a.country].filter(Boolean).join(', ');
 const nextDate=portfolio?.next_expected_sip_date||null;
 const nextFunds=Array.isArray(portfolio?.next_expected_sip_funds)?portfolio.next_expected_sip_funds:[];
 const todayCount=Number(portfolio?.today_executed_sip_count||0);
 const todayTotal=Number(portfolio?.today_executed_sip_total_amount||0);
 const todayFunds=Array.isArray(portfolio?.today_executed_sip_funds)?portfolio.today_executed_sip_funds:[];
 const nextAmounts=portfolio?.next_expected_sip_amounts||{};
 const nextCount=Number(portfolio?.next_expected_sip_count||nextFunds.length||0);
 const nextTotal=Number(portfolio?.next_expected_sip_total_amount||0);
 const nextText=nextDate?`${formatDate(nextDate)}${nextCount?` · ${nextCount} fund${nextCount===1?'':'s'}`:''}${nextTotal?` · ${money(nextTotal)}`:''}`:'—';
 const nextFundText=nextFunds.length?nextFunds.map(n=>{const a=Number(nextAmounts[n]||0);return a?`${n} (${money(a)})`:n}).join(', '):'—';
 const executedText=todayCount?`${todayCount} fund${todayCount===1?'':'s'} · ${money(todayTotal)}`:'None';
 const delayedDetails=Array.isArray(portfolio?.delayed_nav_update_details)?portfolio.delayed_nav_update_details:[];
 const delayedCount=delayedDetails.length;
 const delayedTotal=0;
 const delayedFunds=delayedDetails.map(d=>d?.fund_name).filter(Boolean);
 const delayedText=delayedCount?'ON':'OFF';
 card.innerHTML=`<div class="investor-table-card"><div class="eyebrow">INVESTOR DETAILS</div><table class="detail-table"><tbody>
 <tr><th>Name</th><td><strong>${escapeHtml(p.name)}</strong></td><th>PAN</th><td>${escapeHtml(p.pan||'—')}</td></tr>
 <tr><th>Email IDs</th><td>${escapeHtml((p.emails||[]).join(', ')||'—')}</td><th>Phone / Mobile</th><td>${escapeHtml(p.phone||'—')}</td></tr>
 <tr><th>Address</th><td colspan="3">${escapeHtml(addr||'—')}</td></tr>
 <tr><th>Funds</th><td>${p.fund_count||0}</td><th></th><td></td></tr>
 <tr><th>Next Expected SIP</th><td><strong>${escapeHtml(nextText)}</strong><div class="muted">${escapeHtml(nextFundText)}</div></td><th>SIP Executed Today</th><td><strong>${escapeHtml(executedText)}</strong><div class="muted">${escapeHtml(todayFunds.join(', ')||'—')}</div></td></tr>
 <tr><th>Delayed NAV Update</th><td colspan="3"><strong>${escapeHtml(delayedText)}</strong><div class="muted">${delayedCount?`${escapeHtml(String(delayedCount))} fund${delayedCount===1?'':'s'} · ${escapeHtml(money(delayedTotal))} · ${escapeHtml(delayedFunds.join(', ')||'—')}`:'No delayed NAV updates'}</div></td></tr>
 </tbody></table></div>`;
}
function openProfileModal(profile){const p=profile||{name:'',emails:[],phone:'',pan:'',address:{}};const a=p.address||{};openModal(profile?'Edit Investor':'Add Investor',`<form class="form" onsubmit="submitProfile(event,${profile?profile.id:'null'})"><div class="grid2"><div class="field"><label>Full name</label><input id="p_name" required value="${escapeHtml(p.name||'')}"></div><div class="field"><label>Email IDs (comma separated)</label><input id="p_emails" value="${escapeHtml((p.emails||[]).join(', '))}"></div></div><div class="grid2"><div class="field"><label>Phone / Mobile</label><input id="p_phone" value="${escapeHtml(p.phone||'')}"></div><div class="field"><label>PAN</label><input id="p_pan" value="${escapeHtml(p.pan||'')}"></div></div><div class="grid2"><div class="field"><label>Address line 1</label><input id="p_a1" value="${escapeHtml(a.line1||'')}"></div><div class="field"><label>Address line 2</label><input id="p_a2" value="${escapeHtml(a.line2||'')}"></div></div><div class="grid2"><div class="field"><label>City</label><input id="p_city" value="${escapeHtml(a.city||'')}"></div><div class="field"><label>State</label><input id="p_state" value="${escapeHtml(a.state||'')}"></div></div><div class="grid2"><div class="field"><label>Postal code</label><input id="p_postal" value="${escapeHtml(a.postal_code||'')}"></div><div class="field"><label>Country</label><input id="p_country" value="${escapeHtml(a.country||'India')}"></div></div><div class="hint">PAN is the primary identity key for statement imports. Multiple email IDs can belong to the same investor.</div><div class="form-actions"><button type="button" class="secondary" onclick="closeModal()">Cancel</button><button type="submit" class="primary">Save Investor</button>${profile?`<button type="button" class="danger-btn" onclick="deleteInvestor(${profile.id})">Delete Investor</button>`:''}</div></form>`);}
async function refreshAndReload(){const btn=document.getElementById('refreshBtn');if(btn)btn.disabled=true;renderRefreshProgress({status:'running',progress:0,message:'Starting NAV refresh…'});await api('./api/refresh',{method:'POST'});await pollRefresh();}
async function submitProfile(e,id){e.preventDefault();try{const payload={name:document.getElementById('p_name').value,emails:document.getElementById('p_emails').value.split(',').map(x=>x.trim()).filter(Boolean),phone:document.getElementById('p_phone').value,pan:document.getElementById('p_pan').value,address:{line1:document.getElementById('p_a1').value,line2:document.getElementById('p_a2').value,city:document.getElementById('p_city').value,state:document.getElementById('p_state').value,postal_code:document.getElementById('p_postal').value,country:document.getElementById('p_country').value}};if(id){await api('./api/profiles/'+id,{method:'PUT',body:JSON.stringify(payload)});activeProfileId=String(id);}else{const r=await api('./api/profiles',{method:'POST',body:JSON.stringify(payload)});activeProfileId=String(r.id);}localStorage.setItem('mft_active_profile',activeProfileId);closeModal();await refreshAndReload()}catch(err){alert(err.message)}}
function addInvestor(){openProfileModal(null)}
function editInvestor(){if(activeProfileId==='all'){alert('Select an investor profile first.');return;}const p=profiles.find(x=>String(x.id)===String(activeProfileId));if(p)openProfileModal(p)}
function editSpecificInvestor(id){const p=profiles.find(x=>String(x.id)===String(id));if(p){activeProfileId=String(id);localStorage.setItem('mft_active_profile',activeProfileId);openProfileModal(p)}}
async function deleteInvestor(id){const p=profiles.find(x=>String(x.id)===String(id));if(!p)return;const fundCount=Number(p.fund_count||0);const message=fundCount>0?`Delete investor profile "${p.name}" and ALL ${fundCount} associated fund(s) and their transaction history? This cannot be undone.`:`Delete investor profile "${p.name}"?`;if(!confirm(message))return;try{await api('./api/profiles/'+id,{method:'DELETE'});const remaining=profiles.filter(x=>String(x.id)!==String(id));if(String(activeProfileId)===String(id)){activeProfileId=remaining.length?String(remaining[0].id):'all';}localStorage.setItem('mft_active_profile',activeProfileId);closeModal();await load()}catch(err){alert(err.message)}}
window.addInvestor=addInvestor;window.editInvestor=editInvestor;window.editSpecificInvestor=editSpecificInvestor;window.deleteInvestor=deleteInvestor;window.submitProfile=submitProfile;

function compareFunds(a,b,key){
 const map={fund_name:'fund_name',sip_amount:'sip_amount',sip_day:'sip_day',day_change:'day_change',day_pct:'day_pct',month_change:'month_change',month_pct:'month_pct',value:'value',year_change:'year_change',invested:'invested',profit:'profit',profit_pct:'profit_pct',xirr:'xirr'};
 const k=map[key]||'fund_name';
 if(k==='fund_name') return String(a[k]||'').localeCompare(String(b[k]||''),undefined,{numeric:true,sensitivity:'base'});
 const av=Number(a[k]); const bv=Number(b[k]);
 if(Number.isNaN(av)&&Number.isNaN(bv)) return 0;
 if(Number.isNaN(av)) return 1;
 if(Number.isNaN(bv)) return -1;
 return av-bv;
}
function setSort(key){sortKey=key;localStorage.setItem('mft_sort_key',sortKey);render();}
function toggleSortDir(){sortDir=sortDir==='asc'?'desc':'asc';localStorage.setItem('mft_sort_dir',sortDir);render();}
function toggleFundFilters(){filtersOpen=!filtersOpen;localStorage.setItem('mft_filters_open',filtersOpen?'1':'0');updateFundFilterUi();}
function onFundFilterChange(){currentFundFilters();render();}
window.toggleFundFilters=toggleFundFilters;
function updateSortControls(){const s=document.getElementById('sortSelect');const b=document.getElementById('sortDirBtn');if(s)s.value=sortKey;if(b){b.textContent=sortDir==='asc'?'↑':'↓';b.title=sortDir==='asc'?'Sort ascending':'Sort descending';b.setAttribute('aria-label',b.title);}}
window.setSort=setSort;window.toggleSortDir=toggleSortDir;


function ensurePersistentTableScrollbar(){
 const tableWrap=document.querySelector('.table-wrap');
 if(!tableWrap) return;
 const graphMode=document.getElementById('portfolioGraphsPanel') && !document.getElementById('portfolioGraphsPanel').classList.contains('hidden');
 let bar=document.getElementById('mftPersistentHScroll');
 if(!bar){
   bar=document.createElement('div');
   bar.id='mftPersistentHScroll';
   bar.className='persistent-hscroll';
   const inner=document.createElement('div'); inner.className='persistent-hscroll-inner';
   bar.appendChild(inner); document.body.appendChild(bar);
   let syncing=false;
   bar.addEventListener('scroll',()=>{if(syncing)return;syncing=true;tableWrap.scrollLeft=bar.scrollLeft;syncing=false;});
   tableWrap.addEventListener('scroll',()=>{if(syncing)return;syncing=true;bar.scrollLeft=tableWrap.scrollLeft;syncing=false;});
 }
 const inner=bar.querySelector('.persistent-hscroll-inner');
 inner.style.width=Math.max(tableWrap.scrollWidth,tableWrap.clientWidth)+'px';
 bar.scrollLeft=tableWrap.scrollLeft;
 bar.classList.toggle('hidden',graphMode || tableWrap.scrollWidth<=tableWrap.clientWidth+2);
}

let niftyCountdownTimer=null;
let niftyCountdownContext={open:false,enabled:false,nextAt:null};
function formatNiftyCountdown(ms){ if(!Number.isFinite(ms)||ms<=0)return 'Updating now…'; const total=Math.ceil(ms/1000); const mins=Math.floor(total/60); const secs=total%60; if(mins<=0)return `Next update in ${secs}s`; return `Next update in ${mins}m${secs?` ${secs}s`:''}`; }
function updateNiftyCountdown(){ const info=document.querySelector('#marketStatus .header-status-info-sub'); if(!info)return; if(!niftyCountdownContext.open||!niftyCountdownContext.enabled){info.textContent='Update stopped';return;} if(!niftyCountdownContext.nextAt){info.textContent='Updating now…';return;} info.textContent=formatNiftyCountdown(niftyCountdownContext.nextAt-Date.now()); }
function ensureNiftyCountdownTicker(){ if(niftyCountdownTimer)return; niftyCountdownTimer=setInterval(updateNiftyCountdown,1000); }
function renderMarketStatus(portfolio){
 const wrap=document.getElementById('marketStatus'); if(!wrap)return;
 const h=portfolio?.nse_holiday||{}; const m=portfolio?.market_status||{};
 const nseOpen=Boolean(h.nse_open); const marketOpen=Boolean(m.market_open);
 const tomorrowClosed=h.tomorrow_nse_open===false;
 const nseReason=!nseOpen?(h.description||h.type||'Holiday'):'Functional';
 const n=m.nifty||{};
 const niftyEnabled=Number(appSettings?.nifty_poll_interval_seconds||0)>0;
 const switches=wrap.querySelector('.header-status-switches');
 const info=wrap.querySelector('.header-status-info');
 if(!switches||!info)return;
 const tomorrowOpen=Boolean(h.tomorrow_nse_open);
 const items=[
   {selector:'.header-status-item:nth-child(1)',open:nseOpen},
   {selector:'.header-status-item:nth-child(2)',open:tomorrowOpen},
   {selector:'.header-status-item:nth-child(3)',open:marketOpen}
 ];
 items.forEach(item=>{
   const node=switches.querySelector(item.selector); const sw=node?.querySelector('.app-status-switch');
   if(sw){sw.classList.toggle('is-open',item.open);sw.classList.toggle('is-closed',!item.open);}
 });
 const liveQuote=marketOpen && n.value!=null;
 const niftyChange=n.change==null?null:Number(n.change);
 const niftyTone=niftyChange>0?'is-positive':niftyChange<0?'is-negative':'is-neutral';
 const firstLineHtml=liveQuote
   ? `<span class="header-status-nifty-label">Nifty50</span> <span class="header-status-nifty-value ${niftyTone}">${escapeHtml(Number(n.value).toLocaleString('en-IN',{maximumFractionDigits:2}))}${niftyChange==null?'':`(${niftyChange>=0?'+':''}${niftyChange.toFixed(2)})`}</span>`
   : `<span class="header-status-nifty-label">Market Closed</span>`;
 const secondLine=tomorrowClosed?'Tomorrow Closed':'Tomorrow Open';
 const secondLineClass=tomorrowClosed?'is-tomorrow-holiday':'is-tomorrow-open';
 const nextAtRaw=m.nifty_next_update_at?new Date(m.nifty_next_update_at).getTime():NaN;
 niftyCountdownContext={open:marketOpen,enabled:niftyEnabled,nextAt:Number.isFinite(nextAtRaw)?nextAtRaw:null};
 const countdownText=marketOpen&&niftyEnabled?formatNiftyCountdown((niftyCountdownContext.nextAt||NaN)-Date.now()):'Update stopped';
 info.innerHTML=`<div class="header-status-info-line header-status-nifty">${firstLineHtml}</div><div class="header-status-info-line header-status-nse-reason ${secondLineClass}">${escapeHtml(secondLine)}</div><div class="header-status-info-sub">${escapeHtml(countdownText)}</div>`;
 ensureNiftyCountdownTicker();
 info.querySelectorAll('.header-status-info-line').forEach(line=>{
   line.classList.remove('is-scrollable');
   line.style.removeProperty('--header-scroll-distance');
   if(line.scrollWidth>line.clientWidth+4){
     line.classList.add('is-scrollable');
     line.style.setProperty('--header-scroll-distance',`${-(line.scrollWidth-line.clientWidth)}px`);
   }
 });
}

function togglePlusMenu(force, event){
 if(event){event.preventDefault();event.stopPropagation();}
 const menu=document.getElementById('plusMenu'); const btn=document.getElementById('plusMenuBtn');
 if(!menu||!btn)return;
 const open=force===undefined?!menu.classList.contains('hidden'):Boolean(force);
 menu.classList.toggle('hidden',!open);
 btn.setAttribute('aria-expanded',open?'true':'false');
}
window.togglePlusMenu=togglePlusMenu;

function render(){
 const t=portfolio.total||{};
 const asof=document.getElementById('asof'); if(asof) asof.textContent='Last update: '+formatDateTime(portfolio.updated_at); renderMarketStatus(portfolio);
 document.getElementById('sValue').textContent=money(t.value);
 document.getElementById('sInvested').textContent=money(t.invested);
 document.getElementById('sProfit').textContent=money(t.profit);
 document.getElementById('sProfitPct').textContent=pct(t.profit_pct);
 document.getElementById('sXirr').textContent=pct(t.xirr);
 document.getElementById('sMonth').textContent=money(t.month_change);
 document.getElementById('sDay').textContent=money(t.day_change);
 document.getElementById('fundCount').textContent=funds.length+' fund'+(funds.length===1?'':'s');
 const visible=getFilteredFunds();
 updateSortControls();
 updateFundFilterUi();
 const countEl=document.getElementById('fundCount');if(countEl){countEl.textContent=visible.length===funds.length?`${funds.length} fund${funds.length===1?'':'s'}`:`${visible.length} of ${funds.length} funds`;}
 document.getElementById('fundRows').innerHTML=visible.map(f=>`<tr>
 <td class="fund-name-cell"><strong class="fund-name-main">${escapeHtml(splitFundName(f.fund_name).main)}</strong>${splitFundName(f.fund_name).sub?`<div class="fund-name-sub">${escapeHtml(splitFundName(f.fund_name).sub)}</div>`:''}<div class="muted">${escapeHtml(formatDate(f.nav_date||'NAV unavailable'))}</div></td>
 <td>${f.sip_status==='inactive'?`<span class="sip-badge inactive">Inactive SIP</span><div class="muted">${money(f.sip_amount)} / day ${f.sip_day||'—'}</div>`:(f.sip_enabled?money(f.sip_amount):'0')}</td><td>${f.sip_status==='inactive'?`<span class="sip-inactive-text">Stopped · ${escapeHtml(f.sip_day??'—')}</span>`:(f.sip_enabled?(f.sip_day||'—'):'-')}</td>
 ${cell(f.day_change,money)}${cell(f.day_pct,pct)}
 ${cell(f.month_change,money)}${cell(f.month_pct,pct)}
 <td class="${tone(f.profit)} num">${money(f.value)}</td>
 <td class="${tone(f.year_change)} num">${money(f.year_change)}</td>
 <td class="num">${money(f.invested)}</td>
 ${cell(f.profit,money)}${cell(f.profit_pct,pct)}
 ${cell(f.xirr,pct)}
 <td><div class="row-actions"><button class="blue-icon-btn fund-row-action-btn" type="button" onclick="editFund(${f.id})" title="Edit fund" aria-label="Edit fund"><svg class="fund-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17.5V21h3.5L18.81 9.69l-3.5-3.5-3.5-3.5L4 17.5Zm17-10.81c.39-.39.39-1.02 0-1.41l-2.28-2.28a.9959.9959 0 0 0-1.41 0l-1.79 1.79 3.5 3.5L21 6.69Z"/></svg></button></div></td>
 </tr>`).join('');
 const filtered=hasActiveFundFilters();
 const ft=filtered?aggregateRows(visible):t;
 const sipTotal=visible.reduce((sum,f)=>sum+(f.sip_enabled&&Number(f.sip_amount)>0?Number(f.sip_amount):0),0);
 const tr=`<tr>
 <th style="text-align:left">${filtered?'Filtered totals':'Totals'}</th><th>${money(sipTotal)}</th><th></th>
 ${cell(ft.day_change,money)}<td></td>${cell(ft.month_change,money)}<td></td>
 <th class="${tone(ft.profit)}">${money(ft.value)}</th><th class="${tone(ft.year_change)}">${money(ft.year_change)}</th><th>${money(ft.invested)}</th>
 ${cell(ft.profit,money)}${cell(ft.profit_pct,pct)}<th>${filtered?'—':pct(ft.xirr)}</th><th></th></tr>`;
 document.getElementById('totalsRow').innerHTML=tr;
 if(activePortfolioTab==='graphs')renderGraphs();
 ensurePersistentTableScrollbar();
}
function openModal(title,html,headAction=''){document.getElementById('modalTitle').textContent=title;const head=document.getElementById('modalHeadActions');if(head)head.innerHTML=headAction||'';document.getElementById('modalBody').innerHTML=html;document.getElementById('modal').classList.remove('hidden');document.body.classList.add('modal-open')}
async function importJsonFile(input){
  const file=input.files?.[0]; if(!file) return;
  window.__importData=null; window.__importMapping={}; window.__sipMapping={}; window.__sipReconciliationMapping={}; window.__importConfirmed=false; window.__importReviewed=false; window.__importDecisions={};
  showImportProgress('Reading JSON file',5,`Reading ${file.name}…`);
  try{
    const text=await file.text();
    showImportProgress('Analysing JSON',18,'Checking the file structure and fund data…');
    const data=JSON.parse(text);
    window.__importData=data;
    const preview=await api('./api/import/validate',{method:'POST',body:JSON.stringify(data)});
    renderImportPreview(preview);
  }catch(err){
    document.getElementById('importPreview').innerHTML=`<div class="error-box"><strong>Could not analyse file</strong><p>${escapeHtml(err.message)}</p></div>`;
    window.__importData=null;
  }
}
window.importJsonFile=importJsonFile;
function setImportInvestorMapping(sel){window.__importMapping=window.__importMapping||{};const ref=sel.dataset.investorRef;const v=sel.value;if(v===''){delete window.__importMapping[ref];}else if(v==='new')window.__importMapping[ref]={mode:'new'};else{const parts=v.split(':');window.__importMapping[ref]={mode:'existing',profile_id:Number(parts[1])};}if(importPreview)renderImportPreview(importPreview);}
window.setImportInvestorMapping=setImportInvestorMapping;
function setImportSipMapping(sel){window.__sipMapping=window.__sipMapping||{};const key=sel.dataset.sipStatusKey||sel.dataset.schemeCode;const v=sel.value;if(v===''){delete window.__sipMapping[key];}else{window.__sipMapping[key]=(v==='active');}if(importPreview)renderImportPreview(importPreview)}
window.setImportSipMapping=setImportSipMapping;
function setImportReviewDecision(sel){
 const key=sel.dataset.decisionKey; const mode=sel.value; window.__importDecisions=window.__importDecisions||{};
 const card=sel.closest('.import-review-card');
 if(mode==='accept' || !mode){ delete window.__importDecisions[key]; }
 else if(mode==='merge'){
   const target=card&&card.querySelector('.review-merge-target');
   window.__importDecisions[key]={mode:'merge',holding_id:target&&target.value?Number(target.value):null};
 } else window.__importDecisions[key]={mode};
 if(card){const box=card.querySelector('.review-merge-box');if(box)box.style.display=mode==='merge'?'block':'none';}
 updateImportReviewState();
}
window.setImportReviewDecision=setImportReviewDecision;
function setImportReviewMergeTarget(sel){
 const key=sel.dataset.decisionKey; window.__importDecisions=window.__importDecisions||{};
 const cur=window.__importDecisions[key]||{}; cur.mode='merge'; cur.holding_id=sel.value?Number(sel.value):null; window.__importDecisions[key]=cur;
 updateImportReviewState();
}
window.setImportReviewMergeTarget=setImportReviewMergeTarget;
function setImportSipReconciliation(sel){
 const key=sel.dataset.sipReconciliationKey; const v=sel.value; window.__sipReconciliationMapping=window.__sipReconciliationMapping||{};
 if(v===''){delete window.__sipReconciliationMapping[key];}else{window.__sipReconciliationMapping[key]=v;}
 if(importPreview)renderImportPreview(importPreview);
}
window.setImportSipReconciliation=setImportSipReconciliation;

function updateImportReviewState(){
 const cards=[...document.querySelectorAll('#importPreview .import-review-card')];
 if(!cards.length)return;
 let complete=true;
 for(const card of cards){
   const key=card.dataset.decisionKey; const d=window.__importDecisions?.[key];
   if((d?.mode==='merge')&&!d.holding_id) complete=false;
   if(card.querySelector('.review-action')?.value==='merge'&&!d?.holding_id) complete=false;
 }
 const btn=document.getElementById('confirmImportAfterReview');
 const hasActions=cards.length>0;
 if(btn){btn.disabled=!complete||!hasActions;btn.classList.toggle('disabled',btn.disabled);}
 const note=document.querySelector('.review-decision-status');
 if(note)note.textContent=complete?'All decisions are valid. You can confirm the import.':'Resolve the highlighted decisions before confirming.';
}
window.updateImportReviewState=updateImportReviewState;
function renderImportPreview(p){
 importPreview=p;
 const errors=p.errors||[], warnings=p.warnings||[], funds=p.funds||[], counts=p.counts||{}, invs=p.investors||[];
 const sipNeeds=funds.filter(f=>f.sip_confirmation_required);
 let html=`<div class="import-summary"><b>${counts.funds||0} funds</b> · <b>${counts.transactions||0} transactions</b>${p.template?' · <b>Template</b>':''}</div>`;
 if(p.template)html+=`<div class="info-box"><strong>Blank JSON template detected</strong><p>Fill the <code>funds</code> array from your statement and upload the completed JSON.</p></div>`;
 if(errors.length)html+=`<div class="error-box"><strong>Cannot import yet</strong><ul>${errors.slice(0,12).map(e=>`<li>${escapeHtml(e)}</li>`).join('')}</ul><p class="muted">Correct these problems in the JSON and select the file again.</p></div>`;
 if(warnings.length)html+=`<div class="warning-box"><strong>${p.template?'Information':'Warnings'}</strong><ul>${warnings.slice(0,12).map(e=>`<li>${escapeHtml(e)}</li>`).join('')}</ul></div>`;
 let identityNeedsDecision=false;
 if(invs.length){html+=`<div class="investor-import-section"><h4>Investor identity from statement</h4><p class="hint">PAN is used as the primary identity key. A PAN match with different profile details requires an explicit Merge/New choice.</p>`;html+=invs.map(inv=>{const status=inv.identity_status||'no_match';const saved=window.__importMapping?.[inv.ref];let def=saved;if(!def&&status==='pan_match_same'&&inv.match)def={mode:'existing',profile_id:inv.match.id};if(!def&&status==='no_match')def={mode:'new'};const candidates=inv.matches||[];const candidateOpts=candidates.map(pr=>`<option value="existing:${pr.id}" ${def?.mode==='existing'&&String(def.profile_id)===String(pr.id)?'selected':''}>Merge into: ${escapeHtml(pr.name)}${pr.pan?` (PAN ${escapeHtml(pr.pan)})`:''}</option>`).join('');const needsChoice=(status==='pan_match_different'||status==='fallback_match')&&!def;if(needsChoice)identityNeedsDecision=true;const details=[inv.name,inv.pan,(inv.emails||[]).join(', '),inv.phone].filter(Boolean).join(' · ');let statusHtml='';if(status==='pan_match_same')statusHtml=`<div class="identity-ok">✓ PAN and profile details match an existing investor.</div>`;else if(status==='pan_match_different')statusHtml=`<div class="identity-alert"><strong>⚠ PAN matches an existing investor, but details differ.</strong><div class="muted">${(candidates[0]?.different_fields||[]).map(escapeHtml).join(', ')||'Profile details'} differ.</div></div>`;else if(status==='fallback_match')statusHtml=`<div class="identity-alert"><strong>⚠ PAN missing; weak match found.</strong></div>`;else statusHtml=`<div class="identity-new">No existing investor with this PAN was found.</div>`;const opts=`<option value="" ${needsChoice?'selected':''} disabled>Select an action…</option><option value="new" ${def?.mode==='new'?'selected':''}>Create new investor profile</option>${candidateOpts}`;return `<div class="investor-map-row ${needsChoice?'needs-decision':''}"><div><strong>${escapeHtml(inv.name)}</strong><div class="muted">${escapeHtml(details)}</div>${statusHtml}</div><select data-investor-ref="${escapeHtml(inv.ref)}" onchange="setImportInvestorMapping(this)">${opts}</select></div>`}).join('');html+='</div>';}
 let sipNeedsDecision=false;
 if(sipNeeds.length){html+=`<div class="sip-confirm-section"><h4>SIP activity confirmation</h4><p class="hint">SIP status is handled per holding/folio so multiple folios of the same scheme can be active or inactive independently. Only SIPs whose last transaction is between 30 and 60 days before the statement date need confirmation.</p><table class="detail-table"><thead><tr><th>Fund</th><th>Folio</th><th>Last SIP transaction</th><th>Current JSON setting</th><th>Import as</th></tr></thead><tbody>${sipNeeds.map(f=>{const key=f._import_ref||f.scheme_code;const saved=window.__sipMapping?.[key];const selected=saved===undefined?'':(saved?'active':'inactive');sipNeedsDecision=saved===undefined||sipNeedsDecision;const folio=(f.folios||[]).join(', ')||'—';return `<tr><td>${escapeHtml(f.scheme_name)}</td><td>${escapeHtml(folio)}</td><td>${escapeHtml(formatDate(f.last_sip_transaction_date||'None'))}</td><td>${f.sip_enabled?`₹${num(f.sip_amount,2)} / day ${f.sip_day}`:'Disabled'}</td><td><select data-sip-status-key="${escapeHtml(key)}" data-scheme-code="${escapeHtml(f.scheme_code||'')}" onchange="setImportSipMapping(this)"><option value="" ${selected===''?'selected':''}>Select…</option><option value="active" ${selected==='active'?'selected':''}>Still active</option><option value="inactive" ${selected==='inactive'?'selected':''}>Stopped / inactive</option></select></td></tr>`}).join('')}</tbody></table></div>`;}
 const sipRecs=(funds.flatMap(f=>(f.import_sip_reconciliations||[])));
 const pendingRecs=sipRecs.filter(r=>!r.auto_execute);
 const sipStatusForKey=(key,code)=>{ const v=window.__sipMapping?.[key] ?? window.__sipMapping?.[code]; return v===false?'inactive':(v===true?'active':'unknown'); };
 if(sipRecs.length){html+=`<div class="sip-reconciliation-section"><h4>Recent SIP reconciliation</h4><p class="hint">A SIP after the statement coverage date was detected. If its applicable NAV is already the latest NAV for that fund and the SIP is still active, it will be executed automatically. Otherwise choose whether the SIP was executed or skipped. A fund marked Stopped / inactive above will not be executed.</p><table class="detail-table"><thead><tr><th>Fund</th><th>SIP date</th><th>NAV used</th><th>Latest NAV</th><th>Action</th></tr></thead><tbody>${sipRecs.map(r=>{const key=r.key;const saved=window.__sipReconciliationMapping?.[key]||'';const status=sipStatusForKey(r.import_ref||r.key,r.scheme_code);const effectiveAuto=Boolean(r.auto_execute)&&status!=='inactive';if(!effectiveAuto && status!=='inactive'){sipNeedsDecision=(saved===''||sipNeedsDecision);}let actionHtml;if(status==='inactive'){actionHtml=`<span class="sip-badge inactive">Will not execute · SIP inactive</span>`;}else if(effectiveAuto){actionHtml=`<span class="sip-badge active">Will execute automatically</span>`;}else{actionHtml=`<select data-sip-reconciliation-key="${escapeHtml(key)}" onchange="setImportSipReconciliation(this)"><option value="" ${saved===''?'selected':''}>Select…</option><option value="executed" ${saved==='executed'?'selected':''}>Executed</option><option value="skipped" ${saved==='skipped'?'selected':''}>Skipped</option></select>`;}return `<tr><td>${escapeHtml(r.scheme_name||'Fund')}</td><td>${escapeHtml(formatDate(r.sip_date))}</td><td>${escapeHtml(formatDate(r.nav_date))}<br>${money(r.nav)}</td><td>${escapeHtml(formatDate(r.latest_nav_date))}</td><td>${actionHtml}</td></tr>`}).join('')}</tbody></table></div>`;}
 if(funds.length){html+=`<div class="import-list"><table><thead><tr><th>Fund</th><th>Scheme</th><th>Units</th><th>Cost</th><th>SIP Status</th><th>Txns</th></tr></thead><tbody>${funds.map(f=>`<tr><td>${escapeHtml(f.scheme_name)}</td><td>${escapeHtml(f.scheme_code)}</td><td>${num(f.units,4)}</td><td>${money(f.invested)}</td><td>${f.sip_status==='inactive'?'<span class=\"sip-badge inactive\">Inactive SIP</span>':(f.sip_enabled?'<span class=\"sip-badge active\">Active SIP</span>':'—')}</td><td>${f.transactions?.length||0}</td></tr>`).join('')}</tbody></table></div>`}
 const ready=!errors.length&&!p.template&&!!counts.funds&&!identityNeedsDecision&&!sipNeedsDecision;
 if(identityNeedsDecision)html+=`<div class="error-box"><strong>Investor decision required</strong><p>Select Merge or Create new investor profile.</p></div>`;
 if(sipNeedsDecision)html+=`<div class="error-box"><strong>SIP activity confirmation required</strong><p>Select Still active or Stopped / inactive for every fund shown above.</p></div>`;
 if(ready)html+=`<div class="import-ready"><span class="ready-dot"></span><div><strong>Ready for change review</strong><div class="muted">Review exactly what will be added or changed before anything is saved.</div></div></div>`;
 if(ready)html+=`<div class="form-actions"><button type="button" class="secondary" onclick="closeModal()">Cancel</button><button type="button" class="primary" onclick="reviewImportChanges()">Review changes</button></div>`;else if(!p.template)html+=`<div class="import-not-ready"><strong>Import is waiting for validation.</strong><div class="muted">Complete the highlighted confirmations above.</div></div><div class="form-actions"><button type="button" class="secondary" onclick="closeModal()">Close</button></div>`;
 document.getElementById('importPreview').innerHTML=html;
}

function showImportProgress(stage, percent, message){
 const holder=document.getElementById('importPreview');
 if(!holder) return;
 holder.innerHTML=`<div class="progress-box"><div class="progress-head"><strong>${escapeHtml(stage)}</strong><span>${Math.round(percent)}%</span></div><div class="progress-track"><div class="progress-fill" style="width:${Math.max(0,Math.min(100,percent))}%"></div></div><div class="progress-message">${escapeHtml(message)}</div><div class="progress-sub">Please keep this window open while the portfolio is being analysed.</div></div>`;
}

async function reviewImportChanges(){
 if(window.__importReviewBusy||!window.__importData) return;
 window.__importReviewBusy=true;
 try{
   showImportProgress('Reviewing changes',15,'Comparing folios, scheme identity and transactions with the existing portfolio…');
   const payload={...window.__importData,profile_mapping:window.__importMapping||{},sip_status_mapping:window.__sipMapping||{},sip_reconciliation_mapping:window.__sipReconciliationMapping||{}};
   const res=await api('./api/import/changes',{method:'POST',body:JSON.stringify(payload)});
   if(res.errors?.length) throw new Error(res.errors[0]);
   const changes=res.changes||[];
   const reviews=changes.filter(c=>c.type==='review_required');
   const actionable=changes.filter(c=>c.type!=='no_change');
   const automaticCount=changes.filter(c=>c.automatic_action&&c.automatic_action!=='review_required'&&c.automatic_action!=='no_change').length;
   let html=`<div class="import-summary"><b>Proposed changes</b> · ${changes.length} fund/folio evaluations · <b>${automaticCount}</b> automatic actions · <b>${reviews.length}</b> decisions initially needed</div>`;
   html+=`<div class="info-box"><strong>Review your choices before saving</strong><p>Automatic decisions are shown below. You may accept them or change the action for any item before the import is committed. Nothing is saved until you choose <b>Confirm &amp; Import</b>.</p></div>`;

   const renderCandidates=(c,saved)=>{
     const candidates=c.merge_candidates||[];
     return candidates.map(h=>`<option value="${h.id}" ${saved.mode==='merge'&&String(saved.holding_id)===String(h.id)?'selected':''}>${escapeHtml(h.scheme_name||'Fund')} · Folio ${escapeHtml(h.folio||'—')}${h.isin?` · ISIN ${escapeHtml(h.isin)}`:''} · ${money(h.invested||0)} invested · ${num(h.units||0,4)} units${h.current_value!=null?` · ${money(h.current_value)} value`:''}</option>`).join('');
   };
   const labels={new:'Create as new holding',new_folio:'Create new folio',merge:'Add/merge transactions into existing holding',skip:'Skip this item',no_change:'No changes',review_required:'Needs review'};
   for(const c of actionable){
     const key=c.decision_key; const saved=window.__importDecisions?.[key]||{};
     const auto=c.automatic_action||'no_change';
     const isReview=c.type==='review_required';
     const candidates=c.merge_candidates||[];
     const safeModes=(c.override_options||[]);
     let mode=saved.mode||'';
     if(!mode && !isReview) mode='accept';
     const options=isReview
       ? [{v:'skip',t:'Skip this item'},{v:'merge',t:'Merge into existing holding'},{v:'new',t:'Create as new holding'}]
       : [{v:'accept',t:`Accept automatic decision — ${labels[auto]||auto}`}].concat(safeModes.filter(v=>v!=='new'||auto==='new'||auto==='new_folio').map(v=>({v,t:labels[v]||v})));
     if(isReview && !saved.mode) mode='';
     const placeholder=isReview?`<option value="" ${!mode?'selected':''}>Choose…</option>`:'';
     const opts=placeholder+options.map(o=>`<option value="${o.v}" ${mode===o.v?'selected':''}>${o.t}</option>`).join('');
     html+=`<div class="import-review-card" data-decision-key="${escapeHtml(key)}" data-auto-action="${escapeHtml(auto)}" style="border:1px solid var(--divider-color,#ccc);border-radius:10px;padding:12px;margin:10px 0;background:var(--card-background-color,rgba(0,0,0,.03));">
       <div><strong>${escapeHtml(c.scheme_name||'Fund')}</strong>${c.folios?.length?` · Folio ${escapeHtml(c.folios.join(', '))}`:''}</div>
       <div class="muted" style="margin:5px 0">${c.isin?`ISIN: ${escapeHtml(c.isin)} · `:''}${c.scheme_code?`Scheme code: ${escapeHtml(c.scheme_code)} · `:''}Transactions: ${c.transactions||0}</div>
       <div class="muted" style="margin:6px 0 10px">${escapeHtml(c.reason||`Automatic decision: ${labels[auto]||auto}`)}</div>
       <div class="grid2">
         <div class="field"><label>Decision</label><select class="review-action" data-decision-key="${escapeHtml(key)}" onchange="setImportReviewDecision(this)">${opts}</select></div>
         <div class="field review-merge-box" style="display:${mode==='merge'?'block':'none'}"><label>Merge into existing holding</label><select class="review-merge-target" data-decision-key="${escapeHtml(key)}" onchange="setImportReviewMergeTarget(this)"><option value="">Select existing holding…</option>${renderCandidates(c,saved)}</select></div>
       </div>
       <div class="muted review-auto-note" style="margin-top:8px">Automatic decision: <strong>${escapeHtml(c.automatic_label||labels[auto]||auto)}</strong></div>
       ${mode==='merge'&&candidates.length===0?`<div class="error-box" style="margin-top:8px">No safe existing holding candidates are available for a manual merge.</div>`:''}
     </div>`;
   }
   const completeReviews=reviews.every(c=>{const d=window.__importDecisions?.[c.decision_key];return d?.mode&&(d.mode!=='merge'||d.holding_id)});
   const incompleteOverrides=actionable.some(c=>{
     const d=window.__importDecisions?.[c.decision_key];
     return d?.mode==='merge'&&!d.holding_id;
   });
   const hasActions=actionable.some(c=>(c.automatic_action&&c.automatic_action!=='no_change') || window.__importDecisions?.[c.decision_key]?.mode==='merge');
   const canConfirm=completeReviews&&!incompleteOverrides;
   html+=`<div class="warning-box"><strong>Nothing has been saved yet.</strong><p>${canConfirm?'Your automatic decisions and any overrides are ready.':'Choose a valid decision for each review item before confirming.'}</p></div>`;
   html+=`<div class="form-actions"><button type="button" class="secondary" onclick="renderImportPreview(importPreview)">Back</button>${hasActions&&canConfirm?`<button type="button" id="confirmImportAfterReview" class="primary" onclick="confirmReviewedImport()">Confirm &amp; Import</button>`:`<button type="button" id="confirmImportAfterReview" class="primary" disabled>Confirm &amp; Import</button>`}</div>`;
   html+=`<div class="review-decision-status muted" style="margin-top:8px">${canConfirm?'All decisions are valid. You can confirm the import.':'Resolve the highlighted decisions before confirming.'}</div>`;
   document.getElementById('importPreview').innerHTML=html;
   window.__importReviewed=canConfirm;
   updateImportReviewState();
 }catch(err){
   document.getElementById('importPreview').innerHTML=`<div class="error-box"><strong>Could not review changes</strong><p>${escapeHtml(err.message)}</p></div><div class="form-actions"><button type="button" class="secondary" onclick="renderImportPreview(importPreview)">Back</button></div>`;
 }finally{window.__importReviewBusy=false;}
}
window.reviewImportChanges=reviewImportChanges;
function confirmReviewedImport(){window.__importConfirmed=true;confirmImport();}
window.confirmReviewedImport=confirmReviewedImport;

async function confirmImport(){
 if(window.__importBusy) return;
 if(!window.__importData) throw new Error('Choose a JSON file first');
 window.__importBusy=true;
 try{
   document.querySelectorAll('#modal button').forEach(b=>b.disabled=true);
   showImportProgress('Starting import',2,'Uploading the validated portfolio to the add-on…');
   if(!window.__importConfirmed) throw new Error('Please review and confirm the proposed import changes first.');
   const payloadToImport={...window.__importData,confirm_changes:true,profile_mapping:window.__importMapping||{},sip_status_mapping:window.__sipMapping||{},sip_reconciliation_mapping:window.__sipReconciliationMapping||{},fund_decisions:window.__importDecisions||{}}; const started=await api('./api/import/start',{method:'POST',body:JSON.stringify(payloadToImport)});
   const jobId=started.job_id;
   let done=false;
   while(!done){
     await new Promise(r=>setTimeout(r,500));
     const status=await api(`./api/import/status/${encodeURIComponent(jobId)}`);
     showImportProgress(status.stage||'Processing',status.progress||0,status.message||'Working…');
     if(status.status==='completed'){
       done=true;
       const res=status.result||{};
       const importedProfileIds=[...new Set((res.imported||[]).map(x=>Number(x.profile_id)).filter(Number.isFinite))];
       const resolvedProfileIds=[...new Set(Object.values(res.profiles||{}).map(x=>Number(x)).filter(Number.isFinite))];
       const targetProfileId=importedProfileIds.length===1?importedProfileIds[0]:(resolvedProfileIds.length===1?resolvedProfileIds[0]:null);
       if(targetProfileId!==null){
         activeProfileId=String(targetProfileId);
         localStorage.setItem('mft_active_profile',activeProfileId);
       }
       window.__importData=null;
       window.__importMapping={};
       closeModal();
       await load();
     }else if(status.status==='error'){
       done=true;
       document.getElementById('importPreview').innerHTML=`<div class="error-box"><strong>Import failed</strong><p>${escapeHtml(status.error||status.message||'Unknown error')}</p></div><div class="form-actions"><button type="button" class="secondary" onclick="renderImportAgain()">Back to preview</button><button type="button" class="secondary" onclick="closeModal()">Close</button></div>`;
     }
   }
 }catch(err){
   document.getElementById('importPreview').innerHTML=`<div class="error-box"><strong>Import could not be started</strong><p>${escapeHtml(err.message)}</p></div><div class="form-actions"><button type="button" class="secondary" onclick="renderImportAgain()">Back to preview</button></div>`;
 }finally{
   window.__importBusy=false;
   document.querySelectorAll('#modal button').forEach(b=>b.disabled=false);
 }
}
function renderImportAgain(){
 if(window.__importData){
   api('./api/import/validate',{method:'POST',body:JSON.stringify(window.__importData)}).then(renderImportPreview).catch(e=>{document.getElementById('importPreview').innerHTML=`<div class="error-box">${escapeHtml(e.message)}</div>`});
 }
}
function closeImportAndReload(){window.__importData=null;window.__importMapping={};closeModal();load()}

window.confirmImport=confirmImport;
async function copyImportPrompt(){
 try{
  const r=await fetch('./api/import/prompt',{cache:'no-store'});
  if(!r.ok) throw new Error('Could not load the AI import prompt');
  const t=await r.text();
  let copied=false;
  if(window.isSecureContext && navigator.clipboard && typeof navigator.clipboard.writeText==='function'){
    try{ await navigator.clipboard.writeText(t); copied=true; }catch(_){}
  }
  if(!copied && document.queryCommandSupported && document.queryCommandSupported('copy')){
    const ta=document.createElement('textarea');
    ta.value=t;
    ta.setAttribute('readonly','');
    ta.style.position='fixed';
    ta.style.left='-9999px';
    ta.style.top='0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    copied=document.execCommand('copy');
    document.body.removeChild(ta);
  }
  if(copied){
    alert('AI import prompt copied to clipboard.');
    return;
  }
  openModal('AI Conversion Prompt',`<div class=\"form\"><p class=\"hint\">Your browser/Ingress does not allow background clipboard access. Select the text below and press <strong>Ctrl+C</strong> (or <strong>Cmd+C</strong>).</p><textarea id=\"promptText\" style=\"width:100%;min-height:420px;box-sizing:border-box;font-family:monospace;font-size:13px;\"></textarea><div class=\"form-actions\"><button type=\"button\" class=\"primary\" onclick=\"document.getElementById('promptText').select();document.execCommand('copy');alert('Prompt copied.');\">Try Copy Again</button><button type=\"button\" class=\"secondary\" onclick=\"closeModal()\">Close</button></div></div>`);
  document.getElementById('promptText').value=t;
  document.getElementById('promptText').focus();
  document.getElementById('promptText').select();
 }catch(e){alert('Could not load prompt: '+e.message)}
}
window.copyImportPrompt=copyImportPrompt;
function downloadImportTemplate(){
 const data={
  format_version:2,
  template:true,
  source:{type:'MUTUAL_FUND_TRACKER_TEMPLATE',statement_from:'YYYY-MM-DD',statement_to:'YYYY-MM-DD'},
  investors:[],
  unresolved:[],
  funds:[]
 };
 const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='mutual-fund-tracker-import-template-v2.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}

window.downloadImportTemplate=downloadImportTemplate;
function importFunds(){
 window.__importData=null; window.__importMapping={}; window.__sipMapping={}; window.__sipReconciliationMapping={};
 window.__importBusy=false;
 openModal('Import Mutual Fund Portfolio',`<div class="form"><div class="import-help"><h3>Import from an AI-generated JSON</h3><p>Upload compact v2 JSON from your CAMS/KFintech statement. The add-on extracts investor details, lets you consolidate the statement into an existing investor profile or create a new one, then shows a preview and explicit Import button. During import you will see live progress while SIP history is reconstructed and the portfolio is saved.</p><div class="import-warning"><strong>Important:</strong> Use the latest consolidated report, preferably today’s report. Imports are limited to statements generated within the last 10 days. Using an older report may cause transaction mismatches or other transaction errors during import.</div><div class="form-actions"><button type="button" class="secondary" onclick="copyImportPrompt()">Copy AI conversion prompt</button><button type="button" class="secondary" onclick="downloadImportTemplate()">Download JSON template</button></div></div><div class="field"><label>JSON file</label><input id="importFileInput" type="file" accept="application/json,.json" onchange="importJsonFile(this)"></div><div id="importPreview"><div class="hint">Choose a JSON file to analyse it. After successful validation, an Import button will appear here.</div></div><div class="form-actions"><button type="button" class="secondary" onclick="closeModal()">Close</button></div></div>`);
}
window.importFunds=importFunds;
function closeModal(){if(window.__importBusy){return} const modal=document.getElementById('modal'); modal.classList.remove('fund-editor-modal'); modal.classList.add('hidden'); document.body.classList.remove('modal-open')}
window.closeModal=closeModal;
function addFund(){
 pendingLumpsumPreview=null;
 if(lumpsumJobTimer)clearInterval(lumpsumJobTimer);
 openModal('Add Mutual Fund',`<form class="form" onsubmit="submitAdd(event)">
 <div class="field"><label>Find mutual fund</label><div class="search-mode"><label class="search-mode-option"><input type="radio" name="schemeSearchMode" value="name" checked onchange="setSchemeSearchMode('name')"> Fund name</label><label class="search-mode-option"><input type="radio" name="schemeSearchMode" value="code" onchange="setSchemeSearchMode('code')"> Scheme code</label></div><input id="schemeSearch" autocomplete="off" placeholder="Search fund name…" oninput="searchSchemes(this.value)"><div id="schemeSearchHint" class="hint">Search by fund name or exact scheme code.</div><div id="schemeResults" class="scheme-results"></div></div>
 <input type="hidden" id="schemeCode"><input type="hidden" id="schemeName">
 <div class="field"><label>How was this existing holding built?</label><div class="choice-grid"><label class="choice-card"><input type="radio" name="acquisitionType" value="sip" checked onchange="setAcquisitionType('sip')"><div><strong>SIP</strong><span>Regular monthly investments</span></div></label><label class="choice-card"><input type="radio" name="acquisitionType" value="lumpsum" onchange="setAcquisitionType('lumpsum')"><div><strong>Lumpsum</strong><span>One or more one-time investments</span></div></label></div></div>
 <div id="lumpsumCurrentSummary" class="grid2"><div class="field"><label>Current units held</label><input id="units" type="number" min="0" step="0.0001" value="0" required></div><div class="field"><label>Total amount invested</label><input id="invested" type="number" min="0" step="0.01" value="0" required></div></div>
 <div id="sipSetup">
   <div class="grid2"><div class="field"><label>SIP amount</label><input id="sipAmount" type="number" min="0" step="0.01" value="0" oninput="updateSipInvested();invalidateSipPreview()"></div><div class="field"><label>SIP day (1–31)</label><input id="sipDay" type="number" min="1" max="31" value="10" oninput="invalidateSipPreview()"></div></div>
   <div class="grid2"><div class="field"><label>First SIP date</label><input id="firstSipDate" lang="en-GB" type="date" oninput="invalidateSipPreview()"></div><div class="field"><label>Number of SIPs already made</label><input id="sipCount" type="number" min="1" max="240" value="1" oninput="updateSipInvested();invalidateSipPreview()"></div></div>
   <div id="sipQuote" class="quote-box hidden"></div>
   <div id="sipProgress" class="progress-box operation-progress hidden"></div>
   <div class="hint">The add-on fetches the NAV history, reconstructs the historical SIP cash flows, calculates the units, and asks you to confirm before recording the transactions.</div>
 </div>
 <div id="lumpsumSetup" style="display:none">
   <div class="field"><label>Historical lumpsum investments</label><div id="lumpsumRows"></div><button type="button" class="tiny-btn" onclick="addLumpsumRow()">+ Add another lumpsum</button></div>
   <div id="lumpsumQuote" class="quote-box hidden"></div>
   <div id="lumpsumProgress" class="progress-box operation-progress hidden"></div>
   <div class="hint">For each investment, enter the NAV date and either the amount invested or the units purchased. The add-on will fetch the NAV, calculate the missing value, and ask you to confirm before recording the transactions.</div>
 </div>
 <div class="form-actions"><button type="button" class="secondary" onclick="closeModal()">Cancel</button><button type="submit" class="primary">Get NAV & Preview</button></div>
 </form>`);
 setAcquisitionType('sip');
 updateSipInvested();
}
window.addFund=addFund;
function setAcquisitionType(mode){
 if(mode!=='lumpsum')pendingLumpsumPreview=null;
 if(mode!=='sip')pendingSipPreview=null;
 const sip=document.getElementById('sipSetup'); const lump=document.getElementById('lumpsumSetup'); const summary=document.getElementById('lumpsumCurrentSummary');
 if(sip)sip.style.display=mode==='sip'?'block':'none';
 if(lump)lump.style.display=mode==='lumpsum'?'block':'none';
 if(summary)summary.style.display=mode==='lumpsum'?'none':'grid';
 if(mode==='lumpsum' && document.querySelectorAll('.lumpsum-row').length===0) addLumpsumRow();
 const form=document.querySelector('#sipSetup, #lumpsumSetup')?.closest('form'); const submit=form?.querySelector('button[type="submit"]'); if(submit)submit.textContent='Get NAV & Preview';
 if(mode==='sip') updateSipInvested();
}
window.setAcquisitionType=setAcquisitionType;
function updateSipInvested(){
 const a=Number(document.getElementById('sipAmount')?.value||0), n=Number(document.getElementById('sipCount')?.value||0);
 const el=document.getElementById('invested');
 if(el && document.querySelector('input[name="acquisitionType"]:checked')?.value==='sip') el.value=(a*n).toFixed(2);
}
window.updateSipInvested=updateSipInvested;
let pendingLumpsumPreview=null;
let pendingSipPreview=null;
let lumpsumJobTimer=null;
let sipPreviewJobTimer=null;
function invalidateLumpsumPreview(){
 pendingLumpsumPreview=null;
 const box=document.getElementById('lumpsumQuote'); if(box){box.classList.add('hidden');box.innerHTML='';}
 const form=document.querySelector('#lumpsumSetup')?.closest('form'); const submit=form?.querySelector('button[type="submit"]'); if(submit){submit.textContent='Get NAV & Preview';submit.disabled=false;}
}
function invalidateSipPreview(){
 pendingSipPreview=null;
 const box=document.getElementById('sipQuote'); if(box){box.classList.add('hidden');box.innerHTML='';}
 const progress=document.getElementById('sipProgress'); if(progress){progress.classList.add('hidden');progress.innerHTML='';}
 const form=document.querySelector('#sipSetup')?.closest('form'); const submit=form?.querySelector('button[type="submit"]'); if(submit){submit.textContent='Get NAV & Preview';submit.disabled=false;}
}
function addLumpsumRow(data={}){
 const wrap=document.getElementById('lumpsumRows'); if(!wrap)return;
 const row=document.createElement('div'); row.className='lumpsum-row';
 row.innerHTML=`<div class="ls-date-wrap"><label>NAV date</label><input class="ls-date" lang="en-GB" type="date" value="${data.date||''}" title="NAV date"></div><div class="ls-amount-wrap"><label>Amount</label><input class="ls-amount" type="number" min="0" step="0.01" value="${data.amount||''}" placeholder="Amount" title="Amount invested"></div><div class="ls-units-wrap"><label>Units</label><input class="ls-units" type="number" min="0" step="0.0001" value="${data.units||''}" placeholder="Units" title="Units purchased"></div><button type="button" class="tiny-btn" title="Remove" onclick="this.parentElement.remove();invalidateLumpsumPreview();syncLumpInputModes()">×</button>`;
 wrap.appendChild(row);
 const amount=row.querySelector('.ls-amount'); const units=row.querySelector('.ls-units');
 amount?.addEventListener('input',()=>{invalidateLumpsumPreview();if(amount.value){units.value='';units.disabled=true;}else{units.disabled=false;}syncLumpInvested();});
 units?.addEventListener('input',()=>{invalidateLumpsumPreview();if(units.value){amount.value='';amount.disabled=true;}else{amount.disabled=false;}syncLumpInvested();});
 row.querySelector('.ls-date')?.addEventListener('input',invalidateLumpsumPreview);
 syncLumpInputModes(); syncLumpInvested();
}
window.addLumpsumRow=addLumpsumRow;
function syncLumpInputModes(){
 document.querySelectorAll('.lumpsum-row').forEach(row=>{const amount=row.querySelector('.ls-amount'),units=row.querySelector('.ls-units');if(amount?.value&&!units?.value){units.disabled=true;amount.disabled=false;}else if(units?.value&&!amount?.value){amount.disabled=true;units.disabled=false;}else{amount.disabled=false;units.disabled=false;}});
}
window.syncLumpInputModes=syncLumpInputModes;
function syncLumpInvested(){
 const rows=[...document.querySelectorAll('.lumpsum-row')]; const total=rows.reduce((s,r)=>s+Number(r.querySelector('.ls-amount')?.value||0),0); const el=document.getElementById('invested');
 if(el && document.querySelector('input[name="acquisitionType"]:checked')?.value==='sip') el.value=total.toFixed(2);
}
window.syncLumpInvested=syncLumpInvested;
function lumpProgress(pct,message,sub=''){
 const box=document.getElementById('lumpsumProgress'); if(!box)return; box.classList.remove('hidden'); box.innerHTML=`<div class="progress-head"><span>Lumpsum setup</span><span>${Math.max(0,Math.min(100,Math.round(pct)))}%</span></div><div class="progress-track"><div class="progress-fill" style="width:${Math.max(0,Math.min(100,pct))}%"></div></div><div class="progress-message">${escapeHtml(message)}</div><div class="progress-sub">${escapeHtml(sub)}</div>`;
}
function sipProgress(pct,message,sub=''){
 const box=document.getElementById('sipProgress'); if(!box)return; box.classList.remove('hidden'); box.innerHTML=`<div class="progress-head"><span>SIP setup</span><span>${Math.max(0,Math.min(100,Math.round(pct)))}%</span></div><div class="progress-track"><div class="progress-fill" style="width:${Math.max(0,Math.min(100,pct))}%"></div></div><div class="progress-message">${escapeHtml(message)}</div><div class="progress-sub">${escapeHtml(sub)}</div>`;
}
async function previewLumpsum(){
 const rows=[...document.querySelectorAll('.lumpsum-row')];
 if(!rows.length)throw new Error('Add at least one lumpsum transaction');
 const out=[]; lumpProgress(8,'Preparing NAV lookup…','Checking each lumpsum entry.');
 for(let i=0;i<rows.length;i++){
   const r=rows[i], datev=r.querySelector('.ls-date')?.value?.trim()||'', amount=r.querySelector('.ls-amount')?.value?.trim()||'', units=r.querySelector('.ls-units')?.value?.trim()||'';
   if(!datev)throw new Error(`Lumpsum ${i+1}: NAV date is required`);
   if((amount&&units)||(!amount&&!units))throw new Error(`Lumpsum ${i+1}: enter either amount or units, not both`);
   lumpProgress(10+(i/rows.length)*75,`Fetching NAV ${i+1} of ${rows.length}…`,`NAV date ${datev}`);
   const q=await api('./api/funds/lumpsum-quote',{method:'POST',body:JSON.stringify({scheme_code:document.getElementById('schemeCode').value,nav_date:datev,amount:amount||null,units:units||null})});
   out.push(q);
 }
 pendingLumpsumPreview={scheme_code:document.getElementById('schemeCode').value,scheme_name:document.getElementById('schemeName').value,profile_id:activeProfileId,lumpsum_transactions:out};
 const box=document.getElementById('lumpsumQuote'); if(box){box.classList.remove('hidden');box.innerHTML=`<strong>Lumpsum preview</strong><div class="quote-grid">${out.map((q,i)=>`<span>Lumpsum ${i+1} NAV date</span><b>${escapeHtml(formatDate(q.nav_date))}</b><span>NAV</span><b>${num(q.nav,6)}</b><span>Amount</span><b>${money(q.amount)}</b><span>Units</span><b>${num(q.units,4)}</b>`).join('')}</div><div class="hint">Please confirm these calculated values before the fund and transactions are added.</div>`;}
 lumpProgress(100,'NAV lookup complete','Review the calculated values and then confirm.');
 const form=document.querySelector('#lumpsumSetup')?.closest('form'); const submit=form?.querySelector('button[type="submit"]'); if(submit)submit.textContent='Confirm & Add Fund';
}
async function startLumpsumAdd(){
 if(!pendingLumpsumPreview)throw new Error('Get NAV & Preview before confirming');
 const payload={profile_id:activeProfileId,scheme_code:pendingLumpsumPreview.scheme_code,scheme_name:pendingLumpsumPreview.scheme_name,acquisition_type:'lumpsum',lumpsum_transactions:pendingLumpsumPreview.lumpsum_transactions};
 lumpProgress(5,'Adding fund…','Saving the holding and historical transactions.');
 const job=await api('./api/funds/lumpsum/start',{method:'POST',body:JSON.stringify(payload)});
 if(!job.job_id)throw new Error('Lumpsum add job was not created');
 await pollLumpsumJob(job.job_id);
}
async function pollLumpsumJob(jobId){
 if(lumpsumJobTimer)clearInterval(lumpsumJobTimer);
 let loadedAfterAdd=false;
 const check=async()=>{
   try{const s=await api('./api/funds/lumpsum/status/'+encodeURIComponent(jobId)); const pct=Number(s.progress||0); lumpProgress(pct,s.message||'Adding fund…',s.sub||'');
     if(s.added&&!loadedAfterAdd){loadedAfterAdd=true;await load();}
     if(s.status==='completed'||s.status==='error'){clearInterval(lumpsumJobTimer);lumpsumJobTimer=null; if(s.status==='completed'){pendingLumpsumPreview=null;await load();setTimeout(closeModal,350);}else{throw new Error(s.error||'Unable to add lumpsum fund');}}
   }catch(err){clearInterval(lumpsumJobTimer);lumpsumJobTimer=null;const box=document.getElementById('lumpsumProgress');if(box){box.classList.remove('hidden');box.innerHTML=`<div class="progress-head"><span>Lumpsum setup</span><span>Error</span></div><div class="progress-message">${escapeHtml(err.message||'Update failed')}</div>`;};}
 };
 await check(); lumpsumJobTimer=setInterval(check,700);
}
async function previewSip(){
 invalidateSipPreview();
 const amount=document.getElementById('sipAmount')?.value||''; const day=document.getElementById('sipDay')?.value||''; const first=document.getElementById('firstSipDate')?.value||''; const count=document.getElementById('sipCount')?.value||'';
 if(Number(amount)<=0)throw new Error('SIP amount must be greater than zero');
 if(!/^([1-9]|[12][0-9]|3[01])$/.test(String(day)))throw new Error('SIP day must be between 1 and 31');
 if(!first)throw new Error('First SIP date is required');
 if(Number(count)<1||Number(count)>240)throw new Error('Number of SIPs already made must be between 1 and 240');
 sipProgress(5,'Starting NAV lookup…','Fetching the historical NAV series for the SIP dates.');
 const job=await api('./api/funds/sip/preview/start',{method:'POST',body:JSON.stringify({profile_id:activeProfileId,scheme_code:document.getElementById('schemeCode').value,scheme_name:document.getElementById('schemeName').value,units:document.getElementById('units')?.value||0,invested:document.getElementById('invested')?.value||0,sip_amount:amount,sip_day:day,first_sip_date:first,sip_count:count})});
 await pollSipPreviewJob(job.job_id);
}
async function pollSipPreviewJob(jobId){
 if(sipPreviewJobTimer)clearInterval(sipPreviewJobTimer);
 const check=async()=>{
   try{const s=await api('./api/funds/sip/preview/status/'+encodeURIComponent(jobId));sipProgress(Number(s.progress||0),s.message||'Preparing SIP preview…',s.sub||'');
     if(s.status==='completed'){clearInterval(sipPreviewJobTimer);sipPreviewJobTimer=null;pendingSipPreview=s.result;const q=document.getElementById('sipQuote');if(q&&s.result){q.classList.remove('hidden');const r=s.result;const adjust=Number(r.adjustment_units||0);q.innerHTML=`<strong>SIP preview</strong><div class="quote-grid"><span>SIP count</span><b>${r.sip_count}</b><span>SIP amount</span><b>${money(r.sip_amount)}</b><span>Historical invested</span><b>${money(r.total_invested)}</b><span>Reconstructed units</span><b>${num(r.reconstructed_units,4)}</b><span>Current units</span><b>${num(r.current_units,4)}</b><span>Opening unit adjustment</span><b>${num(adjust,4)}</b><span>First transaction</span><b>${escapeHtml(formatDate(r.first_transaction_date||'—'))}</b><span>Last transaction</span><b>${escapeHtml(formatDate(r.last_transaction_date||'—'))}</b></div><div class="hint">Please confirm these reconstructed SIP values before the fund and transactions are added.</div>`;}sipProgress(100,'NAV lookup complete','Review the reconstructed SIP values and then confirm.');const form=document.querySelector('#sipSetup')?.closest('form');const submit=form?.querySelector('button[type="submit"]');if(submit)submit.textContent='Confirm & Add Fund';}
     if(s.status==='error'){clearInterval(sipPreviewJobTimer);sipPreviewJobTimer=null;throw new Error(s.error||s.message||'Unable to prepare SIP preview');}
   }catch(err){clearInterval(sipPreviewJobTimer);sipPreviewJobTimer=null;const box=document.getElementById('sipProgress');if(box){box.classList.remove('hidden');box.innerHTML=`<div class="progress-head"><span>SIP setup</span><span>Error</span></div><div class="progress-message">${escapeHtml(err.message||'Preview failed')}</div>`;};}
 };
 await check(); sipPreviewJobTimer=setInterval(check,700);
}
async function startSipAdd(){
 if(!pendingSipPreview?.preview_id)throw new Error('Get NAV & Preview before confirming');
 sipProgress(5,'Adding fund…','Saving the holding and reconstructed SIP transactions.');
 const job=await api('./api/funds/sip/start',{method:'POST',body:JSON.stringify({preview_id:pendingSipPreview.preview_id})});
 if(!job.job_id)throw new Error('SIP add job was not created');
 await pollSipAddJob(job.job_id);
}
async function pollSipAddJob(jobId){
 if(lumpsumJobTimer)clearInterval(lumpsumJobTimer);
 let loadedAfterAdd=false;
 const check=async()=>{
   try{const s=await api('./api/funds/sip/status/'+encodeURIComponent(jobId));sipProgress(Number(s.progress||0),s.message||'Adding fund…',s.sub||'');
     if(s.added&&!loadedAfterAdd){loadedAfterAdd=true;await load();}
     if(s.status==='completed'||s.status==='error'){clearInterval(lumpsumJobTimer);lumpsumJobTimer=null;if(s.status==='completed'){pendingSipPreview=null;await load();setTimeout(closeModal,350);}else{throw new Error(s.error||'Unable to add SIP fund');}}
   }catch(err){clearInterval(lumpsumJobTimer);lumpsumJobTimer=null;const box=document.getElementById('sipProgress');if(box){box.classList.remove('hidden');box.innerHTML=`<div class="progress-head"><span>SIP setup</span><span>Error</span></div><div class="progress-message">${escapeHtml(err.message||'Update failed')}</div>`;};}
 };
 await check(); lumpsumJobTimer=setInterval(check,700);
}
async function submitAdd(e){e.preventDefault();const btn=e.target.querySelector('button[type="submit"]');try{if(activeProfileId==='all')throw new Error('Select a specific investor profile before adding a fund');const name=document.getElementById('schemeName').value;if(!name)throw new Error('Select a mutual-fund scheme first');const acquisition=document.querySelector('input[name="acquisitionType"]:checked').value;if(acquisition==='lumpsum'){if(pendingLumpsumPreview){if(btn)btn.disabled=true;await startLumpsumAdd();}else{await previewLumpsum();}return;}if(pendingSipPreview){if(btn)btn.disabled=true;await startSipAdd();}else{await previewSip();}}catch(err){if(btn)btn.disabled=false;alert(err.message)}}
window.submitAdd=submitAdd;

let searchTimer;
let schemeSearchMode='name';
function setSchemeSearchMode(mode){
  schemeSearchMode=mode;
  const input=document.getElementById('schemeSearch');
  const hint=document.getElementById('schemeSearchHint');
  const out=document.getElementById('schemeResults');
  if(mode==='code'){input.placeholder='Enter exact scheme code, e.g. 122639';hint.textContent='Exact lookup against the scheme code.';}
  else{input.placeholder='Search fund name…';hint.textContent='Search by fund name. If it is missing, switch to Scheme code.';}
  out.innerHTML='';
  if(input.value.trim().length>=2) searchSchemes(input.value);
}
window.setSchemeSearchMode=setSchemeSearchMode;
async function searchSchemes(q){clearTimeout(searchTimer);const out=document.getElementById('schemeResults');if(q.trim().length<2){out.innerHTML='';return};searchTimer=setTimeout(async()=>{try{const arr=await api('./api/search?mode='+encodeURIComponent(schemeSearchMode)+'&q='+encodeURIComponent(q));out.innerHTML=arr.map(s=>`<div class="scheme-result" onclick="selectScheme('${String(s.schemeCode).replace(/'/g,"\'")}','${String(s.schemeName).replace(/'/g,"\'")}')"><div class="scheme-name">${escapeHtml(s.schemeName)}</div><div class="scheme-code">Scheme code ${escapeHtml(s.schemeCode)}</div></div>`).join('')||'<div class="scheme-result">No results</div>'}catch(e){out.innerHTML=`<div class="scheme-result">${escapeHtml(e.message)}</div>`}},350)}
function selectScheme(code,name){pendingLumpsumPreview=null;pendingSipPreview=null;document.getElementById('schemeCode').value=code;document.getElementById('schemeName').value=name;document.getElementById('schemeSearch').value=name;document.getElementById('schemeResults').innerHTML=`<div class="scheme-result"><div class="scheme-name">${escapeHtml(name)}</div><div class="scheme-code">Selected scheme ${escapeHtml(code)}</div></div>`}
window.selectScheme=selectScheme;
window.submitAdd=submitAdd;
function fundEditHtml(f,id){return `<form class="form fund-editor-form" onsubmit="submitEdit(event,${id})"><div class="grid2"><div class="field"><label>Units held</label><input id="e_units" type="number" min="0" step="0.0001" value="${f.units??0}" required></div><div class="field"><label>Total invested</label><input id="e_invested" type="number" min="0" step="0.01" value="${f.invested??0}" required></div></div><div class="field"><label>Initial holding date</label><input id="e_initial" lang="en-GB" type="date" value="${f.initial_date||''}"></div><div class="checkbox"><input id="e_sip" type="checkbox" ${f.sip_enabled?'checked':''} onchange="toggleEditSip()"><label for="e_sip">Enable monthly SIP</label></div><div id="e_sip_fields" style="display:${f.sip_enabled?'block':'none'}"><div class="grid2"><div class="field"><label>SIP amount</label><input id="e_sip_amount" type="number" min="0" step="0.01" value="${f.sip_amount??0}"></div><div class="field"><label>SIP day</label><input id="e_sip_day" type="number" min="1" max="31" value="${f.sip_day??10}"></div></div><div class="field"><label>First date the new SIP amount/date applies</label><input id="e_sip_effective" lang="en-GB" type="date"><div class="hint">Use the actual first SIP date for the new instruction. If the change only takes effect next month, choose that month's SIP date. If historical SIPs exist on/after this date, they will be rebuilt using the new amount/day and historical NAVs, and XIRR/current units will be recalculated.</div></div></div><div class="form-actions"><button type="submit" class="primary">Save Changes</button></div></form>`}
function fundTransactionDetailsHtml(f,id){return `<div class="fund-transaction-tab"><div class="grid2"><div><b>Units</b><div class="big">${num(f.units,4)}</div></div><div><b>Current NAV</b><div class="big">${f.nav?num(f.nav,4):'—'}</div></div></div><div class="grid2"><div><b>Invested</b><div class="big">${money(f.invested)}</div></div><div><b>Last NAV date</b><div>${formatDate(f.nav_date||'—')}</div></div></div><div class="hint">Historical transactions are used for XIRR. Manual transactions are recorded as Purchase or Sell. Enter the NAV date and either amount or units; the add-on will obtain the NAV and calculate the other value before you confirm.</div><div class="field"><label>Add transaction</label><form class="form transaction-add-form" style="padding:0" onsubmit="previewTxn(event,${id})"><div class="grid2"><div class="field"><label>Type</label><select id="tx_type"><option value="buy">Purchase</option><option value="sell">Sell</option></select></div><div class="field"><label>NAV date</label><input id="tx_nav_date" lang="en-GB" type="date" required></div></div><div class="tx-input-choice"><div class="field"><label>Amount</label><input id="tx_amount" type="number" min="0" step="0.01"></div><div class="tx-or">OR</div><div class="field"><label>Units</label><input id="tx_units" type="number" min="0" step="0.0001"></div></div><div class="field"><label>Note (optional)</label><input id="tx_note"></div><div id="tx_quote" class="quote-box hidden"></div><div class="form-actions"><button class="primary" id="tx_preview_btn" type="submit">Get NAV & Preview</button><button class="secondary hidden" id="tx_confirm_btn" type="button" onclick="confirmTxn(${id})">Confirm & Record</button></div></form></div></div>`}
function fundTransactionsTableHtml(f,id,tx){const typeLabel=t=>({initial:'Opening',sip:'SIP',lumpsum:'One-time Purchase',buy:'Purchase',sell:'Sell',adjustment:'Adjustment',switch_in:'Switch In',switch_out:'Switch Out',fee:'Fee'}[t]||t);return `<div class="fund-transactions-table-panel"><div class="hint transactions-tab-hint">Transaction history for this fund</div><div class="txn-list"><table><colgroup><col class="txn-date-col"><col class="txn-type-col"><col class="txn-cash-col"><col class="txn-units-col"><col class="txn-nav-col"><col class="txn-note-col"><col class="txn-action-col"></colgroup><thead><tr><th>NAV Date</th><th>Type</th><th>Cash flow</th><th>Units</th><th>NAV</th><th>Note</th><th>Action</th></tr></thead><tbody>${tx.map(t=>`<tr><td>${formatDate(t.txn_date)}</td><td>${typeLabel(t.txn_type)}</td><td class="${Number(t.cashflow)<0?'value-neg':Number(t.cashflow)>0?'value-pos':''}">${money(t.cashflow)}</td><td>${num(Math.abs(t.units),4)}</td><td>${t.nav?num(t.nav,4):'—'}</td><td>${escapeHtml(t.note||'')}</td><td><button type="button" class="tiny-btn danger" onclick="deleteTransaction(${id},${Number(t.id)},this)">Delete</button></td></tr>`).join('')||'<tr><td colspan="7">No transactions</td></tr>'}</tbody></table></div></div>`}
async function openFundEditor(id,tab='edit'){const f=funds.find(x=>x.id===id);if(!f)return;const tx=await api('./api/funds/'+id+'/transactions');pendingTxnQuote=null;const headAction=`<button type="button" class="blue-action-btn fund-delete-header-btn" onclick="deleteFund(${id})" title="Delete fund">Delete Fund</button><button type="button" class="blue-action-btn fund-close-header-btn" onclick="closeModal()" title="Close fund editor">Close</button>`;openModal(f.fund_name,`<div class="fund-editor"><div class="fund-tabs" role="tablist"><button type="button" class="fund-tab-btn ${tab==='edit'?'is-active':''}" onclick="switchFundTab(${id},'edit')">Edit SIP</button><button type="button" class="fund-tab-btn ${tab==='transact'?'is-active':''}" onclick="switchFundTab(${id},'transact')">Transact</button><button type="button" class="fund-tab-btn ${tab==='transactions'?'is-active':''}" onclick="switchFundTab(${id},'transactions')">Transactions</button></div><section id="fundTabEdit" class="fund-tab-panel ${tab==='edit'?'':'hidden'}" role="tabpanel">${fundEditHtml(f,id)}</section><section id="fundTabTransact" class="fund-tab-panel ${tab==='transact'?'':'hidden'}" role="tabpanel">${fundTransactionDetailsHtml(f,id)}</section><section id="fundTabTransactions" class="fund-tab-panel ${tab==='transactions'?'':'hidden'}" role="tabpanel">${fundTransactionsTableHtml(f,id,tx)}</section></div>`,headAction);const modal=document.getElementById('modal');modal?.classList.add('fund-editor-modal');const body=document.getElementById('modalBody');if(body)body.classList.toggle('fund-transactions-active',tab==='transactions');const txDate=document.getElementById('tx_nav_date');if(txDate)txDate.value=new Date().toISOString().slice(0,10);if(tab==='transact')setupTxnInputMode();}
window.editFund=(id)=>openFundEditor(id,'edit');
window.viewFund=(id)=>openFundEditor(id,'transactions');
function switchFundTab(id,tab){const edit=document.getElementById('fundTabEdit');const transact=document.getElementById('fundTabTransact');const transactions=document.getElementById('fundTabTransactions');const body=document.getElementById('modalBody');document.querySelectorAll('.fund-tab-btn').forEach(b=>b.classList.remove('is-active'));const labels={edit:'Edit SIP',transact:'Transact',transactions:'Transactions'};const target=[...document.querySelectorAll('.fund-tab-btn')].find(b=>b.textContent.trim()===labels[tab]);if(target)target.classList.add('is-active');if(edit)edit.classList.toggle('hidden',tab!=='edit');if(transact)transact.classList.toggle('hidden',tab!=='transact');if(transactions)transactions.classList.toggle('hidden',tab!=='transactions');if(body)body.classList.toggle('fund-transactions-active',tab==='transactions');if(tab==='transact')setupTxnInputMode();}
function setupTxnInputMode(){const amountInput=document.getElementById('tx_amount');const unitsInput=document.getElementById('tx_units');if(!amountInput||!unitsInput)return;const sync=()=>{unitsInput.disabled=!!amountInput.value;amountInput.disabled=!!unitsInput.value;if(!amountInput.value&&!unitsInput.value){amountInput.disabled=false;unitsInput.disabled=false;}};if(amountInput.dataset.mftBound!=='1'){amountInput.addEventListener('input',sync);amountInput.dataset.mftBound='1';}if(unitsInput.dataset.mftBound!=='1'){unitsInput.addEventListener('input',sync);unitsInput.dataset.mftBound='1';}sync();}
function toggleEditSip(){const sip=document.getElementById('e_sip');const fields=document.getElementById('e_sip_fields');if(sip&&fields)fields.style.display=sip.checked?'block':'none'}
window.toggleEditSip=toggleEditSip;
async function submitEdit(e,id){e.preventDefault();const form=e.target;const saveBtn=form?.querySelector('button[type="submit"]');const actions=form?.querySelector('.form-actions');let progress=null;try{const f=funds.find(x=>x.id===id);const enabled=document.getElementById('e_sip').checked;const amount=Number(document.getElementById('e_sip_amount')?.value||0);const day=document.getElementById('e_sip_day')?.value||null;const changed=enabled&&f&&(Math.abs(Number(f.sip_amount||0)-amount)>1e-9||Number(f.sip_day||0)!==Number(day||0));if(changed&&!document.getElementById('e_sip_effective')?.value)throw new Error('Enter the first date from which the new SIP amount/date applies');progress=document.createElement('div');progress.className='progress-box operation-progress';progress.innerHTML='<div class="progress-head"><span>Updating fund</span><span id="editProgressPct">0%</span></div><div class="progress-track"><div id="editProgressFill" class="progress-fill" style="width:8%"></div></div><div id="editProgressMessage" class="progress-message">Saving changes…</div><div id="editProgressSub" class="progress-sub">Please keep this window open while the portfolio is updated.</div>';form.insertBefore(progress,actions);if(saveBtn)saveBtn.disabled=true;const setProgress=(pct,msg,sub)=>{const fill=document.getElementById('editProgressFill');const label=document.getElementById('editProgressPct');const m=document.getElementById('editProgressMessage');const ss=document.getElementById('editProgressSub');if(fill)fill.style.width=pct+'%';if(label)label.textContent=pct+'%';if(m)m.textContent=msg;if(ss)ss.textContent=sub||'';};setProgress(20,'Saving changes…','Updating the fund and SIP configuration.');await api('./api/funds/'+id,{method:'PUT',body:JSON.stringify({scheme_name:f.fund_name,units:document.getElementById('e_units').value,invested:document.getElementById('e_invested').value,initial_date:document.getElementById('e_initial').value||null,sip_enabled:enabled,sip_amount:amount,sip_day:day,sip_effective_date:document.getElementById('e_sip_effective')?.value||null})});setProgress(82,'Refreshing portfolio…','Applying the latest NAV, checking SIP eligibility and recalculating the portfolio.');await load();setProgress(100,'Update complete','The table and portfolio totals have been updated.');await new Promise(r=>setTimeout(r,350));closeModal()}catch(err){if(progress){const m=document.getElementById('editProgressMessage');const ss=document.getElementById('editProgressSub');if(m)m.textContent='Update failed';if(ss)ss.textContent=err.message||'An unexpected error occurred.';}if(saveBtn)saveBtn.disabled=false;if(!progress)alert(err.message)}}
window.submitEdit=submitEdit;
async function deleteFund(id){if(!confirm('Delete this fund and its transaction history?'))return;try{await api('./api/funds/'+id,{method:'DELETE'});closeModal();await load()}catch(e){alert(e.message)}}
window.deleteFund=deleteFund;

async function previewTxn(e,id){e.preventDefault();try{const amount=document.getElementById('tx_amount').value.trim();const units=document.getElementById('tx_units').value.trim();if(!amount&&!units)throw new Error('Enter either amount or units');if(amount&&units)throw new Error('Enter either amount or units, not both');const btn=document.getElementById('tx_preview_btn');btn.disabled=true;const q=await api('./api/funds/'+id+'/transaction-quote',{method:'POST',body:JSON.stringify({txn_type:document.getElementById('tx_type').value,nav_date:document.getElementById('tx_nav_date').value,amount:amount||null,units:units||null})});pendingTxnQuote=q;const box=document.getElementById('tx_quote');box.classList.remove('hidden');box.innerHTML=`<strong>Transaction preview</strong><div class="quote-grid"><span>NAV date</span><b>${escapeHtml(formatDate(q.nav_date))}</b><span>NAV</span><b>${num(q.nav,6)}</b><span>Amount</span><b>${money(q.amount)}</b><span>Units</span><b>${num(q.units,4)}</b></div><div class="hint">Please confirm these values before recording the transaction.</div>`;document.getElementById('tx_confirm_btn').classList.remove('hidden');btn.textContent='Recalculate';}catch(err){alert(err.message)}finally{const b=document.getElementById('tx_preview_btn');if(b)b.disabled=false}}
window.previewTxn=previewTxn;
async function confirmTxn(id){if(!pendingTxnQuote)return;try{const note=document.getElementById('tx_note')?.value||'';const q=pendingTxnQuote;await api('./api/funds/'+id+'/transactions',{method:'POST',body:JSON.stringify({txn_type:q.txn_type,txn_date:q.nav_date,amount:q.amount,units:q.units,nav:q.nav,note:note,input_mode:'calculated'})});pendingTxnQuote=null;closeModal();await load()}catch(err){alert(err.message)}}
window.confirmTxn=confirmTxn;
async function deleteTransaction(fundId,transactionId,button){if(!Number.isInteger(Number(transactionId)))return;const row=button?.closest('tr');const cells=row?[...row.cells].slice(0,2).map(c=>c.textContent.trim()).filter(Boolean):[];const label=cells.join(' – ')||'this transaction';if(!confirm('Delete '+label+'? This will remove its financial effect. A completed automatic SIP will be recorded as skipped for its cycle.'))return;try{button.disabled=true;await api('./api/funds/'+fundId+'/transactions',{method:'DELETE',body:JSON.stringify({transaction_id:Number(transactionId)})});await viewFund(fundId);await load()}catch(err){if(button)button.disabled=false;alert(err.message)}}
window.confirmTxn=confirmTxn;
window.deleteTransaction=deleteTransaction;
async function openSettings(){
 appSettings=await api('./api/settings');
 const mins=Math.round((appSettings.refresh_interval_seconds||7200)/60);
 const niftyMins=Math.round((appSettings.nifty_poll_interval_seconds||0)/60);
 openModal('Settings',`<form class="form" onsubmit="saveSettings(event)">
 <div class="field"><label>NAV update interval</label><select id="refreshInterval"><option value="5">Every 5 minutes</option><option value="15">Every 15 minutes</option><option value="30">Every 30 minutes</option><option value="60">Every 1 hour</option><option value="120">Every 2 hours</option><option value="360">Every 6 hours</option><option value="720">Every 12 hours</option><option value="1440">Every 24 hours</option></select><div class="hint">The setting is stored in the add-on database and survives restarts.</div></div>
 <div class="field"><label>NIFTY 50 update interval</label><select id="niftyInterval"><option value="0">Off</option><option value="1">Every 1 minute</option><option value="5">Every 5 minutes</option><option value="10">Every 10 minutes</option><option value="15">Every 15 minutes</option><option value="30">Every 30 minutes</option><option value="60">Every 1 hour</option></select><div class="hint">NIFTY updates only while the Market sensor is open (09:00–15:30 IST).</div></div>
 <div class="form-actions"><button type="button" class="secondary" onclick="closeModal()">Cancel</button><button type="submit" class="primary">Save Settings</button></div></form>`);
 document.getElementById('refreshInterval').value=String(mins);
 document.getElementById('niftyInterval').value=String([0,1,5,10,15,30,60].includes(niftyMins)?niftyMins:1);
}
window.openSettings=openSettings;
async function saveSettings(e){
 e.preventDefault();
 try{
   const mins=Number(document.getElementById('refreshInterval').value);
   const niftyMins=Number(document.getElementById('niftyInterval').value);
   appSettings=await api('./api/settings',{method:'POST',body:JSON.stringify({refresh_interval_minutes:mins,nifty_poll_interval_minutes:niftyMins})});
   closeModal();
   renderRefreshInfo();
   renderMarketStatus(portfolio);
   alert(`Settings saved. NAV: every ${mins} minute${mins===1?'':'s'}; NIFTY: ${niftyMins===0?'off':`every ${niftyMins} minute${niftyMins===1?'':'s'}`}.`);
 }catch(err){alert(err.message)}
}
window.saveSettings=saveSettings;
function exitApp(){
  if (window.history.length > 1) {
    window.history.back();
  } else if (document.referrer) {
    window.location.href = document.referrer;
  } else {
    window.location.href = '/';
  }
}
window.exitApp=exitApp;
function setupHeaderActions(){
 const plusBtn=document.getElementById('plusMenuBtn');
 const plusMenu=document.getElementById('plusMenu');
 const importBtn=document.getElementById('importBtn');
 const addBtn=document.getElementById('addBtn');
 if(!plusBtn || !plusMenu) return false;
 if(plusBtn.dataset.mftBound==='1') return true;
 plusBtn.dataset.mftBound='1';
 plusBtn.addEventListener('click',function(e){
   e.preventDefault();
   e.stopPropagation();
   const open=plusMenu.classList.contains('hidden');
   plusMenu.classList.toggle('hidden',!open);
   plusBtn.setAttribute('aria-expanded',open?'true':'false');
 });
 plusMenu.addEventListener('click',e=>e.stopPropagation());
 importBtn?.addEventListener('click',function(e){
   e.preventDefault(); e.stopPropagation();
   togglePlusMenu(false,e); importFunds();
 });
 addBtn?.addEventListener('click',function(e){
   e.preventDefault(); e.stopPropagation();
   togglePlusMenu(false,e); addFund();
 });
 document.addEventListener('click',function(e){
   if(!e.target.closest('.plus-menu-wrap')) togglePlusMenu(false);
 });
 document.addEventListener('keydown',function(e){if(e.key==='Escape') togglePlusMenu(false);});
 return true;
}
setupHeaderActions();
applyTheme(getTheme());
document.getElementById('themeBtn')?.addEventListener('click',toggleTheme);document.getElementById('settingsBtn').addEventListener('click',openSettings);document.getElementById('exitBtn').addEventListener('click',exitApp);document.getElementById('addInvestorMenuBtn')?.addEventListener('click',()=>{togglePlusMenu(false);addInvestor();});document.getElementById('editUserBtn').addEventListener('click',editInvestor);document.getElementById('refreshBtn').addEventListener('click',refreshAndReload);
document.getElementById('exportPngBtn')?.addEventListener('click',exportPortfolioPng);pollRefresh();document.getElementById('filterBox')?.addEventListener('input',onFundFilterChange);document.getElementById('sipFilter')?.addEventListener('change',onFundFilterChange);document.getElementById('returnsFilter')?.addEventListener('change',onFundFilterChange);document.getElementById('metricFilter')?.addEventListener('change',onFundFilterChange);document.getElementById('metricRelation')?.addEventListener('change',onFundFilterChange);document.getElementById('metricValue')?.addEventListener('input',handleMetricValueInput);document.getElementById('clearFiltersBtn')?.addEventListener('click',clearFundFilters);document.getElementById('filterToggleBtn')?.addEventListener('click',toggleFundFilters);['planFilter','optionFilter','categoryFilter'].forEach(id=>document.getElementById(id)?.addEventListener('change',onFundFilterChange));document.getElementById('portfolioTableTab')?.addEventListener('click',()=>setPortfolioTab('table'));document.getElementById('portfolioGraphsTab')?.addEventListener('click',()=>setPortfolioTab('graphs'));document.getElementById('buildGraphsBtn')?.addEventListener('click',buildGraphs);document.getElementById('selectAllGraphFundsBtn')?.addEventListener('click',()=>{graphSelectedFunds=(funds||[]).map(f=>Number(f.id));renderGraphFundPicker();});document.getElementById('clearGraphFundsBtn')?.addEventListener('click',()=>{graphSelectedFunds=[];renderGraphFundPicker();});document.querySelectorAll('#graphPeriodPicker button').forEach(b=>b.addEventListener('click',()=>{setGraphPeriod(b.dataset.period);}));document.getElementById('sortSelect').addEventListener('change',e=>setSort(e.target.value));document.getElementById('sortDirBtn').addEventListener('click',toggleSortDir);document.getElementById('modal').addEventListener('click',e=>{if(e.target.id==='modal')closeModal()});setPortfolioTab(activePortfolioTab);load();setInterval(load,60000);
