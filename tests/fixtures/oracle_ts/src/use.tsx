import defaultFn, { helper, outer, Child, Base, type Opts, P } from "@/core";
import { helper as h2 } from "./index";
import * as core from "./core";
import { Card } from "./card";

export function a() {
  return helper();
}

export function b() {
  return outer(helper);
}

export function c(x: Child): Child {
  return x;
}

export function d() {
  return core.helper();
}

export function e() {
  return h2();
}

export function f() {
  return [1].map(helper);
}

export function g(obj: Base) {
  return obj.newMethod();
}

export class K extends Child {
  m() {
    return this.n();
  }
  n() {
    return 1;
  }
}

export function h(o: Opts) {
  return o.backoff();
}

export function i() {
  const cb = () => helper();
  return cb();
}

export function j() {
  return defaultFn();
}

export function k(maybe?: Base) {
  return maybe?.newMethod();
}

export function l() {
  return new Child();
}

function compute() {
  return 1;
}
function handler() {}

export function m1() {
  return <Card value={compute()} onClick={handler} />;
}

export function typeVsValue(fn: typeof helper) {
  return typeof helper === "function";
}

export function anon() {
  return [1].map(() => helper());
}

export function ov1() {
  return core.ov(1);
}

export function useProp(p: P) {
  return p.conf;
}
