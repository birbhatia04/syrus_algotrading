import {useEffect,useRef,useState} from 'react'
import {Laptop,Plus,SlidersHorizontal,Trash2,X} from 'lucide-react'
import {api,money,ApiError} from './api'
import type {Strategy,Subscription} from './types'

type Props={refresh:number;changed:()=>void;setError:(s:string)=>void;notify:(title:string,message:string)=>void;symbol:string;live:boolean}
type LocalStrategy=Strategy&{local:true}
type StrategyDraft={name:string;timeframe:string;description:string;rules:string;quantity:string}

const LOCAL_STRATEGIES_KEY='algorhythm-local-strategies'
const EMPTY_DRAFT:StrategyDraft={name:'',timeframe:'1 minute',description:'',rules:'',quantity:'1'}

function isLocalStrategy(value:unknown):value is LocalStrategy{
 if(!value||typeof value!=='object')return false
 const item=value as Partial<LocalStrategy>
 return item.local===true&&typeof item.id==='string'&&item.id.startsWith('local-')&&typeof item.name==='string'&&typeof item.timeframe==='string'&&typeof item.description==='string'&&typeof item.rules==='string'&&!!item.default_parameters&&typeof item.default_parameters==='object'&&Number.isInteger(Number(item.default_parameters.quantity))&&Number(item.default_parameters.quantity)>=1
}

function loadLocalStrategies():LocalStrategy[]{
 try{
  const value=localStorage.getItem(LOCAL_STRATEGIES_KEY)
  const parsed=value?JSON.parse(value):[]
  return Array.isArray(parsed)?parsed.filter(isLocalStrategy):[]
 }catch{return []}
}

export function Strategies({refresh,changed,setError,notify,symbol,live}:Props){
 const [strategies,setStrategies]=useState<Strategy[]>([]),[subs,setSubs]=useState<Subscription[]>([])
 const [selected,setSelected]=useState(symbol),[matches,setMatches]=useState<{symbol:string;lot_size:number}[]>([])
 const [localStrategies,setLocalStrategies]=useState<LocalStrategy[]>(loadLocalStrategies)
 const [startingId,setStartingId]=useState<number|null>(null)
 const [composerOpen,setComposerOpen]=useState(false),[draft,setDraft]=useState<StrategyDraft>(EMPTY_DRAFT)
 useEffect(()=>{Promise.all([api<Strategy[]>('/strategies'),api<Subscription[]>('/subscriptions')]).then(([a,b])=>{setStrategies(a);setSubs(b)}).catch(e=>setError(e.message))},[refresh,setError])
 useEffect(()=>{
  if(!live)return
  let active=true
  const timer=window.setTimeout(()=>api<{symbol:string;lot_size:number}[]>('/broker/instruments?q='+encodeURIComponent(selected)).then(rows=>{if(active)setMatches(rows)}).catch(e=>setError(e.message)),250)
  return()=>{active=false;window.clearTimeout(timer)}
 },[selected,live,setError])
 async function subscribe(id:string){
  try{await api('/subscriptions',{method:'POST',body:JSON.stringify({strategy_id:id,symbol:selected})});changed();notify('Strategy subscribed',`${selected} is ready for ${strategies.find(item=>item.id===id)?.name??'the strategy'}.`)}catch(e){setError((e as Error).message)}
 }
 async function toggle(sub:Subscription){
  const pausing=sub.status==='RUNNING'
  try{
   await api('/subscriptions/'+sub.id+'/'+(pausing?'pause':'start'),{method:'POST'})
   changed();notify(pausing?'Entries paused':'Strategy started',`${sub.symbol} · ${sub.name}`)
  }catch(e){
   const error=e as ApiError
   if(pausing||!(error instanceof ApiError)||error.status!==409){setError(error.message||'Request failed');return}
   setStartingId(sub.id)
   const delay=(error.retryAfter??3)*1000
   const deadline=Date.now()+30000
   try{
    while(Date.now()<deadline){
     await new Promise(resolve=>window.setTimeout(resolve,delay))
     try{
      await api('/subscriptions/'+sub.id+'/start',{method:'POST'})
      changed();notify('Strategy started',`${sub.symbol} · ${sub.name} (after reconciliation)`)
      return
     }catch(retryError){
      const retry=retryError as ApiError
      if(!(retry instanceof ApiError)||retry.status!==409)throw retryError
     }
    }
    setError('The 021 account is still reconciling after 30 seconds. Check the worker and the Overview readiness chips, then start the strategy again.')
   }catch(retryError){
    setError((retryError as Error).message)
   }finally{
    setStartingId(null)
   }
  }
 }
 function saveLocalStrategy(event:React.FormEvent){
  event.preventDefault()
  const quantity=Number(draft.quantity)
  if(!draft.name.trim()||!draft.description.trim()||!draft.rules.trim()||!Number.isInteger(quantity)||quantity<1)return
  const strategy:LocalStrategy={id:`local-${Date.now()}`,name:draft.name.trim(),timeframe:draft.timeframe,description:draft.description.trim(),rules:draft.rules.trim(),default_parameters:{quantity},local:true}
  const next=[strategy,...localStrategies]
  try{localStorage.setItem(LOCAL_STRATEGIES_KEY,JSON.stringify(next))}catch{setError('This browser could not save the local strategy. Check site storage permissions and try again.');return}
  setLocalStrategies(next)
  setDraft(EMPTY_DRAFT)
  setComposerOpen(false)
  notify('Local strategy added',`${strategy.name} is saved in this browser only.`)
 }
 function removeLocalStrategy(id:string){
  const strategy=localStrategies.find(item=>item.id===id)
  const next=localStrategies.filter(item=>item.id!==id)
  try{localStorage.setItem(LOCAL_STRATEGIES_KEY,JSON.stringify(next))}catch{setError('This browser could not update local strategies. Check site storage permissions and try again.');return}
  setLocalStrategies(next)
  if(strategy)notify('Local strategy removed',`${strategy.name} was removed from this browser.`)
 }
 return <div className="page">
  {live&&<section className="panel instrument-picker"><label>NSE symbol<input list="instruments" value={selected} onChange={e=>setSelected(e.target.value.toUpperCase())}/></label><datalist id="instruments">{matches.map(i=><option value={i.symbol} key={i.symbol}>Lot {i.lot_size}</option>)}</datalist><p>Choose from the daily 021 instrument master. Each strategy keeps its own position and P&amp;L.</p></section>}
  <section className="strategy-toolbar" aria-labelledby="strategy-library-title"><div><h2 id="strategy-library-title">Strategy library</h2><p>Use a built-in execution rule or sketch a frontend-only strategy for your workspace.</p></div><button className={composerOpen?'secondary':'primary'} type="button" aria-expanded={composerOpen} aria-controls="strategy-composer" onClick={()=>setComposerOpen(open=>!open)}>{composerOpen?<><X size={17}/>Close</>:<><Plus size={17}/>Add strategy</>}</button></section>
  {composerOpen&&<section className="panel strategy-composer" id="strategy-composer"><div className="composer-heading"><div><h2>Create a local strategy</h2><p>This draft stays in this browser. It does not submit orders or call the backend.</p></div><span className="local-badge"><Laptop size={14}/>Local only</span></div><form onSubmit={saveLocalStrategy}><div className="strategy-form-grid"><label>Strategy name<input required maxLength={48} placeholder="Opening range continuation" value={draft.name} onChange={event=>setDraft({...draft,name:event.target.value})}/></label><label>Timeframe<select value={draft.timeframe} onChange={event=>setDraft({...draft,timeframe:event.target.value})}><option>1 minute</option><option>5 minutes</option><option>15 minutes</option><option>1 hour</option><option>Daily</option></select></label><label className="field-wide">Description<textarea required maxLength={160} rows={3} placeholder="Describe what this strategy is designed to capture." value={draft.description} onChange={event=>setDraft({...draft,description:event.target.value})}/></label><label className="field-wide">Entry and exit rules<textarea required maxLength={320} rows={4} placeholder="Example: Enter after price closes above the opening range; exit at the stop or before market close." value={draft.rules} onChange={event=>setDraft({...draft,rules:event.target.value})}/></label><label>Default quantity<input required type="number" min={1} step={1} value={draft.quantity} onChange={event=>setDraft({...draft,quantity:event.target.value})}/></label></div><div className="composer-actions"><button type="button" className="secondary" onClick={()=>{setDraft(EMPTY_DRAFT);setComposerOpen(false)}}>Cancel</button><button className="primary">Add local strategy</button></div></form></section>}
  <div className="cards">{localStrategies.map(strategy=><LocalStrategyCard key={strategy.id} strategy={strategy} remove={removeLocalStrategy}/>)}{strategies.map(strategy=><StrategyCard key={strategy.id} strategy={strategy} sub={subs.find(s=>s.strategy_id===strategy.id)} selected={selected} subscribe={subscribe} toggle={toggle} changed={changed} setError={setError} notify={notify} starting={startingId===subs.find(s=>s.strategy_id===strategy.id)?.id}/>)}</div>
 </div>
}

function LocalStrategyCard({strategy,remove}:{strategy:LocalStrategy;remove:(id:string)=>void}){
 return <section className="strategy local-strategy">
  <div className="strategytop"><span className="timeframe">{strategy.timeframe}</span><span className="local-badge"><Laptop size={13}/>Local only</span></div>
  <h2>{strategy.name}</h2><p>{strategy.description}</p><div className="rule"><strong>ENTRY / EXIT RULE</strong>{strategy.rules}</div>
  <div className="params">{Object.entries(strategy.default_parameters).map(([key,value])=><span key={key}><small>{key}</small><b>{value}</b></span>)}</div>
  <div className="local-strategy-footer"><p>Prototype only · no orders or subscriptions</p><button className="text-danger" type="button" onClick={()=>remove(strategy.id)} aria-label={`Remove ${strategy.name}`}><Trash2 size={15}/>Remove</button></div>
 </section>
}

function StrategyCard({strategy,sub,selected,subscribe,toggle,changed,setError,notify,starting}:{strategy:Strategy;sub?:Subscription;selected:string;subscribe:(id:string)=>void;toggle:(s:Subscription)=>void;changed:()=>void;setError:(s:string)=>void;notify:(title:string,message:string)=>void;starting:boolean}){
 const [quantity,setQuantity]=useState(String(sub?.parameters.quantity??strategy.default_parameters.quantity??1))
 const [side,setSide]=useState(String(sub?.parameters.side??'BUY'))
 const [saved,setSaved]=useState(false)
 const [saving,setSaving]=useState(false)
 const savingRef=useRef(false)
 const draftKey=`algorhythm-sizing-${sub?.id??strategy.id}`
 useEffect(()=>{
  const persisted=sessionStorage.getItem(draftKey)
  if(persisted){try{const draft=JSON.parse(persisted);setQuantity(String(draft.quantity));setSide(String(draft.side??'BUY'));return}catch{sessionStorage.removeItem(draftKey)}}
  setQuantity(String(sub?.parameters.quantity??strategy.default_parameters.quantity??1));setSide(String(sub?.parameters.side??'BUY'))
 },[draftKey,sub?.parameters.quantity,sub?.parameters.side,strategy.default_parameters.quantity])
 const canSave=!!sub&&sub.status==='PAUSED'&&sub.quantity===0
 const dirty=!!sub&&(String(sub.parameters.quantity)!==quantity||(strategy.id==='time_entry'&&String(sub.parameters.side??'BUY')!==side))
 async function save(){
  if(!sub||!canSave||!dirty||savingRef.current)return
  const value=Number(quantity)
  if(!Number.isInteger(value)||value<1){setError('Enter a whole-number quantity of at least 1.');return}
  savingRef.current=true;setSaving(true);setSaved(false)
  try{const updated=await api<Subscription>('/subscriptions/'+sub.id+'/parameters',{method:'PATCH',body:JSON.stringify({quantity:value,side})});setQuantity(String(updated.parameters.quantity));setSide(String(updated.parameters.side??'BUY'));sessionStorage.removeItem(draftKey);setSaved(true);changed();notify('Position sizing saved',`${strategy.name} now uses quantity ${value}.`)}catch(e){setError((e as Error).message)}finally{savingRef.current=false;setSaving(false)}
 }
 return <section className="strategy">
  <div className="strategytop"><span className="timeframe">{strategy.timeframe}</span><SlidersHorizontal/></div>
  <h2>{strategy.name}</h2><p>{strategy.description}</p><div className="rule"><strong>EXACT RULE</strong>{strategy.rules}</div>
  <div className="params">{Object.entries(sub?.parameters??strategy.default_parameters).map(([k,v])=><span key={k}><small>{k}</small><b>{v}</b></span>)}</div>
  {sub?<><div className="strategy-pnl"><span>{sub.symbol} · <em className={`strategy-status ${sub.status.toLowerCase()}`}>{sub.status}</em></span><b className={sub.net_pnl>=0?'positive':'negative'}>{money(sub.net_pnl)}</b><small>Position {sub.quantity} · today {money(sub.daily_net_pnl)} · net of charges</small>{sub.cycle_limit!==undefined&&<div className={`cycle-progress ${sub.cycle_count!==undefined&&sub.cycle_count>=sub.cycle_limit?'cycle-limit-reached':''}`}><div><span>Entry cycles today</span><strong>{sub.cycle_count} / {sub.cycle_limit}</strong></div><div className="progress" role="progressbar" aria-label="Entry cycles used today" aria-valuenow={sub.cycle_count??0} aria-valuemin={0} aria-valuemax={sub.cycle_limit}><i style={{width:`${sub.cycle_limit?Math.min(100,(sub.cycle_count??0)/sub.cycle_limit*100):0}%`}}/></div>{sub.cycle_count!==undefined&&sub.cycle_count>=sub.cycle_limit&&<small>Daily limit reached · new entries pause until the next cycle</small>}</div>}</div>
   <div className="strategy-actions"><button className={sub.status==='RUNNING'?'secondary running':'primary'} disabled={starting} onClick={()=>toggle(sub)}>{starting?'Waiting for reconciliation…':sub.status==='RUNNING'?'Pause entries':'Start strategy'}</button>{sub.symbol!==selected&&<button className="secondary" disabled={sub.status==='RUNNING'||starting} onClick={()=>subscribe(strategy.id)}>Switch to {selected}</button>}</div>
   <details><summary>Position sizing</summary><div className="sizing"><label>Quantity<input type="number" min={1} value={quantity} onChange={e=>{const value=e.target.value;setQuantity(value);setSaved(false);sessionStorage.setItem(draftKey,JSON.stringify({quantity:value,side}))}} onBlur={()=>void save()}/></label>{strategy.id==='time_entry'&&<label>Entry side<select value={side} onChange={e=>{const value=e.target.value;setSide(value);setSaved(false);sessionStorage.setItem(draftKey,JSON.stringify({quantity,side:value}))}} onBlur={()=>void save()}><option>BUY</option><option>SELL</option></select></label>}<button className="secondary" disabled={!canSave||!dirty||saving} onClick={()=>void save()}>{saving?'Saving…':'Save'}</button></div><p className="sizing-note">{canSave?'Changes save when you leave the field or press Save.': 'You can edit a draft now. Pause entries and wait until the strategy is flat to save it.'}{dirty&&!canSave&&<span> Draft kept on this device.</span>}{saved&&<span> Saved successfully.</span>}</p></details>
  </>:<button className="primary" onClick={()=>subscribe(strategy.id)}>Subscribe to {selected}</button>}
 </section>
}
