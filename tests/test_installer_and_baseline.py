"""New 100% baseline, config migration, and installed startup layout."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from test_cat_app import APP_MODULE
from test_pet_startup import MemoryRegistry
from pet_startup import StartupManager, INSTALL_MARKER, INSTALL_MARKER_CONTENT


class SizeBaselineTests(unittest.TestCase):
    def test_new_default_and_saved_old_85_are_both_new_100(self):
        app = APP_MODULE.DesktopPet
        self.assertEqual(APP_MODULE.DEFAULT_PET_SCALE, 1)
        self.assertEqual(app._saved_pet_scale({}), 1)
        self.assertEqual(app._saved_pet_scale({"pet_scale": .85}), 1)
        self.assertEqual(app._saved_pet_scale({"pet_scale": 1, "pet_size_baseline_version": 2}), 1)

    def test_migration_runs_once_and_preserves_weather_and_ai_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            app = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
            app.settings_path = Path(temp)/"settings.json"
            app.settings_path.write_text(json.dumps({"pet_scale": .85, "weather_city": "青岛",
                "endpoint": "https://api.deepseek.com", "model": "deepseek-chat"}), encoding="utf-8")
            app.settings = app._load_settings()
            self.assertEqual(app.settings["pet_scale"], 1)
            app._save_settings_file()
            again = app._load_settings()
            self.assertEqual(again["pet_scale"], 1)
            self.assertEqual(again["pet_size_baseline_version"], 2)
            self.assertEqual(again["weather_city"], "青岛")
            self.assertEqual(again["endpoint"], "https://api.deepseek.com")

    def test_new_100_renders_old_85_and_hover_uses_same_scale(self):
        app = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        app.width, app.height = 360, 430
        app.window_width, app.window_height = 360, 430
        app.pet_scale = 1
        app.settings = {}
        app.root = Mock()
        app.root.winfo_x.return_value = app.root.winfo_y.return_value = 100
        app.root.winfo_screenwidth.return_value = 1920
        app.root.winfo_screenheight.return_value = 1080
        app._create_surface_bitmap = Mock()
        app._apply_pet_scale(1)
        self.assertEqual(app.render_scale, .85)
        self.assertEqual((app.window_width, app.window_height), (306, 366))
        app.current_sprite_bbox = (20, 112, 340, 412)
        self.assertTrue(app._hover_over_pet(170, 200))
        self.assertFalse(app._hover_over_pet(310, 200))

    def test_invalid_saved_values_fall_back_and_legacy_extremes_stay_bounded(self):
        for value in (None, "bad", "nan", "inf"):
            self.assertEqual(APP_MODULE.DesktopPet._saved_pet_scale({"pet_scale": value}), 1)
        self.assertEqual(APP_MODULE.DesktopPet._saved_pet_scale({"pet_scale": 1.5}), 1.5)

    def test_real_size_dialog_displays_and_restores_100(self):
        root = tk.Tk(); root.withdraw()
        self.addCleanup(root.destroy)
        app = APP_MODULE.DesktopPet.__new__(APP_MODULE.DesktopPet)
        app.root, app.language, app.size_window = root, "zh-CN", None
        app.pet_scale, app._size_resize_job = 1.25, None
        app._apply_pet_scale, app._save_settings_file = Mock(), Mock()
        original_toplevel = tk.Toplevel
        def hidden(parent):
            window = original_toplevel(parent); window.withdraw(); return window
        with patch.object(APP_MODULE.tk, "Toplevel", side_effect=hidden):
            app.open_size_settings()
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)
        widgets = list(children(app.size_window))
        restore = next(w for w in widgets if str(w.winfo_class()) == "TButton" and w.cget("text") == "恢复默认")
        restore.invoke()
        scale = next(w for w in widgets if isinstance(w, tk.Scale))
        self.assertEqual(scale.get(), 100)
        finish = next(w for w in widgets if str(w.winfo_class()) == "TButton" and w.cget("text") == "完成")
        finish.invoke()
        app._apply_pet_scale.assert_called_once_with(1)


class InstalledStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / "Programs/猫咪/PawpalCat.exe"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"packaged fixture")
        self.exe.with_name(INSTALL_MARKER).write_text(INSTALL_MARKER_CONTENT, encoding="utf-8")
        self.registry = MemoryRegistry()

    def manager(self, kind="onefile"):
        return StartupManager(self.exe, self.root/"startup-data", self.registry,
                              frozen=True, bundle_kind=kind)

    def test_installed_layout_registers_in_place_and_never_copies(self):
        manager = self.manager()
        status = manager.enable()
        self.assertTrue(manager.installed)
        self.assertTrue(status.enabled)
        self.assertEqual(status.path, self.exe.resolve())
        self.assertFalse((self.root/"startup-data/app").exists())
        manager.disable()
        self.assertTrue(self.exe.is_file())

    def test_onedir_requires_internal_dependencies_before_registering(self):
        manager = self.manager("onedir")
        self.assertFalse(manager.enable().enabled)
        self.assertEqual(self.registry.writes, [])
        internal = self.exe.parent/"_internal"
        internal.mkdir()
        import sys
        (internal/f"python{sys.version_info.major}{sys.version_info.minor}.dll").write_bytes(b"fixture")
        (internal/"base_library.zip").write_bytes(b"fixture")
        self.assertTrue(manager.enable().enabled)

    def test_arbitrary_marker_and_source_run_do_not_change_portable_behavior(self):
        self.exe.with_name(INSTALL_MARKER).write_text("wrong marker", encoding="utf-8")
        self.assertFalse(self.manager().installed)
        self.exe.with_name(INSTALL_MARKER).write_text(INSTALL_MARKER_CONTENT, encoding="utf-8")
        manager = StartupManager(self.exe, self.root/"source", self.registry, frozen=False)
        self.assertFalse(manager.installed)


if __name__ == "__main__":
    unittest.main()
