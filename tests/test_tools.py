"""Tests for pure helpers in `tools.py` (image ratio, prompt, HTML sanitize, themes)."""

import pytest

import tools as T


# ── detect_optimal_ratio ────────────────────────────────────────────────
class TestDetectRatio:
    @pytest.mark.parametrize("prompt", [
        "a portrait of a man", "beautiful woman face", "human character",
        "selfie photo", "full body figure",
    ])
    def test_portrait(self, prompt):
        assert T.detect_optimal_ratio(prompt) == "9:16"

    @pytest.mark.parametrize("prompt", [
        "mountain landscape", "ocean at sunset", "city street",
        "forest panorama", "desert horizon",
    ])
    def test_landscape(self, prompt):
        assert T.detect_optimal_ratio(prompt) == "16:9"

    @pytest.mark.parametrize("prompt", [
        "a product shot", "company logo", "an icon", "a watch", "shoe design",
    ])
    def test_object(self, prompt):
        assert T.detect_optimal_ratio(prompt) == "1:1"

    def test_default(self):
        assert T.detect_optimal_ratio("abstract concept of joy") == "4:3"

    def test_case_insensitive(self):
        assert T.detect_optimal_ratio("PORTRAIT OF A KING") == "9:16"

    def test_portrait_priority_over_landscape(self):
        # portrait keyword checked first
        assert T.detect_optimal_ratio("person in a landscape") == "9:16"


# ── enhance_prompt ──────────────────────────────────────────────────────
class TestEnhancePrompt:
    def test_appends_style(self):
        out = T.enhance_prompt("a cat", "anime")
        assert out.startswith("a cat, ")
        assert "anime" in out

    def test_unknown_style_falls_back_to_realistic(self):
        out = T.enhance_prompt("a cat", "nonexistent")
        assert "photorealistic" in out

    def test_default_style(self):
        out = T.enhance_prompt("a dog")
        assert "photorealistic" in out

    @pytest.mark.parametrize("style", list(T.STYLE_PRESETS.keys()))
    def test_all_styles(self, style):
        out = T.enhance_prompt("thing", style)
        assert out.startswith("thing, ")
        assert len(out) > len("thing, ")


# ── STYLE_PRESETS integrity ──────────────────────────────────────────────
class TestStylePresets:
    def test_has_expected_count(self):
        assert len(T.STYLE_PRESETS) == 8

    def test_realistic_present(self):
        assert "realistic" in T.STYLE_PRESETS

    def test_all_values_nonempty_str(self):
        for v in T.STYLE_PRESETS.values():
            assert isinstance(v, str) and v


# ── _sanitize_html ───────────────────────────────────────────────────────
class TestSanitizeHtml:
    def test_removes_script(self):
        out = T._sanitize_html("<p>ok</p><script>alert(1)</script>")
        assert "script" not in out.lower()
        assert "ok" in out

    def test_removes_iframe(self):
        out = T._sanitize_html('<iframe src="evil"></iframe>hi')
        assert "iframe" not in out.lower()

    def test_removes_object(self):
        out = T._sanitize_html("<object data='x'></object>")
        assert "object" not in out.lower()

    def test_removes_embed(self):
        out = T._sanitize_html("<embed src='x'></embed>")
        assert "embed" not in out.lower()

    def test_removes_form(self):
        out = T._sanitize_html("<form action='x'>f</form>")
        assert "form" not in out.lower()

    def test_removes_onclick(self):
        out = T._sanitize_html('<div onclick="hack()">x</div>')
        assert "onclick" not in out.lower()

    def test_removes_javascript_uri(self):
        out = T._sanitize_html('<a href="javascript:alert(1)">x</a>')
        assert "javascript:" not in out.lower()

    def test_keeps_safe_markup(self):
        html = "<h1>Title</h1><p><b>bold</b> and <i>italic</i></p>"
        out = T._sanitize_html(html)
        assert "<h1>" in out and "<b>" in out

    def test_self_closing_script(self):
        out = T._sanitize_html("<script src='x'/>text")
        assert "script" not in out.lower()

    def test_multiline_script(self):
        out = T._sanitize_html("<script>\nvar x=1;\nalert(x);\n</script>done")
        assert "alert" not in out
        assert "done" in out


# ── _resolve_booklet_theme ───────────────────────────────────────────────
class TestResolveBookletTheme:
    def test_explicit_known_theme(self):
        assert T._resolve_booklet_theme("blue", "", "") == "blue"

    def test_auto_dark_from_code_keyword(self):
        assert T._resolve_booklet_theme("auto", "Python API", "code sample") == "dark"

    def test_auto_green_from_persian(self):
        assert T._resolve_booklet_theme("auto", "سلامت", "طبیعت") == "green"

    def test_auto_purple_art(self):
        assert T._resolve_booklet_theme("auto", "philosophy", "art") == "purple"

    def test_default_wood(self):
        assert T._resolve_booklet_theme("auto", "random title", "nothing special") == "wood"

    def test_all_known_themes_passthrough(self):
        for name in T._BOOKLET_THEMES:
            assert T._resolve_booklet_theme(name, "x", "y") == name


# ── theme data integrity ─────────────────────────────────────────────────
class TestBookletThemes:
    def test_wood_is_default_present(self):
        assert "wood" in T._BOOKLET_THEMES

    @pytest.mark.parametrize("theme", list(T._BOOKLET_THEMES.keys()))
    def test_each_theme_has_primary(self, theme):
        assert "--primary" in T._BOOKLET_THEMES[theme]

    @pytest.mark.parametrize("theme", list(T._BOOKLET_THEMES.keys()))
    def test_each_theme_has_bg_and_text(self, theme):
        assert "--bg" in T._BOOKLET_THEMES[theme]
        assert "--text" in T._BOOKLET_THEMES[theme]


# ── cache load/save ──────────────────────────────────────────────────────
class TestCache:
    def test_load_returns_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(T, "CACHE_FILE", str(tmp_path / "c.json"), raising=False)
        data = T._load_cache()
        assert isinstance(data, dict)

    def test_save_then_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(T, "CACHE_FILE", str(tmp_path / "c.json"), raising=False)
        T._save_cache({"k": "v"})
        assert T._load_cache().get("k") == "v"
