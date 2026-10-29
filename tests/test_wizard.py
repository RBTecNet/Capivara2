import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from textual.widgets import Button, Input, OptionList, Static, TextArea

from harness.app import (ChoiceStep, ConfirmStep, DoneStep, FilePromptStep,
                         HarnessApp, ManualPromptStep, ProjectStep)
from harness.catalog import Catalog, read_codex_cache
from harness.domain import Model, PROVIDERS, read_prompt_file, resolve_project


def test_catalog() -> Catalog:
    return Catalog(Path("test-catalog.toml"), {
        provider: (Model(f"{provider}-a", "Modelo A", ("low", "high")),
                   Model(f"{provider}-b", "Modelo B", ("medium",)))
        for provider in PROVIDERS
    }, {provider: "Catálogo de teste" for provider in PROVIDERS})


class FileAndCatalogTests(unittest.TestCase):
    def test_relative_paths_and_utf8_bom_preserve_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "prompt.txt").write_text("\ufeffPrimeira linha\nSegunda linha\n", encoding="utf-8")
            path, text = read_prompt_file("prompt.txt", root)
            self.assertEqual(path, root / "prompt.txt")
            self.assertEqual(text, "Primeira linha\nSegunda linha\n")
            self.assertEqual(resolve_project(".", root), root)
            with self.assertRaises(ValueError):
                resolve_project("nao-existe", root)

    def test_invalid_prompt_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty.txt").write_text(" \n\t")
            (root / "binary.txt").write_bytes(b"\xff\xfe")
            for value in ("", "missing.txt", "empty.txt", "binary.txt", "."):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    read_prompt_file(value, root)

    def test_configured_models_and_efforts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harness.toml"
            path.write_text('[providers.claude]\nmodels = [{ id = "custom", efforts = ["low", "high"] }]\n')
            catalog = Catalog.load(path)
            self.assertEqual(catalog.models["claude"][0].efforts, ("low", "high"))
            self.assertEqual(catalog.models["agy"], ())
            for efforts in ('[]', '["low", "low"]', '[1]'):
                path.write_text(f'[providers.codex]\nmodels = [{{ id = "custom", efforts = {efforts} }}]\n')
                with self.assertRaises(ValueError):
                    Catalog.load(path)

    def test_codex_cache_filters_hidden_models_and_recovers_from_invalid_cache(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"CODEX_HOME": directory}):
            cache = Path(directory) / "models_cache.json"
            cache.write_text(json.dumps({"models": [
                {"slug": "visible", "display_name": "Visible", "visibility": "list",
                 "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
                {"slug": "hidden", "visibility": "hide"},
                {"slug": "visible"},
                None,
            ]}))
            self.assertEqual(read_codex_cache(), (Model("visible", "Visible", ("low", "high")),))
            cache.write_text("invalid json")
            self.assertEqual(read_codex_cache(), ())


class WizardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def app(self, catalog=None):
        return HarnessApp(launch_dir=self.root, catalog=catalog or test_catalog())

    async def manual_prompt(self, app, pilot):
        await pilot.press("enter", "enter", "enter")
        self.assertIsInstance(app.screen, ManualPromptStep)
        await pilot.press("P", "r", "o", "j", "e", "t", "o", "enter", "T", "U", "I")
        self.assertIsInstance(app.screen, ManualPromptStep)
        self.assertEqual(app.screen.query_one(TextArea).text, "Projeto\nTUI")
        await pilot.press("ctrl+d")
        self.assertEqual(app.screen.kind, "provider")

    async def test_manual_multiline_shared_reviewer_and_execute_boundary(self):
        app = self.app()
        with patch("subprocess.Popen", side_effect=AssertionError("Nenhum CLI deve ser iniciado")):
            async with app.run_test(size=(100, 36)) as pilot:
                self.assertEqual(app.screen.query_one(Input).value, str(self.root))
                await self.manual_prompt(app, pilot)
                await pilot.press("enter", "enter", "down", "enter")
                self.assertEqual(app.state.writer.effort, "high")
                self.assertEqual(app.screen.kind, "reuse")
                await pilot.press("enter")
                self.assertIsInstance(app.screen, ConfirmStep)
                self.assertEqual(app.state.reviewer, app.state.writer)
                self.assertEqual(app.state.prompt, "Projeto\nTUI")
                await pilot.press("enter")
                self.assertIsInstance(app.screen, DoneStep)
                self.assertTrue(app.state.execute_requested)
                self.assertIn("ainda não foi implementado", str(app.screen.query_one("#hint", Static).render()))
                self.assertEqual(list(self.root.iterdir()), [])
                await pilot.click("#back")
                self.assertIsInstance(app.screen, ConfirmStep)
                self.assertFalse(app.state.execute_requested)
                await pilot.press("enter")
                self.assertIsInstance(app.screen, DoneStep)
                await pilot.click("#close")
        self.assertIs(app.return_value, app.state)

    async def test_independent_reviewer_and_do_not_execute(self):
        app = self.app()
        async with app.run_test(size=(90, 32)) as pilot:
            await self.manual_prompt(app, pilot)
            await pilot.press("enter", "enter", "enter", "down", "enter")
            self.assertEqual(app.screen.kind, "provider")
            self.assertEqual(app.screen.role, "reviewer")
            await pilot.press("down", "enter", "down", "enter", "enter")
            self.assertIsInstance(app.screen, ConfirmStep)
            self.assertEqual(app.state.writer.provider, "codex")
            self.assertEqual(app.state.reviewer.provider, "claude")
            self.assertEqual(app.state.reviewer.model, "claude-b")
            self.assertEqual(app.state.reviewer.effort, "medium")
            await pilot.press("down", "enter")
            self.assertFalse(app.state.execute_requested)
            self.assertIsInstance(app.screen, DoneStep)

    async def test_file_prompt_uses_selected_project_and_recovers_from_missing_file(self):
        project = self.root / "meu projeto"
        project.mkdir()
        prompt = "Um projeto com acentos\ne múltiplas linhas.\n"
        (project / "prompt.txt").write_text(prompt, encoding="utf-8")
        app = self.app()
        async with app.run_test(size=(80, 24)) as pilot:
            app.screen.query_one(Input).value = str(project)
            await pilot.press("enter", "enter", "down", "enter")
            self.assertIsInstance(app.screen, FilePromptStep)
            app.screen.query_one(Input).value = "missing.txt"
            await pilot.press("enter")
            self.assertIsInstance(app.screen, FilePromptStep)
            self.assertIn("Não foi possível ler", str(app.screen.query_one("#error", Static).render()))
            app.screen.query_one(Input).value = "prompt.txt"
            await pilot.press("enter")
            self.assertEqual(app.state.prompt, prompt)
            self.assertEqual(app.state.prompt_file, project / "prompt.txt")
            await pilot.press("enter", "enter", "enter", "enter")
            self.assertIsInstance(app.screen, ConfirmStep)

    async def test_back_keeps_draft_and_allows_changing_writer(self):
        app = self.app()
        async with app.run_test(size=(80, 24)) as pilot:
            await self.manual_prompt(app, pilot)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, ManualPromptStep)
            self.assertEqual(app.screen.query_one(TextArea).text, "Projeto\nTUI")
            self.assertEqual(app.focused.id, "prompt")
            await pilot.press("ctrl+d", "enter", "enter", "enter", "enter")
            self.assertIsInstance(app.screen, ConfirmStep)
            await pilot.press("escape", "escape", "escape")
            self.assertEqual(app.screen.kind, "model")
            await pilot.press("down", "enter", "enter", "enter")
            self.assertIsInstance(app.screen, ConfirmStep)
            self.assertEqual(app.state.writer.model, "codex-b")
            self.assertEqual(app.state.reviewer, app.state.writer)

    async def test_empty_prompt_and_future_options(self):
        app = self.app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("escape")
            self.assertIsInstance(app.screen, ProjectStep)
            app.screen.query_one(Input).value = "missing-folder"
            await pilot.press("enter")
            self.assertIsInstance(app.screen, ProjectStep)
            app.screen.query_one(Input).value = str(self.root)
            await pilot.press("enter")
            menu = app.screen.query_one(OptionList)
            self.assertTrue(menu.get_option("plan").disabled)
            self.assertTrue(menu.get_option("build").disabled)
            await pilot.press("enter")
            self.assertTrue(app.screen.query_one(OptionList).get_option("mcp").disabled)
            await pilot.press("enter", "ctrl+d")
            self.assertIsInstance(app.screen, ManualPromptStep)
            self.assertIn("Escreva o prompt", str(app.screen.query_one("#error", Static).render()))
            await pilot.press("ctrl+q")

    async def test_every_provider_and_missing_model_catalog(self):
        app = self.app()
        async with app.run_test(size=(80, 24)) as pilot:
            await self.manual_prompt(app, pilot)
            for provider in PROVIDERS:
                menu = app.screen.query_one(OptionList)
                menu.highlighted = list(PROVIDERS).index(provider)
                await pilot.press("enter", "enter", "enter", "enter")
                self.assertIsInstance(app.screen, ConfirmStep)
                self.assertEqual(app.state.writer.provider, provider)
                await pilot.press("escape", "escape", "escape", "escape")
                self.assertEqual(app.screen.kind, "provider")
            app.catalog.models["agy"] = ()
            app.screen.query_one(OptionList).highlighted = 3
            await pilot.press("enter")
            self.assertEqual(app.screen.kind, "provider")
            self.assertIn("Nenhum modelo configurado", str(app.screen.query_one("#error", Static).render()))


if __name__ == "__main__":
    unittest.main()
