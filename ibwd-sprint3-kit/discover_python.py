#!/usr/bin/env python3
"""Syntax-only discovery of question candidates, NOT an independent semantic oracle.
No fuzzy matching: resolves module imports and same-module top-level functions only.
Output is explicitly unverified; scope/shadowing/decorators require oracle review.
"""
import ast,collections,json,pathlib,sys
root=pathlib.Path(sys.argv[1]).resolve();audit=json.loads(pathlib.Path(sys.argv[2]).read_text());symbols={};trees={};imports={};byname={};owners={}
for f in audit['production_files']:
 if not f.endswith('.py'):continue
 try:tree=ast.parse((root/f).read_text())
 except (SyntaxError,UnicodeError):continue
 mod=f[:-3].replace('/','.');mod=mod.removesuffix('.__init__');trees[f]=tree
 im={}
 for n in tree.body:
  if isinstance(n,ast.Import):
   for a in n.names:im[a.asname or a.name.split('.')[0]]=a.name if a.asname else a.name.split('.')[0]
  elif isinstance(n,ast.ImportFrom):
   base=n.module or ''
   if n.level:
    package=mod if f.endswith('__init__.py') else mod.rsplit('.',1)[0]
    bits=package.split('.');base='.'.join(bits[:len(bits)-n.level+1]+([base] if base else []))
   for a in n.names:
    if a.name!='*':im[a.asname or a.name]=base+'.'+a.name
 imports[f]=(mod,im)
 def collect(nodes,prefix=''):
  for n in nodes:
   if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
    sid=f+':'+str(n.lineno)+':'+prefix+n.name
    symbols[sid]={'id':sid,'file':f,'line':n.lineno,'name':prefix+n.name};owners[sid]=n;byname[mod+'.'+prefix+n.name]=sid
   elif isinstance(n,ast.ClassDef):collect(n.body,prefix+n.name+'.')
 collect(tree.body)
edges=[]
def dotted(n):
 if isinstance(n,ast.Name):return n.id
 if isinstance(n,ast.Attribute):
  a=dotted(n.value);return a+'.'+n.attr if a else None
for sid,body in owners.items():
 f=symbols[sid]['file'];mod,im=imports[f]
 class Visitor(ast.NodeVisitor):
  def visit_FunctionDef(self,n):
   if n is body:
    for x in n.body:self.visit(x)
  visit_AsyncFunctionDef=visit_FunctionDef
  def visit_ClassDef(self,n):pass
  def visit_Call(self,n):
   name=dotted(n.func)
   if name:
    first,*rest=name.split('.');target=im.get(first,mod+'.'+first)+('.'+'.'.join(rest) if rest else '')
    if target in byname:edges.append({'source':sid,'target':byname[target],'relation':'CALLS','file':f,'line':n.lineno,'status':'unverified_syntax_candidate'})
   self.generic_visit(n)
 Visitor().visit(body)
print(json.dumps({'symbols':list(symbols.values()),'edges':edges,'complete':False,'status':'unverified_syntax_candidates_only'},indent=2))
