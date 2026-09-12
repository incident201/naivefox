# Public application site

NaiveFox starts with the configured HTTPS origin's actual HTML document and its
supported directly declared stylesheet, script and image resources. The client
uses the Firefox HTML speculative scanner without DOM or JavaScript execution.
CSS/script/image bodies are leaves; secondary script-driven loads and CSS
imports are not emulated.

There is no fixed site byte budget, seven-file layout, preamble mode or special
transport manifest in public HTML. The operator chooses the site's names, sizes
and supported resources. The server snapshots those resources on provisioning;
the client checks MIME types, complete bodies and their authenticated shared
snapshot identity before opening destinations.

Root and resource redirects, changed representations, unsupported markup and
incomplete bodies fail according to the single current site contract. The
matching server's docs/SITE.md gives the detailed markup, URL, MIME and reload
rules. [TRANSPORT.md](TRANSPORT.md) describes the carrier transition.

The client inhibits caching of its startup resources. Large pages therefore add
startup traffic and latency on each new carrier, and consume server snapshot
memory. Do not add arbitrary space padding or execute a browser worker inside
the lean client.
