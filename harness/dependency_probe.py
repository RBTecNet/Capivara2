"""Reparo de sondagem npm: metadados instalados não precisam ser exportados pelo pacote."""
from pathlib import Path
import re

from .artifacts import atomic_write


NPM_METADATA_PROBE = r"""
const Module = require('node:module');
const fs = require('node:fs');
const path = require('node:path');
const original = Module._resolveFilename;
Module._resolveFilename = function(request, parent, isMain, options) {
  try { return original.apply(this, arguments); }
  catch (error) {
    if (error.code !== 'ERR_PACKAGE_PATH_NOT_EXPORTED' ||
        typeof request !== 'string' || !request.endsWith('/package.json')) throw error;
    const name = request.slice(0, -'/package.json'.length);
    if (!/^(@[^/]+\/)?[^/]+$/.test(name)) throw error;
    const origins = options && Array.isArray(options.paths) ? options.paths :
      [parent && parent.filename ? path.dirname(parent.filename) : process.cwd()];
    for (const origin of origins) {
      const local = Module.createRequire(path.resolve(origin, '__capivara_metadata_probe__.cjs'));
      for (const directory of local.resolve.paths(name) || []) {
        const file = path.join(directory, name, 'package.json');
        if (fs.existsSync(file) && fs.statSync(file).isFile()) {
          JSON.parse(fs.readFileSync(file, 'utf8'));
          return file;
        }
      }
    }
    throw error;
  }
};
"""


def repair_metadata_check(command,result,store):
    """Só reparar o erro de exports do metadado; outros erros seguem intactos."""
    observed=result.get('stderr','')+'\n'+result.get('stdout','')
    if (Path(command.argv[0]).name not in ('node','nodejs') or
        'ERR_PACKAGE_PATH_NOT_EXPORTED' not in observed or
        not re.search(r"Package subpath ['\"]\./package\.json['\"]",observed)):
        return None
    helper=store.path('control/dependency-probes/npm-metadata.cjs')
    if str(helper) in command.argv:return None
    atomic_write(helper,NPM_METADATA_PROBE)
    return command.model_copy(update={'argv':[command.argv[0],'--require',str(helper),*command.argv[1:]],
        'purpose':command.purpose+' (metadados npm instalados, incluindo package.json não exportado).'})
