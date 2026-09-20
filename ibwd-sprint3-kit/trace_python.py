#!/usr/bin/env python3
"""Observe Python->Python calls during pytest (single process + new threads).
Run inside the repo venv: python trace_python.py REPO OUT.json -- tests -q
Does not observe child processes/C calls or prove absence. Async resumes may repeat events.
"""
import collections,json,pathlib,sys,threading
root=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]).resolve()
args=sys.argv[3:];args=args[1:] if args and args[0]=='--' else args
calls=collections.Counter();lock=threading.Lock()
def rel(filename):
 try:return str(pathlib.Path(filename).resolve().relative_to(root))
 except (ValueError,OSError):return None
def profile(frame,event,arg):
 if event!='call' or frame.f_back is None:return
 caller=frame.f_back;a=rel(caller.f_code.co_filename);b=rel(frame.f_code.co_filename)
 if a is None or b is None:return
 key=(a,caller.f_code.co_firstlineno,caller.f_code.co_qualname,caller.f_lineno,b,frame.f_code.co_firstlineno,frame.f_code.co_qualname)
 with lock:calls[key]+=1
import pytest
code=1
try:
 threading.setprofile(profile);sys.setprofile(profile);code=pytest.main(args)
finally:
 sys.setprofile(None);threading.setprofile(None)
 out.write_text(json.dumps({'repo':str(root),'pytest_exit_code':int(code),'scope':'current process; Python calls; new threading threads','calls':[dict(zip(['caller_file','caller_definition_line','caller','callsite_line','callee_file','callee_definition_line','callee'],k),count=v) for k,v in sorted(calls.items())]},indent=2)+'\n')
sys.exit(int(code))
