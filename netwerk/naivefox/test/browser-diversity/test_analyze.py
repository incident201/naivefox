import json
from pathlib import Path
import tempfile
import unittest

import analyze


class AnalysisTests(unittest.TestCase):
    def rows(self):
        values=[]
        for family in range(8):
            for variant in range(2):
                for role in ("firefox_a","firefox_b","classic","no_connect"):
                    values.append({"family":f"f{family}","partition":family//2,"variant":variant,"role":role,
                                   "session_id":f"f{family}-{variant}-{role}","_weight_group":f"f{family}",
                                   "features":{"whole_client_wire_bytes":100.0 if role in ("classic","no_connect") else 1.0}})
        return values

    def test_complete_family_holdout_and_calibration(self):
        rows=self.rows()
        for fold in range(4):
            train,calibration,test=analyze.split(rows,fold)
            groups=[{rows[i]["family"] for i in indices} for indices in (train,calibration,test)]
            self.assertFalse(groups[0]&groups[1] or groups[0]&groups[2] or groups[1]&groups[2])
            self.assertEqual(set(train)|set(calibration)|set(test),set(range(len(rows))))

    def test_threshold_is_conservative_at_ties(self):
        for scores in ([.5]*48,[i/48 for i in range(48)]):
            threshold=analyze.fpr_threshold(scores,.05)
            self.assertLessEqual(sum(score>=threshold for score in scores),2)
        self.assertGreater(analyze.fpr_threshold([.1,.2,.3],.01),.3)

    def test_held_out_feature_cannot_change_fit_or_threshold(self):
        rows=self.rows()
        a=analyze.comparison(rows,"classic","whole",11)
        self.assertEqual(a["auc"],1.0)
        for row in rows:
            if row["partition"]==0:row["features"]["whole_test_only_wire_bytes"]=1e12
        b=analyze.comparison(rows,"classic","whole",11)
        self.assertEqual(a["folds"][0],b["folds"][0])
        self.assertEqual([p for p in a["predictions"] if p["fold"]==0],[p for p in b["predictions"] if p["fold"]==0])
        self.assertEqual(analyze.comparison(self.rows(),"firefox_null","whole",12)["auc"],.5)

    def test_pilot_is_not_a_main_dataset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"benchmark.json").write_text(json.dumps({"pilot":True,"status":"complete","completed_samples":392}))
            with self.assertRaises(ValueError):analyze.load(root)


if __name__=="__main__":unittest.main()
