export function helper() {
  return 1;
}

export function outer(fn: () => number) {
  return fn();
}

export class Base {
  newMethod() {
    return 1;
  }
}

export class Child extends Base {}

export default function defaultFn() {
  return 2;
}

export function make() {
  function inner() {
    return helper();
  }
  return inner;
}

export class P {
  get conf() {
    return helper();
  }
  set conf(v: number) {
    outer(() => v);
  }
}

export function ov(x: number): number;
export function ov(x: string): string;
export function ov(x: any) {
  return helper();
}

export function defaultBackoff() {
  return 1;
}

export type Opts = { backoff: typeof defaultBackoff };

export const label = "héllo→世界😀"; export const touched = helper();
