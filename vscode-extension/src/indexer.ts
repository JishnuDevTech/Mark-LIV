import * as vscode from 'vscode';
import * as path from 'path';
export class ProjectIndexer implements vscode.Disposable { private files=new Map<string,{mtime:number;language:string}>(); private watcher?:vscode.FileSystemWatcher; private readonly _onDidChange=new vscode.EventEmitter<void>(); readonly onDidChange=this._onDidChange.event;
  async start(){ if(!vscode.workspace.getConfiguration('jarvis').get('indexing',true)) return; const roots=vscode.workspace.workspaceFolders??[]; for(const root of roots) for(const uri of await vscode.workspace.findFiles(new vscode.RelativePattern(root,'**/*'),'**/{node_modules,.git,dist,build,.venv}/**',10000)) this.files.set(uri.fsPath,{mtime:Date.now(),language:path.extname(uri.fsPath)}); this.watcher=vscode.workspace.createFileSystemWatcher('**/*'); this.watcher.onDidCreate(u=>this.touch(u)); this.watcher.onDidChange(u=>this.touch(u)); this.watcher.onDidDelete(u=>{this.files.delete(u.fsPath);this._onDidChange.fire();}); }
  private touch(u:vscode.Uri){if(/\/(node_modules|\.git|dist|build|\.venv)\//.test(u.fsPath))return;this.files.set(u.fsPath,{mtime:Date.now(),language:path.extname(u.fsPath)});this._onDidChange.fire();}
  snapshot(){const languages:Record<string,number>={};for(const f of this.files.values())languages[f.language]=(languages[f.language]||0)+1;return {fileCount:this.files.size,languages,indexedAt:new Date().toISOString()};}
  dispose(){this.watcher?.dispose();this._onDidChange.dispose();}
}
