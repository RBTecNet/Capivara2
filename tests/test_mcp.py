import asyncio
import base64
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from textual.widgets import Input, OptionList, Static

from harness.app import ConfirmStep, HarnessApp, ManualPromptStep
from harness.mcp_client import MCPService
from harness.mcp_registry import MCPError, MCPRegistry, make_instance, resolve_api_key
from harness.mcp_ui import MCPFormStep, MCPInstancesStep, MCPProjectsStep
from mcp_fixture import MCPFixture


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = MCPRegistry(self.root / "config" / "mcps.json")

    def test_instances_persist_separately_even_at_the_same_url(self):
        self.assertEqual(self.registry.instances(), ())
        self.assertFalse(self.registry.path.exists())
        first = make_instance("Equipe A", "http://localhost:7777/mcp", "tenant-a")
        second = make_instance("Equipe B", first.url, "tenant-b")
        self.registry.save(first)
        self.registry.save(second)
        self.assertEqual(MCPRegistry(self.registry.path).instances(), (first, second))
        self.assertEqual(stat.S_IMODE(self.registry.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.registry.path.parent.stat().st_mode), 0o700)
        self.assertNotIn("tenant-a", repr(first))
        self.assertNotIn("tenant-b", repr(second))
        replacement = make_instance("Equipe A nova", first.url, "new-key", instance_id=first.id)
        self.registry.save(replacement)
        self.assertEqual(self.registry.instances(), (replacement, second))
        with self.assertRaises(MCPError):
            self.registry.save(make_instance("equipe b", first.url, "other-key"))

    def test_invalid_registry_is_preserved(self):
        self.registry.path.parent.mkdir()
        self.registry.path.write_text("invalid JSON")
        with self.assertRaises(MCPError):
            self.registry.save(make_instance("Equipe", "http://localhost/mcp", "key"))
        self.assertEqual(self.registry.path.read_text(), "invalid JSON")

    def test_api_key_references_and_endpoint_validation(self):
        (self.root / "key.txt").write_text("\ufefffile-key\n", encoding="utf-8")
        instance = make_instance("Equipe", "http://example.test:7777/mcp", "@key.txt", base_dir=self.root)
        self.assertEqual(resolve_api_key(instance), "file-key")
        self.assertEqual(instance.api_key, f"@{self.root / 'key.txt'}")
        with patch.dict("os.environ", {"TEST_MCP_KEY": "env-key"}):
            self.assertEqual(resolve_api_key(make_instance("Equipe", instance.url, "env:TEST_MCP_KEY")), "env-key")
        for url in ("", "file:///tmp/x", "http://user:secret@example.test/mcp", "http://example.test/mcp?secret=1", "http://example.test:bad/mcp"):
            with self.subTest(url=url), self.assertRaises(MCPError):
                make_instance("Equipe", url, "key")
        with self.assertRaises(MCPError):
            resolve_api_key(make_instance("Equipe", instance.url, "two tokens"))


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = MCPFixture()
        self.addCleanup(self.fixture.close)
        self.service = MCPService(timeout=5)
        self.first = make_instance("Equipe A", self.fixture.url, "tenant-a")
        self.second = make_instance("Equipe B", self.fixture.url, "tenant-b")

    async def test_paginated_listing_and_tenant_isolation(self):
        first = await self.service.projects(self.first)
        second = await self.service.projects(self.second)
        self.assertEqual({p.slug for p in first}, {"shared", "outro"})
        self.assertEqual({p.slug for p in second}, {"shared"})
        self.assertEqual(second[0].name, "Projeto tenant-b")
        self.assertTrue(any(call[1].get("params", {}).get("cursor") for call in self.fixture.calls))
        a, b = await asyncio.gather(
            self.service.load_project(self.first, next(p for p in first if p.slug == "shared")),
            self.service.load_project(self.second, second[0]),
        )
        self.assertIn("tenant-a", a.prompt)
        self.assertIn("tenant-b", b.prompt)
        self.assertNotIn("tenant-a", repr(b))
        self.assertNotEqual(a.instance_id, b.instance_id)

    async def test_full_documents_attachments_and_no_unselected_material(self):
        project = (await self.service.projects(self.second))[0]
        context = await self.service.load_project(self.second, project)
        self.assertEqual(context.count("skill"), 1)
        self.assertEqual(context.count("hook"), 1)
        self.assertEqual(context.count("memoria"), 1)
        self.assertEqual(context.attachments, 2)
        self.assertEqual(len(context.documents), 5)
        skill = next(doc for doc in context.documents if doc.kind == "skill" and not doc.attachment)
        self.assertGreater(len(skill.contents[0].text), 25_000)
        image = next(doc for doc in context.documents if doc.uri.endswith("imagem.png"))
        self.assertEqual(base64.b64decode(image.contents[0].blob), b"binary-reference")
        read_uris = [call[1].get("params", {}).get("uri", "") for call in self.fixture.calls if call[1]["method"] == "resources/read"]
        self.assertFalse(any("geral" in uri or "historico" in uri for uri in read_uris))
        self.assertFalse(any(call[1]["method"] == "tools/call" for call in self.fixture.calls))

    async def test_invalid_token_and_partial_import_are_reported(self):
        with self.assertRaisesRegex(MCPError, "autenticação"):
            await self.service.validate(make_instance("Equipe", self.fixture.url, "invalid-key"))
        project = (await self.service.projects(self.second))[0]
        self.fixture.error_uri = "capivara://shared/docs/hook/check"
        with self.assertRaises(MCPError):
            await self.service.load_project(self.second, project)

    async def test_prompt_fallback_sse_and_empty_instance(self):
        self.fixture.prompt_only = True
        self.fixture.sse = True
        project = (await self.service.projects(self.second))[0]
        context = await self.service.load_project(self.second, project)
        self.assertEqual(context.prompt, "Prompt tenant-b\nSegunda linha")
        self.fixture.empty = True
        self.assertEqual(await self.service.projects(self.second), ())
        await self.service.validate(self.second)

    async def test_timeout_is_bounded_and_empty_prompt_does_not_advance(self):
        self.fixture.delay = 0.3
        with self.assertRaisesRegex(MCPError, "tempo"):
            await MCPService(timeout=0.05).validate(self.second)
        self.fixture.delay = 0
        original = self.fixture.text
        self.fixture.text = lambda uri, token: " " if uri.endswith("/pedido") else original(uri, token)
        project = (await self.service.projects(self.second))[0]
        with self.assertRaisesRegex(MCPError, "prompt preenchido"):
            await self.service.load_project(self.second, project)


class MCPWizardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = MCPFixture()
        self.addCleanup(self.fixture.close)
        self.registry = MCPRegistry(self.root / "user-config" / "mcps.json")

    def app(self):
        return HarnessApp(launch_dir=self.root, mcp_registry=self.registry, mcp_service=MCPService(timeout=5))

    async def open_instances(self, app, pilot):
        await pilot.press("enter", "enter", "down", "down", "enter")
        self.assertIsInstance(app.screen, MCPInstancesStep)

    async def wait_workers(self, app, pilot):
        async with asyncio.timeout(10):
            while True:
                await pilot.pause()
                if not app.workers:
                    return
                await app.workers.wait_for_complete()

    def fill_form(self, app, name, token):
        app.screen.query_one("#mcp-name", Input).value = name
        app.screen.query_one("#mcp-url", Input).value = self.fixture.url
        app.screen.query_one("#mcp-key", Input).value = token

    async def test_add_validate_retry_persist_two_tenants_and_continue_wizard(self):
        app = self.app()
        async with app.run_test(size=(100, 38)) as pilot:
            await self.open_instances(app, pilot)
            self.assertEqual(app.screen.query_one(OptionList).option_count, 1)
            await pilot.press("enter")
            self.assertIsInstance(app.screen, MCPFormStep)
            self.assertTrue(app.screen.query_one("#mcp-key", Input).password)
            self.fill_form(app, "Equipe A", "invalid-key")
            await pilot.press("ctrl+s")
            await self.wait_workers(app, pilot)
            self.assertIsInstance(app.screen, MCPFormStep)
            self.assertFalse(self.registry.path.exists())
            self.assertEqual(app.screen.query_one("#mcp-name", Input).value, "Equipe A")
            self.assertIn("Altere os dados", str(app.screen.query_one("#error", Static).render()))
            self.assertTrue(app.screen.query_one("#error", Static).display)
            app.screen.query_one("#mcp-key", Input).value = "tenant-a"
            await pilot.press("ctrl+s")
            await self.wait_workers(app, pilot)
            self.assertIsInstance(app.screen, MCPInstancesStep)
            self.assertEqual(app.screen.query_one(OptionList).option_count, 2)
            await pilot.press("down", "enter")
            self.fill_form(app, "Equipe B", "tenant-b")
            await pilot.press("ctrl+s")
            await self.wait_workers(app, pilot)
            self.assertEqual(len(self.registry.instances()), 2)
            self.assertEqual(app.screen.query_one(OptionList).option_count, 3)
            await pilot.press("enter")
            await self.wait_workers(app, pilot)
            self.assertIsInstance(app.screen, MCPProjectsStep)
            self.assertEqual(set(app.screen.projects), {"shared"})
            self.assertTrue(app.screen.query_one("#mcp-status", Static).display)
            await pilot.press("enter")
            await self.wait_workers(app, pilot)
            self.assertEqual(app.screen.kind, "provider")
            self.assertEqual(app.state.prompt_source, "mcp")
            self.assertEqual(app.state.prompt, "Prompt tenant-b\nSegunda linha")
            self.assertEqual(app.state.mcp_context.count("skill"), 1)
            self.assertEqual(app.state.mcp_context.instance_name, "Equipe B")
            await pilot.press("enter", "enter", "enter", "enter")
            self.assertIsInstance(app.screen, ConfirmStep)
            summaries = "\n".join(str(widget.render()) for widget in app.screen.query(".summary"))
            self.assertIn("Equipe B", summaries)
            self.assertIn("1 skills", summaries)
            self.assertEqual(list(self.root.iterdir()), [self.registry.path.parent])
        self.assertEqual(len(MCPRegistry(self.registry.path).instances()), 2)

    async def test_expired_instance_can_be_edited_without_duplicate_and_manual_clears_context(self):
        instance = make_instance("Equipe", self.fixture.url, "tenant-a")
        self.registry.save(instance)
        self.fixture.revoked.add("tenant-a")
        app = self.app()
        async with app.run_test(size=(80, 24)) as pilot:
            await self.open_instances(app, pilot)
            await pilot.press("enter")
            await self.wait_workers(app, pilot)
            self.assertIsInstance(app.screen, MCPProjectsStep)
            self.assertIn("autenticação", str(app.screen.query_one("#error", Static).render()))
            self.assertTrue(app.screen.query_one("#error", Static).display)
            await pilot.press("down", "enter")
            self.assertIsInstance(app.screen, MCPFormStep)
            self.assertEqual(app.screen.query_one("#mcp-name", Input).value, "Equipe")
            app.screen.query_one("#mcp-key", Input).value = "tenant-b"
            await pilot.press("ctrl+s")
            await self.wait_workers(app, pilot)
            self.assertIsInstance(app.screen, MCPProjectsStep)
            self.assertEqual(self.registry.instances()[0].id, instance.id)
            self.assertEqual(len(self.registry.instances()), 1)
            await pilot.press("enter")
            await self.wait_workers(app, pilot)
            self.assertEqual(app.state.prompt, "Prompt tenant-b\nSegunda linha")
            await pilot.press("escape")
            await self.wait_workers(app, pilot)
            await pilot.press("escape", "escape", "home", "enter")
            self.assertIsInstance(app.screen, ManualPromptStep)
            await pilot.press("ctrl+d")
            self.assertIsNone(app.state.mcp_context)
            self.assertEqual(app.state.prompt_source, "manual")

    async def test_back_during_validation_keeps_tui_responsive_and_does_not_save(self):
        app = self.app()
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_validation(instance):
            started.set()
            await release.wait()

        with patch.object(app.mcp_service, "validate", side_effect=slow_validation):
            async with app.run_test(size=(80, 24)) as pilot:
                await self.open_instances(app, pilot)
                await pilot.press("enter")
                self.fill_form(app, "Equipe", "tenant-a")
                await pilot.press("ctrl+s")
                await asyncio.wait_for(started.wait(), timeout=2)
                self.assertTrue(app.screen.busy)
                await pilot.press("escape")
                self.assertIsInstance(app.screen, MCPInstancesStep)
                release.set()
                await self.wait_workers(app, pilot)
                self.assertFalse(self.registry.path.exists())


if __name__ == "__main__":
    unittest.main()
