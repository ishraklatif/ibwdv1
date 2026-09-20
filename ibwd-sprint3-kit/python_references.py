#!/usr/bin/env python3
"""Jedi reference oracle for an explicit symbol position; emits exact references.
Usage python python_references.py ROOT FILE LINE ZERO_BASED_COLUMN > refs.json
Definitions are retained and labelled; do not treat all references as calls.
"""
import json,pathlib,sys,jedi
root=pathlib.Path(sys.argv[1]).resolve();file=root/sys.argv[2]
project=jedi.Project(str(root));script=jedi.Script(path=str(file),project=project)
refs=script.get_references(int(sys.argv[3]),int(sys.argv[4]),scope='project',include_builtins=False)
items=[]
for r in refs:
 if r.module_path is None:continue
 try:p=r.module_path.resolve().relative_to(root)
 except ValueError:continue
 lines=r.module_path.read_text().splitlines()
 items.append({'file':str(p),'line':r.line,'column':r.column,'name':r.name,'definition':r.is_definition(),'source_line_sha256':__import__('hashlib').sha256(lines[r.line-1].encode()).hexdigest()})
print(json.dumps({'oracle':'jedi','version':jedi.__version__,'references':items,'complete':False,'note':'Audit unresolved imports and classify each use; absence is not proof.'},indent=2))
