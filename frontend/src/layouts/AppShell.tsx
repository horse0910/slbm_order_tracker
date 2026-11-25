import {useState,type ReactNode} from 'react';
import {useLocation} from 'react-router-dom';
import {useEffect} from 'react';
import {Sidebar} from './Sidebar';
import {TopBar} from './TopBar';
export function AppShell({children}:{children:ReactNode}){
 const [open,setOpen]=useState(false);const location=useLocation();useEffect(()=>setOpen(false),[location.pathname]);
 return <div className="flex h-screen w-screen overflow-hidden bg-surface-0"><div className="hidden md:block no-print"><Sidebar/></div>{open?<div className="fixed inset-0 z-50 flex bg-black/40 md:hidden"><Sidebar/><button className="flex-1" aria-label="Close navigation" onClick={()=>setOpen(false)}/></div>:null}<div className="flex min-w-0 flex-1 flex-col"><TopBar onMenu={()=>setOpen(true)}/><main className="flex-1 overflow-y-auto px-4 py-6 lg:px-7"><div className="mx-auto max-w-[1600px]">{children}</div></main></div></div>;
}
