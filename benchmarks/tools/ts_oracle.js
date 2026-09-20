#!/usr/bin/env node
/* TypeScript oracle adapter: the TypeScript compiler's own symbol resolution, projected onto Sprint 3's edge vocabulary.

   Pipeline, per source file of a real leaf tsconfig program:
     TS AST node -> resolved symbol identity (checker, aliases followed) -> syntactic relation (call / new / JSX tag / value use /
     extends / type position) -> original lexical owner -> Sprint 3 owner projection -> canonical pair `file::qualname`.

   Every occurrence is emitted with: file, start, end (1-based line, UTF-16 column, the unit TypeScript itself uses), target_id,
   original_owner_id, projected_owner_id, relation, resolution_basis. `binding` means the target is reached through lexical scope,
   an import, `this`/`super`, a class or a namespace; `type_declared` means the receiver's inferred type found it (a POSSIBLE
   target). A function-typed property (`options.backoff`, a parameter, an interface member) resolves to a property, not to a
   function definition, so it creates no edge: which function runs is not proven.

   Usage: node ts_oracle.js --typescript PATH --repo DIR --manifest M.json --out OUT.json --project TSCONFIG [--project TSCONFIG ...]
   A manifest file is assigned to the FIRST --project whose program contains it; projects are given leaf-first, so a package's
   production tsconfig comes before its test tsconfig. Bulletproof-style apps are separate projects and are never merged. */
'use strict';
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const opt = (name, multi) => {
  const vals = [];
  for (let i = 0; i < args.length; i++) if (args[i] === name) vals.push(args[i + 1]);
  return multi ? vals : vals[0];
};
const ts = require(path.resolve(opt('--typescript')));
const repo = path.resolve(opt('--repo'));
const manifest = JSON.parse(fs.readFileSync(opt('--manifest'), 'utf8'));
const projects = opt('--project', true).map((p) => path.resolve(repo, p));
const included = new Set(manifest.included_files);
const rel = (abs) => path.relative(repo, abs).split(path.sep).join('/');

const FN_VALUE = (n) => n && (ts.isArrowFunction(n) || ts.isFunctionExpression(n));
const unwrapInit = (n) => { while (n && (ts.isParenthesizedExpression(n) || ts.isAsExpression(n) || ts.isNonNullExpression(n) || ts.isSatisfiesExpression(n))) n = n.expression; return n; };

// ---------------------------------------------------------------------------------------- pass 1: definitions (mirrors IBWD's indexing rules)
const defs = new Map();          // declaration node -> {id, qualname, kind, nested, file}
const symbols = [];
function collectDefs(sf, file) {
  const add = (node, qualname, kind, nested) => {
    const id = `${file}::${qualname}`;
    const start = ts.getLineAndCharacterOfPosition(sf, node.getStart(sf)).line + 1;
    const end = ts.getLineAndCharacterOfPosition(sf, node.getEnd()).line + 1;
    defs.set(node, { id, qualname, kind, nested, file, start, end });
    symbols.push({ id, file, line: start, end_line: end, name: qualname, kind, nested });
  };
  // scope: {qual} of the innermost enclosing INDEXED function-like; null at file/class level. Anonymous callbacks are transparent.
  const visit = (node, classStack, scope) => {
    ts.forEachChild(node, (child) => {
      if (ts.isFunctionDeclaration(child)) {
        if (child.name) {
          const qual = scope ? `${scope}.<locals>.${child.name.text}` : [...classStack, child.name.text].join('.');
          add(child, qual, scope ? 'Function' : (classStack.length ? 'Method' : 'Function'), !!scope);
          if (child.body) visit(child.body, classStack, qual);
        }
        return;
      }
      if (ts.isClassDeclaration(child)) {
        if (child.name && !scope) {
          const qual = [...classStack, child.name.text].join('.');
          add(child, qual, 'Class', false);
          visit(child, [...classStack, child.name.text], null);
        }
        return;
      }
      if ((ts.isMethodDeclaration(child) || ts.isConstructorDeclaration(child) || ts.isGetAccessor(child) || ts.isSetAccessor(child)) && classStack.length && ts.isClassDeclaration(child.parent)) {
        const name = ts.isConstructorDeclaration(child) ? 'constructor' : (child.name && (ts.isIdentifier(child.name) || ts.isStringLiteral(child.name) || ts.isPrivateIdentifier(child.name)) ? child.name.text : null);
        if (name) {
          const qual = [...classStack, name].join('.');
          add(child, qual, 'Method', false);
          if (child.body) visit(child.body, classStack, qual);
        }
        return;
      }
      if (ts.isPropertyDeclaration(child) && classStack.length && FN_VALUE(unwrapInit(child.initializer)) && child.name && ts.isIdentifier(child.name)) {
        const qual = [...classStack, child.name.text].join('.');
        add(child, qual, 'Method', false);
        visit(unwrapInit(child.initializer), classStack, qual);
        return;
      }
      if (ts.isVariableDeclaration(child) && ts.isIdentifier(child.name) && FN_VALUE(unwrapInit(child.initializer))) {
        const qual = scope ? `${scope}.<locals>.${child.name.text}` : [...classStack, child.name.text].join('.');
        add(child, qual, classStack.length ? 'Method' : 'Function', !!scope);
        visit(unwrapInit(child.initializer), classStack, qual);
        return;
      }
      visit(child, classStack, scope);
    });
  };
  visit(sf, [], null);
}

// ---------------------------------------------------------------------------------------- programs
function loadProgram(tsconfigPath) {
  const cfg = ts.readConfigFile(tsconfigPath, ts.sys.readFile);
  if (cfg.error) throw new Error(ts.flattenDiagnosticMessageText(cfg.error.messageText, '\n'));
  const parsed = ts.parseJsonConfigFileContent(cfg.config, ts.sys, path.dirname(tsconfigPath), undefined, tsconfigPath);
  const program = ts.createProgram({ rootNames: parsed.fileNames, options: { ...parsed.options, noEmit: true }, projectReferences: parsed.projectReferences });
  return { program, options: parsed.options, tsconfigPath };
}

const assignment = new Map();   // rel file -> {project, sf, program, checker}
const loaded = [];
for (const tsconfig of projects) {
  const { program, options } = loadProgram(tsconfig);
  const checker = program.getTypeChecker();
  const info = { tsconfig: rel(tsconfig), program, checker, options, files: 0 };
  for (const sf of program.getSourceFiles()) {
    if (sf.isDeclarationFile) continue;
    const file = rel(sf.fileName);
    if (!included.has(file) || assignment.has(file)) continue;
    assignment.set(file, { info, sf });
    info.files++;
  }
  loaded.push(info);
}
const unassigned = [...included].filter((f) => /\.(tsx?|jsx?|mts|cts)$/.test(f) && !assignment.has(f));

for (const [file, { sf }] of assignment) collectDefs(sf, file);
const definedIds = new Set([...defs.values()].filter((d) => !d.nested).map((d) => d.id));

// ---------------------------------------------------------------------------------------- pass 2: occurrences
const occurrences = [];
const nestedTargets = [];
const importOccurrences = [];

function ownersOf(node, file) {
  const chain = [];
  for (let p = node.parent; p; p = p.parent) { const d = defs.get(p); if (d) chain.push(d); }
  const fnLike = chain.filter((d) => d.kind !== 'Class');
  if (fnLike.length) {
    const original = fnLike[0];
    const projected = fnLike.find((d) => !d.nested) || original;
    return [original.id, projected.id];
  }
  const cls = chain[0];
  return cls ? [cls.id, cls.id] : [file, file];
}

const inTypePosition = (node) => {
  for (let p = node.parent, child = node; p; child = p, p = p.parent) {
    if (ts.isExpressionWithTypeArguments(p) && p.parent && ts.isHeritageClause(p.parent) && p.parent.token === ts.SyntaxKind.ExtendsKeyword && p.parent.parent && ts.isClassLike(p.parent.parent)) return false; // class extends: a value
    if (ts.isTypeNode(p)) return true;
    if (ts.isStatement(p) || ts.isBlock(p) || ts.isSourceFile(p)) return false;
    if (ts.isExpression(p) && !ts.isPropertyAccessExpression(p) && !ts.isIdentifier(p) && !ts.isQualifiedName(p)) return false;
  }
  return false;
};

const isDeclarationName = (id) => {
  const p = id.parent;
  if (!p) return false;
  if ((ts.isVariableDeclaration(p) || ts.isFunctionDeclaration(p) || ts.isClassDeclaration(p) || ts.isMethodDeclaration(p) || ts.isParameter(p) || ts.isPropertyDeclaration(p)
    || ts.isBindingElement(p) || ts.isEnumMember(p) || ts.isTypeAliasDeclaration(p) || ts.isInterfaceDeclaration(p) || ts.isPropertySignature(p) || ts.isMethodSignature(p)
    || ts.isGetAccessor(p) || ts.isSetAccessor(p) || ts.isEnumDeclaration(p) || ts.isModuleDeclaration(p) || ts.isTypeParameterDeclaration(p) || ts.isFunctionExpression(p) || ts.isClassExpression(p)
    || ts.isNamespaceExport(p) || ts.isNamespaceImport(p) || ts.isImportClause(p) || ts.isImportSpecifier(p) || ts.isExportSpecifier(p) || ts.isPropertyAssignment(p) || ts.isJsxAttribute(p)
    || ts.isLabeledStatement(p) || ts.isImportEqualsDeclaration(p)) && p.name === id) return true;
  if ((ts.isImportSpecifier(p) || ts.isExportSpecifier(p) || ts.isBindingElement(p)) && p.propertyName === id) return true;
  return false;
};

function resolveTarget(checker, id, expr) {
  let sym;
  if (id.parent && ts.isShorthandPropertyAssignment(id.parent) && id.parent.name === id) sym = checker.getShorthandAssignmentValueSymbol(id.parent);
  else sym = checker.getSymbolAtLocation(id);
  if (!sym) return null;
  if (sym.flags & ts.SymbolFlags.Alias) { try { sym = checker.getAliasedSymbol(sym); } catch (e) { return null; } }
  for (const decl of sym.declarations || []) {
    const d = defs.get(decl);
    if (d) return d;
  }
  return null;
}

function receiverBasis(checker, access) {
  const obj = access.expression;
  if (obj.kind === ts.SyntaxKind.ThisKeyword || obj.kind === ts.SyntaxKind.SuperKeyword) return 'binding';
  if (ts.isIdentifier(obj)) {
    let s = checker.getSymbolAtLocation(obj);
    if (s && (s.flags & ts.SymbolFlags.Alias)) { try { s = checker.getAliasedSymbol(s); } catch (e) { /* keep */ } }
    if (s && (s.flags & (ts.SymbolFlags.Class | ts.SymbolFlags.Enum | ts.SymbolFlags.Module | ts.SymbolFlags.NamespaceModule | ts.SymbolFlags.ValueModule | ts.SymbolFlags.Function))) return 'binding';
  }
  return 'type_declared';
}

function classify(id) {
  // the expression node standing for this identifier's use: a bare name, or the property access it is the member of
  let expr = id, member = null;
  if (id.parent && ts.isPropertyAccessExpression(id.parent) && id.parent.name === id) { member = id.parent; expr = id.parent; }
  else if (id.parent && ts.isPropertyAccessExpression(id.parent) && id.parent.expression === id) { /* head of a chain: an ordinary use of the name */ }
  let up = expr;
  while (up.parent && (ts.isParenthesizedExpression(up.parent) || ts.isNonNullExpression(up.parent) || ts.isAsExpression(up.parent) || ts.isTypeAssertionExpression(up.parent) || ts.isSatisfiesExpression(up.parent))) up = up.parent;
  const p = up.parent;
  if (!p) return { relation: 'REFERENCES', member };
  if (ts.isCallExpression(p) && p.expression === up) return { relation: 'CALLS', member };
  if (ts.isNewExpression(p) && p.expression === up) return { relation: 'CALLS', member };
  if (ts.isTaggedTemplateExpression(p) && p.tag === up) return { relation: 'CALLS', member };
  if (ts.isDecorator(p) && p.expression === up) return { relation: 'CALLS', member };
  if ((ts.isJsxOpeningElement(p) || ts.isJsxSelfClosingElement(p)) && p.tagName === up) return { relation: 'CALLS', member };
  if (ts.isJsxClosingElement(p)) return { relation: 'SKIP', member };
  // assignments to a name/property are writes, not uses
  if (ts.isBinaryExpression(p) && p.left === up && p.operatorToken.kind === ts.SyntaxKind.EqualsToken) return { relation: 'SKIP', member };
  if (ts.isExportAssignment(p) && p.expression === up) return { relation: 'REFERENCES', member };
  return { relation: 'REFERENCES', member };
}

function moduleFileFor(program, options, sf, specifier) {
  const r = ts.resolveModuleName(specifier, sf.fileName, options, ts.sys);
  const resolved = r.resolvedModule && r.resolvedModule.resolvedFileName;
  return resolved && !r.resolvedModule.isExternalLibraryImport ? rel(resolved) : null;
}

function isTypeOnlyImport(node) {
  if (ts.isImportDeclaration(node)) {
    const c = node.importClause;
    if (!c) return false;
    if (c.isTypeOnly) return true;
    if (c.namedBindings && ts.isNamedImports(c.namedBindings) && !c.name) return c.namedBindings.elements.length > 0 && c.namedBindings.elements.every((e) => e.isTypeOnly);
    return false;
  }
  if (ts.isExportDeclaration(node)) return !!node.isTypeOnly;
  return false;
}

for (const [file, { info, sf }] of assignment) {
  const { checker, options, program } = info;
  const at = (pos) => { const lc = ts.getLineAndCharacterOfPosition(sf, pos); return [lc.line + 1, lc.character]; };
  const emit = (node, relation, basis, targetId, extra) => {
    const [oOwner, pOwner] = ownersOf(node, file);
    occurrences.push({ file, start: at(node.getStart(sf)), end: at(node.getEnd()), target_id: targetId, original_owner_id: oOwner, projected_owner_id: pOwner, relation, resolution_basis: basis, class_id: (extra && extra.class_id) || null });
  };
  const walk = (node) => {
    if (ts.isImportDeclaration(node) || (ts.isExportDeclaration(node) && node.moduleSpecifier)) {
      const spec = node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier) ? node.moduleSpecifier.text : null;
      const target = spec ? moduleFileFor(program, options, sf, spec) : null;
      if (target && target !== file && included.has(target)) importOccurrences.push({ file, start: at(node.getStart(sf)), end: at(node.getEnd()), target_id: target, original_owner_id: file, projected_owner_id: file, relation: 'IMPORTS', resolution_basis: isTypeOnlyImport(node) ? 'type_only' : 'path', class_id: null });
      return;                                     // the specifier names are not separate uses
    }
    if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(node.expression) && node.expression.text === 'require')) && node.arguments.length && ts.isStringLiteralLike(node.arguments[0])) {
      const target = moduleFileFor(program, options, sf, node.arguments[0].text);
      if (target && target !== file && included.has(target)) importOccurrences.push({ file, start: at(node.getStart(sf)), end: at(node.getEnd()), target_id: target, original_owner_id: ownersOf(node, file)[0], projected_owner_id: ownersOf(node, file)[1], relation: 'IMPORTS', resolution_basis: 'path', class_id: null });
    }
    if (ts.isIdentifier(node) && !isDeclarationName(node)) {
      handleIdentifier(node);
    }
    ts.forEachChild(node, walk);
  };

  const handleIdentifier = (id) => {
    if (id.parent && (ts.isExportSpecifier(id.parent) || ts.isImportSpecifier(id.parent))) return;
    const typePos = inTypePosition(id);
    const target = resolveTarget(checker, id);
    if (!target) return;
    const c = classify(id);
    // `class X extends Base`: the heritage expression is INHERITS from the class
    let heritage = null;
    for (let p = id.parent; p; p = p.parent) {
      if (ts.isExpressionWithTypeArguments(p) && p.parent && ts.isHeritageClause(p.parent) && p.parent.token === ts.SyntaxKind.ExtendsKeyword && p.parent.parent && ts.isClassDeclaration(p.parent.parent)) { heritage = p.parent.parent; break; }
      if (ts.isStatement(p) || ts.isBlock(p)) break;
    }
    if (heritage && !(id.parent && ts.isPropertyAccessExpression(id.parent) && id.parent.expression === id && false)) {
      const owner = defs.get(heritage);
      if (owner && !(id.parent && ts.isPropertyAccessExpression(id.parent) && id.parent.expression === id)) {
        emit(id, 'INHERITS', 'binding', target.id, { class_id: owner.id });
        return;
      }
      return;
    }
    if (typePos) { emit(id, 'TYPE_USE', 'binding', target.id); return; }
    if (c.relation === 'SKIP') return;
    // `Base.member` where Base is the head of a chain: the head is a use of the class/namespace; the member is its own occurrence
    const basis = c.member ? receiverBasis(checker, c.member) : 'binding';
    if (target.nested) { nestedTargets.push({ file, start: at(id.getStart(sf)), end: at(id.getEnd()), target_id: target.id, original_owner_id: ownersOf(id, file)[0], projected_owner_id: ownersOf(id, file)[1], relation: c.relation, resolution_basis: basis, note: 'target is a nested definition, not an indexed symbol; never replaced by its enclosing function' }); return; }
    emit(id, c.relation, basis, target.id);
  };
  walk(sf);
}

// ---------------------------------------------------------------------------------------- edges
const all = [...occurrences, ...importOccurrences];
const edges = new Map();
for (const o of all) {
  if (o.relation === 'TYPE_USE') continue;
  const source = o.relation === 'IMPORTS' ? o.file : (o.relation === 'INHERITS' ? o.class_id : o.projected_owner_id);
  if (source === o.target_id && o.relation !== 'CALLS') continue;
  if (o.relation !== 'IMPORTS' && !definedIds.has(o.target_id)) continue;
  const key = `${source}\u0000${o.target_id}\u0000${o.relation}`;
  const e = edges.get(key) || { source, target: o.target_id, relation: o.relation, basis: new Set(), file: o.file, line: o.start[0] };
  e.basis.add(o.resolution_basis);
  edges.set(key, e);
}
const outEdges = [...edges.values()].map((e) => ({ ...e, basis: e.basis.size === 1 && e.basis.has('type_declared') ? 'type_declared' : (e.basis.has('path') ? 'path' : (e.basis.size === 1 && e.basis.has('type_only') ? 'type_only' : 'binding')) }))
  .sort((a, b) => (a.source + a.target + a.relation < b.source + b.target + b.relation ? -1 : 1));
for (const f of assignment.keys()) symbols.push({ id: f, file: f, line: 1, name: path.basename(f), kind: 'File' });

const out = {
  oracle: `TypeScript compiler ${ts.version} (checker symbol resolution)`, adapter: 'benchmarks/tools/ts_oracle.js',
  manifest_sha256: manifest.manifest_sha256, repo_sha: manifest.repo_sha, text_encoding: 'UTF16',
  complete: false, status: 'static semantic oracle; runtime dispatch not covered; type_declared edges are POSSIBLE targets',
  projects: loaded.map((l) => ({ tsconfig: l.tsconfig, files: l.files })), unassigned_files: unassigned,
  symbols, edges: outEdges, occurrences: all, nested_target_occurrences: nestedTargets,
};
fs.writeFileSync(opt('--out'), JSON.stringify(out, null, 1) + '\n');
const byRel = {};
for (const e of outEdges) byRel[e.relation] = (byRel[e.relation] || 0) + 1;
const callBasis = { binding: 0, type_declared: 0 };
for (const e of outEdges) if (e.relation === 'CALLS') callBasis[e.basis === 'type_declared' ? 'type_declared' : 'binding']++;
console.log(`${symbols.length} symbols, ${all.length} occurrences | edges ${JSON.stringify(byRel)} | CALLS by basis ${JSON.stringify(callBasis)} | nested-target occurrences ${nestedTargets.length} | unassigned files ${unassigned.length}`);
