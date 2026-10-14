#!/usr/bin/env python3
"""Local VIVI MVP API. Only Python standard library is required."""
import hashlib
import json
import os
import re
import sqlite3
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent
DATA = ROOT / 'source' / 'VIVI_MVP_Sample_Dataset'
DB = Path(os.environ.get('VIVI_DB', ROOT / 'vivi.sqlite3'))
RULE_VERSION = 'priority-v4'
SCHEMA_LOCK = threading.Lock()


def read(name):
    return json.loads((DATA / (name + '.json')).read_text())


QUESTIONS = read('questions')
OBSERVATIONS = read('observations')
MENTIONS = read('mentions')
CITATIONS = read('citations')
SOURCES = {s['source_id']: s for s in read('sources')}
BRANDS = {b['brand_id']: b for b in read('brands')}
QUESTION_BY_ID = {q['question_id']: q for q in QUESTIONS}
SIMILAR_QUESTION_GROUPS = [('Q001', 'Q002'), ('Q056', 'Q057'), ('Q066', 'Q067')]
MENTIONS_BY_OBS = defaultdict(list)
CITATIONS_BY_OBS = defaultdict(list)
for row in MENTIONS:
    MENTIONS_BY_OBS[row['observation_id']].append(row)
for row in CITATIONS:
    CITATIONS_BY_OBS[row['observation_id']].append(row)


def connection():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=5000')
    with SCHEMA_LOCK:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY, question_id TEXT NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN (\'todo\',\'doing\',\'done\')), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        db.execute('CREATE TABLE IF NOT EXISTS analysis_cache (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(actions)')}
        for name in ('evidence_key', 'snapshot_id', 'owner', 'completion_check', 'success_check', 'idempotency_key'):
            if name not in columns:
                db.execute(f'ALTER TABLE actions ADD COLUMN {name} TEXT')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_action_idempotency ON actions(idempotency_key)')
        db.commit()
    return db


def latest_observations(question_id):
    rows = [o for o in OBSERVATIONS if o['question_id'] == question_id]
    # Same question/channel/snapshot can be recollected; newest timestamp, then id wins.
    latest = {}
    for row in rows:
        key = (row['channel'], row['source_snapshot_id'])
        if key not in latest or (row['collected_at'], row['observation_id']) > (latest[key]['collected_at'], latest[key]['observation_id']):
            latest[key] = row
    newest_snapshot = max((o['source_snapshot_id'] for o in latest.values()), default='')
    return sorted((o for o in latest.values() if o['source_snapshot_id'] == newest_snapshot), key=lambda o: o['channel'])


def observation_view(o, q):
    ms = MENTIONS_BY_OBS[o['observation_id']]
    cs = CITATIONS_BY_OBS[o['observation_id']]
    valid = o['collection_status'] == 'complete' and o.get('response_language') == q['language']
    target = any(m['brand_id'] == 'BR001' for m in ms) if valid else None
    return {
        'id': o['observation_id'], 'channel': o['channel'], 'date': o['collected_at'],
        'status': o['collection_status'], 'qualityFlag': o.get('quality_flag'),
        'language': o.get('response_language'), 'valid': valid, 'targetMentioned': target,
        'response': o.get('response_text') or '',
        'mentions': [{**m, 'brand_name': BRANDS.get(m['brand_id'], {}).get('name', m['brand_id'])} for m in ms],
        'citations': [{**c, 'source': SOURCES.get(c['source_id'], {})} for c in cs],
    }


def evidence_key(q):
    evidence = []
    for observation in latest_observations(q['question_id']):
        oid = observation['observation_id']
        evidence.append({
            'observation': observation,
            'mentions': MENTIONS_BY_OBS[oid],
            'citations': [{**citation, 'source': SOURCES.get(citation['source_id'])} for citation in CITATIONS_BY_OBS[oid]],
        })
    return hashlib.sha256(json.dumps([q, evidence, RULE_VERSION, 'no-model', 'no-prompt'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def analysis(q):
    observations = [observation_view(o, q) for o in latest_observations(q['question_id'])]
    valid = [o for o in observations if o['valid']]
    hits = sum(o['targetMentioned'] for o in valid)
    gaps = len(valid) - hits
    visibility = round(hits / len(valid), 3) if valid else None
    owned_citations = sum(1 for o in valid for c in o['citations'] if c['source'].get('brand_id') == 'BR001' and c['source'].get('is_brand_owned') is True)
    unknown_owner_citations = sum(1 for o in valid for c in o['citations'] if c['source'].get('is_brand_owned') is True and not c['source'].get('brand_id'))
    competitors = defaultdict(int)
    for o in valid:
        for brand_id in {m['brand_id'] for m in o['mentions'] if m['brand_id'] != 'BR001'}:
            competitors[BRANDS.get(brand_id, {}).get('name', brand_id)] += 1
    # A gap is only counted from successful, language-matched observations.
    negative = sum(1 for o in valid for m in o['mentions'] if m['brand_id'] == 'BR001' and m['sentiment'] == 'negative')
    interest_factor = 1 + min(q['estimated_monthly_interest'], 3000) / 6000
    priority = round(q['importance_score'] * (gaps / len(valid)) * interest_factor) if len(valid) >= 2 else None
    if len(valid) < 2:
        decision = 'recollect'
        suggestion = '수집 상태를 확인한 뒤 다시 분석하세요.'
        reason = f'최신 수집의 신뢰 가능한 응답이 {len(valid)}건뿐입니다. 최소 2개 채널을 확인한 뒤 판단합니다.'
        success_check = '다음 수집에서 정상·언어 일치 채널 2개 이상 확보'
        completion_check = '수집 실패 원인을 확인하고 재수집 요청 완료'
    elif negative:
        decision = 'product_review'
        suggestion = f'{q["topic_label"]} 관련 부정적 언급을 제품팀과 점검하세요.'
        reason = f'정상 응답 {len(valid)}건에서 자사 부정적 언급 {negative}건이 확인됐습니다. 콘텐츠 보강 전에 불만 원인을 확인하세요.'
        success_check = '제품 이슈 검토 완료 후 같은 질문·채널의 부정 언급 추적'
        completion_check = '부정 언급 사례를 제품팀에 전달하고 이슈 검토 완료'
    elif gaps == 0:
        decision = 'maintain'
        suggestion = '현재 노출을 유지하고 다음 수집에서 변화를 확인하세요.'
        reason = f'정상 응답 {len(valid)}건 모두에서 자사 언급을 확인했습니다.'
        success_check = '다음 수집에서 동일 질문·채널의 자사 언급 유지 확인'
        completion_check = '다음 수집 점검 일정과 담당자 지정 완료'
    elif owned_citations == 0:
        decision = 'improve'
        suggestion = f'{q["topic_label"]} 관련 인용을 점검하고 비교·설명 근거를 보강하세요.'
        reason = f'정상 응답 {len(valid)}건 중 {gaps}건에 자사 언급이 없고, 확인된 자사 소유 인용은 0건입니다. 소유 정보가 없는 출처는 별도 확인이 필요합니다.'
        success_check = '다음 수집에서 동일 질문·채널의 자사 언급 증가 확인'
        completion_check = '인용 출처 검토 및 비교·설명 근거 수정 완료'
    else:
        decision = 'improve'
        suggestion = f'{q["topic_label"]} 질문에 맞는 자사 페이지의 근거와 제목을 보강하세요.'
        reason = f'정상 응답 {len(valid)}건 중 {gaps}건에 자사 언급이 없습니다. 자사 소유 인용 {owned_citations}건을 확인했습니다.'
        success_check = '다음 수집에서 동일 질문·채널의 자사 언급 증가 확인'
        completion_check = '질문에 맞춘 기존 페이지 수정 완료'
    snapshot_id = next((o['source_snapshot_id'] for o in latest_observations(q['question_id'])), '')
    similar = next(([other for other in group if other != q['question_id']] for group in SIMILAR_QUESTION_GROUPS if q['question_id'] in group), [])
    return {'question': q, 'observations': observations, 'validCount': len(valid), 'unknownCount': len(observations)-len(valid), 'hits': hits, 'negativeCount': negative, 'ownedCitations': owned_citations, 'unknownOwnerCitations': unknown_owner_citations, 'competitors': sorted(({'name': name, 'count': count} for name, count in competitors.items()), key=lambda x: -x['count']), 'visibility': visibility, 'priority': priority, 'scoreComponents': {'importance': q['importance_score'], 'gapRate': round(gaps / len(valid), 3) if valid else None, 'interestFactor': round(interest_factor, 3)}, 'decision': decision, 'suggestion': suggestion, 'reason': reason, 'completionCheck': completion_check, 'successCheck': success_check, 'similarQuestions': similar, 'snapshotId': snapshot_id, 'evidenceKey': evidence_key(q), 'ruleVersion': RULE_VERSION}


def topic_analysis(topic_id, db):
    questions = [q for q in QUESTIONS if q['topic_id'] == topic_id]
    if not questions:
        return None
    details = [cached_analysis(q, db) for q in questions]
    total = sum(len(a['observations']) for a in details)
    valid = sum(a['validCount'] for a in details)
    hits = sum(a['hits'] for a in details)
    negative = sum(a['negativeCount'] for a in details)
    owned = sum(a['ownedCitations'] for a in details)
    unknown_owner = sum(a['unknownOwnerCitations'] for a in details)
    competitor_counts = defaultdict(int)
    for a in details:
        for competitor in a['competitors']:
            competitor_counts[competitor['name']] += competitor['count']
    coverage = valid / total if total else 0
    gap = (valid - hits) / valid if valid else 0
    importance = round(sum(q['importance_score'] for q in questions) / len(questions))
    interest_factor = 1 + min(sum(q['estimated_monthly_interest'] for q in questions) / len(questions), 3000) / 6000
    score = round(importance * gap * interest_factor * coverage) if valid else None
    if coverage < 0.8:
        decision, next_step = 'recollect', '수집 품질을 먼저 점검하세요.'
    elif negative >= 3:
        decision, next_step = 'product_review', '부정 언급의 제품 원인을 먼저 점검하세요.'
    elif hits == valid:
        decision, next_step = 'maintain', '현재 노출을 유지하며 다음 수집을 비교하세요.'
    else:
        decision, next_step = 'improve', '미언급 질문의 근거를 확인하고 개선 액션을 선택하세요.'
    return {'topicId': topic_id, 'label': questions[0]['topic_label'], 'questionCount': len(questions), 'snapshotId': details[0]['snapshotId'], 'observationCount': total, 'validCount': valid, 'unknownCount': total-valid, 'hits': hits, 'negativeCount': negative, 'ownedCitations': owned, 'unknownOwnerCitations': unknown_owner, 'competitors': sorted(({'name': name, 'count': count} for name, count in competitor_counts.items()), key=lambda x: -x['count']), 'coverage': round(coverage, 3), 'score': score, 'scoreComponents': {'meanImportance': importance, 'gapRate': round(gap, 3), 'interestFactor': round(interest_factor, 3), 'coverage': round(coverage, 3)}, 'decision': decision, 'nextStep': next_step, 'questions': [{k: v for k, v in a.items() if k != 'observations'} for a in details]}


def cached_analysis(q, db):
    key = evidence_key(q)
    row = db.execute('SELECT payload FROM analysis_cache WHERE cache_key=?', (key,)).fetchone()
    if row:
        return {**json.loads(row['payload']), 'cacheHit': True}
    payload = analysis(q)
    db.execute('INSERT OR REPLACE INTO analysis_cache(cache_key,payload) VALUES (?,?)', (key, json.dumps(payload, ensure_ascii=False)))
    db.commit()
    return {**payload, 'cacheHit': False}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 10000:
            raise ValueError('요청 본문이 너무 큽니다.')
        try:
            return json.loads(self.rfile.read(size))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError('유효한 JSON을 입력하세요.')

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        with connection() as db:
            if path == '/api/topics':
                topic_ids = sorted({q['topic_id'] for q in QUESTIONS})
                rows = [topic_analysis(topic_id, db) for topic_id in topic_ids]
                rows.sort(key=lambda row: row['score'] if row['score'] is not None else -1, reverse=True)
                self.send_json(200, {'items': rows})
            elif path.startswith('/api/topics/'):
                row = topic_analysis(path.rsplit('/', 1)[-1], db)
                self.send_json(200, row) if row else self.send_json(404, {'error': '주제를 찾을 수 없습니다.'})
            elif path == '/api/questions':
                query = params.get('q', [''])[0].casefold().strip()
                topic = params.get('topic', [''])[0]
                rows = [cached_analysis(q, db) for q in QUESTIONS if (not query or query in q['query'].casefold() or query in q['topic_label'].casefold()) and (not topic or q['topic_id'] == topic)]
                rows.sort(key=lambda x: x['priority'] if x['priority'] is not None else -1, reverse=True)
                self.send_json(200, {'items': [{k: v for k, v in row.items() if k != 'observations'} for row in rows], 'topics': sorted({(q['topic_id'], q['topic_label']) for q in QUESTIONS})})
            elif path.startswith('/api/questions/'):
                q = QUESTION_BY_ID.get(path.rsplit('/', 1)[-1])
                self.send_json(200, cached_analysis(q, db)) if q else self.send_json(404, {'error': '질문을 찾을 수 없습니다.'})
            elif path == '/api/actions':
                rows = db.execute('SELECT * FROM actions ORDER BY id DESC').fetchall()
                self.send_json(200, {'items': [{**dict(row), 'question_topic_id': QUESTION_BY_ID[row['question_id']]['topic_id']} for row in rows]})
            else:
                self.send_json(404, {'error': '경로를 찾을 수 없습니다.'})

    def do_POST(self):
        if self.path != '/api/actions':
            return self.send_json(404, {'error': '경로를 찾을 수 없습니다.'})
        try:
            data = self.body()
            if not isinstance(data, dict):
                raise ValueError('객체를 입력하세요.')
            qid, title, note = data.get('question_id'), data.get('title'), data.get('note', '')
            evidence = data.get('evidence_key')
            idem = data.get('idempotency_key')
            owner = data.get('owner', '')
            completion_check = data.get('completion_check', '')
            success_check = data.get('success_check', '')
            if qid not in QUESTION_BY_ID or not isinstance(title, str) or not 3 <= len(title.strip()) <= 160 or not isinstance(note, str) or len(note) > 1000:
                raise ValueError('유효한 질문과 3~160자 제목, 1000자 이내 메모를 입력하세요.')
            if not isinstance(evidence, str) or not re.fullmatch(r'[0-9a-f]{64}', evidence):
                raise ValueError('근거 버전이 필요합니다. 질문을 다시 선택하세요.')
            if not isinstance(idem, str) or not re.fullmatch(r'[A-Za-z0-9-]{8,80}', idem):
                raise ValueError('유효한 저장 요청 번호가 필요합니다.')
            if not isinstance(owner, str) or len(owner.strip()) > 80 or not isinstance(completion_check, str) or not 3 <= len(completion_check.strip()) <= 300 or not isinstance(success_check, str) or not 3 <= len(success_check.strip()) <= 300:
                raise ValueError('담당자는 80자, 실행 완료·후속 관측 기준은 각각 3~300자여야 합니다.')
            with connection() as db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute('SELECT * FROM actions WHERE idempotency_key=?', (idem,)).fetchone()
                if existing:
                    db.commit()
                    if (existing['question_id'], existing['title'], existing['note'], existing['owner'], existing['completion_check'], existing['success_check'], existing['evidence_key']) != (qid, title.strip(), note.strip(), owner.strip(), completion_check.strip(), success_check.strip(), evidence):
                        return self.send_json(409, {'error': '같은 저장 요청 번호에 다른 내용이 들어왔습니다. 질문을 다시 열어 주세요.'})
                    return self.send_json(200, dict(existing))
                if evidence != evidence_key(QUESTION_BY_ID[qid]):
                    db.rollback()
                    return self.send_json(409, {'error': '근거가 변경됐습니다. 질문을 다시 열고 확인하세요.'})
                duplicate = db.execute('SELECT id FROM actions WHERE question_id=? AND evidence_key=? AND title=?', (qid, evidence, title.strip())).fetchone()
                if duplicate:
                    db.rollback()
                    return self.send_json(409, {'error': '같은 근거와 제목의 액션이 이미 있습니다.'})
                snapshot = latest_observations(qid)[0]['source_snapshot_id']
                cursor = db.execute('INSERT INTO actions(question_id,title,note,status,evidence_key,snapshot_id,owner,completion_check,success_check,idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?)', (qid, title.strip(), note.strip(), 'todo', evidence, snapshot, owner.strip(), completion_check.strip(), success_check.strip(), idem))
                db.commit()
                self.send_json(201, dict(db.execute('SELECT * FROM actions WHERE id=?', (cursor.lastrowid,)).fetchone()))
        except ValueError as e:
            self.send_json(400, {'error': str(e)})

    def do_PATCH(self):
        if not self.path.startswith('/api/actions/'):
            return self.send_json(404, {'error': '경로를 찾을 수 없습니다.'})
        try:
            aid = int(self.path.rsplit('/', 1)[-1])
            data = self.body()
            if not isinstance(data, dict) or data.get('status') not in ('todo', 'doing', 'done'):
                raise ValueError('상태는 todo, doing, done 중 하나여야 합니다.')
            with connection() as db:
                cursor = db.execute('UPDATE actions SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', (data['status'], aid))
                db.commit()
                row = db.execute('SELECT * FROM actions WHERE id=?', (aid,)).fetchone()
                self.send_json(200, dict(row)) if cursor.rowcount else self.send_json(404, {'error': '액션을 찾을 수 없습니다.'})
        except (ValueError, TypeError):
            self.send_json(400, {'error': '유효한 액션 번호와 상태를 입력하세요.'})


if __name__ == '__main__':
    connection().close()
    port = int(os.environ.get('PORT', '8000'))
    print(f'VIVI API http://localhost:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
