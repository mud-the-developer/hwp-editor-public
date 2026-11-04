// Real Edge, disposable loopback mock ONLY. No real report, approval file or Hancom.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
const root=resolve(import.meta.dirname,'..'),edge=process.env.EDGE_BINARY||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const artifacts=join(root,'work','fill-check-'+Date.now()+'-'+randomUUID().slice(0,8));
if(!existsSync(edge))throw new Error('Edge required for synthetic fill UI checks');
const template=(await readFile(join(root,'web/fill-review.html'),'utf8')).replace('__TOKEN__','SYNTHETIC-FILL-TOKEN');
let posts=0,compilePosts=0,failCompile=false,failState=false;
const state={manifest:{workspace_id:'synthetic-ui',baseline_hwpx_sha256:'synthetic-baseline',pages:[{page:1,width:300,height:400}]},input_sha256:'synthetic-input',proposal_revision:'absent',
 draft:{assignments:[{field_id:'f1',key:'Name',value:'<script>NOT EXECUTABLE</script>',source:'Synthetic source A'},{field_id:'f2',key:'Code',value:'7',source:'Synthetic source B'}]},
 fields:[{field_id:'f1',table_path:[0,0],cell_address:{rowAddr:1,colAddr:1},label_hints:['Name']},{field_id:'f2',table_path:[0,0],cell_address:{rowAddr:2,colAddr:1},label_hints:['Code']}],
 regions:[{page:1,region:[81,61,219,89]},{page:1,region:[81,91,219,119]}],
 header_spine:{part:'Contents/content.hpf',idref:'header',before:'yes',after:'no'},versions:[],latest_attempt:null,proposal:null,proposal_sha256:null,approved_sha256:null};
const image='<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400"><rect width="300" height="400" fill="white"/><text x="20" y="30">SYNTHETIC UI FIXTURE ONLY</text><path d="M20 60H220V120H20Z M80 60V120 M20 90H220" fill="none" stroke="black"/><text x="25" y="80">Name</text><text x="25" y="110">Code</text></svg>';
const server=createServer(async(req,res)=>{
 const route=new URL(req.url,'http://127.0.0.1').pathname;res.setHeader('Cache-Control','no-store');
 if(route==='/fill'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(template);return}
 if(route.startsWith('/page/')||route.startsWith('/comparison/')){res.setHeader('Content-Type','image/svg+xml');res.end(image);return}
 res.setHeader('Content-Type','application/json');
 if(route==='/api/fill-state'){if(failState){res.statusCode=503;res.end(JSON.stringify({error:'Synthetic state failure'}));return}res.end(JSON.stringify(state));return}
 if(req.method==='POST'){
  assert.equal(req.headers.origin,`http://127.0.0.1:${server.address().port}`);assert.equal(req.headers['x-review-token'],'SYNTHETIC-FILL-TOKEN');
  let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);
  if(route==='/api/fill-proposal'){
   compilePosts++;
   if(req.headers['if-match']!=='"'+state.proposal_revision+'"'){res.statusCode=409;res.end(JSON.stringify({error:'Synthetic stale proposal conflict'}));return}
   if(failCompile){res.statusCode=503;res.end(JSON.stringify({error:'Synthetic compile failure'}));return}
   assert.equal(body.input_sha256,state.input_sha256);assert.equal(body.placements.length,2);
   state.proposal={schema:4,placements:body.placements};state.proposal_sha256='synthetic-plan-'+compilePosts;state.proposal_revision='revision-'+compilePosts;state.approved_sha256=null;
   res.end(JSON.stringify({proposal_sha256:state.proposal_sha256}));return;
  }
  if(route==='/api/approve'){
   assert.equal(body.plan_sha256,state.proposal_sha256);assert.equal(body.proposal_kind,'fill');assert.equal(body.fill_policy_confirmed,true);assert.equal(body.unsigned_template_confirmed,true);assert.equal(body.header_spine_confirmed,true);assert.deepEqual(body.fields_reviewed,['f1','f2']);
   posts++;state.approved_sha256=state.proposal_sha256;res.end(JSON.stringify({approved:true}));return;
  }
 }
 res.statusCode=route==='/favicon.ico'?204:404;res.end('{}');
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const origin=`http://127.0.0.1:${server.address().port}`,profile=await mkdtemp(join(tmpdir(),'hwp-fill-ui-'));
const browser=spawn(edge,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:'ignore'});
let socket,next=1,accept=false;const pending=new Map(),errors=[];
async function until(callback,label){const deadline=Date.now()+15000;while(Date.now()<deadline){if(await callback())return;await new Promise(done=>setTimeout(done,40))}throw new Error('Timed out: '+label)}
// Shared CI may cold-start local navigation slowly; keep other CDP calls short and never retry.
function call(method,params={}){const id=next++;return new Promise((done,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP timeout '+method))},method==='Page.navigate'?45000:15000);pending.set(id,{done,reject,timer});socket.send(JSON.stringify({id,method,params}))})}
async function evaluate(expression){const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value}
async function click(selector){assert.equal(await evaluate('location.origin'),origin);const point=await evaluate(`(()=>{const element=document.querySelector(${JSON.stringify(selector)});element.scrollIntoView({block:'nearest'});const box=element.getBoundingClientRect();return {x:box.x+box.width/2,y:box.y+box.height/2}})()`);await call('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});await call('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1})}
const pass=text=>console.log('PASS: '+text);
try{
 let port;await until(async()=>{try{port=Number((await readFile(join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]);return port>0}catch{return false}},'Edge startup');
 const targets=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();socket=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
 await new Promise((done,reject)=>{socket.addEventListener('open',done,{once:true});socket.addEventListener('error',reject,{once:true})});
 socket.addEventListener('message',({data})=>{const message=JSON.parse(data);if(message.id){const task=pending.get(message.id);if(!task)return;pending.delete(message.id);clearTimeout(task.timer);if(message.error)task.reject(new Error(JSON.stringify(message.error)));else task.done(message.result)}else if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails.text);else if(message.method==='Page.javascriptDialogOpening')call('Page.handleJavaScriptDialog',{accept}).catch(error=>errors.push(error.message))});
 await call('Page.enable');await call('Runtime.enable');await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});const navigation=await call('Page.navigate',{url:origin+'/fill'});assert.equal(navigation.errorText,undefined,'local fixture navigation failed');assert.equal(typeof navigation.frameId,'string');
 await until(()=>evaluate("document.querySelectorAll('#rows tr').length===2&&document.querySelector('#image').complete"),'fill initialization');
 const capture=async name=>{const output=join(artifacts,'readability');await mkdir(output,{recursive:true});const image=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(join(output,name+'.png'),Buffer.from(image.data,'base64'))};
 await capture('fill-desktop');
 assert.equal(await evaluate("document.querySelectorAll('#rows script').length"),0);assert((await evaluate("document.querySelector('#rows').textContent")).includes('<script>NOT EXECUTABLE</script>'));assert.equal(await evaluate("document.querySelector('#approve').disabled"),true);
 pass('fill values and source references render as text, never HTML or executable code');
 assert((await evaluate("document.querySelector('#guide-title').textContent")).includes('빈칸을 선택'));assert.equal(await evaluate("document.querySelector('#consent-step').open"),false);assert.equal(await evaluate("document.querySelectorAll('#rows details[open]').length"),0);assert.equal(await evaluate("parseFloat(getComputedStyle(document.body).fontSize)>=16"),true);
 await click('#guide-action');assert.equal(await evaluate('document.activeElement.id'),'field');assert.equal(await evaluate('placements.size'),0);assert.equal(posts,0);assert.equal(compilePosts,0);pass('readable cards and next-step guide show essential content without exposing technical clutter or changing data');
 await click('#overlay rect:nth-child(1)');await evaluate("document.querySelector('#field').value='f2'");await click('#overlay rect:nth-child(2)');assert.equal(await evaluate("document.querySelector('#compile').disabled"),false);
 failCompile=true;await click('#compile');await until(()=>evaluate("document.querySelector('#status').textContent.includes('Synthetic compile failure')"),'compile failure');assert.equal(await evaluate("document.querySelectorAll('#overlay .selected').length"),2);assert.equal(posts,0);assert.equal(await evaluate("document.querySelector('#approve').disabled"),true);
 pass('failed proposal compile retains selected regions and never approves or retries');
 failCompile=false;await click('#compile');await until(()=>evaluate("!dirty&&state.proposal!==null&&!busy"),'compile success');assert.equal(posts,0);
 await click('#check-all');await click('#unsigned');await click('#policy');assert.equal(await evaluate("document.querySelector('#approve').disabled"),true);await click('#header-spine');assert.equal(await evaluate("document.querySelector('#approve').disabled"),false);
 pass('exact values, unsigned template, native policy and header reading-order confirmation are all required');
 assert.equal(await evaluate("document.querySelector('#consent-step').open"),true);assert((await evaluate("document.querySelector('#guide-title').textContent")).includes('승인'));
 await click('#guide-action');assert.equal(await evaluate('document.activeElement.id'),'approve');assert.equal(posts,0);assert.equal(await evaluate("document.querySelector('#approve').disabled"),false);pass('guide explains why approval is unavailable and only focuses the final approval button after all exact checks');
 await click('#approve');await until(()=>evaluate('!busy'),'cancelled final dialog');assert.equal(posts,0);
 accept=true;await click('#approve');await until(()=>evaluate("sameApproval()&&!busy"),'approved fixture');assert.equal(posts,1);assert.equal(await evaluate("document.querySelector('#approve').disabled"),true);
 pass('cancellable separate final consent writes only one synthetic approval request');
 await click('#reload');await until(()=>evaluate('!busy'),'approved reload');assert.equal(posts,1);assert.equal(await evaluate("document.querySelector('#header-spine').checked"),true);
 await evaluate("document.querySelector('#field').value='f1'");await click('#overlay rect:nth-child(1)');assert.equal(await evaluate('dirty'),false);assert.equal(await evaluate("document.querySelector('#unsigned').checked"),true);assert.equal(posts,1);
 pass('selecting the identical mapped cell does not clear review or repost consent');
 await click('#overlay rect:nth-child(2)');assert.equal(await evaluate('dirty'),false);assert.equal(await evaluate('placements.size'),2);assert((await evaluate("document.querySelector('#status').textContent")).includes('이미 다른 항목'));
 pass('occupied cell mappings are rejected locally without losing the existing mapping');
 await click('#clear-placement');assert.equal(await evaluate('placements.size'),1);assert.equal(await evaluate("document.querySelector('#unsigned').checked"),false);assert.equal(await evaluate("document.querySelector('#header-spine').checked"),false);assert.equal(await evaluate("document.querySelector('#check-all').checked"),false);assert.equal(await evaluate("document.querySelector('#compile').disabled"),true);assert.equal(await evaluate("document.querySelector('#approve').disabled"),true);
 await click('#overlay rect:nth-child(1)');assert((await evaluate("document.querySelector('#progress').textContent")).includes('위치 2/2'));
 pass('explicit mapping release clears checks, keeps the immutable server approval, and updates progress');
 state.proposal_revision='another-window-revision';state.proposal_sha256='another-window-plan';state.approved_sha256=null;
 const priorCompile=compilePosts;await click('#compile');await until(()=>evaluate("document.querySelector('#status').textContent.includes('Synthetic stale proposal conflict')"),'stale compile conflict');assert.equal(compilePosts,priorCompile+1);assert.equal(await evaluate('placements.size'),2);assert.equal(posts,1);assert.equal(await evaluate("document.querySelector('#approve').disabled"),true);
 await click('#reload-keep');await until(()=>evaluate("!busy&&state.proposal_revision==='another-window-revision'"),'read latest retain mapping');assert.equal(await evaluate('placements.size'),2);assert.equal(await evaluate('dirty'),true);assert.equal(compilePosts,priorCompile+1);
 await click('#compile');await until(()=>evaluate('!busy&&!dirty'),'explicit retry against latest revision');assert.equal(compilePosts,priorCompile+2);assert.equal(posts,1);
 pass('multi-window conflicts preserve location drafts, require reading the latest version, and never auto-retry');
 await click('#check-all');await click('#unsigned');await click('#policy');await click('#header-spine');assert.equal(await evaluate("document.querySelector('#approve').disabled"),false);
 await click('#rows input');await click('#reload');await until(()=>evaluate('!busy'),'same-plan rereview reload');assert.equal(await evaluate('reviewed.size'),1);assert.equal(await evaluate("document.querySelector('#approve').disabled"),true);
 pass('same-plan refresh retains local re-review marks separately from server approval');
 failState=true;await click('#reload');await until(()=>evaluate("document.querySelector('#status').textContent.includes('Synthetic state failure')"),'failed state refresh');assert.equal(await evaluate('placements.size'),2);assert.equal(await evaluate('connected'),false);assert.equal(await evaluate("document.querySelector('#approve').disabled"),true);assert.equal(await evaluate("document.querySelector('#compile').disabled"),true);
 failState=false;await click('#reload-keep');await until(()=>evaluate('!busy&&connected'),'state reconnect');assert.equal(posts,1);
 pass('failed reconnect retains selections but locks compile and approval until source state is confirmed');
 state.latest_attempt={id:'synthetic-blocked-run',status:'blocked',plan_sha256:state.proposal_sha256,stage:'rendered_verification',error:'<img src=x onerror=alert(1)> SYNTHETIC failure only',rendered_issues:[{code:'glyph_overlap',page:1,field_id:'f1'},{code:'<script>',page:1,field_id:'f1'},{code:'glyph_overlap',page:99,field_id:'f2'},{code:'glyph_overlap',page:true,field_id:'f1'},{code:'glyph_overlap',page:1,field_id:'foreign'}]};
 state.versions=[{id:'synthetic-blocked-run',status:'blocked',page_count_equal:true}];
 await click('#reload');await until(()=>evaluate('!busy&&!dirty'),'diagnostic reload');
 assert.equal(await evaluate("document.querySelector('#verification-result').hidden"),false);assert((await evaluate("document.querySelector('#verification-status').textContent")).includes('NOT FOR DELIVERY'));
 assert.equal(await evaluate("document.querySelectorAll('#verification-result img,#verification-result script').length"),0);assert.equal(await evaluate("document.querySelectorAll('.verification-link').length"),1);
 assert.equal(await evaluate("document.querySelectorAll('.verification-failed').length"),1);assert((await evaluate("document.querySelector('.verification-link').textContent")).includes('글자가 심하게 겹쳐'));
 pass('blocked output shows bounded field/page diagnostics as text and never invents foreign navigation');
 const snapshot=()=>evaluate("JSON.stringify({placements:[...placements],reviewed:[...reviewed],dirty,flags:['policy','unsigned','header-spine'].map(id=>document.querySelector('#'+id).checked),proposal:state.proposal_sha256,approval:state.approved_sha256})");
 const beforeDiagnostics=await snapshot(),beforeDiagnosticPosts=posts,beforeDiagnosticCompiles=compilePosts;
 await click('.verification-link');await until(()=>evaluate("document.querySelector('#image').complete&&document.querySelector('#view').value==='after'"),'readonly failure image');
 assert.equal(await snapshot(),beforeDiagnostics);assert.equal(posts,beforeDiagnosticPosts);assert.equal(compilePosts,beforeDiagnosticCompiles);
 assert((await evaluate("document.querySelector('#image').getAttribute('src')")).includes('/comparison/synthetic-blocked-run/after/1.png'));
 assert.equal(await evaluate("document.querySelectorAll('.verification-region').length"),1);assert.equal(await evaluate("document.querySelector('.verification-region').onclick"),null);
 assert((await evaluate("document.querySelector('#image').alt")).includes('제출 불가'));await click('.verification-region');assert.equal(await snapshot(),beforeDiagnostics);
 pass('diagnostic navigation opens only the blocked reopened result without editing data, consent or retrying');
 await evaluate("document.querySelector('#view').value='before';draw();document.querySelector('#field').value='f1'");await click('#clear-placement');
 await click('#reload-keep');await until(()=>evaluate('!busy&&dirty'),'retain draft with old diagnostics');assert.equal(await evaluate('placements.size'),1);
 assert((await evaluate("document.querySelector('#verification-note').textContent")).includes('미저장'));assert.equal(await evaluate("document.querySelectorAll('.verification-failed').length"),0);
 const retainedDiagnostics=await snapshot();await click('.verification-link');assert.equal(await snapshot(),retainedDiagnostics);assert.equal(posts,beforeDiagnosticPosts);assert.equal(compilePosts,beforeDiagnosticCompiles);
 assert.equal(await evaluate("Number(document.querySelector('.verification-region').getAttribute('x'))"),state.proposal.placements[0].region[0]);
 pass('saved-run diagnostics remain distinct from unsaved location drafts and cannot restore or overwrite them');
 state.versions=[];await click('#reload-keep');await until(()=>evaluate('!busy'),'missing reopened PDF reload');
 assert.equal(await evaluate("document.querySelector('#view option[value=after]').disabled"),true);await click('.verification-link');assert.equal(await evaluate("document.querySelector('#view').value"),'before');assert((await evaluate("document.querySelector('#status').textContent")).includes('원본만 표시'));
 state.latest_attempt.stage='native_edit_export';state.latest_attempt.rendered_issues=[];await click('#reload-keep');await until(()=>evaluate('!busy'),'early failure stage');assert((await evaluate("document.querySelector('#verification-stage').textContent")).includes('한글 편집'));assert.equal(await evaluate("document.querySelectorAll('.verification-link').length"),0);
 pass('early export failure or a missing reopened PDF stays blocked and never creates a candidate preview');
 state.latest_attempt.plan_sha256='foreign-plan';await click('#reload-keep');await until(()=>evaluate('!busy'),'foreign diagnostic digest');assert.equal(await evaluate("document.querySelector('#verification-result').hidden"),true);assert.equal(await evaluate("document.querySelector('#view option[value=after]').disabled"),true);
 assert.equal(posts,beforeDiagnosticPosts);assert.equal(compilePosts,beforeDiagnosticCompiles);pass('diagnostics from another proposal are hidden rather than attached to current data');
 state.apply_state={locked:false};state.latest_attempt={id:'synthetic-legacy-run',status:'checks_passed_needs_human_visual_review',artifact_integrity:'unbound_legacy',plan_sha256:state.proposal_sha256,stage:'checks_passed',error:'',rendered_issues:[]};state.versions=[{id:'synthetic-legacy-run',status:'checks_passed_needs_human_visual_review',artifact_integrity:'unbound_legacy',page_count_equal:true}];
 await click('#reload-keep');await until(()=>evaluate('!busy'),'unbound legacy fill result');
 assert((await evaluate("document.querySelector('#verification-status').textContent")).includes('파일 무결성 미확인'));assert.equal(await evaluate("document.querySelector('#verification-status').className"),'error');assert((await evaluate("document.querySelector('#verification-integrity').textContent")).includes('재작성하지 않습니다'));
 assert.equal(await snapshot(),retainedDiagnostics);assert.equal(posts,beforeDiagnosticPosts);assert.equal(compilePosts,beforeDiagnosticCompiles);pass('legacy verification is explicitly unbound and never turned into a new pass or rewritten');
 state.latest_attempt={...state.latest_attempt,status:'blocked',artifact_integrity:'mismatch',stage:'result_integrity',error:'Result artifact integrity is not confirmed; preview blocked',rendered_issues:[{code:'glyph_overlap',page:1,field_id:'f1'}]};state.versions=[];
 await click('#reload-keep');await until(()=>evaluate('!busy'),'changed fill result');assert.equal(await evaluate("document.querySelector('#verification-issues').children.length"),0);assert.equal(await evaluate("document.querySelector('#view option[value=after]').disabled"),true);assert((await evaluate("document.querySelector('#image').getAttribute('src')")).startsWith('/page/'));assert((await evaluate("document.querySelector('#verification-integrity').textContent")).includes('미리보기를 차단'));
 assert.equal(await snapshot(),retainedDiagnostics);assert.equal(posts,beforeDiagnosticPosts);assert.equal(compilePosts,beforeDiagnosticCompiles);pass('changed artifacts block result previews and stale diagnostics without resetting drafts or consent');
 state.apply_state={locked:true,record_valid:true,run_id:'synthetic-progress-run'};state.latest_attempt={id:'synthetic-progress-run',status:'in_progress',plan_sha256:state.proposal_sha256,stage:'saved_hwp_reopen',error:'',rendered_issues:[]};state.versions=[];
 await click('#reload-keep');await until(()=>evaluate('!busy'),'in-progress fill apply');
 assert.equal(await evaluate("document.querySelector('#apply-lock-status').hidden"),false);assert((await evaluate("document.querySelector('#verification-status').textContent")).includes('아직 검증 통과 아님'));assert.equal(await evaluate("document.querySelector('#verification-status').className"),'error');
 assert.equal(await evaluate("document.querySelector('#compile').disabled&&document.querySelector('#approve').disabled&&document.querySelector('#clear-placement').disabled"),true);assert.equal(await evaluate("document.querySelector('#view option[value=after]').disabled"),true);
 const duringApply=await snapshot();await click('#overlay rect');assert.equal(await snapshot(),duringApply);assert.equal(posts,beforeDiagnosticPosts);assert.equal(compilePosts,beforeDiagnosticCompiles);
 pass('in-progress or stopped apply locks edits and consent while preserving drafts and never showing success');
 state.apply_state={locked:true,record_valid:false};await click('#reload-keep');await until(()=>evaluate('!busy'),'unknown lock fill state');assert.equal(await evaluate("document.querySelector('#compile').disabled&&document.querySelector('#approve').disabled"),true);assert.equal(await snapshot(),duringApply);assert((await evaluate("document.querySelector('#apply-lock-status').textContent")).includes('자동 해제'));
 assert.equal(posts,beforeDiagnosticPosts);assert.equal(compilePosts,beforeDiagnosticCompiles);pass('invalid or stale lock metadata remains blocking and is never automatically recovered');
 assert((await evaluate("document.querySelector('#guide-title').textContent")).includes('작업을 확인'));await click('#guide-action');assert.equal(await evaluate('document.activeElement.id'),'apply-lock-status');assert.equal(posts,beforeDiagnosticPosts);assert.equal(compilePosts,beforeDiagnosticCompiles);pass('locked-state next action opens read-only status instead of implying retry or new approval');
 await call('Emulation.setDeviceMetricsOverride',{width:700,height:900,deviceScaleFactor:1,mobile:false});await until(()=>evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'narrow layout');
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await until(()=>evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'phone layout');await evaluate('window.scrollTo(0,0)');await capture('fill-mobile');
 assert.deepEqual(errors,[]);pass('narrow fill layout is contained and no browser JavaScript errors occur');
 console.log('Artifacts:',artifacts);
}catch(error){console.error(error);process.exitCode=1}
finally{if(socket)socket.close();for(const task of pending.values()){clearTimeout(task.timer);task.reject(new Error('CDP closing'))}pending.clear();browser.kill();await new Promise(done=>server.close(done));await rm(profile,{recursive:true,force:true,maxRetries:20,retryDelay:100})}
