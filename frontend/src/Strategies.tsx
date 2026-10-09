import {useEffect,useState} from 'react'
import {SlidersHorizontal} from 'lucide-react'
import {api,money} from './api'
import type {Strategy,Subscription} from './types'

type Props={refresh:number;changed:()=>void;setError:(s:string)=>void;symbol:string;live:boolean}
export function Strategies({refresh,changed,setError,symbol,live}:Props){
 const [strategies,setStrategies]=useState<Strategy[]>([]),[subs,setSubs]=useState<Subscription[]>([])
 const [selected,setSelected]=useState(symbol),[matches,setMatches]=useState<{symbol:string;lot_size:number}[]>([])
 useEffect(()=>{Promise.all([api<Strategy[]>('/strategies'),api<Subscription[]>('/subscriptions')]).then(([a,b])=>{setStrategies(a);setSubs(b)}).catch(e=>setError(e.message))},[refresh,setError])
 useEffect(()=>{
  if(!live)return
  let active=true
  const timer=window.setTimeout(()=>api<{symbol:string;lot_size:number}[]>('/broker/instruments?q='+encodeURIComponent(selected)).then(rows=>{if(active)setMatches(rows)}).catch(e=>setError(e.message)),250)
  return()=>{active=false;window.clearTimeout(timer)}
 },[selected,live,setError])
 async function subscribe(id:string){
  try{await api('/subscriptions',{method:'POST',body:JSON.stringify({strategy_id:id,symbol:selected})});changed()}catch(e){setError((e as Error).message)}
 }
 async function toggle(sub:Subscription){
  try{await api('/subscriptions/'+sub.id+'/'+(sub.status==='RUNNING'?'pause':'start'),{method:'POST'});changed()}catch(e){setError((e as Error).message)}
 }
 return <div className="page">
  {live&&<section className="panel instrument-picker"><label>NSE symbol<input list="instruments" value={selected} onChange={e=>setSelected(e.target.value.toUpperCase())}/></label><datalist id="instruments">{matches.map(i=><option value={i.symbol} key={i.symbol}>Lot {i.lot_size}</option>)}</datalist><p>Choose from the daily 021 instrument master. Each strategy keeps its own position and P&L.</p></section>}
  <div className="cards">{strategies.map(strategy=><StrategyCard key={strategy.id} strategy={strategy} sub={subs.find(s=>s.strategy_id===strategy.id)} selected={selected} subscribe={subscribe} toggle={toggle} changed={changed} setError={setError}/>)}</div>
 </div>
}

function StrategyCard({strategy,sub,selected,subscribe,toggle,changed,setError}:{strategy:Strategy;sub?:Subscription;selected:string;subscribe:(id:string)=>void;toggle:(s:Subscription)=>void;changed:()=>void;setError:(s:string)=>void}){
 const [quantity,setQuantity]=useState(String(sub?.parameters.quantity??strategy.default_parameters.quantity??1))
 const [side,setSide]=useState(String(sub?.parameters.side??'BUY'))
 async function save(){
  if(!sub)return
  try{await api('/subscriptions/'+sub.id+'/parameters',{method:'PATCH',body:JSON.stringify({quantity:Number(quantity),side})});changed()}catch(e){setError((e as Error).message)}
 }
 return <section className="strategy">
  <div className="strategytop"><span className="timeframe">{strategy.timeframe}</span><SlidersHorizontal/></div>
  <h2>{strategy.name}</h2><p>{strategy.description}</p><div className="rule"><strong>EXACT RULE</strong>{strategy.rules}</div>
  <div className="params">{Object.entries(sub?.parameters??strategy.default_parameters).map(([k,v])=><span key={k}><small>{k}</small><b>{v}</b></span>)}</div>
  {sub?<><div className="strategy-pnl"><span>{sub.symbol} · {sub.status}</span><b className={sub.net_pnl>=0?'positive':'negative'}>{money(sub.net_pnl)}</b><small>Position {sub.quantity} · today {money(sub.daily_net_pnl)} · assumed charges</small></div>
   <div className="strategy-actions"><button className={sub.status==='RUNNING'?'secondary running':'primary'} onClick={()=>toggle(sub)}>{sub.status==='RUNNING'?'Pause entries':'Start strategy'}</button>{sub.symbol!==selected&&<button className="secondary" disabled={sub.status==='RUNNING'} onClick={()=>subscribe(strategy.id)}>Switch to {selected}</button>}</div>
   <details><summary>Position sizing</summary><div className="sizing"><label>Quantity<input type="number" min={1} value={quantity} onChange={e=>setQuantity(e.target.value)}/></label>{strategy.id==='time_entry'&&<label>Entry side<select value={side} onChange={e=>setSide(e.target.value)}><option>BUY</option><option>SELL</option></select></label>}<button className="secondary" disabled={sub.status==='RUNNING'} onClick={save}>Save</button></div></details>
  </>:<button className="primary" onClick={()=>subscribe(strategy.id)}>Subscribe to {selected}</button>}
 </section>
}
