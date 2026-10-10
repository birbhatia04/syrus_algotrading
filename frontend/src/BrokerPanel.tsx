import {useEffect,useState} from 'react'
import {api} from './api'

type Status={configured:boolean;bound:boolean;enabled:boolean;worker_alive:boolean;market_connected:boolean;orders_connected:boolean;reconciled:boolean;last_error:string;kill_elapsed_ms:number|null}

export function BrokerPanel(){
 const [status,setStatus]=useState<Status|null>(null)
 const [error,setError]=useState(''),[localId,setLocalId]=useState(''),[brokerId,setBrokerId]=useState(''),[busy,setBusy]=useState(false)
 useEffect(()=>{
  let alive=true
  const load=()=>api<Status>('/broker/status').then(s=>{if(alive)setStatus(s)}).catch(e=>{if(alive)setError(e.message)})
  load();const timer=window.setInterval(load,1000)
  return()=>{alive=false;window.clearInterval(timer)}
 },[])
 async function resolve(e:React.FormEvent){
  e.preventDefault();setBusy(true);setError('')
  try{await api('/broker/orders/'+localId+'/resolve',{method:'POST',body:JSON.stringify({broker_order_id:brokerId})});setLocalId('');setBrokerId('')}
  catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }
 return <section className="panel broker-panel">
  <div className="panelhead"><div><h2>021 sandbox connection</h2><p>Virtual funds · NSE cash · INTRADAY</p></div></div>
  <div className="broker-health">
   {([['Token',status?.configured],['Account bound',status?.bound],['Worker',status?.worker_alive],['Market socket',status?.market_connected],['Orders socket',status?.orders_connected],['Reconciled',status?.reconciled]] as [string,boolean|undefined][]).map(([name,ok])=><span key={name} className={`readiness-chip ${ok?'ready':'waiting'}`}><i aria-hidden="true"/>{name}<strong>{ok?'Ready':'Waiting'}</strong></span>)}
  </div>
  {status?.last_error&&<p role="status" className="broker-warning">{status.last_error}</p>}
  {status?.kill_elapsed_ms!==null&&status?.kill_elapsed_ms!==undefined&&<p>Last confirmed flatten: {(status.kill_elapsed_ms/1000).toFixed(2)} seconds {status.kill_elapsed_ms>10000?'— exceeded 10-second target':''}</p>}
  <details><summary>Resolve an ambiguous submission</summary><p>Review the order in 021, then link its broker ID to the local UNKNOWN order. The platform checks the instrument, side, quantity and submission time before reconciliation.</p>
   <form className="resolve-form" onSubmit={resolve}><label>Local order ID<input required pattern="[0-9]+" value={localId} onChange={e=>setLocalId(e.target.value)}/></label><label>021 order ID<input required pattern="[0-9]+" value={brokerId} onChange={e=>setBrokerId(e.target.value)}/></label><button className="secondary" disabled={busy}>Link and reconcile</button></form>
  </details>
  {error&&<p className="negative" role="alert">{error}</p>}
 </section>
}
