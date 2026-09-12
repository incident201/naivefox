# NaiveFox transport

NaiveFox supports one current transport and coordinated client/server updates.
There is no classic mode, protocol-version negotiation or legacy fallback.
CONNECT is permitted when justified by the native protocol architecture.

See [ARCHITECTURE.md](ARCHITECTURE.md) for carrier ownership, H2/H3 selection,
bounded queues, delivery credit and lifecycle. The separately maintained
naivefox-transport repository documents the matching wire contract in
docs/PROTOCOL.md and the public site in docs/SITE.md.

H2 uses native WSS after public-site and twenty-pair HTTP startup.
H3 uses the same startup followed by a persistent HTTP/3 GET and at most eight
concurrent finite POSTs. Its sustained data remains on Neqo QUIC. A four-byte
length prefix delimits downstream cells in the GET response.

Both carriers use NFOX cells, the naivefox HELLO identity, bounded
stream credit and the same DATA/CREDIT/FIN/RESET rules. There is one current
subprotocol identity, not a list of supported versions.
