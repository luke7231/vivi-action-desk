import json
import io
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import server


class DataAndPersistenceTests(unittest.TestCase):
    def test_recollection_and_unknown_not_counted_as_misses(self):
        unique = {(o['question_id'], o['channel'], o['source_snapshot_id']) for o in server.OBSERVATIONS}
        self.assertEqual(len(unique), 450)
        self.assertEqual(len(server.OBSERVATIONS), 456)
        analyses = [server.analysis(q) for q in server.QUESTIONS]
        self.assertTrue(any(a['unknownCount'] > 0 for a in analyses))
        for a in analyses:
            self.assertEqual(a['validCount'] + a['unknownCount'], len(a['observations']))
            self.assertLessEqual(a['hits'], a['validCount'])
        self.assertEqual(sum(a['hits'] for a in analyses), 63)
        self.assertEqual(sum(a['validCount'] for a in analyses), 212)
        self.assertEqual(sum(a['unknownCount'] for a in analyses), 13)

    def test_evidence_drives_distinct_decisions(self):
        by_topic = {}
        for q in server.QUESTIONS:
            by_topic.setdefault(q['topic_id'], []).append(server.analysis(q))
        automation = by_topic['automation']
        self.assertEqual((sum(a['hits'] for a in automation), sum(a['validCount'] for a in automation)), (15, 15))
        self.assertTrue(all(a['priority'] == 0 for a in automation))
        security = by_topic['security']
        owned = sum(1 for a in security for o in a['observations'] if o['valid'] for c in o['citations'] if c['source'].get('brand_id') == 'BR001' and c['source'].get('is_brand_owned') is True)
        self.assertEqual((sum(a['hits'] for a in security), sum(a['validCount'] for a in security), owned), (0, 12, 6))
        self.assertEqual((sum(a['hits'] for a in by_topic['mobile']), sum(a['negativeCount'] for a in by_topic['mobile'])), (8, 5))
        self.assertEqual((sum(a['validCount'] for a in by_topic['scale']), sum(a['unknownCount'] for a in by_topic['scale'])), (9, 6))
        self.assertIsNone(server.SOURCES['SRC024']['brand_id'])
        with tempfile.TemporaryDirectory() as d, patch.object(server, 'DB', Path(d) / 'test.sqlite3'):
            with server.connection() as db:
                by_topic = {t: server.topic_analysis(t, db) for t in ('automation', 'security', 'mobile', 'scale')}
            self.assertEqual(by_topic['automation']['decision'], 'maintain')
            self.assertEqual(by_topic['security']['ownedCitations'], 6)
            self.assertEqual(by_topic['mobile']['decision'], 'product_review')
            self.assertEqual(by_topic['scale']['decision'], 'recollect')
            self.assertEqual(server.analysis(server.QUESTION_BY_ID['Q001'])['similarQuestions'], ['Q002'])

    def test_summary_flags_are_not_authority(self):
        o = next(o for o in server.OBSERVATIONS if o['observation_id'] == 'OBS0127')
        q = server.QUESTION_BY_ID[o['question_id']]
        self.assertNotEqual(o['target_mentioned'], server.observation_view(o, q)['targetMentioned'])

    def test_cache_and_action_database_survive_reopen(self):
        with tempfile.TemporaryDirectory() as d, patch.object(server, 'DB', Path(d) / 'test.sqlite3'):
            with server.connection() as db:
                first = server.cached_analysis(server.QUESTIONS[0], db)
                self.assertFalse(first['cacheHit'])
                db.execute("INSERT INTO actions(question_id,title,note,status) VALUES (?,?,?,?)", ('Q001','검증 액션','근거','todo'))
                db.commit()
            with server.connection() as db:
                second = server.cached_analysis(server.QUESTIONS[0], db)
                self.assertTrue(second['cacheHit'])
                with patch.object(server, 'RULE_VERSION', 'changed-rule'):
                    self.assertFalse(server.cached_analysis(server.QUESTIONS[0], db)['cacheHit'])
                oid = server.latest_observations('Q001')[0]['observation_id']
                server.MENTIONS_BY_OBS[oid].append({'mention_id':'CORRECTION', 'observation_id':oid, 'brand_id':'BR001', 'mention_rank':99, 'mention_context':'listed', 'sentiment':'neutral'})
                try:
                    self.assertFalse(server.cached_analysis(server.QUESTIONS[0], db)['cacheHit'])
                finally:
                    server.MENTIONS_BY_OBS[oid].pop()
                self.assertEqual(db.execute('SELECT status FROM actions').fetchone()['status'], 'todo')
                db.execute("UPDATE actions SET status='done' WHERE question_id='Q001'")
                db.commit()
            with server.connection() as db:
                self.assertEqual(db.execute('SELECT status FROM actions').fetchone()['status'], 'done')

    def test_existing_action_schema_migrates_without_loss(self):
        with tempfile.TemporaryDirectory() as d, patch.object(server, 'DB', Path(d) / 'old.sqlite3'):
            with sqlite3.connect(server.DB) as db:
                db.execute("CREATE TABLE actions (id INTEGER PRIMARY KEY, question_id TEXT, title TEXT, note TEXT, status TEXT, created_at TEXT, updated_at TEXT)")
                db.execute("INSERT INTO actions VALUES (1,'Q008','old action','old note','done','2026-01-01','2026-01-01')")
            with server.connection() as db:
                row = db.execute('SELECT * FROM actions WHERE id=1').fetchone()
                self.assertEqual((row['title'], row['status']), ('old action', 'done'))
                self.assertIsNone(row['evidence_key'])
                self.assertIn('completion_check', row.keys())

    def test_action_api_validation_and_not_found(self):
        def call(method, path, body):
            handler = object.__new__(server.Handler)
            handler.path = path
            encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
            handler.headers = {'Content-Length': str(len(encoded))}
            handler.rfile = io.BytesIO(encoded)
            result = []
            handler.send_json = lambda status, obj: result.append((status, obj))
            getattr(handler, method)()
            return result[0]
        with tempfile.TemporaryDirectory() as d, patch.object(server, 'DB', Path(d) / 'test.sqlite3'):
            self.assertEqual(call('do_GET', '/api/questions/NOT_FOUND', {})[0], 404)
            self.assertEqual(call('do_GET', '/api/topics/NOT_FOUND', {})[0], 404)
            self.assertEqual(call('do_POST', '/api/actions', {'question_id': 'bad', 'title': 'valid'})[0], 400)
            self.assertEqual(call('do_POST', '/api/actions', b'{broken json')[0], 400)
            self.assertEqual(call('do_POST', '/api/actions', b'x' * 10001)[0], 400)
            self.assertEqual(call('do_POST', '/api/actions', {'question_id': 'Q001', 'title': 'x'})[0], 400)
            self.assertEqual(call('do_POST', '/api/actions', {'question_id': 'Q001', 'title': 'x' * 161})[0], 400)
            self.assertEqual(call('do_POST', '/api/actions', {'question_id': 'Q001', 'title': 'valid', 'note': 'x' * 1001})[0], 400)
            base = {'question_id':'Q001', 'title':'valid action', 'evidence_key':server.evidence_key(server.QUESTION_BY_ID['Q001']), 'idempotency_key':'test-request-0001', 'completion_check':'수정 완료', 'success_check':'다음 수집 확인'}
            self.assertEqual(call('do_POST', '/api/actions', {**base, 'evidence_key':'0'*64})[0], 409)
            status, created = call('do_POST', '/api/actions', base)
            self.assertEqual(status, 201)
            self.assertEqual(created['snapshot_id'], 'SNAP-2')
            self.assertEqual(created['evidence_key'], base['evidence_key'])
            self.assertEqual(call('do_POST', '/api/actions', base)[0], 200)
            self.assertEqual(call('do_POST', '/api/actions', {**base, 'title':'another valid action'})[0], 409)
            self.assertEqual(call('do_POST', '/api/actions', {**base, 'idempotency_key':'test-request-0002'})[0], 409)
            self.assertEqual(call('do_POST', '/api/actions', {**base, 'completion_check':''})[0], 400)
            self.assertEqual(call('do_PATCH', f"/api/actions/{created['id']}", {'status': 'invalid'})[0], 400)
            self.assertEqual(call('do_PATCH', '/api/actions/9999', {'status': 'done'})[0], 404)
            self.assertEqual(call('do_PATCH', f"/api/actions/{created['id']}", {'status': 'done'})[1]['status'], 'done')
            with server.connection() as db:
                self.assertEqual(db.execute('SELECT count(*) AS n FROM actions').fetchone()['n'], 1)

    def test_simultaneous_retries_create_one_action(self):
        def submit():
            handler = object.__new__(server.Handler)
            body = {'question_id':'Q008', 'title':'경쟁사 근거 검토', 'note':'정상 응답 확인', 'evidence_key':server.evidence_key(server.QUESTION_BY_ID['Q008']), 'idempotency_key':'same-retry-key-001', 'completion_check':'검토 완료', 'success_check':'다음 수집 비교'}
            raw = json.dumps(body).encode()
            handler.path = '/api/actions'; handler.headers = {'Content-Length':str(len(raw))}; handler.rfile = io.BytesIO(raw)
            result = []
            handler.send_json = lambda status, obj: result.append((status,obj))
            handler.do_POST()
            return result[0]
        with tempfile.TemporaryDirectory() as d, patch.object(server, 'DB', Path(d) / 'test.sqlite3'):
            with ThreadPoolExecutor(max_workers=2) as executor:
                a, b = list(executor.map(lambda _: submit(), range(2)))
            self.assertEqual({a[0], b[0]}, {200, 201})
            self.assertEqual(a[1]['id'], b[1]['id'])
            with server.connection() as db:
                self.assertEqual(db.execute('SELECT count(*) AS n FROM actions').fetchone()['n'], 1)


if __name__ == '__main__':
    unittest.main()
