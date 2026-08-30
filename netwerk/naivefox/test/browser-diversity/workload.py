class BrowserWorkload:
    capture_seconds = 5

    def __init__(self, origin, page, fronting=False):
        self.origin=origin
        self.page=page
        self.target=origin.pages[page["target_id"]]
        self.fronting=fronting
        self.scrolled=set()
        self.latest={}

    def configure(self, config, campaign, mode):
        routes=[(campaign.target_port,self.target["id"])]
        if mode=="reference" and not self.fronting:routes.append((campaign.port,self.page["id"]))
        for port,page_id in routes:
            for server in config["apps"]["http"]["servers"].values():
                if f"127.0.0.1:{port}" not in server["listen"]:continue
                server["routes"].insert(0,{"match":[{"expression":f"{{http.request.hostport}} == 'localhost:{port}'","method":["GET","HEAD","POST"]}],
                    "handle":[{"handler":"encode","encodings":{"gzip":{}}},
                              {"handler":"reverse_proxy","upstreams":[{"dial":f"127.0.0.1:{self.origin.port}"}],
                               "headers":{"request":{"set":{"X-Corpus-Page":[page_id]}}}}],"terminal":True})

    def status(self, driver, page, role, elapsed):
        value=driver.execute_script("""return {id:document.body?.dataset?.corpus||'',loaded:document.readyState==='complete',
          ready:window.appReady===true,errors:Number.isFinite(window.appErrors)?window.appErrors:-1,
          fonts:!document.fonts||document.fonts.status==='loaded',
          images:[...document.images].filter(i=>i.complete&&i.naturalWidth>0).length,
          visible_pending:[...document.images].filter(i=>{const r=i.getBoundingClientRect();return r.top<innerHeight&&r.bottom>0&&!i.complete}).length,
          resources:performance.getEntriesByType('resource').length};""")
        if not value or not value.get("id"):return False
        if value["id"]!=page["id"]:raise RuntimeError("corpus page identity mismatch")
        if value["errors"]!=0:raise RuntimeError("corpus browser resource/script error")
        ready=value["loaded"] and value["fonts"] and (page["script"]=="none" or value["ready"])
        if ready and page["scroll"] and role not in self.scrolled:
            if elapsed<.6:return False
            driver.execute_script("window.scrollTo(0,document.body.scrollHeight)")
            self.scrolled.add(role)
            return False
        self.latest[role]={key:value[key] for key in ("loaded","ready","images","resources","visible_pending")}
        return ready and value["visible_pending"]==0

    def reference_state(self, driver, elapsed):
        done=self.status(driver,self.page,"reference",elapsed)
        return {"done":done,"error":None,"round":0,"early":0,"early_filler":0,"action":False,"phase":"idle","alive":True,"dynamic":0,"idle":0,"wake":0}

    def target_done(self, driver, elapsed):
        return self.status(driver,self.target,"target",elapsed)

    def finish(self, driver, reference):
        if not (reference and self.fronting):
            page=self.page if reference else self.target
            if not self.status(driver,page,"reference" if reference else "target",self.capture_seconds):
                raise RuntimeError("corpus workload incomplete at capture end")
        origin=self.origin.snapshot()
        if any(int(status)>=400 and count for status,count in origin.items() if status.isdecimal()):
            raise RuntimeError("corpus origin returned an error")
        return {"reference_page":self.page["id"],"target_page":self.target["id"],
                "fronting_control":self.fronting,"browser":self.latest,"origin":origin}
