import unittest
from types import SimpleNamespace

from workload import BrowserWorkload


class WorkloadTests(unittest.TestCase):
    def test_port_matched_corpus_routes_cannot_intercept_connect(self):
        page={"id":"a-0","target_id":"a-1"}
        origin=SimpleNamespace(port=1234,pages={"a-1":{"id":"a-1"}})
        workload=BrowserWorkload(origin,page)
        campaign=SimpleNamespace(port=4567,target_port=4568)
        config={"apps":{"http":{"servers":{"shared":{"listen":["127.0.0.1:4567","127.0.0.1:4568"],"routes":[]}}}}}
        workload.configure(config,campaign,"reference")
        routes=config["apps"]["http"]["servers"]["shared"]["routes"]
        self.assertEqual(len(routes),2)
        for route in routes:self.assertNotIn("CONNECT",route["match"][0]["method"])
        self.assertIn("localhost:4567",routes[0]["match"][0]["expression"])
        self.assertEqual(routes[0]["handle"][1]["headers"]["request"]["set"]["X-Corpus-Page"],["a-0"])


if __name__=="__main__":unittest.main()
