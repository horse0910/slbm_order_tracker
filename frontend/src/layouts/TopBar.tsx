import {useEffect,useState} from 'react';
import {useLocation} from 'react-router-dom';
import {healthApi} from '@/api/dashboard';
import {monitorApi} from '@/api/monitor';
import {useApiResource} from '@/hooks/useApiResource';
import {pageTitle} from '@/utils/navigation';
import {toApiError} from '@/api/client';
function BackendStatus(){const {data,error}=useApiResource(s=>healthApi.check(s),[],{pollIntervalMs:30000});return <div className="flex items-center gap-2" title={error?.userMessage}><span className={`status-dot ${data?.status==='healthy'?'online':''}`}/><span className="text-2xs text-slate-400">{error?'Backend offline':data?`API v${data.version}`:'Checking…'}</span></div>;}
export function TopBar({onMenu}:{onMenu?:()=>void}){
 const {pathname}=useLocation();
 const [theme,setTheme]=useState(()=>localStorage.getItem('slbm-theme')||'light');
 const monitor=useApiResource(s=>monitorApi.status(s),[],{pollIntervalMs:30000});
 const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
 useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('slbm-theme',theme);},[theme]);
 async function awake(){setBusy(true);setMessage('');try{await monitorApi.awake(!monitor.data?.power.enabled);monitor.refetch();}catch(e){setMessage(toApiError(e).userMessage);}finally{setBusy(false);}}
 return <header className="app-topbar no-print"><div className="flex items-center gap-3"><button className="btn-ghost md:hidden" aria-label="Open navigation" onClick={onMenu}>☰</button><div><p className="topbar-eyebrow">NSE SECURITIES LENDING</p><h1 className="font-semibold text-slate-100">{pageTitle(pathname)}</h1></div></div><div className="flex items-center gap-3"><span className="hidden xl:inline text-2xs text-slate-500">{new Date().toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric',timeZone:'Asia/Kolkata'})}</span><button className={`awake-control ${monitor.data?.power.enabled?'enabled':''}`} disabled={busy||!monitor.data?.power.supported} onClick={awake} title="Temporarily prevent idle sleep while this app is running" aria-pressed={monitor.data?.power.enabled||false}>{monitor.data?.power.enabled?'☕ Mac stays awake':'☕ Keep Mac awake'}</button><button className="theme-toggle" aria-label={theme==='light'?'Switch to dark mode':'Switch to light mode'} onClick={()=>setTheme(t=>t==='light'?'dark':'light')}>{theme==='light'?'☾':'☀'}</button><div className="hidden sm:block"><BackendStatus/></div></div>{message?<span role="alert" className="power-error">{message}</span>:null}</header>;
}
