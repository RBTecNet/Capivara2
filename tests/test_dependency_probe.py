"""Pacote instalado com exports restritos não deve causar reinstalação nem bloqueio falso."""
import json
from pathlib import Path
import shutil
import sys
import unittest

from harness.build_artifacts import BuildStore,DependenciesBlocked
from harness.build_engine import BuildEngine
from harness.build_models import CommandSpec
from harness.dependency_probe import repair_metadata_check
from build_fixture import Builders
import test_build


def package(root,name,kind='commonjs'):
    path=root/'node_modules'/name;path.mkdir(parents=True,exist_ok=True)
    manifest={'name':name,'version':'1.0.0','type':kind,'exports':{'.':'./index.js'}}
    if name.startswith('@types/'):
        manifest['exports']={'.':{'types':'./index.d.ts'}}
        (path/'index.d.ts').write_text('export declare const value: number;\n')
    else:(path/'index.js').write_text('export const value=1;\n' if kind=='module' else 'module.exports=1;\n')
    (path/'package.json').write_text(json.dumps(manifest))


def dependency(names,install):
    code=json.dumps(names)+".forEach(m=>require.resolve(m+'/package.json'))"
    return {'name':'Pacotes do projeto','purpose':'Bibliotecas e ferramentas necessárias.',
        'check':{'argv':['node','-e',code],'purpose':'Verificar instalação dos pacotes.','timeout':10,'env':[]},
        'install':install,'manual_instructions':'Instalar os pacotes declarados.'}


@unittest.skipUnless(shutil.which('node'),'Node necessário para verificar a resolução real de exports.')
class DependencyProbeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=test_build.BuildTests.asyncSetUp

    async def test_installed_commonjs_esm_and_types_skip_install_and_clear_old_pending_instructions(self):
        names=['@example/private-meta','esm-tool','@types/contract']
        for name,kind in zip(names,['commonjs','module','module']):package(self.root,name,kind)
        marker=self.root/'should-not-reinstall'
        install=[{'argv':[sys.executable,'-c',f'from pathlib import Path; Path({str(marker)!r}).write_text("reinstalled")'],
                  'purpose':'Instalar somente se realmente ausente.','timeout':10,'env':[]}]
        store=BuildStore(self.root);store.begin(self.state)
        store.json('control/missing-dependencies.json',[{'dependency':{'name':'Pendência antiga'}}])
        store.write('install-manual.sh','#!/bin/sh\necho instrução anterior\n')
        store.write('manual-install.md','Histórico da instrução anterior.');store.close()
        agents=Builders();agents.dependencies=[dependency(names,install)]
        engine=BuildEngine(self.state,agents);await engine.run()
        self.assertFalse(marker.exists())
        self.assertEqual(engine.saved['environment']['status'],'completed')
        self.assertEqual(json.loads((self.root/'.harness/build/control/missing-dependencies.json').read_text()),[])
        self.assertFalse((self.root/'.harness/build/install-manual.sh').exists())
        self.assertTrue(list((self.root/'.harness/build/control/resolved-dependencies').glob('*/install-manual.sh')))
        repairs=list((self.root/'.harness/build/control/dependency-probes').glob('*.json'))
        self.assertTrue(repairs);self.assertEqual(json.loads(repairs[0].read_text())['exit_code'],0)

    async def test_first_install_recheck_repairs_hidden_manifest_without_manual_block(self):
        folder=self.root/'node_modules/new-library'
        manifest=json.dumps({'name':'new-library','version':'1.0.0','exports':{'.':'./index.js'}})
        code=f'from pathlib import Path; p=Path({str(folder)!r}); p.mkdir(parents=True,exist_ok=True); (p/"package.json").write_text({manifest!r}); (p/"index.js").write_text("module.exports=1;")'
        install=[{'argv':[sys.executable,'-c',code],'purpose':'Instalar biblioteca ausente.','timeout':10,'env':[]}]
        agents=Builders();agents.dependencies=[dependency(['new-library'],install)]
        engine=BuildEngine(self.state,agents);await engine.run()
        self.assertEqual(engine.saved['environment']['status'],'completed')
        self.assertTrue(list((self.root/'.harness/build/control/dependency-probes').glob('*-recheck.json')))

    async def test_genuinely_missing_package_is_still_reported(self):
        package(self.root,'installed-package')
        agents=Builders();agents.dependencies=[dependency(['installed-package','absent-package'],[])]
        engine=BuildEngine(self.state,agents)
        with self.assertRaises(DependenciesBlocked):await engine.run()
        missing=json.loads((self.root/'.harness/build/control/missing-dependencies.json').read_text())
        self.assertIn('absent-package',missing[0]['error'])
        self.assertNotIn('ERR_PACKAGE_PATH_NOT_EXPORTED',missing[0]['error'])
        self.assertFalse((self.root/'dobro.py').exists())

    async def test_nonmetadata_export_error_is_not_bypassed(self):
        store=BuildStore(self.root)
        command=CommandSpec(argv=['node','-e',"require.resolve('library/private')"],purpose='Verificar entrada real.')
        result={'stderr':"ERR_PACKAGE_PATH_NOT_EXPORTED: Package subpath './private' is not defined by exports in library/package.json",'stdout':''}
        self.assertIsNone(repair_metadata_check(command,result,store))
