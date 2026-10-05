"""Preview the app's UI in an ordinary browser, without pywebview or Roblox.

    python dev_tests/ui_preview.py [port]      -> http://localhost:8765

Serves ui/ and fakes window.pywebview.api: read-only calls (get_meta, get_index,
get_general, ...) go to the real fischui.Api; everything else is a no-op, and
saves only change an in-memory copy -- your settings files are never written.
"""
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import fischui  # noqa: E402

UI = os.path.join(ROOT, "ui")
READ = {"get_meta", "get_index", "get_general", "get_hotkeys", "get_skins", "get_state", "scan_progress"}
api = fischui.Api()
memory: dict = {}

MOCK = """<script>
window.pywebview = {api: new Proxy({}, {get: (_, name) => async (...args) => {
  const r = await fetch("/api/" + name, {method: "POST", body: JSON.stringify(args)});
  return r.json();
}})};
addEventListener("load", () => dispatchEvent(new Event("pywebviewready")));
</script>"""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=UI, **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")     # always serve the files as edited
        super().end_headers()

    def _send(self, body: bytes, kind: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(UI, "index.html"), encoding="utf-8") as f:
                page = f.read().replace("<head>", "<head>" + MOCK, 1)
            return self._send(page.encode("utf-8"), "text/html; charset=utf-8")
        return super().do_GET()

    def do_POST(self):
        name = self.path.rsplit("/", 1)[-1]
        args = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or "[]")
        if name in READ:
            out = getattr(api, name)(*args)
            if name == "get_meta" and "rotations" in memory:
                out["rotations"] = memory["rotations"]
            if name == "get_general" and "general" in memory:
                out = {"ok": True, "general": memory["general"]}
        elif name in ("save_rotation", "delete_rotation"):
            rots = memory.setdefault("rotations", api.get_meta()["rotations"])
            if name == "delete_rotation":
                rots[:] = [r for r in rots if r["id"] != args[0]]
                out = {"ok": True, "rotations": rots}
            else:
                from fischconfig import rotation
                from uuid import uuid4
                try:
                    row = dict(id=(args[2] if len(args) > 2 and args[2] else uuid4().hex),
                               name=args[0].strip(), **rotation(json.loads(args[1]), fischui.DEFAULTS,
                                                                api._profiles.rods))
                    rots[:] = [r for r in rots if r["id"] != row["id"]] + [row]
                    out = {"ok": True, "rotation": row, "rotations": rots}
                except ValueError as exc:
                    out = {"ok": False, "error": str(exc)}
        elif name == "save_general":
            g = memory.get("general") or api.get_general()["general"]
            g = dict(g, **json.loads(args[0]))
            memory["general"] = g
            out = {"ok": True, "general": g}
        else:
            out = {"ok": True}
        self._send(json.dumps(out).encode("utf-8"), "application/json")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"UI preview: http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
