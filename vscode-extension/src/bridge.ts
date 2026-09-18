import * as vscode from 'vscode';
import { BridgeSnapshot, BridgeStatus, JarvisState } from './types';

export class JarvisBridge implements vscode.Disposable {
  private endpoint!: string; private token!: string; private timer?: NodeJS.Timeout; private listeners = new Set<(s: BridgeSnapshot) => void>();
  private snapshot: BridgeSnapshot = { status: 'checking' };
  constructor(private readonly output: vscode.OutputChannel) { this.reload(); }
  private reload() { const c=vscode.workspace.getConfiguration('jarvis'); this.endpoint=(c.get<string>('coreEndpoint')||'http://127.0.0.1:8000').replace(/\/$/,''); this.token=c.get<string>('authToken')||''; }
  onDidChange(listener:(s:BridgeSnapshot)=>void) { this.listeners.add(listener); return { dispose:()=>this.listeners.delete(listener) }; }
  getSnapshot() { return this.snapshot; }
  private emit(s:BridgeSnapshot) { this.snapshot=s; this.listeners.forEach(l=>l(s)); }
  private headers() { return { 'Content-Type':'application/json', ...(this.token ? {'Authorization':`Bearer ${this.token}`} : {}) }; }
  async request<T>(path:string, init:RequestInit={}) : Promise<T> { this.reload(); const controller=new AbortController(); const timeout=setTimeout(()=>controller.abort(),7000); try { const res=await fetch(`${this.endpoint}${path}`,{...init,headers:{...this.headers(),...(init.headers||{})},signal:controller.signal}); if(!res.ok) throw new Error(`${res.status} ${res.statusText}`); return await res.json() as T; } finally { clearTimeout(timeout); } }
  async refresh(): Promise<BridgeSnapshot> { this.emit({...this.snapshot,status:'checking'}); try { const [state,health,controls]=await Promise.all([this.request<JarvisState>('/api/state'),this.request<unknown>('/api/health'),this.request<unknown>('/api/controls')]); const s={status:'online' as BridgeStatus,state,health,controls,checkedAt:new Date().toISOString()}; this.emit(s); return s; } catch(e) { const lastError=e instanceof Error?e.message:String(e); const s={status:'offline' as BridgeStatus,lastError,checkedAt:new Date().toISOString()}; this.emit(s); return s; } }
  startHeartbeat(interval=15000) { void this.refresh(); this.timer=setInterval(()=>void this.refresh(),interval); }
  async command(text:string, context?:unknown) { return this.request<{ok?:boolean}>('/api/command',{method:'POST',body:JSON.stringify({text: context ? `${text}\n\nVS Code context:\n${JSON.stringify(context)}` : text})}); }
  async control(control:string,value?:unknown) { return this.request('/api/controls',{method:'POST',body:JSON.stringify({control,value})}); }
  async registerCapabilities(capabilities: string[], metadata: Record<string, unknown> = {}) {
    // The existing bridge has a single control surface; registration is best-effort
    // so older cores remain compatible and still receive context with commands.
    try { await this.request('/api/capabilities/register',{method:'POST',body:JSON.stringify({source:'vscode',capabilities,metadata})}); } catch (e) {
      this.output.appendLine(`Capability registration skipped: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  dispose(){ if(this.timer) clearInterval(this.timer); this.listeners.clear(); }
}
