"""Aceite do módulo isolado; nenhuma CLI remota é usada nestes testes."""

import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capivara2ptn.providers import CliRunner, Selection
from capivara2ptn.reverse_source import ReverseError, inventory, relevant, redact, source_chunks, validate_roots, known_secrets
from capivara2ptn.reverse_state import ReverseStore
from capivara2ptn.reverse_flow import run_reverse, validate_report, domains_valid, protect_data, discovery_batches
from capivara2ptn.reverse_verify import verify


class FakeRunner:
    selection = Selection('codex', 'fixture', 'Default')
    def __init__(self, fail_at=None, stop_at=None):
        self.calls = []
        self.secrets = []
        self.fail_at = fail_at
        self.stop_at = stop_at
        self.log = lambda text: None
    def safe(self, text):
        for secret in self.secrets:
            text = text.replace(secret, '[OCULTO]')
        return text
    async def invoke(self, instructions, data, **kwargs):
        self.calls.append(data)
        if len(self.calls) == self.fail_at:
            raise ReverseError('Falha simulada do provedor.')
        if len(self.calls) == self.stop_at:
            raise asyncio.CancelledError
        if 'inventory_batch' in data:
            groups = {}
            for item in data['inventory_batch']:
                name = item['path'].split('.')[0].split('/')[0]
                groups.setdefault(name, []).append(item['path'])
            return json.dumps({'overview': 'Sistema de cálculo de preços e estoque.', 'domains': [
                {'name': name, 'purpose': 'Calcula preços com regras explícitas.', 'files': files,
                 'entities': [], 'dependencies': [], 'shared': False}
                for name, files in groups.items()]})
        chunk = data['chunk']
        evidence = [{'path': data['path'], 'start': chunk['start'], 'end': chunk['end'], 'symbol': 'calcular'}]
        return json.dumps({'summary': 'Aplica desconto de dez por cento.',
            'coverage': {'complete': True, 'explanation': 'Cálculo e retorno examinados.'},
            'routines': [{'name': 'calcular', 'behavior': 'Recebe valor, devolve valor * 0.9.',
                          'status': 'confirmado', 'analyzed': True, 'limitation': '', 'evidence': evidence}],
            'findings': [{'category': 'regras', 'status': 'confirmado', 'description': 'Desconto de 10%.', 'evidence': evidence},
                         {'category': 'interfaces', 'status': 'inferido', 'description': 'Uso em interface não observado.', 'evidence': []}],
            'limitations': ['Confirmar arredondamento no INIT.'], 'related_files': [], 'new_domains': []})


async def approve(domains):
    return domains


async def accept_changes(paths, domains):
    return True


def update(state, message):
    pass


class ReverseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='reverse-tests-')
        self.root = Path(self.temporary.name)
        self.dest = self.root / 'novo'
        self.source = self.root / 'legado'
        self.dest.mkdir()
        self.source.mkdir()
        self.config = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'config')})
        self.config.start()
        (self.source / 'precos.py').write_text('def calcular(valor):\n    return valor * 0.9\n')
        (self.source / 'estoque.py').write_text('def reservar(qtd):\n    return qtd > 0\n')
    def tearDown(self):
        self.config.stop()
        self.temporary.cleanup()
    def fingerprint(self):
        return {str(p.relative_to(self.source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob('*') if p.is_file() and not p.is_symlink()}
    async def run_flow(self, runner=None, store=None, **kwargs):
        runner = runner or FakeRunner()
        store = store or ReverseStore(self.dest)
        path = await run_reverse(store, self.source, runner, kwargs.pop('approve', approve),
                                 kwargs.pop('confirm_changes', accept_changes), update, **kwargs)
        return path, store, runner

    def test_equal_nested_symlink_and_harness_roots_rejected(self):
        for dest, source in ((self.dest, self.dest), (self.source / 'child', self.source), (self.dest, self.dest / 'child')):
            dest.mkdir(exist_ok=True)
            source.mkdir(exist_ok=True)
            with self.assertRaises(ReverseError):
                validate_roots(dest, source)
        alias = self.root / 'alias'
        alias.symlink_to(self.dest, target_is_directory=True)
        with self.assertRaises(ReverseError):
            validate_roots(alias, self.dest)
        checkout = Path(__file__).resolve().parents[1]
        with self.assertRaises(Exception):
            validate_roots(checkout, self.source)
        with self.assertRaises(Exception):
            validate_roots(self.dest, checkout)
        with self.assertRaises(ReverseError):
            validate_roots(self.dest, self.root / 'missing')

    def test_inventory_is_deterministic_classifies_hidden_dependencies_and_links(self):
        (self.source / '.env').write_text('SECRET_TOKEN=super-credential\nDB_PASSWORD=senha-original\n')
        (self.source / '.settings.json').write_text('{"token": "super-credential"}')
        deps = self.source / 'node_modules'
        deps.mkdir()
        (deps / 'installed.js').write_text('third-party')
        (self.source / 'external').symlink_to(self.dest, target_is_directory=True)
        before = self.fingerprint()
        first, second = inventory(self.source), inventory(self.source)
        self.assertEqual(first, second)
        entries = {e['path']: e for e in first['entries']}
        self.assertEqual(entries['node_modules']['classification'], 'excluded')
        self.assertTrue(entries['node_modules']['subtree'])
        self.assertNotIn('node_modules/installed.js', entries)
        self.assertEqual(entries['external']['classification'], 'excluded')
        self.assertEqual(entries['.env']['config_keys'], ['DB_PASSWORD', 'SECRET_TOKEN'])
        self.assertIn('configuração', entries['.settings.json']['signals'])
        self.assertNotIn('super-credential', json.dumps(first))
        self.assertEqual(before, self.fingerprint())

    def test_symlink_artifacts_cannot_escape_destination(self):
        store = ReverseStore(self.dest)
        (self.dest / '.harness').symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ReverseError):
            store.write('estado.json', '{}')
        (self.dest / '.harness').unlink()
        store.write('documentacao/ok.md', 'ok')
        store.target('evidencias').symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ReverseError):
            store.write('evidencias/file.md', 'bad')
        with self.assertRaises(ReverseError):
            store.write('../outside.md', 'bad')

    def test_complete_chunks_preserve_lines_and_detect_source_changes(self):
        file = self.source / 'long.py'
        file.write_text('\n'.join('x = ' + str(i) for i in range(8000)))
        entry = next(e for e in relevant(inventory(self.source)) if e['path'] == 'long.py')
        chunks = source_chunks(self.source, entry)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0]['start'], 1)
        self.assertEqual(chunks[-1]['end'], 8000)
        for left, right in zip(chunks, chunks[1:]):
            self.assertEqual(left['end'] + 1, right['start'])
        file.write_text('changed')
        with self.assertRaises(ReverseError):
            source_chunks(self.source, entry)

    def test_redaction_preserves_line_numbers(self):
        text = 'PASSWORD="canary-secret"\nAuthorization: Bearer abcdef\nhttps://user:pass@example.com\n-----BEGIN RSA PRIVATE KEY-----\ncanary-private\n-----END RSA PRIVATE KEY-----\nnext()'
        safe = redact(text)
        for secret in ('canary-secret', 'abcdef', 'user:pass', 'canary-private'):
            self.assertNotIn(secret, safe)
        self.assertEqual(text.count('\n'), safe.count('\n'))

    async def test_full_flow_evidence_prompt_and_read_only_source(self):
        before = self.fingerprint()
        path, store, runner = await self.run_flow()
        self.assertEqual(path, self.dest / '.harness/reverse/prompt-final.md')
        self.assertEqual(store.state['status'], 'done')
        self.assertEqual(len(runner.calls), 3)
        self.assertEqual(before, self.fingerprint())
        self.assertFalse((self.source / '.harness').exists())
        self.assertEqual(set(p.name for p in self.dest.iterdir()), {'.harness'})
        final = path.read_text()
        for text in ('Desconto de 10%.', 'Recebe valor', 'Inferido', 'arredondamento', 'INIT → PLAN → BUILD', 'estoque', 'precos'):
            self.assertIn(text, final)
        self.assertNotIn('Consulte o arquivo', final)
        self.assertEqual(store.state['coverage']['files_pending'], [])
        self.assertEqual(store.state['coverage']['routines_analyzed'], 2)
        self.assertFalse(store.state['coverage']['integral'])
        self.assertGreater(store.elapsed(), 0)
        self.assertIsNone(store.started)

    async def test_failure_and_resume_keep_confirmed_units(self):
        store = ReverseStore(self.dest)
        with self.assertRaises(ReverseError):
            await self.run_flow(FakeRunner(fail_at=3), store)
        store.fail('Falha simulada')
        self.assertEqual(len(store.state['units']), 1)
        resumed = ReverseStore(self.dest)
        resumed.load()
        path, resumed, runner = await self.run_flow(store=resumed)
        self.assertEqual(len(runner.calls), 1)
        self.assertTrue(path.exists())
        self.assertEqual(len(resumed.state['units']), 2)

    async def test_interruption_and_resume(self):
        store = ReverseStore(self.dest)
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow(FakeRunner(stop_at=3), store)
        store.fail('Interrompido', interrupted=True)
        loaded = ReverseStore(self.dest)
        loaded.load()
        self.assertEqual(loaded.state['status'], 'interrupted')
        _, loaded, runner = await self.run_flow(store=loaded)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(loaded.state['status'], 'done')

    async def test_changes_only_reinvestigate_affected_domain(self):
        _, store, _ = await self.run_flow()
        old = {r['path']: r['sha256'] for r in store.state['units'].values()}
        (self.source / 'precos.py').write_text('def calcular(v):\n    return v * 0.8\n')
        calls = []
        async def confirm(paths, names):
            calls.append((paths, names))
            return True
        path, store, runner = await self.run_flow(store=store, confirm_changes=confirm)
        self.assertEqual(calls, [(['precos.py'], ['precos'])])
        self.assertEqual(len([c for c in runner.calls if 'chunk' in c]), 1)
        self.assertEqual(next(r['sha256'] for r in store.state['units'].values() if r['path'] == 'estoque.py'), old['estoque.py'])
        self.assertTrue(path.exists())
        self.assertTrue(any(store.target('historico').iterdir()))

    async def test_source_changes_declined_preserve_previous_prompt_and_units(self):
        path, store, _ = await self.run_flow()
        old_text = path.read_text()
        old_units = dict(store.state['units'])
        (self.source / 'precos.py').write_text('changed')
        async def decline(paths, names):
            return False
        result, store, runner = await self.run_flow(store=store, confirm_changes=decline)
        self.assertIsNone(result)
        self.assertEqual(store.state['units'], old_units)
        self.assertEqual(path.read_text(), old_text)
        self.assertEqual(runner.calls, [])

    async def test_completed_valid_run_makes_no_model_calls(self):
        path, store, _ = await self.run_flow()
        before = path.read_bytes()
        _, store, runner = await self.run_flow(store=store)
        self.assertEqual(runner.calls, [])
        self.assertEqual(before, path.read_bytes())

    async def test_approval_is_required_before_investigation(self):
        async def decline(domains):
            return None
        result, store, runner = await self.run_flow(approve=decline)
        self.assertIsNone(result)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(store.state['units'], {})
        self.assertTrue(store.target('inventario.json').exists())

    async def test_secrets_never_sent_to_model_or_written_in_artifacts(self):
        (self.source / '.env').write_text('API_TOKEN=canary-credential-947\n')
        (self.source / 'precos.py').write_text('API_TOKEN="canary-credential-947"\ndef f():\n    return "canary-credential-947"\n')
        path, store, runner = await self.run_flow()
        self.assertNotIn('canary-credential-947', json.dumps(runner.calls))
        for artifact in store.directory.rglob('*'):
            if artifact.is_file():
                self.assertNotIn('canary-credential-947', artifact.read_text())

    async def test_tampered_artifact_blocks_resume(self):
        _, store, _ = await self.run_flow()
        record = next(iter(store.state['units'].values()))
        store.target(record['artifact']).write_text('{}')
        with self.assertRaises(ReverseError):
            await self.run_flow(store=store)

    async def test_archive_preserves_downstream_and_source(self):
        _, store, _ = await self.run_flow()
        downstream = self.dest / '.harness/dados/plano.md'
        downstream.parent.mkdir()
        downstream.write_text('PLAN original')
        before = self.fingerprint()
        store.archive()
        self.assertIsNone(store.state)
        self.assertEqual(downstream.read_text(), 'PLAN original')
        self.assertEqual(before, self.fingerprint())
        self.assertTrue(list(store.target('historico').rglob('prompt-final.md')))
        await self.run_flow(store=store)
        self.assertEqual(downstream.read_text(), 'PLAN original')

    async def test_optional_verification_cannot_write_legacy_or_use_network(self):
        _, store, _ = await self.run_flow()
        inv = inventory(self.source)
        before = self.fingerprint()
        code = ('import pathlib,socket; '
                'pathlib.Path("marker").write_text("isolated"); '
                'print("copied", pathlib.Path("precos.py").exists()); '
                f'print("legacy-visible", pathlib.Path({str(self.source)!r}).exists()); '
                'print("sandbox")')
        result = await verify(store, inv, 'python3 -c ' + __import__('shlex').quote(code), lambda text: None)
        self.assertEqual(before, self.fingerprint())
        self.assertFalse((self.dest / 'marker').exists())
        if result['status'] == 'observed':
            output = store.target(result['artifact']).read_text()
            self.assertIn('legacy-visible False', output)
            self.assertIn('copied True', output)
        else:
            self.assertIn(result['status'], ('failed', 'unavailable'))
            self.assertTrue(result['limitation'])

    async def test_clock_excludes_user_approval_wait(self):
        async def slow_approval(domains):
            await asyncio.sleep(0.12)
            return domains
        _, store, _ = await self.run_flow(approve=slow_approval)
        self.assertLess(store.elapsed(), 0.12)

    async def test_new_file_does_not_repeat_unrelated_domains(self):
        _, store, _ = await self.run_flow()
        (self.source / 'relatorios.py').write_text('def listar():\n    return []\n')
        _, store, runner = await self.run_flow(store=store)
        analyzed = [call['path'] for call in runner.calls if 'chunk' in call]
        self.assertEqual(analyzed, ['relatorios.py'])
        self.assertEqual(store.state['status'], 'done')

    async def test_removed_file_preserves_unaffected_domain(self):
        _, store, _ = await self.run_flow()
        (self.source / 'precos.py').unlink()
        _, store, runner = await self.run_flow(store=store)
        self.assertEqual([call for call in runner.calls if 'chunk' in call], [])
        self.assertEqual([d['name'] for d in store.state['domains']], ['estoque'])
        self.assertEqual(store.state['coverage']['files_pending'], [])

    async def test_sensitive_configuration_changes_are_detected(self):
        config = self.source / '.env'
        config.write_text('API_TOKEN=credential-old\n')
        _, store, _ = await self.run_flow()
        config.write_text('API_TOKEN=credential-new\n')
        notices = []
        async def changed(paths, names):
            notices.append((paths, names))
            return True
        _, store, runner = await self.run_flow(store=store, confirm_changes=changed)
        self.assertEqual(notices[0][0], ['.env'])
        self.assertEqual(set(notices[0][1]), {'precos', 'estoque'})
        self.assertEqual(len([c for c in runner.calls if 'chunk' in c]), 2)

    async def test_changes_during_analysis_block_final_delivery(self):
        class ChangingRunner(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                result = await super().invoke(instructions, data, **kwargs)
                if 'chunk' in data and data['path'] == 'precos.py':
                    (self.source / 'precos.py').write_text('changed during model response')
                return result
        store = ReverseStore(self.dest)
        with self.assertRaisesRegex(ReverseError, 'mudou durante'):
            await self.run_flow(ChangingRunner(), store)
        self.assertFalse(store.target('prompt-final.md').exists())
        self.assertTrue(store.state['units'])

    async def test_invalid_evidence_is_not_counted_as_coverage(self):
        class InvalidRunner(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                if 'reexamination' in data:
                    raise asyncio.CancelledError
                result = await super().invoke(instructions, data, **kwargs)
                if 'chunk' in data:
                    value = json.loads(result)
                    value['findings'][0]['evidence'][0]['end'] = 9000
                    return json.dumps(value)
                return result
        store = ReverseStore(self.dest)
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow(InvalidRunner(), store)
        self.assertEqual(store.state['units'], {})
        self.assertIn('Evidência', store.state['pending_unit']['reason'])
        self.assertFalse(store.target('prompt-final.md').exists())

    async def test_invalid_model_response_can_be_retried_without_reset(self):
        class InvalidRunner(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                if 'reexamination' in data:
                    raise asyncio.CancelledError
                if 'chunk' in data:
                    return 'invalid json'
                return await super().invoke(instructions, data, **kwargs)
        store = ReverseStore(self.dest)
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow(InvalidRunner(), store)
        store.fail('Formato inválido')
        _, store, runner = await self.run_flow(store=store)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(store.state['status'], 'done')

    async def test_new_domain_discovered_mid_investigation_requires_approval(self):
        class DiscoveryRunner(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                result = await super().invoke(instructions, data, **kwargs)
                if 'chunk' in data and data['domain']['name'] != 'Financeiro':
                    value = json.loads(result)
                    value['new_domains'] = [{'name': 'Financeiro', 'purpose': 'Regra financeira compartilhada',
                        'files': ['precos.py'], 'entities': [], 'dependencies': [], 'shared': False}]
                    return json.dumps(value)
                return result
        approved = []
        async def review(domains):
            approved.append([d['name'] for d in domains])
            return domains
        _, store, runner = await self.run_flow(DiscoveryRunner(), approve=review)
        self.assertEqual(len(approved), 2)
        self.assertIn('Financeiro', approved[-1])
        self.assertEqual(store.state['coverage']['domains_done'], 3)

    async def test_sandbox_hides_env_and_prevents_host_network_access(self):
        _, store, _ = await self.run_flow()
        command = "python3 -c 'import os,socket; print(os.getenv(\"CANARY_HOST_SECRET\", \"clean\")); s=socket.socket(); s.settimeout(0.1); s.connect((\"1.1.1.1\",53))'"
        with patch.dict(os.environ, {'CANARY_HOST_SECRET': 'must-not-inherit'}):
            result = await verify(store, inventory(self.source), command, lambda text: None)
        if result.get('artifact'):
            output = store.target(result['artifact']).read_text()
            self.assertNotIn('must-not-inherit', output)
            self.assertIn('clean', output)
            self.assertNotEqual(result['exit_code'], 0)

    async def test_sandbox_copy_and_output_are_sanitized(self):
        (self.source / '.env').write_text('API_TOKEN=canary-verify-secret\n')
        (self.source / 'precos.py').write_text('print("canary-verify-secret")\n')
        _, store, _ = await self.run_flow()
        result = await verify(store, inventory(self.source), 'python3 precos.py', lambda text: None)
        if result.get('artifact'):
            output = store.target(result['artifact']).read_text()
            self.assertNotIn('canary-verify-secret', output)

    def test_symlink_substitution_in_source_is_refused(self):
        inv = inventory(self.source)
        entry = next(e for e in relevant(inv) if e['path'] == 'precos.py')
        (self.source / 'precos.py').unlink()
        (self.source / 'precos.py').symlink_to(self.dest / 'outside')
        (self.dest / 'outside').write_text('private')
        with self.assertRaises(OSError):
            source_chunks(self.source, entry)

    def test_inventory_extracts_manifests_frameworks_routes_and_data(self):
        (self.source / 'package.json').write_text(json.dumps({
            'dependencies': {'express': '^4.0', 'react': '^18'},
            'scripts': {'start': 'node index.js --password canary-script-secret'}, 'main': 'index.js'}))
        (self.source / 'pyproject.toml').write_text('[project]\ndependencies = ["fastapi>=0.1"]\n[project.scripts]\napp = "app:main"\n')
        (self.source / 'routes.py').write_text('@app.get("/clientes")\ndef listar():\n    return []\n')
        (self.source / 'schema.sql').write_text('CREATE TABLE clientes (id INT PRIMARY KEY);\n')
        inv = inventory(self.source)
        entries = {e['path']: e for e in inv['entries']}
        self.assertEqual(inv['frameworks'], ['Express', 'FastAPI', 'React'])
        self.assertEqual(entries['package.json']['technical']['dependencies'][0]['name'], 'express')
        self.assertTrue(entries['package.json']['technical']['startup'])
        self.assertEqual(entries['routes.py']['technical']['routes'][0]['line'], 1)
        self.assertEqual(entries['routes.py']['technical']['routine_candidates'][0]['symbol'], 'listar')
        self.assertTrue(entries['schema.sql']['technical']['data_definitions'])
        self.assertNotIn('canary-script-secret', json.dumps(inv))

    async def test_new_discovery_survives_interruption_during_approval(self):
        class DiscoveringRunner(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                result = await super().invoke(instructions, data, **kwargs)
                if 'chunk' in data:
                    value = json.loads(result)
                    value['new_domains'] = [{'name': 'Financeiro', 'purpose': 'Regra financeira',
                        'files': ['precos.py'], 'entities': [], 'dependencies': [], 'shared': False}]
                    return json.dumps(value)
                return result
        approvals = 0
        async def interrupted(domains):
            nonlocal approvals
            approvals += 1
            if approvals == 2:
                raise asyncio.CancelledError
            return domains
        store = ReverseStore(self.dest)
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow(DiscoveringRunner(), store, approve=interrupted)
        store.fail('Interrompido no modal', interrupted=True)
        resumed = ReverseStore(self.dest)
        resumed.load()
        self.assertFalse(resumed.state['approved'])
        self.assertIn('Financeiro', [d['name'] for d in resumed.state['domains']])
        _, resumed, runner = await self.run_flow(store=resumed)
        self.assertEqual(resumed.state['coverage']['domains_done'], 3)
        self.assertEqual(len(runner.calls), 2)

    async def test_incomplete_chunk_is_reexamined_with_continuation_and_reason(self):
        file = self.source / 'precos.py'
        file.write_text('\n'.join('x = ' + str(i) for i in range(6000)))
        first_key = []
        class IncompleteOnce(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                text = await super().invoke(instructions, data, **kwargs)
                if 'chunk' in data and data['path'] == 'precos.py' and not first_key:
                    first_key.append(data['chunk']['start'])
                    value = json.loads(text)
                    value['coverage'] = {'complete': False, 'explanation': 'A requisição PATCH continua no próximo trecho.'}
                    value['context_requests'] = [{'path': 'precos.py', 'start': data['chunk']['end'] + 1}]
                    return json.dumps(value)
                if 'reexamination' in data:
                    self.assertIn('PATCH', data['reexamination']['reason'])
                    self.assertTrue(data['supporting_context'])
                    self.assertEqual(data['supporting_context'][0]['start'], data['chunk']['end'] + 1)
                    self.assertTrue(any(c['end'] > data['chunk']['end'] for c in data['supporting_context']))
                return text
        _, store, runner = await self.run_flow(IncompleteOnce())
        self.assertEqual(store.state['status'], 'done')
        self.assertTrue(any('reexamination' in c for c in runner.calls))
        pending = list(store.target('evidencias/pendentes').glob('*.json'))
        self.assertEqual(len(pending), 1)
        self.assertEqual(json.loads(pending[0].read_text())['status'], 'resolved')
        self.assertNotIn('pending_unit', store.state)

    async def test_incomplete_reexamination_is_resumable_after_interruption(self):
        class InterruptedReexamination(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                text = await super().invoke(instructions, data, **kwargs)
                if 'reexamination' in data:
                    raise asyncio.CancelledError
                if 'chunk' in data and data['path'] == 'precos.py':
                    value = json.loads(text)
                    value['coverage'] = {'complete': False, 'explanation': 'Examinar dependência de estoque.'}
                    value['context_requests'] = [{'path': 'estoque.py'}]
                    return json.dumps(value)
                return text
        store = ReverseStore(self.dest)
        with self.assertRaises(asyncio.CancelledError):
            await self.run_flow(InterruptedReexamination(), store)
        store.fail('Interrompido durante reexame', interrupted=True)
        self.assertEqual(len(store.state['units']), 1)
        pending = store.state['pending_unit']
        self.assertIn('estoque', store.read_json(pending['artifact'])['reason'])
        resumed = ReverseStore(self.dest)
        resumed.load()
        _, resumed, runner = await self.run_flow(store=resumed)
        self.assertEqual(len(runner.calls), 1)
        self.assertIn('reexamination', runner.calls[0])
        self.assertEqual(resumed.state['status'], 'done')

    async def test_external_blocker_is_reported_with_reason_and_preserves_coverage(self):
        class ExternallyBlocked(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                text = await super().invoke(instructions, data, **kwargs)
                if 'chunk' in data and data['path'] == 'precos.py':
                    value = json.loads(text)
                    value['coverage'] = {'complete': False, 'explanation': 'Rotina essencial está em pacote ausente.'}
                    value['external_blocker'] = 'Fornecer código do pacote ausente.'
                    return json.dumps(value)
                return text
        store = ReverseStore(self.dest)
        with self.assertRaisesRegex(ReverseError, 'Ação externa necessária.*pacote ausente'):
            await self.run_flow(ExternallyBlocked(), store)
        self.assertEqual(len(store.state['units']), 1)
        self.assertTrue(store.state['pending_unit'])
        self.assertFalse(store.target('prompt-final.md').exists())

    async def test_related_file_evidence_requires_supplied_context(self):
        runner = FakeRunner()
        report = json.loads(await runner.invoke('', {'path': 'precos.py', 'chunk': {'start': 1, 'end': 2}}))
        report['findings'][0]['evidence'] = [{'path': 'estoque.py', 'start': 1, 'end': 2}]
        with self.assertRaises(ReverseError):
            validate_report(report, 'precos.py', {'start': 1, 'end': 2}, {'precos.py', 'estoque.py'})
        validated = validate_report(report, 'precos.py', {'start': 1, 'end': 2}, {'precos.py', 'estoque.py'},
                                    [{'path': 'estoque.py', 'start': 1, 'end': 2}])
        self.assertEqual(validated['findings'][0]['status'], 'confirmado')

    def test_protocol_is_added_without_overwriting_existing_project_prompts(self):
        from capivara2ptn.reverse_prompts import load_reverse_prompt
        store = ReverseStore(self.dest)
        store.begin(self.source)
        load_reverse_prompt('reverse-investigation.md', store)
        store.write('prompts/reverse-investigation.md', 'Instruções personalizadas pelo usuário.')
        text = load_reverse_prompt('reverse-investigation-protocol.md', store)
        self.assertIn('context_requests', text)
        self.assertEqual(load_reverse_prompt('reverse-investigation.md', store), 'Instruções personalizadas pelo usuário.')

    async def test_evidence_can_span_contiguous_chunks_but_not_missing_lines(self):
        runner = FakeRunner()
        report = json.loads(await runner.invoke('', {'path': 'precos.py', 'chunk': {'start': 1, 'end': 2}}))
        report['findings'][0]['evidence'] = [{'path': 'precos.py', 'start': 1, 'end': 4}]
        validated = validate_report(report, 'precos.py', {'start': 1, 'end': 2}, {'precos.py'},
                                    [{'path': 'precos.py', 'start': 3, 'end': 4}])
        self.assertEqual(validated['findings'][0]['evidence'][0]['end'], 4)
        report['findings'][0]['evidence'][0]['end'] = 5
        with self.assertRaisesRegex(ReverseError, 'Intervalos disponíveis'):
            validate_report(report, 'precos.py', {'start': 1, 'end': 2}, {'precos.py'},
                            [{'path': 'precos.py', 'start': 4, 'end': 5}])

    async def test_malformed_response_is_repaired_with_feedback(self):
        class InvalidOnce(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                result = await super().invoke(instructions, data, **kwargs)
                if 'chunk' in data and not getattr(inner, 'invalid_sent', False):
                    inner.invalid_sent = True
                    return 'invalid json'
                return result
        _, store, runner = await self.run_flow(InvalidOnce())
        self.assertEqual(store.state['status'], 'done')
        corrected = next(c for c in runner.calls if 'reexamination' in c)
        self.assertIn('JSON inválido', corrected['reexamination']['reason'])
        self.assertEqual(corrected['reexamination']['previous_report'], 'invalid json')


    def test_public_env_values_are_not_collected_as_credentials(self):
        (self.source / '.env').write_text('APP_ENV=local\nLOG_LEVEL=error\nREVERB_SCHEME=http\nDB_PORT=3306\nDB_DATABASE=cronus\nAPP_KEY=credential-canary\nDB_PASSWORD=password-canary\nSENHA=senha-canary\n')
        values = known_secrets(self.source, inventory(self.source))
        self.assertEqual(set(values), {'credential-canary', 'password-canary', 'senha-canary'})

    def test_redaction_preserves_inventory_paths_and_hashes_but_not_content(self):
        runner = FakeRunner();runner.secrets = ['live', '3306']
        path, checksum = 'app/livewire3306.py', 'a' * 60 + '3306'
        packet = {'inventory_batch': [{'path': path, 'sha256': checksum, 'sample': 'private = "live"'}],
                  'allowed_paths': [path], 'domains': [{'files': [path]}], 'related_files': [path],
                  'untrusted': {'path': 'invented-live.py', 'sha256': 'b' * 60 + '3306'}}
        result = protect_data(packet, runner.safe, {path}, {checksum})
        self.assertEqual(result['inventory_batch'][0]['path'], path)
        self.assertEqual(result['inventory_batch'][0]['sha256'], checksum)
        self.assertEqual(result['allowed_paths'], [path])
        self.assertEqual(result['domains'][0]['files'], [path])
        self.assertEqual(result['related_files'], [path])
        self.assertNotIn('live', result['inventory_batch'][0]['sample'])
        self.assertNotIn('live', result['untrusted']['path'])
        self.assertNotIn('3306', result['untrusted']['sha256'])

    async def test_secret_substrings_do_not_break_file_references_during_investigation(self):
        (self.source / '.env').write_text('APP_ENV=local\nLOG_LEVEL=error\nDB_PASSWORD=live\n')
        (self.source / 'local-livewire-error.py').write_text('def calcular(v):\n    return v * .9\n')
        before = self.fingerprint()
        _, store, runner = await self.run_flow()
        self.assertEqual(store.state['status'], 'done')
        self.assertTrue(any(record['path'] == 'local-livewire-error.py' for record in store.state['units'].values()))
        persisted = store.read_json('inventario.json')
        self.assertEqual(persisted, inventory(self.source))
        self.assertEqual(before, self.fingerprint())

    async def test_invalid_discovery_path_gets_precise_feedback_without_skipping_batch(self):
        class InvalidOnce(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                raw = await super().invoke(instructions, data, **kwargs)
                if 'inventory_batch' in data and 'reexamination' not in data:
                    value = json.loads(raw);value['domains'][0]['files'] = ['inventado.py']
                    return json.dumps(value)
                return raw
        _, store, runner = await self.run_flow(InvalidOnce())
        retry = next(c for c in runner.calls if 'inventory_batch' in c and 'reexamination' in c)
        self.assertIn('inventado.py', retry['reexamination']['reason'])
        self.assertEqual(set(retry['allowed_paths']), {'estoque.py', 'precos.py'})
        self.assertNotIn('inventado.py', store.target('prompt-final.md').read_text())
        self.assertNotIn('pending_discovery', store.state)
        self.assertEqual(next(store.target('evidencias/descoberta').glob('*.json')).exists(), True)

    async def test_empty_discovery_domain_is_reexamined_instead_of_accepted(self):
        class EmptyOnce(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                raw = await super().invoke(instructions, data, **kwargs)
                if 'inventory_batch' in data and 'reexamination' not in data:
                    value = json.loads(raw);value['domains'][0]['files'] = []
                    return json.dumps(value)
                return raw
        _, store, runner = await self.run_flow(EmptyOnce())
        retry = next(c for c in runner.calls if 'inventory_batch' in c and 'reexamination' in c)
        self.assertIn('lista files está vazia', retry['reexamination']['reason'])
        self.assertEqual(store.state['status'], 'done')

    async def test_discovery_resume_keeps_valid_batches_and_pending_feedback(self):
        class PendingSecond(FakeRunner):
            async def invoke(inner, instructions, data, **kwargs):
                if 'inventory_batch' in data and 'reexamination' in data:raise asyncio.CancelledError
                raw = await super().invoke(instructions, data, **kwargs)
                if 'inventory_batch' in data and data['inventory_batch'][0]['path'] == 'precos.py':return 'invalid json'
                return raw
        store = ReverseStore(self.dest)
        def one_file_batches(entries):return [[entry] for batch in discovery_batches(entries) for entry in batch]
        with patch('capivara2ptn.reverse_flow.discovery_batches', side_effect=one_file_batches):
            with self.assertRaises(asyncio.CancelledError):await self.run_flow(PendingSecond(), store)
            store.fail('Interrompido', interrupted=True)
            self.assertEqual(len(store.state['discovery']), 1)
            self.assertIn('JSON inválido', store.state['pending_discovery']['reason'])
            reopened = ReverseStore(self.dest);reopened.load()
            _, reopened, runner = await self.run_flow(store=reopened)
        calls = [c for c in runner.calls if 'inventory_batch' in c]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['inventory_batch'][0]['path'], 'precos.py')
        self.assertEqual(calls[0]['reexamination']['attempt'], 2)
        self.assertEqual(calls[0]['reexamination']['previous_report'], 'invalid json')
        self.assertEqual(reopened.state['status'], 'done')
        self.assertNotIn('pending_discovery', reopened.state)

    def test_discovery_protocol_is_added_without_overwriting_custom_prompt(self):
        from capivara2ptn.reverse_prompts import load_reverse_prompt
        store = ReverseStore(self.dest);store.begin(self.source)
        load_reverse_prompt('reverse-discovery.md', store)
        store.write('prompts/reverse-discovery.md', 'Descoberta personalizada.')
        protocol = load_reverse_prompt('reverse-discovery-protocol.md', store)
        self.assertIn('allowed_paths', protocol)
        self.assertIn('reexamination', protocol)
        self.assertEqual(load_reverse_prompt('reverse-discovery.md', store), 'Descoberta personalizada.')
        self.assertTrue(store.target('prompts/.discovery-protocol-installed').exists())


if __name__ == '__main__':
    unittest.main()
