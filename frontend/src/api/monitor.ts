import {http} from './client';
export interface MonitorStatus {running:boolean;market_open:boolean;refresh_seconds:number;backoff_until:string|null;generated_at:string;last_sweep:{fetched:number;symbols:number;duration_seconds:number;at:string}|null;power:{supported:boolean;enabled:boolean};}
export const monitorApi={status:(signal?:AbortSignal)=>http.get<MonitorStatus>('/api/scheduler/status',signal),awake:(enabled:boolean)=>http.put<{supported:boolean;enabled:boolean}>('/api/scheduler/awake',{enabled})};
