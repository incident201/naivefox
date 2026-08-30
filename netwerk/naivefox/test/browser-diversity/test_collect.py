import collections
import unittest
from pathlib import Path
from types import SimpleNamespace

import collect


class CollectionTests(unittest.TestCase):
    def manifest(self):
        return {"pages":[{"id":f"f{family}-{variant}","family":f"f{family}","variant":variant,"partition":family//6} for family in range(24) for variant in range(4)]}

    def test_schedule_is_balanced_and_frozen(self):
        manifest=self.manifest()
        a=collect.schedule(manifest,123)
        self.assertEqual(a,collect.schedule(manifest,123))
        self.assertNotEqual(a,collect.schedule(manifest,124))
        self.assertEqual(len(a),392)
        self.assertEqual(collections.Counter(row["role"] for row in a),{"firefox_a":96,"firefox_b":96,"classic":96,"no_connect":96,"fronting-browser":8})
        for page in manifest["pages"]:
            roles=[row["role"] for row in a if row["page"]==page["id"] and row["role"]!="fronting-browser"]
            self.assertEqual(set(roles),set(collect.ROLES))
        self.assertEqual(len(collect.schedule(manifest,123,True,["f0-0"])),4)

    def test_diversity_cannot_silently_change_ordinary_capture_modes(self):
        campaign=collect.carrier.Campaign(Path("/unused"),"h2")
        workload=SimpleNamespace(fronting=False,capture_seconds=5)
        with self.assertRaises(ValueError):campaign.sample("unused",browser_workload=workload)
        workload.capture_seconds=2
        with self.assertRaises(ValueError):campaign.sample("unused",capture=True,browser_workload=workload)


if __name__=="__main__":unittest.main()
