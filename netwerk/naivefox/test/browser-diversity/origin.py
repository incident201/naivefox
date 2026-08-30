"""Loopback-only corpus origin; Caddy supplies the browser-facing H2/H3 stack."""
import collections
import hashlib
import http.server
import json
from pathlib import Path
import threading
import time
import urllib.parse


class Origin:
    def __init__(self, root):
        self.manifest=json.loads((root/"manifest.json").read_text())
        self.pages={page["id"]:page for page in self.manifest["pages"]}
        self.assets={}
        for path,meta in self.manifest["assets"].items():
            body=(root/path).read_bytes()
            if len(body)!=meta["bytes"] or hashlib.sha256(body).hexdigest()!=meta["sha256"]:
                raise ValueError("corpus asset differs from frozen manifest")
            self.assets[path]=(body,meta)
        self.lock=threading.Lock();self.reset()
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version="HTTP/1.1"
            def log_message(self,*args):pass
            def reply(self,status,body=b"",mime="text/plain",cache=False):
                self.send_response(status)
                self.send_header("Content-Type",mime)
                self.send_header("Content-Length",str(len(body)))
                self.send_header("Cache-Control","public, max-age=120" if cache else "no-store")
                self.end_headers()
                if self.command!="HEAD":self.wfile.write(body)
                with owner.lock:
                    owner.counts[str(status)]+=1;owner.counts["bytes"]+=len(body)
            def do_HEAD(self):self.do_GET()
            def do_GET(self):
                page=self.headers.get("X-Corpus-Page","")
                if page not in owner.pages:return self.reply(404)
                path=urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
                if path=="/":key=page+"/index.html"
                elif path=="/assets/sans.ttf":key="shared/sans.ttf"
                elif path.startswith("/assets/"):key=page+"/"+path[len("/assets/"):]
                elif path.startswith("/api/"):key=page+path
                else:return self.reply(404)
                asset=owner.assets.get(key)
                if asset is None:return self.reply(404)
                body,meta=asset
                if meta["delay_ms"]:time.sleep(meta["delay_ms"]/1000)
                self.reply(200,body,meta["mime"],not path.startswith("/api/"))
            def do_POST(self):
                page=self.headers.get("X-Corpus-Page","")
                if page not in owner.pages or self.path!="/api/sync":return self.reply(404)
                try:
                    size=int(self.headers.get("Content-Length","-1"))
                    if not 0<=size<=131072:raise ValueError("body bound")
                    body=self.rfile.read(size)
                    value=json.loads(body)
                    if len(body)!=size or len(value["edits"])!=owner.pages[page]["post_items"]:raise ValueError("unexpected edits")
                except (ValueError,KeyError,TypeError):return self.reply(400)
                with owner.lock:owner.counts["upload_bytes"]+=size
                self.reply(200,json.dumps({"saved":len(value["edits"])}).encode(),"application/json")
        self.server=http.server.ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.server.daemon_threads=True
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={"poll_interval":.05},daemon=True)

    def start(self):self.thread.start();return self
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def reset(self):
        with getattr(self,"lock",threading.Lock()):self.counts=collections.Counter()
    def snapshot(self):
        with self.lock:return dict(self.counts)
