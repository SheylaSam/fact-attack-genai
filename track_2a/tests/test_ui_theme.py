from src import ui_theme

TOKENS = {"bg", "surface", "surface2", "border", "text", "muted", "blue", "green", "yellow", "red", "grey", "orange",
          "blue-text", "green-text", "yellow-text", "red-text", "orange-text"}

def test_both_palettes_have_the_same_tokens():
    assert set(ui_theme.PALETTES["light"]) == set(ui_theme.PALETTES["dark"]) == TOKENS

def test_palette_values_from_the_spec():
    assert ui_theme.PALETTES["light"]["bg"] == "#F3F4F8" and ui_theme.PALETTES["dark"]["bg"] == "#121419"
    assert ui_theme.PALETTES["light"]["blue"] == "#3B62C6" and ui_theme.PALETTES["dark"]["green"] == "#2FD1AE"

def test_text_contrast_in_both_modes():
    for p in ui_theme.PALETTES.values():
        for fg in ("text", "muted", "blue-text", "green-text", "yellow-text", "red-text", "orange-text"):
            for bg in ("bg", "surface", "surface2"):
                assert ui_theme.contrast(p[fg], p[bg]) >= 4.5

def test_css_declares_every_token_and_links_font_awesome():
    css = ui_theme.theme_css("dark")
    assert all(f"--{name}:" in css for name in TOKENS)
    assert "font-awesome/5.15.4/css/all.min.css" in css and "ambient-glow" not in css

def test_css_uses_the_palette_of_the_requested_mode():
    assert "#121419" in ui_theme.theme_css("dark") and "#F3F4F8" not in ui_theme.theme_css("dark")
    assert "#F3F4F8" in ui_theme.theme_css("light") and "#121419" not in ui_theme.theme_css("light")

def test_css_has_no_blank_line_so_markdown_keeps_it_one_html_block():
    for mode in ("light", "dark"):
        assert not any(not line.strip() for line in ui_theme.theme_css(mode).splitlines())

def test_text_colours_in_the_stylesheet_use_the_text_safe_tokens():
    import re
    from pathlib import Path
    css = Path(ui_theme.__file__).with_name("ui_theme.css").read_text()
    for line in css.splitlines():
        if line.startswith(".tone-") or "flow-arrow" in line:  # icons, not text
            continue
        assert not re.search(r"((?<![-\w])color|text-fill-color): var\(--(blue|green|yellow|red|orange|grey)\)", line), line

def test_sidebar_controls_are_not_hidden_and_lato_beats_streamlit_fonts():
    import re
    css = ui_theme.theme_css("light")
    hidden = re.findall(r"([^{}]+)\{[^}]*display: *none", css)
    assert not any(x in sel for sel in hidden for x in ('"stToolbar"]', 'stHeader', 'ExpandSidebar', 'SidebarCollapse'))
    rule = re.search(r"([^{}]*)\{[^}]*font-family: *'Lato'[^}]*\}", css)
    assert rule and ".stApp *" in rule.group(1) and "!important" in rule.group(0)
    assert "stIconMaterial" in rule.group(1) and "code" in rule.group(1)  # icon and monospace fonts stay alone

def test_matrix_cells_use_palette_text_not_fixed_white():
    from pathlib import Path
    css = Path(ui_theme.__file__).with_name("ui_theme.css").read_text()
    assert "#fff" not in css.lower()
    # strongest tint used by app.py is 85% * 40% blue over the surface; text must stay readable on it
    from src.ui_theme import PALETTES, contrast
    for p in PALETTES.values():
        mix = [round(int(p["blue"][i:i + 2], 16) * 0.34 + int(p["surface"][i:i + 2], 16) * 0.66) for i in (1, 3, 5)]
        cell = "#%02X%02X%02X" % tuple(mix)
        assert contrast(p["text"], cell) >= 4.5


def test_page_tag_text_reaches_4_5_on_its_tinted_background():
    for p in ui_theme.PALETTES.values():
        def mix(a, b, share):  # color-mix(in srgb, a share%, b)
            ca, cb = [[int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)] for c in (a, b)]
            return "#" + "".join(f"{round(x * share + y * (1 - share)):02X}" for x, y in zip(ca, cb))
        tint = mix(p["blue"], p["surface"], 0.14)
        css = ui_theme.theme_css("dark")
        line = next(l for l in css.splitlines() if l.startswith(".tag-page"))
        token = line.split("color: var(--")[-1].split(")")[0]
        assert ui_theme.contrast(p[token], tint) >= 4.5, token
