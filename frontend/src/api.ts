const API = import.meta.env.VITE_API_URL || '/api/v1'
export const token = () => localStorage.getItem('algorhythm_token') || localStorage.getItem('aegis_token')
export async function api<T>(path:string, options:RequestInit={}):Promise<T>{
  const response=await fetch(`${API}${path}`,{...options,headers:{'Content-Type':'application/json',...(token()?{Authorization:`Bearer ${token()}`}:{...{}}),...options.headers}})
  if(response.status===204) return undefined as T
  const data=await response.json().catch(()=>({detail:'Request failed'}))
  if(!response.ok){
    const detail=Array.isArray(data.detail)?data.detail.map((e:{msg?:string})=>e.msg||'Invalid value').join('; '):data.detail
    throw new Error(typeof detail==='string'?detail:'Request failed')
  }
  return data
}
export const money=(n:number,currency='INR')=>new Intl.NumberFormat(currency==='USD'?'en-US':'en-IN',{style:'currency',currency,minimumFractionDigits:2}).format(n)
export const dt=(s:string)=>new Intl.DateTimeFormat('en-IN',{dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Kolkata'}).format(new Date(s))
