"""核对演练资料的完整性及关键的输入关系。"""
from hashlib import sha256
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent / 'records'


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / 'catalog.json').read_bytes())
        self.records = {}
        for name, expected in self.catalog.items():
            raw = (ROOT / (name + '.jsonl')).read_bytes()
            self.assertEqual(sha256(raw).hexdigest(), expected['sha256'])
            rows = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual(len(rows), expected['rows'])
            self.assertEqual(len({row['id'] for row in rows}), len(rows))
            self.records[name] = rows

    def test_business_inputs(self):
        d = self.records
        self.assertTrue({'played','scheduled','administrative','withdrawal'} <= {x['status'] for x in d['games']})
        self.assertTrue(any(x['stage']=='bridge' for x in d['games']))
        self.assertTrue(any(x['operation']=='promoted_locked' for x in d['rulings']))
        self.assertTrue(any(x['blocked'] for x in d['slots']))
