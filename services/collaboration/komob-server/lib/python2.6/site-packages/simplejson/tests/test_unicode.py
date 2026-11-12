from unittest import TestCase

import simplejson as json

class TestUnicode(TestCase):
    def test_encoding1(self):
        encoder = json.JSONEncoder(encoding='utf-8')
        u = r'\N{GREEK SMALL LETTER ALPHA}\N{GREEK CAPITAL LETTER OMEGA}'
        s = u.encode('utf-8')
        ju = encoder.encode(u)
        js = encoder.encode(s)
        self.assertEquals(ju, js)

    def test_encoding2(self):
        u = r'\N{GREEK SMALL LETTER ALPHA}\N{GREEK CAPITAL LETTER OMEGA}'
        s = u.encode('utf-8')
        ju = json.dumps(u, encoding='utf-8')
        js = json.dumps(s, encoding='utf-8')
        self.assertEquals(ju, js)

    def test_encoding3(self):
        u = r'\N{GREEK SMALL LETTER ALPHA}\N{GREEK CAPITAL LETTER OMEGA}'
        j = json.dumps(u)
        self.assertEquals(j, '"\\u03b1\\u03a9"')

    def test_encoding4(self):
        u = r'\N{GREEK SMALL LETTER ALPHA}\N{GREEK CAPITAL LETTER OMEGA}'
        j = json.dumps([u])
        self.assertEquals(j, '["\\u03b1\\u03a9"]')

    def test_encoding5(self):
        u = r'\N{GREEK SMALL LETTER ALPHA}\N{GREEK CAPITAL LETTER OMEGA}'
        j = json.dumps(u, ensure_ascii=False)
        self.assertEquals(j, '"' + u + '"')

    def test_encoding6(self):
        u = r'\N{GREEK SMALL LETTER ALPHA}\N{GREEK CAPITAL LETTER OMEGA}'
        j = json.dumps([u], ensure_ascii=False)
        self.assertEquals(j, '["' + u + '"]')

    def test_big_unicode_encode(self):
        u = '\U0001d120'
        self.assertEquals(json.dumps(u), '"\\ud834\\udd20"')
        self.assertEquals(json.dumps(u, ensure_ascii=False), '"\U0001d120"')

    def test_big_unicode_decode(self):
        u = 'z\U0001d120x'
        self.assertEquals(json.loads('"' + u + '"'), u)
        self.assertEquals(json.loads('"z\\ud834\\udd20x"'), u)

    def test_unicode_decode(self):
        for i in range(0, 0xd7ff):
            u = chr(i)
            #s = '"\\u{0:04x}"'.format(i)
            s = '"\\u%04x"' % (i,)
            self.assertEquals(json.loads(s), u)

    def test_object_pairs_hook_with_unicode(self):
        s = '{"xkd":1, "kcw":2, "art":3, "hxm":4, "qrt":5, "pad":6, "hoy":7}'
        p = [("xkd", 1), ("kcw", 2), ("art", 3), ("hxm", 4),
             ("qrt", 5), ("pad", 6), ("hoy", 7)]
        self.assertEqual(json.loads(s), eval(s))
        self.assertEqual(json.loads(s, object_pairs_hook=lambda x: x), p)
        od = json.loads(s, object_pairs_hook=json.OrderedDict)
        self.assertEqual(od, json.OrderedDict(p))
        self.assertEqual(type(od), json.OrderedDict)
        # the object_pairs_hook takes priority over the object_hook
        self.assertEqual(json.loads(s,
                                    object_pairs_hook=json.OrderedDict,
                                    object_hook=lambda x: None),
                         json.OrderedDict(p))


    def test_default_encoding(self):
        self.assertEquals(json.loads('{"a": "\xe9"}'.encode('utf-8')),
            {'a': '\xe9'})

    def test_unicode_preservation(self):
        self.assertEquals(type(json.loads('""')), str)
        self.assertEquals(type(json.loads('"a"')), str)
        self.assertEquals(type(json.loads('["a"]')[0]), str)

    def test_ensure_ascii_false_returns_unicode(self):
        # http://code.google.com/p/simplejson/issues/detail?id=48
        self.assertEquals(type(json.dumps([], ensure_ascii=False)), str)
        self.assertEquals(type(json.dumps(0, ensure_ascii=False)), str)
        self.assertEquals(type(json.dumps({}, ensure_ascii=False)), str)
        self.assertEquals(type(json.dumps("", ensure_ascii=False)), str)

    def test_ensure_ascii_false_bytestring_encoding(self):
        # http://code.google.com/p/simplejson/issues/detail?id=48
        doc1 = {'quux': 'Arr\xc3\xaat sur images'}
        doc2 = {'quux': 'Arr\xeat sur images'}
        doc_ascii = '{"quux": "Arr\\u00eat sur images"}'
        doc_unicode = '{"quux": "Arr\xeat sur images"}'
        self.assertEquals(json.dumps(doc1), doc_ascii)
        self.assertEquals(json.dumps(doc2), doc_ascii)
        self.assertEquals(json.dumps(doc1, ensure_ascii=False), doc_unicode)
        self.assertEquals(json.dumps(doc2, ensure_ascii=False), doc_unicode)
