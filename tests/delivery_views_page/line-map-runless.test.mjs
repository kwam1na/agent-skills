import { test } from 'node:test';
import { expect, openPage, pageWithRuns, renderPage } from './support.mjs';
const A = 'a'.repeat(40), B = 'b'.repeat(40);
function document() {
  const result = (round, candidate, finding) => ({round, outcome: finding ? 'unresolved' : 'aligned', entries: [{
    lens: 'lens.outcome-correctness', state: 'obtained', binding: {subjectRef:'WORK-1',candidateRef:candidate,round}, result: {
      schemaVersion:'review-lens-result/1',lensId:'lens.outcome-correctness',subjectRef:'WORK-1',candidateRef:candidate,round,evidence:{observed:true},
      outcome: finding ? 'changes-requested' : 'aligned', findings: finding ? [{id:'F1',severity:'P1',summary:'</script><img src=x onerror=alert(1)>',state:'open'}] : [],
      resolvedFindings: finding ? [] : [{id:'F1',state:'closed',fixCommit:B,resolution:'Original reviewer confirmed <b>the fix</b>.'}]
    }}]});
  return {state:{lanes:[],queue:[{subject:'WORK-1',title:'Work',stage:'merged',head:B,runId:null}]},journal:[],
    reviewEvidence:{'WORK-1':{candidateRef:B,digest:'d'.repeat(64),outcomes:[result(1,A,true),result(2,B,false)]}}};
}
const config={spec:'delivery-views-config/1'};
test('closure explanations start collapsed and expand without hiding the settlement summary',async()=>{
  const [closed,opened,first,last]=await openPage(pageWithRuns(document(),{}, {config}),{steps:[
    {toggle:'.run-closures'}, {click:'.run-pick',index:0}, {click:'.run-pick',index:1}
  ]});
  expect(closed.app.querySelector('.run-closures').hasAttribute('open')).toBe(false);
  expect(closed.app.querySelector('.run-closures > summary').textContent).toContain('Original reviewer confirmed');
  expect(closed.app.querySelector('.run-settled').closest('details')).toBeNull();
  expect(opened.app.querySelector('.run-closures').hasAttribute('open')).toBe(true);
  expect(opened.app.querySelector('.run-closure').textContent).toContain('Original reviewer confirmed <b>the fix</b>.');
  expect(first.app.querySelector('.run-closures')).toBeNull();
  expect(last.app.querySelector('.run-closures').hasAttribute('open')).toBe(false);
});
test('runless actual rounds share cards, escaped findings and originating closure text', async()=>{
  const page=pageWithRuns(document(),{}, {config});
  expect(page).not.toContain('</script><img');
  const [last,first]=await openPage(page,{steps:[{click:'.run-pick',index:0}]});
  expect(last.app.querySelector('.run-card-outcome').textContent).toBe('Approved');
  expect(last.app.querySelector('.run-closure').textContent).toContain('Original reviewer confirmed <b>the fix</b>.');
  expect(last.app.querySelector('.run-card .run-quiet').getAttribute('aria-label')).toBe('Reviewed candidate '+B);
  expect(first.app.querySelector('.run-card-outcome').textContent).toBe('Changes requested');
  expect(first.app.querySelector('.run-finding-text').textContent).toContain('<img');
  expect(first.app.querySelector('.run-settled').textContent).toContain('confirmed round 2');
  expect(first.app.querySelector('img')).toBeNull();
  expect(first.app.querySelector('.run-record-row')).toBeNull();
});
test('missing or stale runless reports stay visibly unavailable without approval from journal counts',async()=>{
  for(const mode of ['missing','stale','damaged']){
    const doc=document();
    doc.journal=[{kind:'review.round',subject:'WORK-1',review:{round:2,verdicts:{'lens.outcome-correctness':'approve'}}}];
    if(mode==='missing') delete doc.reviewEvidence;
    if(mode==='stale') doc.state.queue[0].head=A;
    if(mode==='damaged') doc.reviewEvidence['WORK-1']={unavailable:true};
    const [shown]=await openPage(pageWithRuns(doc,{}, {config}));
    expect(shown.app.querySelector('.d-run-none').textContent).toContain('unavailable or stale');
    expect(shown.app.querySelector('.run-card')).toBeNull();
  }
});
test('candidate changes in a live push invalidate retained native approval immediately',async()=>{
  const doc=document(),changed=structuredClone(doc);changed.state.queue[0].head=A;
  const [,shown]=await openPage(pageWithRuns(doc,{}, {config}),{steps:[{push:changed}]});
  expect(shown.app.querySelector('.d-run-none').textContent).toContain('unavailable or stale');
  expect(shown.app.querySelector('.run-card')).toBeNull();
});
test('same-head digest, later review and unavailable pushes immediately invalidate approval',async()=>{
  for(const mode of ['digest','review','unavailable']){
    const doc=document(),changed=structuredClone(doc);
    if(mode==='digest') changed.journal.push({id:1,subject:'WORK-1',kind:'review.evidence',reviewArtifact:{candidateRef:B,digest:'e'.repeat(64)}});
    if(mode==='review') changed.journal.push({id:1,subject:'WORK-1',kind:'review.round',review:{round:3,verdicts:{'lens.outcome-correctness':'changes'}}});
    if(mode==='unavailable') changed.reviewEvidence['WORK-1']={unavailable:true};
    const [before,after]=await openPage(pageWithRuns(doc,{}, {config}),{steps:[{push:changed}]});
    expect(before.app.querySelector('.run-card-outcome').textContent).toBe('Approved');
    expect(after.app.querySelector('.d-run-none').textContent).toContain('unavailable or stale');
    expect(after.app.querySelector('.run-card')).toBeNull();
  }
});
test('actual renderer contains malformed observation input without losing the page',async()=>{
  for(const change of [o=>{o.outcomes=null;},o=>{o.outcomes=[{}];},o=>{o.outcomes[0].entries[0].result=null;},
    o=>{o.outcomes[1].entries[0].result.resolvedFindings=[null];},o=>{o.digest=[];},o=>{o.candidateRef={};}]){
    const doc=document();change(doc.reviewEvidence['WORK-1']);
    const [shown]=await openPage(renderPage(doc,{config}));
    expect(shown.app.querySelector('.d-run-none').textContent).toContain('unavailable or stale');
    expect(shown.app.querySelector('.run-card')).toBeNull();
  }
});
test('a fresh matching render restores the observed verdict after immediate invalidation',async()=>{
  const doc=document(),changed=structuredClone(doc);
  changed.reviewEvidence['WORK-1'].digest='e'.repeat(64);
  changed.journal.push({id:1,subject:'WORK-1',kind:'review.evidence',reviewArtifact:{candidateRef:B,digest:'e'.repeat(64)}});
  const [,pending,fresh]=await openPage(pageWithRuns(doc,{}, {config}),{
    rendered:pageWithRuns(changed,{}, {config}),steps:[{push:changed},{wait:3100}]});
  expect(pending.app.querySelector('.run-card')).toBeNull();
  expect(pending.app.querySelector('.d-run-none').textContent).toContain('unavailable or stale');
  expect(fresh.app.querySelector('.run-card-outcome').textContent).toBe('Approved');
});
test('malformed journal boundary data yields unavailable evidence without crashing the renderer',async()=>{
  for(const journal of [null,[null],[42],['row'],[{id:null,kind:'review.evidence',subject:'WORK-1'}],
    [{id:'1',kind:'review.round',subject:'WORK-1'}], [{id:true,kind:'review.round',subject:'WORK-1'}],
    [{id:1,kind:'review.evidence',subject:'WORK-1',reviewArtifact:null}]]){
    const doc=document();doc.journal=journal;
    const [shown]=await openPage(renderPage(doc,{config}));
    expect(shown.app.querySelector('.d-run-none').textContent).toContain('unavailable or stale');
    expect(shown.app.querySelector('.run-card')).toBeNull();
  }
});
test('a runner sync onto a moved base keeps the reviewed head approval and its notes',async()=>{
  const doc=document();doc.state.queue[0].syncedHead='c'.repeat(40);
  const [shown]=await openPage(pageWithRuns(doc,{}, {config}));
  expect(shown.app.querySelector('.d-run-none')).toBeNull();
  expect(shown.app.querySelector('.run-card-outcome').textContent).toBe('Approved');
  expect(shown.app.querySelector('.run-closure').textContent).toContain('Original reviewer confirmed');
});
function plannedDocument() {
  const doc = document();
  doc.state.queue = [];
  doc.state.planned = [{subject:'WORK-1',title:'Work',lane:'lane-1',stage:'review'}];
  const round = (id, number, verdict) => ({id, at:'2026-01-01T00:0'+id+':00Z', kind:'review.round', subject:'WORK-1', lane:'lane-1',
    summary:'WORK-1: review round '+number, review:{round:number, startedAt:'2026-01-01T00:00:00Z', finishedAt:'2026-01-01T00:0'+id+':00Z',
      verdicts:{'lens.outcome-correctness':verdict}, findings:{P0:0,P1:verdict==='approve'?0:1,P2:0,P3:0}}});
  doc.journal = [round(1, 1, 'changes'), round(2, 2, 'approve'),
    {id:3, at:'2026-01-01T00:03:00Z', kind:'review.evidence', subject:'WORK-1', lane:'lane-1', summary:'captured',
      reviewArtifact:{candidateRef:B, digest:'d'.repeat(64)}}];
  return doc;
}
test('before hand-off, a planned story shows its round cards and notes from its latest reviewed round', async()=>{
  const [shown,first]=await openPage(renderPage(plannedDocument(),{config}),{steps:[{click:'.run-pick',index:0}]});
  expect(shown.app.querySelector('.d-run-none')).toBeNull();
  expect(shown.app.querySelector('.run-card-outcome').textContent).toBe('Approved');
  expect(shown.app.querySelector('.run-card .run-quiet').getAttribute('aria-label')).toBe('Reviewed candidate '+B);
  expect(shown.app.querySelector('.run-closure').textContent).toContain('Original reviewer confirmed');
  expect(first.app.querySelector('.run-card-outcome').textContent).toBe('Changes requested');
  expect(first.app.querySelector('.run-finding-text').textContent).toContain('<img');
});
test('a planned story whose latest round was not captured stays visibly stale', async()=>{
  const doc=plannedDocument();
  doc.journal.push({id:4, at:'2026-01-01T00:04:00Z', kind:'review.round', subject:'WORK-1', lane:'lane-1', summary:'WORK-1: review round 3',
    review:{round:3, startedAt:'2026-01-01T00:03:00Z', finishedAt:'2026-01-01T00:04:00Z', verdicts:{'lens.outcome-correctness':'changes'},
      findings:{P0:0,P1:1,P2:0,P3:0}}});
  const [shown]=await openPage(renderPage(doc,{config}));
  expect(shown.app.querySelector('.d-run-none').textContent).toBe('Review evidence unavailable or stale for this candidate.'
    +" Its rounds show here again once the lane's review-reduce records its latest round.");
  expect(shown.app.querySelector('.run-card')).toBeNull();
  // Closed, nothing will record a later round, so the sentence stops at what is true.
  const closed=structuredClone(doc);
  closed.journal.push({id:5, at:'2026-01-01T00:05:00Z', kind:'delivery.closed', subject:null, lane:null, summary:'closed'});
  const [shut]=await openPage(renderPage(closed,{config}));
  expect(shut.app.querySelector('.d-run-none').textContent).toBe('Review evidence unavailable or stale for this candidate.');
  const fresh=plannedDocument(),pushed=structuredClone(fresh);pushed.journal.push(doc.journal[3]);
  const [before,after]=await openPage(pageWithRuns(fresh,{}, {config}),{steps:[{push:pushed}]});
  expect(before.app.querySelector('.run-card-outcome').textContent).toBe('Approved');
  expect(after.app.querySelector('.d-run-none').textContent).toContain('unavailable or stale');
});
test('a planned story with no round yet says when its rounds will appear', async()=>{
  const doc=plannedDocument();doc.journal=[];delete doc.reviewEvidence;
  const [shown]=await openPage(renderPage(doc,{config}));
  expect(shown.app.querySelector('.d-run-none').textContent)
    .toBe('No review rounds yet. Each round shows here as soon as the lane reduces it, before the hand-off.');
  expect(shown.app.querySelector('.run-card')).toBeNull();
});
test('evidence the lead carried to a later head shows as carried, and only at that head', async()=>{
  const C = 'c'.repeat(40);
  const carriedDocument = (head, carried) => {
    const doc = document();
    doc.state.queue = [{subject:'WORK-1',title:'Work',stage:'queued',head,runId:null}];
    doc.reviewEvidence['WORK-1'] = {...doc.reviewEvidence['WORK-1'], ...(carried ? {carried} : {})};
    return doc;
  };
  const carry = {from:B, to:C, note:'The lead carried WORK-1\'s review from '+B+' to '+C+', no new round needed: only a fixture changed'};
  const [shown] = await openPage(pageWithRuns(carriedDocument(C, carry), {}, {config}));
  expect(shown.app.querySelector('.d-run-none')).toBeNull();
  expect(shown.app.querySelector('.d-run-carried').textContent).toBe('Carried by the lead. '+carry.note);
  expect(shown.app.querySelector('.run-card-outcome').textContent).toBe('Approved');
  // The rounds still name the head they reviewed, never the carried one.
  expect(shown.app.querySelector('.run-card .run-quiet').getAttribute('aria-label')).toBe('Reviewed candidate '+B);
  // The same capture at the later head without a carry, or carried to another head, is stale.
  for (const doc of [carriedDocument(C, null), carriedDocument(C, {...carry, to:'d'.repeat(40)})]) {
    const [stale] = await openPage(pageWithRuns(doc, {}, {config}));
    expect(stale.app.querySelector('.d-run-none').textContent).toBe('Review evidence unavailable or stale for this candidate.');
    expect(stale.app.querySelector('.d-run-carried')).toBeNull();
  }
  // A carry that isn't the shape the state tool writes is refused outright.
  for (const malformed of [{...carry, extra:'x'}, {...carry, note:''}, {...carry, to:7}]) {
    const [refused] = await openPage(pageWithRuns(carriedDocument(C, malformed), {}, {config}));
    expect(refused.app.querySelector('.d-run-carried')).toBeNull();
    expect(refused.app.querySelector('.d-run-none').textContent).toContain('unavailable or stale');
  }
});
