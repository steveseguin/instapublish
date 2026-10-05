"""Offline HTML/JavaScript regressions. Run: python3 -m unittest discover -s tests

Requires Python 3 and Node.js; no packages or network access are needed.
"""
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import unittest


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_script = False
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.in_script = True
            self.scripts.append("")

    def handle_data(self, data):
        if self.in_script:
            self.scripts[-1] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False


HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const { script, assertion } = JSON.parse(fs.readFileSync(0, 'utf8'));
const listeners = new Map();
const elements = new Map();
function getElementById(id) {
    if (!elements.has(id)) {
        const contentDocument = {
            html: '',
            open() { this.html = ''; },
            write(html) { this.html += html; },
            close() { this.closed = true; }
        };
        elements.set(id, {
            value: '', style: {}, contentDocument,
            addEventListener(event, callback) {
                listeners.set(`${id}:${event}`, callback);
            }
        });
    }
    return elements.get(id);
}
const pending = [];
const context = vm.createContext({
    document: { getElementById },
    setTimeout(callback) { pending.push(callback); },
    listeners, pending, elements
});
vm.runInContext(script, context, { filename: 'index.html inline script' });
console.log(JSON.stringify(vm.runInContext(assertion, context)));
"""


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text()
        parser = ScriptParser()
        parser.feed(html)
        cls.scripts = parser.scripts

    def evaluate(self, assertion):
        result = subprocess.run(
            ["node", "-e", HARNESS],
            input=json.dumps({"script": self.scripts[0], "assertion": assertion}),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_outer_script_remains_one_complete_html_script(self):
        self.assertEqual(len(self.scripts), 1)
        result = subprocess.run(
            ["node", "--check"], input=self.scripts[0], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_initialization_binds_all_controls(self):
        self.assertEqual(
            self.evaluate("Array.from(listeners.keys()).sort()"),
            ["closeBtn:click", "codeInput:paste", "fullscreenBtn:click", "renderBtn:click"],
        )

    def test_render_button_preserves_complete_html(self):
        self.assertEqual(self.evaluate("""
            codeInput.value = '<!DOCTYPE html><html><body><h1>Hello</h1></body></html>';
            listeners.get('renderBtn:click')();
            outputFrame.contentDocument.html;
        """), "<!DOCTYPE html><html><body><h1>Hello</h1></body></html>")

    def test_javascript_output_has_a_real_closing_script_tag(self):
        for code in ["document.body.textContent = 'Hello';", "<script>window.example = 42;</script>"]:
            with self.subTest(code=code):
                html = self.evaluate(f"formatContent({json.dumps(code)}, 'javascript')")
                parser = ScriptParser()
                parser.feed(html)
                self.assertEqual(len(parser.scripts), 1)
                expected = code if not code.startswith("<script>") else "window.example = 42;"
                self.assertEqual(parser.scripts[0].strip(), expected)
                self.assertNotIn(r"<\/script>", html)

    def test_other_preview_types_keep_their_content(self):
        for code, kind in [("<h1>Hello</h1>", "html-fragment"), ("<body>Hello</body>", "body-html"), ("body { color: red; }", "css"), ("<style>body { color: red; }</style>", "css")]:
            with self.subTest(kind=kind, code=code):
                result = self.evaluate(f"[detectContentType({json.dumps(code)}), formatContent({json.dumps(code)}, {json.dumps(kind)})]")
                self.assertEqual(result[0], kind)
                self.assertIn(code, result[1])

    def test_fullscreen_open_close_and_reopen(self):
        result = self.evaluate("""
            codeInput.value = '<h1>First preview</h1>';
            listeners.get('fullscreenBtn:click')();
            const first = [fullscreenContainer.style.display, fullscreenFrame.contentDocument.html];
            listeners.get('closeBtn:click')();
            const closed = fullscreenContainer.style.display;
            codeInput.value = '<h1>Second preview</h1>';
            listeners.get('fullscreenBtn:click')();
            [first, closed, fullscreenContainer.style.display, fullscreenFrame.contentDocument.html];
        """)
        self.assertEqual(result[0][0], "block")
        self.assertIn("First preview", result[0][1])
        self.assertEqual(result[1:3], ["none", "block"])
        self.assertIn("Second preview", result[3])
        self.assertNotIn("First preview", result[3])

    def test_paste_renders_after_input_updates(self):
        html = self.evaluate("""
            listeners.get('codeInput:paste')();
            codeInput.value = '<h1>Pasted preview</h1>';
            pending.shift()();
            outputFrame.contentDocument.html;
        """)
        self.assertIn("Pasted preview", html)


if __name__ == "__main__":
    unittest.main()
