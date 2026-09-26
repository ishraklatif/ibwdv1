'use strict';
// Opt-in installed compiler only. No package manager or project code execution.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const [compiler, root, project, file, lineText, columnText, output] = process.argv.slice(2);
const ts = require(compiler);
const config = ts.readConfigFile(project, ts.sys.readFile);
const parsed = config.error ? {fileNames: [], options: {}, errors: [config.error]} :
  ts.parseJsonConfigFileContent(config.config, ts.sys, path.dirname(project), undefined, project);
const program = ts.createProgram({rootNames: parsed.fileNames, options: {...parsed.options, noEmit: true}, projectReferences: parsed.projectReferences});
const checker = program.getTypeChecker();
const source = program.getSourceFile(file);
const relative = f => path.relative(root, f).split(path.sep).join('/');
const diagnostics = [...parsed.errors, ...ts.getPreEmitDiagnostics(program)];
const result = {status: 'unknown', compiler: 'typescript', version: ts.version,
  project: relative(project), project_files: parsed.fileNames.length,
  diagnostics: diagnostics.slice(0, 50).map(d => ({code: d.code, category: d.category,
    file: d.file ? relative(d.file.fileName) : null, message: ts.flattenDiagnosticMessageText(d.messageText, '\n')})),
  diagnostics_truncated: diagnostics.length > 50, items: [], truncated: false,
  certainty: 'symbol references and possible runtime targets; never proof of dispatch or test coverage'};
if (source) {
  const line = Number(lineText) - 1, column = Number(columnText) - 1;
  const starts = source.getLineStarts();
  if (line < 0 || line >= starts.length || column < 0 || starts[line] + column >= (starts[line + 1] || source.end)) throw new Error('Position outside source');
  const position = starts[line] + column;
  let selected;
  function locate(node) {
    if (node.getStart(source) <= position && position < node.getEnd()) {
      if (ts.isIdentifier(node)) selected = node;
      ts.forEachChild(node, locate);
    }
  }
  locate(source);
  const resolve = node => {
    let symbol = checker.getSymbolAtLocation(node);
    if (symbol && (symbol.flags & ts.SymbolFlags.Alias)) symbol = checker.getAliasedSymbol(symbol);
    return symbol;
  };
  const target = selected && resolve(selected);
  if (target) {
    result.status = diagnostics.length ? 'incomplete' : 'available';
    const declarations = new Set(target.declarations || []);
    const sameTarget = node => {
      const symbol = resolve(node);
      return symbol === target || (symbol && (symbol.declarations || []).some(d => declarations.has(d)));
    };
    let visited = 0;
    for (const sf of program.getSourceFiles()) {
      if (sf.isDeclarationFile || relative(sf.fileName).startsWith('../')) continue;
      const visit = node => {
        if (++visited > 200000 || result.items.length >= 200) { result.truncated = true; return; }
        if (ts.isIdentifier(node) && sameTarget(node)) {
          const at = sf.getLineAndCharacterOfPosition(node.getStart(sf));
          result.items.push({file: relative(sf.fileName), line: at.line + 1, column: at.character + 1,
            relation: 'SYMBOL_REFERENCE', resolution_status: 'possible', provenance: 'typescript_checker'});
        }
        ts.forEachChild(node, visit);
      };
      visit(sf);
      if (result.truncated) break;
    }
  } else result.reason = 'No compiler symbol at this position';
} else result.reason = 'File is outside the selected project';
// Fingerprint the actual compiler program, including declaration dependencies.
// A concurrent edit cannot silently produce apparently current evidence.
const hashes = [];
for (const sf of program.getSourceFiles()) {
  const digest = text => crypto.createHash('sha256').update(text).digest('hex');
  const hash = digest(sf.text);
  if (!fs.existsSync(sf.fileName) || digest(fs.readFileSync(sf.fileName, 'utf8')) !== hash) {
    result.status = 'unknown'; result.reason = 'Compiler sources changed during query'; result.items = [];
  }
  hashes.push([relative(sf.fileName), hash]);
}
result.program_sha256 = crypto.createHash('sha256').update(JSON.stringify(hashes.sort())).digest('hex');
result.program_file_count = hashes.length;
fs.writeFileSync(output, JSON.stringify(result));
