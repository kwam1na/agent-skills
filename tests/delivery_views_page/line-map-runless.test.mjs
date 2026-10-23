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
test('runless actual rounds share cards, escaped findings and originating closure text', async()=>{
  const page=pageWithRuns(document(),{}, {config});
  expect(page).not.toContain('</script><img');
  const [last,first]=await openPage(page,{steps:[{click:'.run-pick',index:0}]});
  expect(last.app.querySelector('.run-card-outcome').textContent).toBe('Approved');
  expect(last.app.querySelector('.run-closure').textContent).toContain('Original reviewer confirmed <b>the fix</b>.');
  expect(last.app.querySelector('.run-card .run-quiet').title).toBe(B);
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
