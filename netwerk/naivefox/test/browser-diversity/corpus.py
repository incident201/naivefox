#!/usr/bin/env python3
"""Deterministic, executable browser pages; sizes arise from content, not padding."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import random
import struct
import zlib

FAMILIES = (
    ("article", 0, "none", 0, 0, 0, False, False),
    ("documentation", 2, "defer", 2, 0, 0, True, False),
    ("magazine", 2, "async", 8, 1, 0, True, False),
    ("gallery", 1, "module", 12, 0, 0, False, True),
    ("catalog", 3, "defer", 16, 1, 0, False, True),
    ("product", 2, "module", 5, 2, 0, False, False),
    ("forum", 1, "defer", 8, 0, 0, False, False),
    ("search", 1, "module", 3, 2, 0, False, False),
    ("dashboard", 3, "dynamic", 4, 3, 0, False, False),
    ("atlas", 2, "dynamic", 9, 2, 0, False, True),
    ("media-library", 2, "async", 15, 1, 0, False, True),
    ("portfolio", 2, "module", 6, 0, 0, True, True),
    ("landing", 1, "defer", 4, 0, 0, True, False),
    ("sign-in", 1, "defer", 1, 0, 0, False, False),
    ("settings", 2, "dynamic", 1, 2, 12, False, False),
    ("inbox", 2, "module", 6, 3, 24, False, False),
    ("analytics", 3, "dynamic", 2, 3, 0, False, False),
    ("code-browser", 2, "module", 3, 1, 0, True, False),
    ("image-tool", 1, "dynamic", 3, 1, 0, False, False),
    ("download-index", 0, "none", 1, 0, 0, False, False),
    ("photo-story", 1, "async", 10, 0, 0, True, True),
    ("knowledge-base", 2, "defer", 4, 0, 0, False, False),
    ("activity-feed", 2, "module", 7, 3, 80, False, False),
    ("calculator", 1, "dynamic", 1, 1, 0, False, False),
)
WORDS = "river archive garden observatory workshop library coast mountain station meadow network document project measure account session record season branch collection window system design history review public local field region route display table package".split()
BASE_CSS = """*{box-sizing:border-box}body{margin:0;background:#f7f6f1;color:#233437;font:16px/1.6 system-ui,sans-serif}header,main,footer{max-width:1080px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #ccd3ce}nav a{margin-right:16px;color:#355e51}h1{font-size:2.5rem;line-height:1.2}h2{font-size:1.3rem}p{max-width:75ch}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:18px}.card{padding:16px;background:white;border:1px solid #d8ddd6;border-radius:8px}img{display:block;width:100%;height:auto}.hero{max-height:440px;object-fit:cover}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;padding:8px;border-bottom:1px solid #ccd3ce}button,input,select{font:inherit;padding:8px;border:1px solid #6b8172;border-radius:4px}button{cursor:pointer;background:#dce7da}pre,code{font-family:monospace;background:#e4e9e1}pre{overflow:auto;padding:18px}#status{color:#456354}footer{border-top:1px solid #ccd3ce;margin-top:24px}.columns{display:grid;grid-template-columns:2fr 1fr;gap:24px}@media(max-width:680px){.columns{display:block}header{display:block}h1{font-size:1.9rem}}
"""


def sentence(rng, count=18):
    return " ".join(rng.choice(WORDS) for _ in range(count)).capitalize() + "."


def png(width, height, seed):
    rng = random.Random(seed)
    palette = bytes(channel for value in range(256) for channel in ((value*3)%256, (value+70)%256, (255-value)//2+40))
    rows = []
    for y in range(height):
        grain = rng.randbytes(width)
        rows.append(b"\0" + bytes(((x//7 + y//5 + (grain[x]&15)) % 256) for x in range(width)))
    def chunk(kind, body):
        return struct.pack("!I", len(body)) + kind + body + struct.pack("!I", zlib.crc32(kind+body)&0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB",width,height,8,3,0,0,0)) + chunk(b"PLTE",palette) + chunk(b"IDAT",zlib.compress(b"".join(rows),6)) + chunk(b"IEND",b"")


def svg(seed):
    rng=random.Random(seed)
    bars="".join(f'<rect x="{15+i*27}" y="{180-h}" width="19" height="{h}" fill="hsl({rng.randrange(120,230)} 35% 48%)"/>' for i,h in enumerate(rng.randrange(20,155) for _ in range(10)))
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 200"><rect width="300" height="200" fill="#ecf1e9"/>{bars}<path d="M10 182H290" stroke="#345"/></svg>'


def build(destination, seed=202608360, families=FAMILIES):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("corpus output must be empty")
    destination.mkdir(mode=0o700,parents=True,exist_ok=True)
    assets, pages = {}, []
    def put(path, body, mime, delay=0):
        data=body.encode() if isinstance(body,str) else body
        if path in assets: raise ValueError("duplicate corpus asset")
        output=destination/path;output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(data)
        assets[path]={"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"mime":mime,"delay_ms":delay}
    put("shared/sans.ttf",Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").read_bytes(),"font/ttf")
    put("FONT-LICENSE.txt",Path("/usr/share/doc/fonts-dejavu-core/copyright").read_bytes(),"text/plain")
    folds=list(range(24));random.Random(seed).shuffle(folds)
    fold_by_family={family:index//6 for index,family in enumerate(folds)}
    for family_index, (family, styles, script, images, apis, post_items, font, lazy) in enumerate(families):
        for variant in range(4):
            page_id=f"{family}-{variant}"
            rng=random.Random(seed+family_index*1009+variant*113)
            multiplier=(0.7,1,1.4,1.9)[variant]
            image_count=max(0,round(images*multiplier))
            paragraphs=(3+family_index%5)*(variant+1)
            card_count=max(3,image_count)
            table_rows=(12+variant*14) if family in ("analytics","code-browser","download-index","dashboard") else 0
            head=['<meta charset="utf-8">','<meta name="viewport" content="width=device-width,initial-scale=1">','<link rel="icon" href="data:,">']
            if styles:
                extra='@import url("details.css");\n' if family_index%3==0 else ""
                if font:extra+='@font-face{font-family:CorpusSans;src:url("/assets/sans.ttf")}body{font-family:CorpusSans,sans-serif}\n'
                put(f"{page_id}/site.css",extra+BASE_CSS,"text/css")
                head.append('<link rel="stylesheet" href="assets/site.css">')
                if family_index%3==0:put(f"{page_id}/details.css","blockquote{border-left:3px solid #739679;padding-left:18px}li{margin:8px 0}aside{background:#e5e9df;padding:18px}","text/css")
                for index in range(1,styles):
                    put(f"{page_id}/theme-{index}.css",f'.card:nth-child({index+2}n){{border-top:3px solid hsl({family_index*13} 30% 46%)}}header{{background:hsl({family_index*11} 20% {94-index}%)}}',"text/css")
                    head.append(f'<link rel="stylesheet" href="assets/theme-{index}.css">')
            else:head.append(f"<style>{BASE_CSS}</style>")
            cards=[]
            for index in range(card_count):
                picture=""
                if index<image_count:
                    raster=family in ("gallery","catalog","product","atlas","media-library","portfolio","photo-story","image-tool")
                    name=f"image-{index}."+("png" if raster else "svg")
                    if raster:
                        width=(160,256,384,512)[variant]
                        if index==0 and family in ("photo-story","product","portfolio"):width*=2
                        put(f"{page_id}/{name}",png(width,width*3//5,seed+family_index*10000+variant*100+index),"image/png")
                    else:put(f"{page_id}/{name}",svg(seed+family_index*10000+variant*100+index),"image/svg+xml")
                    loading=' loading="lazy"' if lazy and index>2 else ""
                    picture=f'<img src="assets/{name}" alt="Collection study {index+1}" width="320" height="192"{loading}>'
                cards.append(f'<article class="card">{picture}<h2>{sentence(rng,4)}</h2><p>{sentence(rng,12)}</p></article>')
            text="".join(f"<p>{sentence(rng,36)}</p>" for _ in range(paragraphs))
            table=""
            if table_rows:
                table='<table><thead><tr><th>Record</th><th>Region</th><th>Value</th></tr></thead><tbody>'+"".join(f'<tr><td>{i+1}</td><td>{sentence(rng,3)}</td><td>{rng.randrange(100,9000)}</td></tr>' for i in range(table_rows))+'</tbody></table>'
            form='<form id="filter"><label>Search <input name="query" value="archive"></label> <button>Update view</button></form>'
            api_paths=[]
            for index in range(apis):
                count=(30+family_index*3)*(variant+1)
                if family in ("analytics","search","activity-feed"):count*=4
                rows=[{"id":i,"title":sentence(rng,7),"region":rng.choice(WORDS),"value":rng.randrange(10000)} for i in range(count)]
                path=f"api/data-{index}.json";api_paths.append(path)
                put(f"{page_id}/{path}",json.dumps({"records":rows,"page":index},separators=(",",":")),"application/json",(index*75+variant*20) if index else 0)
            if script!="none":
                imports='import {render} from "./render.mjs";\n' if script in ("module","dynamic") else 'function render(data){document.getElementById("records").textContent=data.records.slice(0,30).map(row=>row.title+": "+row.value).join(" | ");}\n'
                if script in ("module","dynamic"):
                    put(f"{page_id}/format.mjs",'export const format = row => row.title+": "+row.value;\n',"text/javascript")
                    put(f"{page_id}/render.mjs",'import {format} from "./format.mjs"; export function render(data){document.getElementById("records").textContent=data.records.slice(0,30).map(format).join(" | ");}\n',"text/javascript")
                dynamic='const view=await import("./chart.mjs");view.decorate();' if script=="dynamic" else ""
                if dynamic:put(f"{page_id}/chart.mjs",'export function decorate(){document.querySelectorAll(".card").forEach((node,index)=>node.style.opacity=String(.8+(index%3)*.1));}\n',"text/javascript")
                count=post_items*(variant+1)
                post=f'const edits=Array.from({{length:{count}}},(_,i)=>({{id:i,value:i*7,note:"local preference update"}}));const saved=await fetch("api/sync",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{edits}})}});if(!saved.ok)throw Error("save");await saved.json();' if count else ""
                app=imports+f'''window.appReady=false;
async function main(){{
  for(const url of {json.dumps(api_paths)}){{const response=await fetch(url);if(!response.ok)throw Error("data");render(await response.json());}}
  {post}
  {dynamic}
  const filter=document.getElementById("filter");filter.addEventListener("submit",event=>{{event.preventDefault();document.getElementById("status").textContent="View updated";}});
  document.getElementById("status").textContent="Ready";window.appReady=true;
}}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",()=>main().catch(()=>window.appErrors++),{{once:true}});else main().catch(()=>window.appErrors++);
'''
                put(f"{page_id}/app.js",app,"text/javascript")
                attr='type="module"' if script in ("module","dynamic") else script
                head.append(f'<script {attr} src="assets/app.js"></script>')
            instrumentation='<script>window.appErrors=0;addEventListener("error",()=>window.appErrors++,true);addEventListener("unhandledrejection",()=>window.appErrors++);</script>'
            document=f'''<!doctype html><html><head>{''.join(head)}<title>{html.escape(family.title())} — Field Notes</title>{instrumentation}</head>
<body data-corpus="{page_id}"><header><strong>Field Notes / {family.title()}</strong><nav><a href="#overview">Overview</a><a href="#collection">Collection</a></nav></header>
<main><h1 id="overview">{sentence(rng,6)}</h1>{form}<p id="status">{"Loading" if script!="none" else "Ready"}</p><section class="columns"><div>{text}</div><aside>{sentence(rng,28)}</aside></section>
<section id="collection" class="cards">{''.join(cards)}</section>{table}<section id="records" aria-live="polite"></section></main><footer>Field Notes · Local demonstration collection</footer></body></html>'''
            put(f"{page_id}/index.html",document,"text/html")
            own=[key for key in assets if key.startswith(page_id+"/")]
            pages.append({"id":page_id,"family":family,"variant":variant,"partition":fold_by_family[family_index],
                          "target_id":f"{family}-{(variant+1)%4}","script":script,"css_links":styles,"images":image_count,
                          "api_gets":apis,"post_items":count if script!="none" else 0,"scroll":lazy,"font":font,
                          "path":"/","resources":own,
                          "declared_bytes":sum(assets[key]["bytes"] for key in own)+(assets["shared/sans.ttf"]["bytes"] if font else 0)})
    manifest={"schema_version":1,"seed":seed,"family_count":len(families),"variants_per_family":4,"pages":pages,"assets":assets,
              "scope":"synthetic single-origin resource graphs; not representative Internet prevalence"}
    body=json.dumps(manifest,sort_keys=True,indent=2)+"\n"
    (destination/"manifest.json").write_text(body)
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--seed",type=int,default=202608360)
    args=parser.parse_args()
    value=build(args.output,args.seed)
    print(json.dumps({"families":value["family_count"],"pages":len(value["pages"]),"assets":len(value["assets"]),"bytes":sum(v["bytes"] for v in value["assets"].values())}))
