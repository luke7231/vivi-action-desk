import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

type Question = { question_id: string; query: string; topic_label: string; topic_id: string; importance_score: number; estimated_monthly_interest: number };
type Mention = { brand_id: string; brand_name: string; sentiment: string; mention_context: string };
type Citation = { source_id: string; source: { title?: string|null; domain?: string; is_brand_owned?: boolean; brand_id?: string|null } };
type Observation = { id: string; channel: string; status: string; valid: boolean; targetMentioned: boolean|null; response: string; qualityFlag: string|null; language: string; mentions: Mention[]; citations: Citation[] };
type Competitor = { name: string; count: number };
type Analysis = {
  question: Question; observations?: Observation[]; validCount: number; unknownCount: number; hits: number;
  negativeCount: number; ownedCitations: number; unknownOwnerCitations: number; competitors: Competitor[];
  visibility: number|null; priority: number|null; scoreComponents: {importance:number;gapRate:number|null;interestFactor:number};
  decision: string; suggestion: string; reason: string; completionCheck: string; successCheck: string; similarQuestions: string[]; snapshotId: string; evidenceKey: string;
};
type Topic = {
  topicId: string; label: string; questionCount: number; snapshotId: string; observationCount: number; validCount: number;
  unknownCount: number; hits: number; negativeCount: number; ownedCitations: number; unknownOwnerCitations: number;
  competitors: Competitor[]; coverage: number; score: number|null;
  scoreComponents: {meanImportance:number;gapRate:number;interestFactor:number;coverage:number};
  decision: string; nextStep: string;
};
type Action = { id: number; question_id: string; question_topic_id: string; title: string; note: string; owner: string|null; completion_check: string|null; success_check: string|null; evidence_key: string|null; snapshot_id: string|null; status: 'todo'|'doing'|'done'; created_at: string };

class ApiError extends Error { constructor(message:string, public status:number) { super(message); } }
async function api<T>(path:string, options?:RequestInit):Promise<T> {
  let response:Response;
  try { response = await fetch(path, { ...options, headers: { 'Content-Type':'application/json', ...(options?.headers || {}) } }); }
  catch { throw new ApiError('서버에 연결할 수 없습니다. 입력은 유지되며 다시 시도할 수 있습니다.', 0); }
  let data;
  try { data = await response.json(); }
  catch { throw new ApiError('서버 응답을 확인할 수 없습니다. 입력은 유지되며 다시 시도할 수 있습니다.', response.status); }
  if (!response.ok) throw new ApiError(data.error || '요청에 실패했습니다.', response.status);
  return data as T;
}
const decisionLabel:Record<string,string> = {improve:'개선 검토',maintain:'유지',recollect:'수집 점검',product_review:'제품 점검'};
const percent = (value:number) => `${Math.round(value*100)}%`;

function App() {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [selectedTopic, setSelectedTopic] = useState<Topic|null>(null);
  const [items, setItems] = useState<Analysis[]>([]);
  const [selected, setSelected] = useState<Analysis|null>(null);
  const [activeView, setActiveView] = useState<'analysis'|'actions'>('analysis');
  const [detailTab, setDetailTab] = useState<'evidence'|'action'>('evidence');
  const [actions, setActions] = useState<Action[]>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [message, setMessage] = useState('');
  const [retry, setRetry] = useState(0);
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [owner, setOwner] = useState('');
  const [completionCheck, setCompletionCheck] = useState('');
  const [successCheck, setSuccessCheck] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const savingRef = useRef(false);
  const idempotencyRef = useRef('');

  async function refreshActions() {
    const result = await api<{items:Action[]}>('/api/actions');
    setActions(result.items);
  }
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    Promise.all([api<{items:Topic[]}>('/api/topics'), api<{items:Action[]}>('/api/actions')])
      .then(([topicData, actionData]) => {
        if (!active) return;
        setTopics(topicData.items);
        setActions(actionData.items);
        setSelectedTopic(current => current ? topicData.items.find(t => t.topicId === current.topicId) || null : topicData.items[0] || null);
      })
      .catch(e => { if (active) setError((e as Error).message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [retry]);
  useEffect(() => {
    let active = true;
    if (!selectedTopic) return;
    setLoading(true);
    const timer = setTimeout(() => api<{items:Analysis[]}>(`/api/questions?q=${encodeURIComponent(query)}&topic=${encodeURIComponent(selectedTopic.topicId)}`)
      .then(data => { if (active) setItems(data.items); })
      .catch(e => { if (active) setError((e as Error).message); })
      .finally(() => { if (active) setLoading(false); }), 150);
    return () => { active = false; clearTimeout(timer); };
  }, [query, selectedTopic?.topicId, retry]);

  async function showDetail(id:string) {
    setDetailLoading(true); setError(''); setFormError(''); setMessage(''); setSaved(false);
    setDetailTab('evidence');
    try {
      const data = await api<Analysis>(`/api/questions/${id}`);
      setSelected(data); setTitle(data.suggestion); setNote(data.reason);
      setCompletionCheck(data.completionCheck); setSuccessCheck(data.successCheck); setOwner(''); idempotencyRef.current = '';
    } catch (e) { setError((e as Error).message); }
    finally { setDetailLoading(false); }
  }
  function chooseTopic(topic:Topic) {
    setSelectedTopic(topic); setSelected(null); setQuery(''); setError(''); setFormError(''); setMessage('');
  }
  async function createAction(e:React.FormEvent) {
    e.preventDefault();
    if (!selected || savingRef.current || saved) return;
    savingRef.current = true; setSaving(true); setFormError(''); setMessage('');
    if (!idempotencyRef.current) idempotencyRef.current = crypto.randomUUID();
    try {
      const saved = await api<Action>('/api/actions', { method:'POST', body:JSON.stringify({
        question_id:selected.question.question_id, evidence_key:selected.evidenceKey,
        idempotency_key:idempotencyRef.current, title, note, owner, completion_check:completionCheck, success_check:successCheck
      }) });
      await refreshActions();
      setMessage(`액션 #${saved.id}번을 저장했습니다. 아래 목록에서 상태를 변경할 수 있습니다.`);
      setSaved(true);
      setActiveView('actions');
      idempotencyRef.current = '';
    } catch (error) {
      setFormError((error as Error).message);
      if (error instanceof ApiError && error.status === 409) idempotencyRef.current = '';
    } finally { savingRef.current = false; setSaving(false); }
  }
  async function changeStatus(id:number, status:Action['status']) {
    setError(''); setMessage('');
    try { await api<Action>(`/api/actions/${id}`, {method:'PATCH', body:JSON.stringify({status})}); await refreshActions(); setMessage('상태를 저장했습니다.'); }
    catch(e) { setError((e as Error).message); }
  }
  async function viewActionEvidence(action:Action) {
    setActiveView('analysis');
    setDetailTab('evidence');
    const topic = topics.find(t => t.topicId === action.question_topic_id);
    if (topic && topic.topicId !== selectedTopic?.topicId) chooseTopic(topic);
    await showDetail(action.question_id);
    document.getElementById('detail-panel')?.scrollIntoView({behavior:'smooth',block:'start'});
  }

  const totalValid = topics.reduce((sum, topic) => sum + topic.validCount, 0);
  return <div className="shell">
    <aside className="sidebar" aria-label="주 메뉴">
      <div className="brand"><span className="logo">V</span><div><strong>VIVI</strong><small>Action Desk</small></div></div>
      <div className="nav-group-label">워크스페이스</div>
      <nav className="side-nav" aria-label="화면 선택">
        <button className={activeView==='analysis'?'selected':''} aria-current={activeView==='analysis'?'page':undefined} onClick={()=>setActiveView('analysis')}><span className="nav-icon" aria-hidden="true">▦</span>관측 분석</button>
        <button className={activeView==='actions'?'selected':''} aria-current={activeView==='actions'?'page':undefined} onClick={()=>setActiveView('actions')}><span className="nav-icon" aria-hidden="true">☷</span>액션 관리<span className="nav-count">{actions.length}</span></button>
      </nav>
      <div className="sidebar-bottom"><strong>OrbitCRM</strong><span>합성 데이터 · 로컬 데모</span></div>
    </aside>
    <div className="app-area">
      <header className="topbar"><span>VIVI <span className="topbar-divider">/</span> {activeView==='analysis'?'관측 분석':'액션 관리'}</span><span className="topbar-meta">최신 근거 {selectedTopic?.snapshotId || 'SNAP-2'} <span className="topbar-dot">·</span> 합성 데이터</span></header>
      <main className="content">
        {error && <div className="error" role="alert">{error} <button onClick={()=>setRetry(v=>v+1)}>다시 시도</button></div>}
        {message && <div className="success" role="status">{message}</div>}
        {activeView==='analysis' ? <>
          <section className="page-heading"><div><h1>관측 분석</h1><p>주제를 선택하고 질문별 근거를 확인해 다음 액션을 결정하세요.</p></div><div className="workspace-name">분석 대상 <strong>OrbitCRM</strong></div></section>
          <section className="stats-strip" aria-label="분석 현황"><div><span>분석 질문</span><strong>75<span className="unit">개</span></strong></div><div><span>분석 주제</span><strong>{topics.length}<span className="unit">개</span></strong></div><div><span>정상 관측</span><strong>{totalValid}<span className="unit">건</span></strong></div><div><span>저장된 액션</span><strong>{actions.length}<span className="unit">건</span></strong></div></section>
          <p className="data-note">실패·부분 수집은 미언급으로 계산하지 않습니다. 우선순위는 제공된 합성 데이터의 탐색 순서이며 원인 진단이 아닙니다.</p>
          <div className="workbench">
            <div className="table-column">
              <section className="surface topic-section"><div className="section-head"><div><h2>주제별 의사결정</h2><p>우선순위와 수집 상태를 함께 확인하세요.</p></div><span className="section-count">{topics.length}개 주제</span></div>
                <div className="table-head topic-columns" aria-hidden="true"><span>주제</span><span>판단</span><span>자사 언급</span><span>정상 수집</span><span>점수</span></div>
                {loading && topics.length===0 ? <p className="state">주제 분석을 불러오는 중…</p> : topics.length===0 ? <p className="state">주제를 불러오지 못했습니다. 다시 시도해 주세요.</p> : <div className="topic-list">{topics.map(topic=><button key={topic.topicId} onClick={()=>chooseTopic(topic)} className={'topic-row '+(selectedTopic?.topicId===topic.topicId?'active':'')} aria-current={selectedTopic?.topicId===topic.topicId?'true':undefined}>
                  <strong>{topic.label}</strong><span className={'status status-'+topic.decision}>{decisionLabel[topic.decision]}</span><span className="numeric">{topic.hits}/{topic.validCount}</span><span className="numeric">{topic.validCount}/{topic.observationCount}</span><b className="numeric">{topic.score===null?'—':topic.score}</b>
                </button>)}</div>}
              </section>
              <section className="surface question-section"><div className="section-head"><div><h2>{selectedTopic?.label || '주제'} 질문</h2><p>질문을 선택하면 오른쪽에서 원문 근거를 볼 수 있습니다.</p></div><span className="section-count">{items.length}개 질문</span></div>
                <div className="filters"><label className="sr-only" htmlFor="question-search">질문 검색</label><input id="question-search" aria-label="질문 검색" placeholder="선택한 주제에서 질문 검색" value={query} onChange={e=>setQuery(e.target.value)}/></div>
                {loading ? <p className="state">질문을 불러오는 중…</p> : items.length===0 ? <p className="state">조건에 맞는 질문이 없습니다. 검색어를 지워 보세요.</p> : <div className="question-list">{items.map(item=><button className={'question-row '+(selected?.question.question_id===item.question.question_id?'active':'')} key={item.question.question_id} onClick={()=>showDetail(item.question.question_id)} aria-current={selected?.question.question_id===item.question.question_id?'true':undefined}><span className="question-id">{item.question.question_id}</span><span className="question-name">{item.question.query}</span><span className={'status status-'+item.decision}>{decisionLabel[item.decision]}</span><span className="question-sub">언급 {item.hits}/{item.validCount} · 정상 {item.validCount}/{item.validCount+item.unknownCount} · {item.priority===null?'수집 확인':`점수 ${item.priority}`}</span></button>)}</div>}
              </section>
            </div>
            <div className="detail-column">
              <section className="surface topic-summary"><div className="section-head compact"><div><h2>선택한 주제</h2></div></div>
                {!selectedTopic ? <p className="state">주제를 선택하세요.</p> : <div className="summary-content"><div className="summary-title"><div><h3>{selectedTopic.label}</h3><span className={'status status-'+selectedTopic.decision}>{decisionLabel[selectedTopic.decision]}</span></div><div className="summary-score"><strong>{selectedTopic.score??'—'}</strong><span>우선순위</span></div></div>
                <p className="next-step">{selectedTopic.nextStep}</p>
                <div className="summary-grid"><div><span>자사 언급</span><strong>{selectedTopic.hits}/{selectedTopic.validCount}</strong></div><div><span>정상 수집</span><strong>{selectedTopic.validCount}/{selectedTopic.observationCount}</strong></div><div><span>소유 인용</span><strong>{selectedTopic.ownedCitations}건</strong></div><div><span>부정 언급</span><strong>{selectedTopic.negativeCount}건</strong></div></div>
                <div className="facts"><div><span>경쟁사 언급</span><strong>{selectedTopic.competitors.length ? selectedTopic.competitors.slice(0,3).map(c=>`${c.name} ${c.count}건`).join(' · ') : '없음'}</strong></div><div><span>미확인 관측</span><strong>{selectedTopic.unknownCount}건</strong></div><div><span>소유 미상 인용</span><strong>{selectedTopic.unknownOwnerCitations}건</strong></div></div>
                <details className="formula"><summary>점수 계산 근거</summary><p>평균 중요도 {selectedTopic.scoreComponents.meanImportance} × 미언급률 {selectedTopic.scoreComponents.gapRate} × 관심도 계수 {selectedTopic.scoreComponents.interestFactor} × 수집률 {selectedTopic.scoreComponents.coverage} ≈ {selectedTopic.score??'—'}</p><p>중요도와 관심도는 합성 데이터의 가정입니다.</p></details></div>}
              </section>
              <section className="surface detail-panel" id="detail-panel"><div className="section-head compact"><div><h2>질문 상세</h2></div>{selected&&<span className="section-count">{selected.question.question_id}</span>}</div>
                {detailLoading ? <p className="state">상세 근거를 불러오는 중…</p> : !selected ? <div className="empty-detail"><strong>질문을 선택하세요</strong><p>원문 관측, 추천 근거, 액션 작성 화면이 여기에 표시됩니다.</p></div> : <div className="detail-content">
                  <div className="detail-title"><span className={'status status-'+selected.decision}>{decisionLabel[selected.decision]}</span><h3>{selected.question.query}</h3><p>근거 {selected.snapshotId} · 버전 {selected.evidenceKey.slice(0,10)}</p></div>
                  <div className="detail-tabs" role="tablist" aria-label="질문 상세 메뉴"><button type="button" role="tab" aria-selected={detailTab==='evidence'} className={detailTab==='evidence'?'active':''} onClick={()=>setDetailTab('evidence')}>판단 근거</button><button type="button" role="tab" aria-selected={detailTab==='action'} className={detailTab==='action'?'active':''} onClick={()=>setDetailTab('action')}>액션 작성</button></div>
                  {detailTab==='evidence' ? <div className="evidence-view" role="tabpanel">
                    <div className="metrics"><div><span>정상 관측</span><strong>{selected.validCount}/{selected.validCount+selected.unknownCount}</strong></div><div><span>자사 언급</span><strong>{selected.hits}/{selected.validCount}</strong></div><div><span>우선순위</span><strong>{selected.priority??'—'}</strong></div></div>
                    {selected.similarQuestions.length>0&&<p className="caution">유사 질문 {selected.similarQuestions.join(', ')}도 함께 검토해 중복 작업을 줄이세요.</p>}
                    {selected.unknownCount>0&&<p className="caution">미확인 {selected.unknownCount}건은 미언급으로 계산하지 않았습니다.</p>}
                    <div className="recommend"><span>추천 액션 · 규칙 기반 초안</span><h4>{selected.suggestion}</h4><p>{selected.reason}</p><dl><div><dt>실행 완료</dt><dd>{selected.completionCheck}</dd></div><div><dt>후속 관측</dt><dd>{selected.successCheck}</dd></div></dl><button className="primary" type="button" onClick={()=>setDetailTab('action')}>이 근거로 액션 만들기</button></div>
                    <div className="evidence-summary"><span>자사 소유 인용 {selected.ownedCitations}건</span><span>소유 미상 {selected.unknownOwnerCitations}건</span><span>부정 언급 {selected.negativeCount}건</span></div>
                    <p className="competitors">경쟁사 언급: {selected.competitors.length ? selected.competitors.map(c=>`${c.name} ${c.count}건`).join(' · ') : '없음'}</p>
                    <details className="formula"><summary>질문 점수 계산</summary><p>중요도 {selected.scoreComponents.importance} × 미언급률 {selected.scoreComponents.gapRate??'—'} × 관심도 계수 {selected.scoreComponents.interestFactor} ≈ {selected.priority??'—'}</p></details>
                    <div className="subsection-head"><h4>채널별 관측</h4><span>{selected.observations?.length || 0}건</span></div>
                    <div className="observations">{selected.observations?.map(o=><details id={o.id} key={o.id} className="observation"><summary><strong>{o.channel} <small>{o.id}</small></strong><span className={o.valid?(o.targetMentioned?'positive':'negative'):'unknown'}>{!o.valid?'미확인':o.targetMentioned?'자사 언급':'자사 미언급'}</span></summary><div className="observation-body"><p>{o.response || '응답 본문 없음'}</p><div>언급: {o.mentions.length ? o.mentions.map(m=>`${m.brand_name}(${m.sentiment})`).join(' · ') : '없음'}</div><div>인용: {o.citations.length ? o.citations.map(c=>`${c.source.title || c.source.domain || c.source_id} [${c.source_id}]`).join(' · ') : '없음'}</div>{!o.valid&&<div>수집 상태 {o.status} · 응답 언어 {o.language} · 집계에서 제외</div>}</div></details>)}</div>
                  </div> : <form onSubmit={createAction} className="action-form" role="tabpanel"><p className="form-intro">선택한 질문의 근거를 연결해 액션을 저장합니다. 내용은 검토 후 수정할 수 있습니다.</p><label>제목<input minLength={3} maxLength={160} required value={title} onChange={e=>{setTitle(e.target.value);setSaved(false);}}/></label><label>근거·설명<textarea maxLength={1000} value={note} onChange={e=>setNote(e.target.value)}/></label><label>담당자 (선택)<input maxLength={80} value={owner} onChange={e=>setOwner(e.target.value)} placeholder="예: 마케팅 담당"/></label><label>실행 완료 기준<input minLength={3} maxLength={300} required value={completionCheck} onChange={e=>setCompletionCheck(e.target.value)}/></label><label>후속 관측 기준<input minLength={3} maxLength={300} required value={successCheck} onChange={e=>setSuccessCheck(e.target.value)}/></label>{formError&&<p className="form-error" role="alert">{formError}</p>}<button className="primary" disabled={saving||saved}>{saving?'저장 중…':saved?'저장 완료':'액션 저장'}</button></form>}
                </div>}
              </section>
            </div>
          </div>
        </> : <section className="actions-view"><div className="page-heading"><div><h1>액션 관리</h1><p>저장된 실행 계획과 진행 상태를 확인하세요.</p></div><button className="secondary" onClick={()=>setActiveView('analysis')}>관측 분석으로 돌아가기</button></div><div className="surface actions-panel"><div className="section-head"><div><h2>저장된 액션</h2><p>상태를 변경하면 SQLite에 즉시 저장됩니다.</p></div><span className="section-count">{actions.length}개</span></div>{actions.length===0?<p className="state">아직 저장된 액션이 없습니다. 관측 분석에서 질문을 선택해 첫 액션을 만드세요.</p>:<div className="actions-list">{actions.map(a=><div className="action-row" key={a.id}><div className="action-main"><div className="action-meta">{a.question_id} · {a.snapshot_id || '근거 미기록'} · {a.created_at} UTC</div><strong>{a.title}</strong><p>{a.note}</p><div className="action-checks">{a.owner&&<span>담당 {a.owner}</span>}{a.completion_check&&<span>실행 완료 {a.completion_check}</span>}{a.success_check&&<span>후속 관측 {a.success_check}</span>}</div><button className="text-button" onClick={()=>viewActionEvidence(a)}>현재 질문 근거 보기</button></div><label className="status-field">상태<select aria-label={`${a.title} 상태`} value={a.status} onChange={e=>changeStatus(a.id,e.target.value as Action['status'])}><option value="todo">할 일</option><option value="doing">진행 중</option><option value="done">완료</option></select></label></div>)}</div>}</div></section>}
      </main>
    </div>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App/>);
