"""Static UI/CSP checks and shared-copy integrity; no API calls."""
import hashlib
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class HtmlAudit(HTMLParser):
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        assert not any(key == "style" or re.fullmatch(r"on[a-z]+", key) for key in attrs), (tag, attrs)
        if tag == "script":
            assert attrs.get("src"), "Inline script"


def main():
    source = ROOT / "shared/ui"
    original = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in source.rglob("*") if path.is_file()}
    for relative in ["windows/src/ExaoneChat/wwwroot", "web/frontend/public"]:
        app = ROOT / relative
        HtmlAudit().feed((app / "index.html").read_text(encoding="utf-8"))
        assert not (app / "vendor").exists(), "Duplicate legacy vendor directory"
        copies = {str(path.relative_to(app / "shared")): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (app / "shared").rglob("*") if path.is_file()}
        assert copies == original, "Shared copy differs: " + relative
        js = (app / "app.js").read_text(encoding="utf-8")
        assert not re.search(r"marked\.parse|DOMPurify|function renderMarkdown", js), "Duplicate renderer"
        for path in app.rglob("*.css"):
            css = path.read_text(encoding="utf-8")
            assert not re.search(r"box-shadow|gradient|blur", css, re.I), path
            assert all(value.strip() == "0" for value in re.findall(r"border-radius\s*:\s*([^;}]+)", css)), path
        print("PASS HTML/CSS and shared SHA256 copies:", relative)
    fonts = list((source / "vendor/fonts").iterdir())
    assert len(fonts) == 20 and all(path.suffix == ".woff2" for path in fonts)
    public = ROOT / "web/frontend/public"
    for file in public.rglob("*"):
        if not file.is_file():
            continue
        with urllib.request.urlopen("http://localhost:8080/" + file.relative_to(public).as_posix()) as response:
            assert response.status == 200 and response.read() == file.read_bytes(), file
            if file.suffix == ".woff2":
                assert response.headers.get_content_type() == "font/woff2"
            csp = response.headers.get("Content-Security-Policy", "")
            assert "style-src-attr 'unsafe-inline'" in csp and "script-src 'self'" in csp
    print("PASS every web static file and all 20 KaTeX woff2 fonts: 200 / current content / CSP")


if __name__ == "__main__":
    main()
