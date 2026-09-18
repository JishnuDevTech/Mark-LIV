import * as vscode from 'vscode';
import * as path from 'path';
import { ProjectContext } from './types';

export class WorkspaceContextProvider {
  async getContext(includeContents=true): Promise<ProjectContext> {
    const folders=vscode.workspace.workspaceFolders?.map(f=>f.uri.fsPath)??[];
    const editor=vscode.window.activeTextEditor; const selection=editor?.selection;
    const diagnostics:Array<{diagnostic:vscode.Diagnostic;file:string}>=[]; 
    for(const [uri, ds] of vscode.languages.getDiagnostics()) for (const diagnostic of ds) diagnostics.push({diagnostic,file:uri.fsPath});
    const changedFiles=await this.getChangedFiles(); const limit=Math.max(1,vscode.workspace.getConfiguration('jarvis').get<number>('contextFileLimit',20));
    const candidates=[...(editor?[editor.document.uri]:[]),...changedFiles.map(f=>vscode.Uri.file(f)),...await vscode.workspace.findFiles('**/*','**/{node_modules,.git,dist,build,.venv}/**',limit)];
    const seen=new Set<string>(); const files:Array<{path:string;content?:string;truncated?:boolean}>=[];
    for(const uri of candidates) { if(seen.has(uri.fsPath)||files.length>=limit) continue; seen.add(uri.fsPath); const f={path:this.relative(uri.fsPath)}; if(includeContents && this.isReadable(uri)) { try { const bytes=await vscode.workspace.fs.readFile(uri); const text=new TextDecoder().decode(bytes); files.push({...f,content:text.slice(0,12000),truncated:text.length>12000}); } catch { files.push(f); } } else files.push(f); }
    const languages:Record<string,number>={}; for(const f of files) { const ext=path.extname(f.path)||'<none>'; languages[ext]=(languages[ext]||0)+1; }
    return {folders,activeEditor:editor?{path:this.relative(editor.document.uri.fsPath),languageId:editor.document.languageId,selection:selection?editor.document.getText(selection).slice(0,8000):undefined,line:editor.selection.active.line+1}:undefined,diagnostics:diagnostics.slice(0,200).map(({diagnostic:d,file})=>({file:this.relative(file),severity:this.severity(d.severity),message:d.message,line:d.range.start.line+1,source:d.source})),changedFiles:changedFiles.map(this.relative),files,snapshot:{fileCount:files.length,languages,indexedAt:new Date().toISOString()}};
  }
  async readFile(file:string) { const uri=vscode.Uri.file(file); return new TextDecoder().decode(await vscode.workspace.fs.readFile(uri)); }
  async search(query:string) { return vscode.workspace.findFiles(`**/*${query}*`,'**/{node_modules,.git,dist,build}/**',100); }
  private relative(file:string){ const root=vscode.workspace.workspaceFolders?.[0]?.uri.fsPath; return root&&file.startsWith(root)?path.relative(root,file):file; }
  private isReadable(uri:vscode.Uri){ return !/\.(png|jpg|jpeg|gif|ico|pdf|zip|jar|class|woff2?)$/i.test(uri.fsPath); }
  private severity(s:vscode.DiagnosticSeverity){ return ['error','warning','information','hint'][s]||'unknown'; }
  private async getChangedFiles():Promise<string[]> { try { const git=await vscode.extensions.getExtension('vscode.git')?.exports?.getAPI(1); const repo=git?.repositories?.[0]; if(!repo) return []; await repo.status(); return [...repo.state.workingTreeChanges,...repo.state.indexChanges].map((c:any)=>c.uri.fsPath); } catch { return []; } }
}
