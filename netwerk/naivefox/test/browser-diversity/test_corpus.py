import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import unittest

import corpus
from origin import Origin


class CorpusTests(unittest.TestCase):
    def test_png_and_family_definitions(self):
        self.assertEqual(len(corpus.FAMILIES),24)
        self.assertEqual(len({value[0] for value in corpus.FAMILIES}),24)
        a=corpus.png(64,48,17)
        self.assertTrue(a.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(a,corpus.png(64,48,17))
        self.assertNotEqual(a,corpus.png(64,48,18))

    def test_manifest_origin_and_no_public_page_selector(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            specs=(corpus.FAMILIES[0],corpus.FAMILIES[14])
            a=corpus.build(root/"a",123,families=specs)
            b=corpus.build(root/"b",123,families=specs)
            self.assertEqual(a,b)
            self.assertEqual(len(a["pages"]),8)
            for page in a["pages"]:
                self.assertEqual(page["path"],"/")
                self.assertNotEqual(page["id"],page["target_id"])
            server=Origin(root/"a").start()
            try:
                def request(path,headers=None,method="GET",body=None):
                    connection=http.client.HTTPConnection("127.0.0.1",server.port)
                    connection.request(method,path,body=body,headers=headers or {})
                    response=connection.getresponse();data=response.read();status=response.status;connection.close()
                    return status,data
                self.assertEqual(request("/")[0],404)
                status,body=request("/",{"X-Corpus-Page":"article-0"})
                self.assertEqual(status,200)
                self.assertEqual(hashlib.sha256(body).hexdigest(),a["assets"]["article-0/index.html"]["sha256"])
                self.assertEqual(request("/../../manifest.json",{"X-Corpus-Page":"article-0"})[0],404)
                self.assertEqual(request("/assets/site.css",{"X-Corpus-Page":"settings-0"})[0],200)
                body=json.dumps({"edits":[{"id":i} for i in range(12)]})
                self.assertEqual(request("/api/sync",{"X-Corpus-Page":"settings-0"},"POST",body)[0],200)
            finally:server.close()


if __name__=="__main__":unittest.main()
