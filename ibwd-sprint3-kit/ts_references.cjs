#!/usr/bin/env node
// TypeScript Language Service reference export. Run with repo's TypeScript via NODE_PATH.
// node ts_references.cjs ROOT TSCONFIG FILE LINE ZERO_BASED_COLUMN > references.json
const ts=require('typescript'),fs=require('fs'),path=require('path'),crypto=require('crypto');
const [rootArg,configArg,fileArg,lineArg,colArg]=process.argv.slice(2);
const root=path.resolve(rootArg),config=path.resolve(root,configArg),file=path.resolve(root,fileArg);
const read=ts.readConfigFile(config,ts.sys.readFile);
if(read.error) throw Error(ts.flattenDiagnosticMessageText(read.error.messageText,'\n'));
const parsed=ts.parseJsonConfigFileContent(read.config,ts.sys,path.dirname(config));
const host={getScriptFileNames:()=>parsed.fileNames,getScriptVersion:()=> '0',getScriptSnapshot:f=>fs.existsSync(f)?ts.ScriptSnapshot.fromString(fs.readFileSync(f,'utf8')):undefined,getCurrentDirectory:()=>root,getCompilationSettings:()=>parsed.options,getDefaultLibFileName:o=>ts.getDefaultLibFilePath(o),fileExists:ts.sys.fileExists,readFile:ts.sys.readFile,readDirectory:ts.sys.readDirectory,directoryExists:ts.sys.directoryExists,getDirectories:ts.sys.getDirectories};
const ls=ts.createLanguageService(host),program=ls.getProgram(),sf=program.getSourceFile(file);
if(!sf)throw Error('Target file is not in this tsconfig program. Select the leaf app config.');
const pos=sf.getPositionOfLineAndCharacter(Number(lineArg)-1,Number(colArg));
const groups=ls.findReferences(file,pos)||[],result=[];
for(const group of groups)for(const r of group.references){
 const f=program.getSourceFile(r.fileName);if(!f)continue;
 const rel=path.relative(root,r.fileName);if(rel.startsWith('..')||path.isAbsolute(rel))continue;
 const lc=f.getLineAndCharacterOfPosition(r.textSpan.start);
 let token=null;function visit(n){if(n.getStart(f)<=r.textSpan.start&&n.end>=r.textSpan.start+r.textSpan.length){token=n;ts.forEachChild(n,visit)}}visit(f);
 let ancestors=[];for(let n=token;n;n=n.parent)ancestors.push(n);
 const closing=ancestors.some(ts.isJsxClosingElement);
 const jsx=ancestors.some(n=>ts.isJsxOpeningElement(n)||ts.isJsxSelfClosingElement(n));
 const syntax=closing?'JSX_CLOSING':jsx?'JSX_USE':ancestors.some(n=>ts.isImportDeclaration(n)||ts.isImportEqualsDeclaration(n))?'IMPORT':ancestors.some(ts.isTypeNode)?'TYPE_USE':'REFERENCE_REQUIRES_CLASSIFICATION';
 result.push({file:rel.split(path.sep).join('/'),line:lc.line+1,column:lc.character,length:r.textSpan.length,definition:!!r.isDefinition,write:!!r.isWriteAccess,syntax,source_line_sha256:crypto.createHash('sha256').update(f.text.split(/\r?\n/)[lc.line]).digest('hex')});
}
const diagnostics=ts.getPreEmitDiagnostics(program).map(d=>({file:d.file?path.relative(root,d.file.fileName):null,code:d.code,message:ts.flattenDiagnosticMessageText(d.messageText,'\n')}));
console.log(JSON.stringify({oracle:'TypeScript.LanguageService.findReferences',version:ts.version,config:path.relative(root,config),references:result,diagnostics,complete:false,note:'Audit diagnostics, classify call/value/export/type use, merge leaf project outputs and deduplicate ranges. Closing JSX tags are not extra uses.'},null,2));
