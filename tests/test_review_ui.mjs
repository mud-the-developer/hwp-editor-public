// Real Edge browser tests against a synthetic loopback fixture ONLY.
// No real document comments, approval files, or work/review data are changed.
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtemp, readFile, rm, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { REVIEW_READY } from './review_ready.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const edge = process.env.EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const template = (await readFile(join(root, 'web/review.html'), 'utf8')).replace('__TOKEN__', 'synthetic-ui-test-token');
const templateReview = (await readFile(join(root, 'web/template-review.html'), 'utf8')).replaceAll('__REVIEW_TOKEN__', 'synthetic-ui-test-token');
const reportWorkspace = (await readFile(join(root, 'web/report-workspace.html'), 'utf8')).replaceAll('__REVIEW_TOKEN__', 'synthetic-ui-test-token');
const templateMalicious = '<img src=x onerror="globalThis.templateInjected=true">';
const templateLongText = '긴 한글 추출 본문과 숫자 123.45 및 문단을 끝까지 보존합니다. '.repeat(80);
const templateJob = {
  label: 'SYNTHETIC 양식 검토 '+templateMalicious,
  source_path: 'C:\\SYNTHETIC\\'+templateMalicious+'\\source.hwpx',
  payload_path: 'C:/SYNTHETIC/'+('긴경로'.repeat(80))+'/payload.hwpx',
  source_sha256: 'a'.repeat(64), payload_sha256: 'b'.repeat(64),
  target_range: [2, 5], payload_ranges: [[0, 3]],
  before_anchor: ['SYNTHETIC 앞 경계 '+templateMalicious], after_anchor: ['SYNTHETIC 뒤 경계'],
  caption_policy: 'owned-table-picture-crossref-v1',
  preview: {
    removed_paragraphs: 3, removed_pictures: 1, inserted_paragraphs: 3, inserted_tables: 1, inserted_pictures: 1,
    old_text: ['그림 15. SYNTHETIC 동일 캡션', templateMalicious, templateLongText],
    new_text: ['그림 15. SYNTHETIC 동일 캡션', templateMalicious, templateLongText],
    candidate_sha256: 'c'.repeat(64), resource_imports: {charProperties: 1}, font_imports: {HANGUL: 1},
    native_table_captions: 1, native_picture_captions: 1, body_cross_references: 1, caption_number_bases: {PICTURE: 14, TABLE: 0},
  },
};
const templateSecondJob = structuredClone(templateJob);
templateSecondJob.label = 'SYNTHETIC 문단 경계가 다른 추출';
templateSecondJob.preview.old_text = ['alpha\n\nbeta'];
templateSecondJob.preview.new_text = ['alpha', 'beta'];
const templateState = {plan: {jobs: [templateJob, templateSecondJob]}, plan_sha256: '7'.repeat(64), revision: '"template-1"', draft_authorized: false, locked: false, execution_recorded: false, result_error: '', artifacts: []};
const templatePNG=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAAEElEQVR4nGPgK5kAQQxwFgA9QAZtzKsBwQAAAABJRU5ErkJggg==','base64');
const templateImage={index:0,caption:'그림 21. SYNTHETIC PNG '+templateMalicious,sha256:createHash('sha256').update(templatePNG).digest('hex'),width:'43636',height:'36000',available:true,reason:''};
const templateImageEvidence=[{matched_count:6,before:[templateImage],after:[{...templateImage,height:'8932'}]},{matched_count:0,before:[{...templateImage,available:false,reason:'SYNTHETIC JPEG 미지원 '+templateMalicious}],after:[]}];
const templatePosts = [], templateDownloads = [], templateImageReads=[];
let templateImageFailure=false,templateDownloadError=false,templateStateFailure=false;
let templatePostError = false;
const reportModel={provider:'SYNTHETIC',id:'NO-REAL-MODEL'};
const reportState={name:'SYNTHETIC document workspace',source_name:'SYNTHETIC original',phase:'awaiting_request',revision:'report-synthetic-revision',gate_passed:true,issues:[],sections:[{id:'scope-a',text:'SYNTHETIC selected body',selectable:true,boundary:false,has_objects:false},{id:'scope-b',text:'SYNTHETIC other body',selectable:true,boundary:false,has_objects:false}],selection_ids:['scope-a'],versions:[],requests:[],active:false,active_request:null,progress:null,model:{status:'ready',selection:reportModel,chat_id:'c'.repeat(32),runtime_sha256:'d'.repeat(64),discovery_status:'finished',operation_active:false},error:'',details:{}};
const reportPosts=[];let reportReadFailures=0,reportFailedReads=0,reportTaskNumber=0;
function finishReportVersion(){
 const number=reportTaskNumber,id='version-'+number,request=reportState.requests.at(-1);
 reportState.versions.push({id,request_id:request.request_id,number,label:'SYNTHETIC 수정본 '+number,filename:'synthetic-'+number+'.hwpx',sha256:String(number).repeat(64),accepted:false,external:false,summary:['SYNTHETIC changed body'],comparison:[],review_copy_available:false});
 request.state='generated';reportState.active=false;reportState.phase='generated_unapproved';
}
const pages = Array.from({ length: 139 }, (_, i) => ({ page: i + 1, width: 595, height: 842 }));
const manifest = {
  workspace_id: 'browser-test-only', baseline_hwpx_sha256:'f'.repeat(64), pages, objects: { tbl: 118, pic: 136 }, protected_pages: [4],
  anchors: [{ anchor_id: 'fixture-anchor', page_hint: 2, old_text: 'Synthetic fixture phrase',
    boxes: [[50, 50, 180, 65]], cell: null, char_style: '1', simple:true }],
};
let comments = { workspace_id: manifest.workspace_id, comments: [] };
let revision = 0, saveFailure = false, versions = [], nativeProposal = null, nativeApprovedHash = null, nativeApprovalCount = 0;
let nativeHash = 'b'.repeat(64), latestAttempt = null, applyState = {locked:false};
let textProposal=null,textRevision='absent',textHash='e'.repeat(64),textSaveFailure=false,textSaveCount=0;
let textApply={enabled:false,available:false,active:false,status:'idle',plan_sha256:null,reason:'SYNTHETIC ONLY'},textApprovedHash=null,applyTextCount=0,applyTextFailure=false;
let applyContractCount=0;
const downloadBytes={hwp:Buffer.from('SYNTHETIC HWP DOWNLOAD ONLY'),pdf:Buffer.from('SYNTHETIC PDF DOWNLOAD ONLY')};
const downloadInfo=Object.fromEntries(Object.entries(downloadBytes).map(([kind,blob])=>[kind,{sha256:createHash('sha256').update(blob).digest('hex'),bytes:blob.length}]));
let downloadMode='ok',downloadCalls=0;
let recoverySnapshot=null,recoveryRevision='absent',recoverySaveCount=0,recoveryFailure=false;
let agentMode='ok',agentDiscoveryCalls=0,agentReadCalls=0,piMode='ok',piStatus='idle',piPrepareCalls=0,piProbeCalls=0;
function piFixture(status=piStatus){
 const passedState=status==='passed',runtime=['prepared','probing','passed'].includes(status)?{cli:passedState&&piMode==='bad-runtime-path'?null:passedState&&piMode==='empty-runtime-path'?'':'SYNTHETIC/pi.cmd',node:'SYNTHETIC/node.exe',sdk_entry:'SYNTHETIC/index.js',sdk_version:passedState&&piMode==='unsupported-version'?'1.3.1':'1.2.0',sha256:passedState&&piMode==='bad-runtime-hash'?'BAD':passedState&&piMode==='bad-runtime-hash-type'?['d'.repeat(64)]:'d'.repeat(64)}:null;
 return {status,probe_id:passedState&&piMode==='bad-probe-id'?'BAD':passedState&&piMode==='bad-probe-id-type'?['c'.repeat(32)]:'c'.repeat(32),runtime:passedState&&piMode==='missing-runtime'?null:runtime,stage:piMode==='error'?'<img src=x onerror=alert(1)> SYNTHETIC SDK BLOCK':null,result:passedState?{sdk_version:piMode==='version-mismatch'?'1.3.0':'1.2.0',transport_verified:true,process_exited:true,connected:false,authenticated:false,model_called:piMode==='malformed',tools_enabled:false,document_shared:false}:null,connected:false,authenticated:false,model_called:false,document_shared:false};
}
function agentFixture(status='finished'){return {status,connected:false,runtime_verified:false,executed:false,model_called:false,ignored_entries:1,unreadable_candidates:0,agents:status==='discovering'?[]:[{id:'pi',status:'detected',path:'SYNTHETIC/'+('long-path-'.repeat(25))+'/<img src=x onerror=alert(1)>/pi.cmd',connected:false,runtime_verified:false}]}}
let chatStatus='disconnected',chatMode='ok',chatTurns=[],chatLast=null,chatConnectCalls=0,chatSendCalls=0,chatReadTools=false,chatShared=false,contextPreviewCalls=0,chatProposalTools=false,chatArtifacts=[],artifactReviews=0,artifactReviewFailure=false,chatProgress=null;
const streamUsage={input_tokens:12,output_tokens:4,cache_read_tokens:0,cache_write_tokens:0,total_tokens:16,cost_usd:0.004};
function chatFixture(){const loaded=['ready','sending','full'].includes(chatStatus);return {status:chatStatus,chat_id:'e'.repeat(32),runtime:['prepared','connecting','ready','sending','full'].includes(chatStatus)?{cli:'SYNTHETIC/agent/bin/pi.cmd',node:'SYNTHETIC/node.exe',sdk_entry:'SYNTHETIC/index.js',sdk_version:'1.2.0',sha256:'b'.repeat(64)}:null,session_id:loaded?'f'.repeat(32):null,model:loaded?{provider:'fixture',id:'model'}:null,profile_loaded:loaded,turns:chatTurns,last_turn:chatLast,progress:chatProgress,stage:chatMode==='error'?'model_request':null,authentication_verified:false,tools_enabled:chatReadTools,tool_names:chatReadTools?(chatProposalTools?['get_review_context','get_job_status','propose_text_edits','request_approved_job']:['get_review_context','get_job_status']):[],proposal_tools_enabled:chatProposalTools,artifacts:chatArtifacts,write_tools_enabled:false,document_shared:chatShared,session_persisted:false}}
const requestedImages = new Set();
const fixture = createServer(async (req, res) => {
  const parsed = new URL(req.url, 'http://127.0.0.1');
  const route = parsed.pathname;
  res.setHeader('Cache-Control', 'no-store');
  if(route==='/report-document'){
    res.setHeader('Content-Type','text/html; charset=utf-8');
    res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'nonce-synthetic-ui-test-token'; style-src 'nonce-synthetic-ui-test-token'; img-src blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
    res.end(reportWorkspace);return;
  }
  if(route==='/api/report-state'){
    assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');res.setHeader('Content-Type','application/json');
    if(reportReadFailures){reportReadFailures--;reportFailedReads++;res.statusCode=503;res.end(JSON.stringify({error:'SYNTHETIC temporary read failure'}));}
    else res.end(JSON.stringify(reportState));return;
  }
  if(route.startsWith('/api/report-')){
    assert.equal(req.method,'POST');assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);reportPosts.push({route,body});res.setHeader('Content-Type','application/json');
    if(route==='/api/report-preview'){res.end(JSON.stringify({preview_sha256:'9'.repeat(64),request_id:'report-request-'+(reportTaskNumber+1),shared_context:{source:'SYNTHETIC'},model:reportModel}));}
    else if(route==='/api/report-start'){reportTaskNumber++;reportState.active=true;reportState.active_request='report-request-'+reportTaskNumber;reportState.requests.push({request_id:reportState.active_request,state:'model_running'});if(reportTaskNumber===1)setTimeout(()=>{reportReadFailures=1},30);res.statusCode=202;res.end(JSON.stringify(reportState));}
    else if(route==='/api/report-model/discover'){reportState.model.discovery_status='discovering';setTimeout(()=>{reportState.model.discovery_status='finished'},180);res.end('{}');}
    else if(route==='/api/report-model/prepare'){reportState.model.status='preparing';reportState.model.operation_active=true;setTimeout(()=>{reportState.model.status='prepared';reportState.model.operation_active=false},30);res.end('{}');}
    else if(route==='/api/report-model/connect'){reportState.model.status='connecting';reportState.model.operation_active=true;setTimeout(()=>{reportState.model.status='ready';reportState.model.selection=reportModel;reportState.model.operation_active=false},30);res.end('{}');}
    else{res.statusCode=404;res.end(JSON.stringify({error:'Unknown synthetic report action'}));}return;
  }
  if (route === '/template') {
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'nonce-synthetic-ui-test-token'; style-src 'nonce-synthetic-ui-test-token'; img-src blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
    res.end(templateReview);
  } else if (route === '/api/template-state') {
    assert.equal(req.method, 'GET');assert.equal(req.headers['x-review-token'], 'synthetic-ui-test-token');
    res.setHeader('Content-Type', 'application/json');
    if(templateStateFailure){res.statusCode=409;res.end(JSON.stringify({error:'SYNTHETIC state unavailable '+templateMalicious}));}
    else res.end(JSON.stringify(templateState));
  } else if (route.startsWith('/api/template-images/')) {
    assert.equal(req.method,'GET');assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');templateImageReads.push(route);
    const match=route.match(/^\/api\/template-images\/([a-f0-9]{64})\/([01])(?:\/(before|after)\/(0))?$/);assert(match);assert.equal(match[1],templateState.plan_sha256);
    if(templateImageFailure){res.statusCode=409;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({error:'SYNTHETIC preview conflict '+templateMalicious}));}
    else if(!match[3]){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(templateImageEvidence[Number(match[2])]));}
    else{assert.equal(match[2],'0');res.setHeader('Content-Type','image/png');res.end(templatePNG);}
  } else if (route === '/api/template-draft' || route === '/api/template-accept') {
    assert.equal(req.method, 'POST');assert.equal(req.headers['x-review-token'], 'synthetic-ui-test-token');
    assert.equal(req.headers['if-match'], templateState.revision);assert.equal(req.headers.origin, `http://${req.headers.host}`);
    let raw='';for await(const chunk of req)raw+=chunk;
    const body=JSON.parse(raw);templatePosts.push({route, body});
    res.setHeader('Content-Type', 'application/json');
    if(templatePostError){res.statusCode=409;res.end(JSON.stringify({error:'SYNTHETIC CAS conflict '+templateMalicious}));}
    else {
      if(route==='/api/template-draft'){assert.equal(body.plan_sha256,templateState.plan_sha256);assert.equal(body.draft_only_confirmed,true);templateState.draft_authorized=true;templateState.execution_recorded=true;templateState.artifacts=[{label:'SYNTHETIC 검토용 초안 '+templateMalicious,sha256:'c'.repeat(64),accepted:false}];}
      else{assert.equal(body.index,0);assert.equal(body.sha256,templateState.artifacts[0].sha256);templateState.artifacts[0].accepted=true;}
      res.end(JSON.stringify({ok:true}));
    }
  } else if (/^\/artifact\/[01]$/.test(route)) {
    assert.equal(req.method, 'GET');assert.equal(req.headers['x-review-token'], 'synthetic-ui-test-token');assert(templateState.execution_recorded);
    templateDownloads.push(route);
    if(templateDownloadError){templateState.artifacts=[];templateState.result_error='SYNTHETIC artifact changed '+templateMalicious;res.statusCode=409;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({error:templateState.result_error}));}
    else{res.setHeader('Content-Type','application/octet-stream');res.end('SYNTHETIC DOWNLOAD ONLY');}
  } else if (route === '/') {
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end(template);
  } else if (route === '/api/agents/discover' && req.method==='POST') {
    assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');agentDiscoveryCalls++;
    let raw='';for await(const chunk of req)raw+=chunk;assert.deepEqual(JSON.parse(raw),{});
    res.setHeader('Content-Type','application/json');
    if(agentMode==='error'){res.statusCode=503;res.end(JSON.stringify({error:'SYNTHETIC <img src=x onerror=alert(1)> discovery error'}))}
    else res.end(JSON.stringify(agentMode==='malformed'?{...agentFixture(),executed:true}:agentFixture(agentMode==='pending'?'discovering':'finished')));
  } else if (route === '/api/agents') {
    assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');agentReadCalls++;
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(agentFixture()));
  } else if (route.startsWith('/api/pi/chat')) {
    assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');let body=null;if(req.method==='POST'){let raw='';for await(const chunk of req)raw+=chunk;body=JSON.parse(raw)}
    if(route==='/api/pi/chat/artifact-review'){assert.deepEqual(Object.keys(body).sort(),['artifact_id','review_confirmed','session_id']);assert.equal(body.session_id,'f'.repeat(32));assert.equal(body.review_confirmed,true);artifactReviews++;const item=chatArtifacts.find(row=>row.artifact_id===body.artifact_id);res.setHeader('Content-Type','application/json');if(artifactReviewFailure||!item){res.statusCode=400;res.end(JSON.stringify({error:'SYNTHETIC stale artifact; no writes'}));return}const raw=item.artifact;res.statusCode=202;res.end(JSON.stringify(raw.kind==='text_draft'?{kind:'text_draft',workspace_id:manifest.workspace_id,baseline_hwpx_sha256:manifest.baseline_hwpx_sha256,proposal_revision:textRevision,edit:{anchor_id:raw.anchor_id,old_text:raw.old_text,new_text:raw.new_text},saved:false,approved:false,applied:false}:{kind:'approved_job_request',job:raw.job,applied:false,requires_separate_apply_confirmation:true}));return}
    if(route==='/api/pi/chat/context'){assert.deepEqual(Object.keys(body).sort(),['selection','session_id']);assert.equal(body.session_id,'f'.repeat(32));assert.equal(body.selection.view_mode,'original');assert.equal(body.selection.anchor_id,'fixture-anchor');contextPreviewCalls++;res.statusCode=202;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({context_id:'8'.repeat(32),context_sha256:'9'.repeat(64),selection:body.selection,preview:{review_context:{workspace_id:manifest.workspace_id,proposal_kind:body.selection.proposal_kind,selected_anchor:{anchor_id:'fixture-anchor',page:2,old_text:manifest.anchors[0].old_text},saved_memos:[],write_authority:'none'},job_snapshot:{captured_at:'SYNTHETIC',live:false,request_can_execute:false},bindings:{}}}));return}
    if(route==='/api/pi/chat/prepare'){assert.deepEqual(body,{prepare_confirmed:true});chatStatus='prepared'}
    else if(route==='/api/pi/chat/connect'){assert.deepEqual(body,{chat_id:'e'.repeat(32),runtime_sha256:'b'.repeat(64),reuse_pi_confirmed:true});chatConnectCalls++;chatStatus='ready'}
    else if(route==='/api/pi/chat/send'){const expected=['message','send_confirmed','session_id','turn_id'];if(body.context_id)expected.push('context_confirmed','context_id','context_sha256','read_tools_confirmed');if(body.proposal_tools_confirmed!==undefined){assert.equal(body.proposal_tools_confirmed,true);expected.push('proposal_tools_confirmed')}assert.deepEqual(Object.keys(body).sort(),expected.sort());chatProposalTools=body.proposal_tools_confirmed===true;if(body.context_id){assert.equal(body.context_id,'8'.repeat(32));assert.equal(body.context_sha256,'9'.repeat(64));assert.equal(body.context_confirmed,true);assert.equal(body.read_tools_confirmed,true)}chatReadTools=!!body.context_id;chatShared=chatShared||chatReadTools;assert.equal(body.send_confirmed,true);assert.equal(body.session_id,'f'.repeat(32));chatSendCalls++;chatTurns.push({role:'user',text:body.message});chatLast={turn_id:body.turn_id,message:body.message,outcome:chatMode==='error'?'failed':'sending'};chatStatus=chatMode==='error'?'ready':'sending';chatProgress=chatMode==='stream'?{turn_id:body.turn_id,state:'sending',stage:'responding',text:'SYNTHETIC LIVE 한글 <img src=x onerror=alert(1)>',requests:1,request_limit:chatProposalTools?3:chatReadTools?2:1,tool_activity:chatReadTools?[{id:'live-call',name:'get_review_context',status:'started'}]:[],usage:streamUsage,elapsed_seconds:2.4}:null}
    else if(route==='/api/pi/chat/cancel'){assert.deepEqual(body,{session_id:'f'.repeat(32),cancel_confirmed:true});chatStatus='ready';chatLast={...chatLast,outcome:'cancelled'};if(chatProgress)chatProgress={...chatProgress,state:'cancelled'}}
    else if(route==='/api/pi/chat/disconnect'){assert.deepEqual(body,{session_id:'f'.repeat(32),disconnect_confirmed:true});chatStatus='disconnected';chatTurns=[];chatLast=null;chatReadTools=false;chatShared=false;chatProposalTools=false;chatArtifacts=[];chatProgress=null}
    else if(route==='/api/pi/chat'&&chatStatus==='sending'&&chatMode==='pending'){chatStatus='ready';chatLast={...chatLast,outcome:'settled'};const usage=chatProgress?.usage||null;chatProgress=null;if(chatProposalTools)chatArtifacts.push({artifact_id:'a-'+'1'.repeat(32),turn_id:chatLast.turn_id,artifact:{kind:'text_draft',anchor_id:'fixture-anchor',old_text:manifest.anchors[0].old_text,new_text:'SYNTHETIC PI DRAFT'}});chatTurns.push({role:'assistant',text:'SYNTHETIC <img src=x onerror=alert(1)> reply',outcome:'settled',usage,tool_activity:chatReadTools?[{id:'call-1',name:'get_review_context',status:'started'},{id:'call-1',name:'get_review_context',status:'finished'},{id:'call-2',name:'get_job_status',status:'started'},{id:'call-2',name:'get_job_status',status:'finished'}]:[]})}
    res.statusCode=req.method==='POST'?202:200;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(chatFixture()));
  } else if (route === '/api/pi/prepare' && req.method==='POST') {
    assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');let raw='';for await(const chunk of req)raw+=chunk;assert.deepEqual(JSON.parse(raw),{prepare_confirmed:true});piPrepareCalls++;
    piStatus=piMode==='error'?'failed':'prepared';res.statusCode=202;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(piFixture()));
  } else if (route === '/api/pi/probe' && req.method==='POST') {
    assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');let raw='';for await(const chunk of req)raw+=chunk;assert.deepEqual(JSON.parse(raw),{probe_id:'c'.repeat(32),runtime_sha256:'d'.repeat(64),execute_confirmed:true});piProbeCalls++;
    piStatus=piMode==='pending'?'probing':'passed';res.statusCode=202;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(piFixture()));
  } else if (route === '/api/pi') {
    assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');if(piStatus==='probing')piStatus='passed';res.setHeader('Content-Type','application/json');res.end(JSON.stringify(piFixture()));
  } else if (route === '/api/state') {
    res.setHeader('Content-Type', 'application/json');
    const proposal = ['native','layout'].includes(parsed.searchParams.get('proposal')) ? nativeProposal : textProposal;
    res.end(JSON.stringify({ manifest, comments, comments_revision: String(revision), proposal, proposal_error: null,
      proposal_sha256: proposal ? (proposal.schema===1?textHash:nativeHash) : null, approved_sha256: proposal?.schema===1?textApprovedHash:(proposal?nativeApprovedHash:null), text_apply:textApply,
      proposal_revision: ['native','layout'].includes(parsed.searchParams.get('proposal'))?nativeHash:textRevision,
      proposal_kind: parsed.searchParams.get('proposal') || 'text', versions,
      apply_state:applyState, latest_attempt: proposal && latestAttempt?.plan_sha256 === (proposal.schema===1?textHash:nativeHash) ? latestAttempt : null }));
  } else if (route === '/api/session' && req.method==='GET') {
    const foreign=recoverySnapshot&&(recoverySnapshot.workspace_id!==manifest.workspace_id||recoverySnapshot.baseline_hwpx_sha256!==manifest.baseline_hwpx_sha256);
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({revision:recoveryRevision,snapshot:foreign?null:recoverySnapshot,issue:foreign?'Fixture baseline changed; no automatic rebind':null}));
  } else if ((route==='/api/session'||route==='/api/session-clear') && req.method==='POST') {
    let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);res.setHeader('Content-Type','application/json');
    if(recoveryFailure){res.statusCode=503;res.end(JSON.stringify({error:'Fixture recovery save failed; no retry'}))}
    else if(req.headers['if-match']!=='"'+recoveryRevision+'"'){res.statusCode=409;res.end(JSON.stringify({error:'Fixture recovery CAS conflict'}))}
    else{if(route==='/api/session-clear')assert.deepEqual(body,{clear_confirmed:true});else{assert.equal(body.schema,1);assert.equal(body.workspace_id,manifest.workspace_id);assert(!('approved' in body));}
      recoverySnapshot=route==='/api/session-clear'?null:body;recoveryRevision='recovery-'+(++recoverySaveCount);res.end(JSON.stringify({saved:true,revision:recoveryRevision,document_changed:false,approval_changed:false}));}
  } else if (route === '/api/versions') {
    res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ versions, attempts: latestAttempt ? [latestAttempt] : [], apply_state:applyState, text_apply:textApply }));
  } else if (/^\/comparison\/[0-9T\-a-f]+\/(before|after|diff)\/\d+\.png$/.test(route)) {
    res.setHeader('Content-Type', 'image/svg+xml');
    const side = route.split('/')[3];
    res.end(`<svg xmlns="http://www.w3.org/2000/svg" width="595" height="842"><rect width="595" height="842" fill="white"/><text x="50" y="65" font-size="20">TEST ONLY ${side}</text><rect x="50" y="100" width="200" height="30" fill="${side === 'diff' ? '#ef4456' : '#dce8ff'}"/></svg>`);
  } else if (/^\/result\/\d{8}T\d{6}-[a-f0-9]{8}\/(hwp|pdf)$/.test(route)) {
    assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');downloadCalls++;
    const kind=route.split('/')[3];if(downloadMode==='slow')await new Promise(resolve=>setTimeout(resolve,250));
    if(downloadMode==='error'){res.statusCode=409;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({error:'SYNTHETIC result changed; no retry'}))}
    else{res.setHeader('X-Artifact-Sha256',downloadInfo[kind].sha256);res.end(downloadMode==='corrupt'?Buffer.alloc(downloadBytes[kind].length,88):downloadBytes[kind])}
  } else if (route === '/api/search') {
    const query = parsed.searchParams.get('q');
    await new Promise(resolve => setTimeout(resolve, query === 'slow' ? 300 : 20));
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ query, ocr: false, matches: query === 'unique' ? [{ page: 2, snippet: 'Synthetic unique fixture phrase' }] : query === 'slow' ? [{ page: 20, snippet: 'Slow fixture result' }] : [] }));
  } else if (/^\/page\/\d+\.png$/.test(route)) {
    const number = Number(route.split('/')[2].split('.')[0]);
    requestedImages.add(number);
    res.setHeader('Content-Type', 'image/svg+xml');
    res.end(`<svg xmlns="http://www.w3.org/2000/svg" width="595" height="842"><rect width="595" height="842" fill="white"/><rect x="50" y="50" width="495" height="40" fill="#e4ecff"/><text x="60" y="77" font-size="20">TEST ONLY - Page ${number}</text><path d="M50 150H545 M50 200H545 M50 250H545" stroke="#ced6e3"/></svg>`);
  } else if(route==='/api/apply-contract'&&req.method==='POST'){
    assert.equal(req.headers['x-review-token'],'synthetic-ui-test-token');applyContractCount++;
    for await(const chunk of req)assert(chunk.length>0);
    res.statusCode=400;res.setHeader('Content-Type','application/json');res.end(JSON.stringify({error:'SYNTHETIC Native disabled; no document action'}));
  } else if(route==='/api/apply-text'&&req.method==='POST'){
    let raw='';for await(const chunk of req)raw+=chunk;
    assert.deepEqual(JSON.parse(raw),{workspace_id:manifest.workspace_id,baseline_hwpx_sha256:manifest.baseline_hwpx_sha256,plan_sha256:textHash,text_apply_confirmed:true});
    assert.equal(req.headers['if-match'],'"'+textRevision+'"');applyTextCount++;
    res.setHeader('Content-Type','application/json');
    textApply={...textApply,plan_sha256:textHash,status:applyTextFailure?'blocked':'running',active:!applyTextFailure,elapsed_seconds:12,error:applyTextFailure?'SYNTHETIC <img src=x onerror=alert(1)> blocked; no retry':''};
    res.statusCode=applyTextFailure?503:202;res.end(JSON.stringify(applyTextFailure?{error:textApply.error}:{accepted:true,plan_sha256:textHash}));
  } else if(route==='/api/text-proposal'&&req.method==='POST'){
    let raw='';for await(const chunk of req)raw+=chunk;
    const body=JSON.parse(raw);assert.deepEqual(Object.keys(body).sort(),['baseline_hwpx_sha256','edits','workspace_id']);
    assert.equal(body.workspace_id,manifest.workspace_id);assert.equal(body.baseline_hwpx_sha256,manifest.baseline_hwpx_sha256);
    await new Promise(resolve=>setTimeout(resolve,100));res.setHeader('Content-Type','application/json');
    if(textSaveFailure){res.statusCode=503;res.end(JSON.stringify({error:'Fixture strict cache failed; NO Native fallback'}))}
    else if(req.headers['if-match']!=='"'+textRevision+'"'){res.statusCode=409;res.end(JSON.stringify({error:'Fixture proposal conflict'}))}
    else{textProposal={schema:1,...body};textRevision='text-'+(++textSaveCount);res.end(JSON.stringify({proposal:textProposal,proposal_sha256:textHash,proposal_revision:textRevision,approved:false}))}
  } else if (route === '/api/approve' && req.method === 'POST') {
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    const expected={ plan_sha256: nativeHash, proposal_kind: nativeProposal.schema===3?'layout':'native', native_policy_confirmed: true, regions_reviewed: nativeProposal.edits.map(e=>e.anchor_id) };
    if(nativeProposal.schema===3)expected.row_minima_confirmed=true;
    assert.deepEqual(body,expected);
    nativeApprovalCount++; nativeApprovedHash = nativeHash;
    res.setHeader('Content-Type', 'application/json');res.end(JSON.stringify({ approved: true }));
  } else if (route === '/api/comments' && req.method === 'POST') {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    assert.equal(body.workspace_id, manifest.workspace_id);
    await new Promise(resolve => setTimeout(resolve, 150));
    res.setHeader('Content-Type', 'application/json');
    if (saveFailure) { res.statusCode = 503; res.end(JSON.stringify({ error: 'Fixture save failure; draft retained' })); }
    else if (req.headers['if-match'] !== '"' + revision + '"') { res.statusCode = 409; res.end(JSON.stringify({ error: 'Fixture conflict; read latest comments and keep draft' })); }
    else { comments = body; revision++; res.end(JSON.stringify({ saved: true, comments_revision: String(revision) })); }
  } else {
    res.statusCode = route === '/favicon.ico' ? 204 : 404;
    res.end();
  }
});
await new Promise(resolve => fixture.listen(0, '127.0.0.1', resolve));
const fixtureUrl = `http://127.0.0.1:${fixture.address().port}`;
const profile = await mkdtemp(join(tmpdir(), 'hwp-ui-edge-'));
const browser = spawn(edge, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' });
let client;
const checks = [];
const output = join(root, 'work', 'ui-check-' + Date.now() + '-' + randomUUID().slice(0, 8));

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.pending = new Map();
    this.listeners = new Map();
    this.nextId = 1;
    socket.addEventListener('message', ({ data }) => {
      const message = JSON.parse(data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        clearTimeout(pending.timer);
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
        else pending.resolve(message.result);
      } else {
        for (const handler of this.listeners.get(message.method) || []) handler(message.params);
      }
    });
    socket.addEventListener('close', () => {
      for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(new Error('CDP closed')); }
      this.pending.clear();
    });
  }
  on(event, callback) {
    if (!this.listeners.has(event)) this.listeners.set(event, []);
    this.listeners.get(event).push(callback);
  }
  call(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const result = await this.call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text + ': ' + (result.exceptionDetails.exception?.description || ''));
    return result.result.value;
  }
}

async function until(callback, label) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await callback()) return;
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  throw new Error('Timed out: ' + label);
}
function passed(name) { checks.push(name); console.log('PASS:', name); }
async function mouseClick(selector) {
  // Real pointer dispatch ONLY to the synthetic fixture, never the actual report UI.
  assert.equal(await client.evaluate('location.origin'), fixtureUrl);
  await client.evaluate(`(() => {document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',behavior:'instant'});})()`);
  let position;
  await until(async()=>{
    position=await client.evaluate(`(() => {const e=document.querySelector(${JSON.stringify(selector)}),b=e.getBoundingClientRect(),x=b.x+b.width/2,y=b.y+b.height/2;return {x,y,hit:x>0&&y>0&&x<innerWidth&&y<innerHeight&&e.contains(document.elementFromPoint(x,y))};})()`);
    return position.hit;
  },'actual clickable fixture target '+selector);
  const {x,y}=position;
  await client.call('Input.dispatchMouseEvent',{type:'mouseMoved',x,y});
  await client.call('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});
  await client.call('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});
}

try {
  let debuggingPort;
  await until(async () => {
    try { debuggingPort = Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); return debuggingPort > 0; }
    catch { return false; }
  }, 'Edge startup');
  const targets = await (await fetch(`http://127.0.0.1:${debuggingPort}/json/list`)).json();
  const target = targets.find(t => t.type === 'page');
  assert(target, 'Edge page target missing');
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  client = new CDP(socket);
  const errors = [];
  client.on('Runtime.exceptionThrown', ({ exceptionDetails }) => errors.push(exceptionDetails.text));
  let acceptDialog = false, dialogs = 0;
  client.on('Page.javascriptDialogOpening', () => { dialogs++; client.call('Page.handleJavaScriptDialog', { accept: acceptDialog }).catch(error => errors.push(error.message)); });
  await client.call('Page.enable');
  await client.call('Runtime.enable');
  await client.call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await client.call('Page.navigate', { url: fixtureUrl });
  await until(() => client.evaluate("document.querySelectorAll('.thumbnail-button').length===139 && document.querySelector('#page-image').complete"), 'thumbnail initialization');
  const downloadFolder=join(profile,'owned-fixture-downloads');await mkdir(downloadFolder);
  await client.call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloadFolder,eventsEnabled:true});
  const capture=async name=>{const directory=join(output,'readability');await mkdir(directory,{recursive:true});const image=await client.call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(join(directory,name+'.png'),Buffer.from(image.data,'base64'))};
  await capture('review-desktop');
  const initial = await client.evaluate(`(() => { const list=$('thumbnail-list');return {count:list.children.length,scrollable:list.scrollHeight>list.clientHeight,current:list.querySelector('[aria-current="page"]').dataset.page,lazy:[...list.querySelectorAll('img')].every(i=>i.loading==='lazy'),left:list.getBoundingClientRect().right<document.querySelector('.document-panel').getBoundingClientRect().left}; })()`);
  assert.deepEqual(initial, { count: 139, scrollable: true, current: '1', lazy: true, left: true });
  assert(requestedImages.size < 139, 'All previews fetched eagerly');
  const initialImageRequests = requestedImages.size;
  passed('139 lazy thumbnails in an independently scrollable left pane');
  const tabCounts=[nativeApprovalCount,applyTextCount,textSaveCount,revision,piPrepareCalls,piProbeCalls];
  assert.deepEqual(await client.evaluate("['review','pi','jobs'].map(id=>!$('inspector-'+id).hidden)"),[true,false,false]);
  await mouseClick('#inspector-tab-pi');assert(await client.evaluate("!$('inspector-pi').hidden&&$('inspector-review').hidden"));await capture('pi-workspace');
  await client.call('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});await client.call('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
  assert(await client.evaluate("!$('inspector-jobs').hidden&&document.activeElement.id==='inspector-tab-jobs'"));assert.equal(await client.evaluate("$('page-number').value"),'1');await capture('jobs-workspace');
  await client.call('Input.dispatchKeyEvent',{type:'keyDown',key:'Home',code:'Home',windowsVirtualKeyCode:36});await client.call('Input.dispatchKeyEvent',{type:'keyUp',key:'Home',code:'Home',windowsVirtualKeyCode:36});
  assert(await client.evaluate("!$('inspector-review').hidden&&document.activeElement.id==='inspector-tab-review'"));
  assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision,piPrepareCalls,piProbeCalls],tabCounts);
  passed('review/Pi/jobs tabs use real pointer and keyboard navigation without moving the document or invoking any action');
  await client.evaluate("$('comment-text').value='SYNTHETIC UNSAVED TAB';updateComposer()");
  await mouseClick('#inspector-tab-pi');await client.evaluate("focusReviewControl($('comment-text'))");assert(await client.evaluate("!$('inspector-review').hidden&&document.activeElement.id==='comment-text'"));
  assert.equal(await client.evaluate("$('comment-text').value"),'SYNTHETIC UNSAVED TAB');await client.evaluate("$('comment-text').value='';updateComposer()");
  assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision,piPrepareCalls,piProbeCalls],tabCounts);
  passed('contextual review focus reveals its tab while preserving comments, approval and Pi execution state');
  await mouseClick('#inspector-tab-pi');
  await until(()=>client.evaluate("!agentDiscoveryPending&&$('agent-presence-list').children.length===1"),'automatic presence discovery only');
  assert.equal(agentDiscoveryCalls,1);assert.equal(agentReadCalls,0);
  assert.match(await client.evaluate("$('agent-presence-list').textContent"),/경로 발견 · 실행\/인증 미검증/);
  assert.doesNotMatch(await client.evaluate("$('agent-presence-list').textContent"),/Codex|Hermes/);
  assert.equal(await client.evaluate("document.querySelectorAll('#agent-jobs img').length"),0);
  passed('same-GUI agent discovery starts once without CLI/model execution and never claims login or full installation absence');
  for(const width of [430,980]){await client.call('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});assert(await client.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'Agent path overflow');}
  await client.call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  passed('detected CLI paths are inert text and remain contained on narrow review layouts');
  const agentWriteCounts=[nativeApprovalCount,applyTextCount,textSaveCount,revision];agentMode='pending';
  await client.evaluate("$('agent-discover').click();$('agent-discover').click()");
  await until(()=>client.evaluate('!agentDiscoveryPending'),'bounded presence status polling');
  assert.equal(agentDiscoveryCalls,2);assert.equal(agentReadCalls,1);
  assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],agentWriteCounts);
  passed('presence polling is token-bound, independent of document writes and cannot duplicate an in-flight scan');
  agentMode='error';await client.evaluate("$('agent-discover').click()");await until(()=>client.evaluate('!agentDiscoveryPending'),'explicit discovery failure');
  const failedCalls=agentDiscoveryCalls;await new Promise(resolve=>setTimeout(resolve,350));assert.equal(agentDiscoveryCalls,failedCalls);
  assert.match(await client.evaluate("$('agent-presence-status').textContent"),/SYNTHETIC/);assert.equal(await client.evaluate("document.querySelectorAll('#agent-jobs img').length"),0);
  agentMode='malformed';await client.evaluate("$('agent-discover').click()");await until(()=>client.evaluate('!agentDiscoveryPending'),'reject false runtime proof');
  assert.match(await client.evaluate("$('agent-presence-status').textContent"),/응답 확인 필요/);
  assert.equal(await client.evaluate("$('agent-presence-list').children.length"),0);
  agentMode='ok';await client.evaluate("$('agent-discover').click()");await until(()=>client.evaluate("!agentDiscoveryPending&&$('agent-presence-list').children.length===1"),'explicit presence re-read');
  assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],agentWriteCounts);
  passed('discovery failures stay visible without auto retry and malformed availability cannot enable an agent or document action');
  assert.equal(piPrepareCalls,0);assert.equal(piProbeCalls,0);
  await client.evaluate("$('pi-prepare').click()");await until(()=>client.evaluate("!piPending&&piState?.status==='prepared'"),'explicit Pi metadata preparation');
  assert.match(await client.evaluate("$('pi-runtime-info').textContent"),/SYNTHETIC\/node.exe/);
  assert(await client.evaluate("$('pi-runtime-details').open"));assert.equal(piProbeCalls,0);
  passed('Pi program hashes reveal on demand before separate execution confirmation, never hidden by the clean layout');
  const preparedPiSaved=await client.evaluate('JSON.stringify(piState)');
  await client.evaluate(`{const data=JSON.parse(${JSON.stringify(preparedPiSaved)});data.runtime.sdk_version='1.3.0';renderPiProbe(data)}`);
  assert.equal(await client.evaluate('piState.runtime.sdk_version'),'1.3.0');assert(await client.evaluate("!$('pi-probe').disabled"));
  const badPiVersion=await client.evaluate("(()=>{const data={...piState,runtime:{...piState.runtime,sdk_version:'1.3.1'}};try{renderPiProbe(data);return ''}catch(error){return error.message}})()");
  assert.match(badPiVersion,/프로그램 결합 확인 필요/);assert.equal(piProbeCalls,0);assert.equal(await client.evaluate('piState.runtime.sdk_version'),'1.3.0');
  for(const version of ['1.2.0','1.3.0','1.4.0']){
   const evidence={...JSON.parse(preparedPiSaved),status:'passed',result:{sdk_version:version,transport_verified:true,process_exited:true,connected:false,authenticated:false,model_called:false,tools_enabled:false,document_shared:false}};evidence.runtime.sdk_version=version;
   await client.evaluate(`renderPiProbe(${JSON.stringify(evidence)})`);
   assert(await client.evaluate("piState.status==='passed'&&$('pi-probe').disabled"));assert.equal(await client.evaluate('piState.result.sdk_version'),version);assert.match(await client.evaluate("$('pi-probe-status').textContent"),/SDK 통신·종료 검사 통과/);assert.equal(piProbeCalls,0);
  }
  await client.evaluate(`renderPiProbe(JSON.parse(${JSON.stringify(preparedPiSaved)}))`);
  assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],agentWriteCounts);
  passed('prepared and passed Pi 1.2/1.3 receipts bind runtime metadata/version while unknown releases never grant execution or document authority');
  acceptDialog=false;await client.evaluate("$('pi-probe').click()");await until(()=>client.evaluate('!piPending'),'cancel Pi execution');assert.equal(piProbeCalls,0);
  acceptDialog=true;piMode='pending';await client.evaluate("$('pi-probe').click();$('pi-probe').click()");await until(()=>client.evaluate("!piPending&&piState?.status==='passed'"),'fixed no-model SDK check');
  assert.equal(piProbeCalls,1);assert.match(await client.evaluate("$('pi-probe-status').textContent"),/실제 대화\/로그인은 미연결/);
  assert(await client.evaluate("$('pi-probe').disabled"));
  assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],agentWriteCounts);
  passed('Pi program preparation cannot execute; exact separate cancellable confirmation sends one fixed no-model receipt, never HWP approval');
  piMode='error';await client.evaluate("$('pi-prepare').click()");await until(()=>client.evaluate('!piPending'),'Pi preparation blocked');
  const piCalls=piPrepareCalls;await new Promise(resolve=>setTimeout(resolve,350));assert.equal(piPrepareCalls,piCalls);assert.match(await client.evaluate("$('pi-probe-status').textContent"),/SYNTHETIC SDK BLOCK/);assert.equal(await client.evaluate("document.querySelectorAll('#pi-probe-panel img').length"),0);
  piMode='ok';await client.evaluate("$('pi-prepare').click()");await until(()=>client.evaluate("!piPending&&piState?.status==='prepared'"),'fresh exact Pi receipt');piMode='malformed';await client.evaluate("$('pi-probe').click()");await until(()=>client.evaluate('!piPending'),'reject false Pi model-free evidence');
  assert.match(await client.evaluate("$('pi-probe-status').textContent"),/증거 확인 필요/);assert(await client.evaluate("$('pi-probe').disabled"));assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],agentWriteCounts);
  for(const mode of ['version-mismatch','missing-runtime','bad-probe-id','bad-probe-id-type','bad-runtime-hash','bad-runtime-hash-type','bad-runtime-path','empty-runtime-path','unsupported-version']){
   piMode='ok';await client.evaluate("$('pi-prepare').click()");await until(()=>client.evaluate("!piPending&&piState?.status==='prepared'"),'fresh synthetic program receipt '+mode);
   piMode=mode;await client.evaluate("$('pi-probe').click()");await until(()=>client.evaluate('!piPending'),'reject malformed passed receipt '+mode);
   assert.equal(await client.evaluate('piState'),null,mode);assert(await client.evaluate("$('pi-probe').disabled&&$('pi-runtime-info').textContent===''"),mode);
   assert(!/SDK 통신·종료 검사 통과/.test(await client.evaluate("$('pi-probe-status').textContent")),mode);assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],agentWriteCounts);
   const rejectedCalls=piProbeCalls,rejectedDialogs=dialogs;await client.evaluate("Promise.resolve($('pi-probe').onclick()).then(()=>true)");assert.equal(piProbeCalls,rejectedCalls,mode);assert.equal(dialogs,rejectedDialogs,mode);
  }
  passed('malformed or cross-version passed SDK receipts clear stale program state and cannot retain success, prompt execution again or touch HWP');
  piMode='ok';dialogs=0;acceptDialog=false;
  assert.equal(chatConnectCalls,0);assert.equal(chatSendCalls,0);const chatWrites=[nativeApprovalCount,applyTextCount,textSaveCount,revision];
  await mouseClick('#pi-chat-connect');await until(()=>client.evaluate("!chatBusy&&chatState?.status==='prepared'"),'cancel profile reuse consent');assert.equal(chatConnectCalls,0);assert.equal(chatSendCalls,0);
  acceptDialog=true;await mouseClick('#pi-chat-connect');await until(()=>client.evaluate("!chatBusy&&chatState?.status==='ready'"),'explicit existing Pi settings connection');assert.equal(chatConnectCalls,1);assert.equal(chatSendCalls,0);
  await client.evaluate("$('pi-chat-input').value='SYNTHETIC user question';updateChatControls()");assert(await client.evaluate("$('pi-chat-send').disabled"));await mouseClick('#pi-chat-consent');chatMode='pending';
  await client.evaluate("$('pi-chat-send').click();$('pi-chat-send').click()");await until(()=>client.evaluate("!chatBusy&&chatState.last_turn?.outcome==='settled'"),'one consented Pi message');assert.equal(chatSendCalls,1);
  assert.match(await client.evaluate("$('pi-chat-messages').textContent"),/SYNTHETIC.*reply/);assert.equal(await client.evaluate("$('pi-chat-messages').querySelectorAll('img').length"),0);assert.equal(await client.evaluate("$('pi-chat-input').value"),'');assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],chatWrites);
  passed('Pi profile reuse and message transmission need separate consent; one app chat turn never invokes document actions or renders HTML');
  chatMode='error';await client.evaluate("$('pi-chat-input').value='SYNTHETIC failed question';updateChatControls();$('pi-chat-send').click()");await until(()=>client.evaluate("!chatBusy&&chatState.last_turn?.outcome==='failed'"),'failed Pi reply preserves draft');const chatCalls=chatSendCalls;await new Promise(resolve=>setTimeout(resolve,350));assert.equal(chatSendCalls,chatCalls);assert.equal(await client.evaluate("$('pi-chat-input').value"),'SYNTHETIC failed question');
  chatMode='hold';await client.evaluate("$('pi-chat-send').click()");await until(()=>client.evaluate("chatState?.status==='sending'"),'pending model request');await client.evaluate("$('pi-chat-cancel').click()");await until(()=>client.evaluate("!chatBusy&&chatState.last_turn?.outcome==='cancelled'"),'cancel app conversation only');assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],chatWrites);
  passed('Pi model failure/cancellation keeps the question and never auto resends, clears HWP locks or claims a document result');
  chatMode='stream';await client.evaluate("$('pi-chat-input').value='SYNTHETIC stream question';updateChatControls();$('pi-chat-send').click()");await until(()=>client.evaluate("!$('pi-chat-progress').hidden&&$('pi-chat-progress-text').textContent.includes('SYNTHETIC LIVE')"),'streamed partial before final process receipt');assert.equal(await client.evaluate("$('pi-chat-progress').getAttribute('aria-busy')"),'true');assert.equal(await client.evaluate("$('pi-chat-progress-text').querySelectorAll('img').length"),0);assert.match(await client.evaluate("$('pi-chat-progress-usage').textContent"),/SDK.*16토큰.*0\.004000/);assert.equal(await client.evaluate("$('pi-chat-messages').textContent.includes('SYNTHETIC LIVE')"),false);assert.equal(await client.evaluate("$('pi-chat-progress').querySelectorAll('button').length"),0);
  const noResend=chatSendCalls;chatStatus='ready';chatLast={...chatLast,outcome:'failed'};chatProgress={...chatProgress,state:'failed'};await until(()=>client.evaluate("!chatBusy&&$('pi-chat-progress-title').textContent.includes('실패')"),'failed partial stays explicitly unverified');await new Promise(resolve=>setTimeout(resolve,300));assert.equal(chatSendCalls,noResend);assert.equal(await client.evaluate("$('pi-chat-input').value"),'SYNTHETIC stream question');assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],chatWrites);
  passed('streamed public text and SDK usage arrive before completion, remain inert provisional data and never become draft/approval actions after failure');
  await client.evaluate("$('pi-chat-send').click()");await until(()=>client.evaluate("chatState.status==='sending'&&!$('pi-chat-progress').hidden"),'new explicit streaming turn');await client.evaluate("$('pi-chat-cancel').click()");await until(()=>client.evaluate("!chatBusy&&$('pi-chat-progress-title').textContent.includes('중단')"),'cancel streaming response');assert.equal(await client.evaluate("$('pi-chat-progress').getAttribute('aria-busy')"),'false');assert.equal(await client.evaluate("$('pi-chat-input').value"),'SYNTHETIC stream question');assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],chatWrites);
  passed('cancelled streaming text is read-only and keeps the question without a follow-up model or document job');
  assert.equal(contextPreviewCalls,0);const beforeContextSend=chatSendCalls;
  await client.evaluate("navigate(2);$('target').value='fixture-anchor';updateComposer();$('pi-context-panel').open=true");await mouseClick('#pi-context-preview');await until(()=>client.evaluate('!contextPending&&!!contextPreview'),'exact context preview without model');assert.equal(chatSendCalls,beforeContextSend);assert.equal(contextPreviewCalls,1);
  assert.match(await client.evaluate("$('pi-context-data').textContent"),/Synthetic fixture phrase/);assert.match(await client.evaluate("$('pi-context-data').textContent"),/\"live\": false/);assert.equal(await client.evaluate("$('pi-context-confirm').checked"),false);
  await client.evaluate("$('pi-context-confirm').checked=true;$('target').value='';updateComposer()");assert.equal(await client.evaluate("$('pi-context-confirm').checked"),false);assert.equal(await client.evaluate('contextPreview'),null);assert.equal(chatSendCalls,beforeContextSend);
  passed('HWP context is locally previewed before any model send, and source/anchor changes revoke the per-turn sharing choice');
  await client.evaluate("$('target').value='fixture-anchor';updateComposer();$('pi-context-preview').click()");await until(()=>client.evaluate('!contextPending&&!!contextPreview'),'fresh selected HWP snapshot');
  chatMode='stream';await client.evaluate("$('pi-context-confirm').checked=true;$('pi-chat-input').value='SYNTHETIC review selected source';updateChatControls();$('pi-chat-send').click()");await until(()=>client.evaluate("!$('pi-chat-progress').hidden&&$('pi-chat-progress-tools').textContent.includes('get_review_context')"),'live consented read tool phase');assert.match(await client.evaluate("$('pi-chat-progress-meta').textContent"),/1\/2/);chatMode='pending';await until(()=>client.evaluate("!chatBusy&&chatState.last_turn?.outcome==='settled'"),'read-only document tool turn');assert.equal(chatSendCalls,beforeContextSend+1);
  assert.match(await client.evaluate("$('pi-chat-messages').textContent"),/get_review_context/);assert.match(await client.evaluate("$('pi-chat-messages').textContent"),/get_job_status/);assert.equal(await client.evaluate("$('pi-context-confirm').checked"),false);assert.equal(await client.evaluate("$('pi-context-history-warning').hidden"),false);assert.equal(await client.evaluate("$('pi-chat-messages').querySelectorAll('img').length"),0);assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],chatWrites);
  passed('explicit HWP sharing binds its snapshot and read-only tool consent, shows actual tool records, and never grants write/approval authority');
  assert.equal(await client.evaluate("$('pi-chat-progress').hidden"),true);assert.equal(await client.evaluate("$('pi-chat-progress-text').textContent"),'');assert.match(await client.evaluate("$('pi-chat-messages').textContent"),/SDK.*16토큰/);
  passed('a verified final receipt replaces the streamed preview and exposes only allowed tool names and SDK-reported non-invoice usage');
  await client.evaluate("$('pi-context-confirm').checked=true;updateChatControls();$('pi-context-actions').checked=true;$('pi-chat-input').value='SYNTHETIC stage a draft';updateChatControls();$('pi-chat-send').click()");await until(()=>client.evaluate("!chatBusy&&chatState.artifacts.length===1"),'Pi staging-only draft');assert.equal(await client.evaluate("$('pi-context-actions').checked"),false);
  const beforeImport=[nativeApprovalCount,applyTextCount,textSaveCount,revision];await mouseClick('#pi-chat-artifacts button');await until(()=>client.evaluate("$('draft-new').value==='SYNTHETIC PI DRAFT'"),'explicit Pi draft import');assert.equal(await client.evaluate("document.activeElement.id"),'draft-new');assert.equal(await client.evaluate('textDraftEdits.length'),0);assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],beforeImport);
  passed('Pi staging consent creates a pending card; explicit import fills only the existing editor without adding/saving/approving/applying');
  await client.evaluate("$('draft-new').value='OWNER PENDING TEXT';$('draft-new').dispatchEvent(new Event('input'));showInspectorTab('pi')");const reviewsBefore=artifactReviews;acceptDialog=false;await mouseClick('#pi-chat-artifacts button');assert.equal(artifactReviews,reviewsBefore);assert.equal(await client.evaluate("$('draft-new').value"),'OWNER PENDING TEXT');
  acceptDialog=true;artifactReviewFailure=true;await mouseClick('#pi-chat-artifacts button');await until(()=>client.evaluate("$('status').textContent.includes('stale artifact')"),'stale Pi artifact stays readonly');assert.equal(await client.evaluate("$('draft-new').value"),'OWNER PENDING TEXT');assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],beforeImport);artifactReviewFailure=false;
  await client.evaluate("$('draft-new').value='';textDraftActive=false;textDraftEdits=[];textDraftBase=[];textDraftRevision=null;textDraftIdentity=null;textDraftTarget='';renderTextDraft();updateComposer()");
  passed('Pi draft import cancellation preserves unsaved owner text and stale artifacts cannot replace drafts or invoke document APIs');
  chatMode='stream';await client.evaluate("$('pi-chat-input').value='SYNTHETIC clear partial';updateChatControls();$('pi-chat-send').click()");await until(()=>client.evaluate("!$('pi-chat-progress').hidden"),'partial before disconnect');
  await client.evaluate("$('pi-chat-disconnect').click()");await until(()=>client.evaluate("chatState?.status==='disconnected'"),'explicit app chat clearing');assert.equal(await client.evaluate("$('pi-chat-progress').hidden"),true);assert.equal(await client.evaluate("$('pi-chat-progress-text').textContent"),'');assert.equal(await client.evaluate("$('pi-chat-messages').children.length"),0);assert(await client.evaluate("$('pi-chat-send').disabled"));assert.equal(await client.evaluate("$('pi-chat-consent').checked"),false);assert.deepEqual([nativeApprovalCount,applyTextCount,textSaveCount,revision],chatWrites);
  passed('disconnect explicitly clears app memory only, and a new model/session cannot inherit old send consent');
  chatMode='ok';dialogs=0;acceptDialog=false;
  await client.evaluate("$('target').value='';navigate(1);updateComposer()");
  await mouseClick('#inspector-tab-review');
  passed('Pi failures stay literal, do not auto retry and cannot treat missing isolation/exit evidence as authenticated chat');
  assert.match(await client.evaluate("$('review-guide-title').textContent"),/메모/);
  const guideInitialNotes=comments.comments.length;
  await mouseClick('#review-guide-action');assert.equal(await client.evaluate('document.activeElement.id'),'comment-text');assert.equal(comments.comments.length,guideInitialNotes);assert.equal(nativeApprovalCount,0);
  passed('guided next action focuses the right control without saving notes, checking consent or applying documents');

  const scroll = await client.evaluate(`(() => {const y=scrollY;$('thumbnail-list').scrollTop=600;return {pane:$('thumbnail-list').scrollTop,body:scrollY-y};})()`);
  assert(scroll.pane > 0); assert.equal(scroll.body, 0);
  passed('thumbnail scrolling leaves document scroll unchanged');

  await client.evaluate("document.querySelector('.thumbnail-button[data-page=\"10\"]').click()");
  assert.equal(await client.evaluate("$('page-number').value"), '10');
  assert.equal(await client.evaluate("document.querySelector('.thumbnail-button[aria-current=\"page\"]').dataset.page"), '10');
  passed('thumbnail click changes page and active highlight');

  await client.evaluate("$('comment-text').value='UNSAVED SYNTHETIC DRAFT';document.querySelector('.thumbnail-button[data-page=\"11\"]').click()");
  assert.equal(await client.evaluate("$('page-number').value"), '10');
  assert.equal(await client.evaluate("$('comment-text').value"), 'UNSAVED SYNTHETIC DRAFT');
  assert.equal(dialogs, 1);
  await client.evaluate("document.querySelector('.thumbnail-button[data-page=\"10\"]').click()");
  assert.equal(dialogs, 1, 'Same-page click must not discard a draft');
  passed('unsaved draft survives cancelled and same-page navigation');

  acceptDialog = true;
  await client.evaluate("document.querySelector('.thumbnail-button[data-page=\"11\"]').click()");
  assert.equal(await client.evaluate("$('page-number').value"), '11');
  assert.equal(await client.evaluate("$('comment-text').value"), '');
  passed('explicit draft discard allows navigation');

  await client.evaluate("$('comment-text').value='Synthetic browser fixture comment';$('comment-text').dispatchEvent(new Event('input'));$('save-comment').click()");
  assert(await client.evaluate("$('save-comment').disabled && $('comment-text').readOnly"), 'Composer not locked during save');
  await until(() => client.evaluate("$('comment-list').children.length===1 && !busy"), 'fixture comment save');
  assert.match(await client.evaluate("document.querySelector('.thumbnail-button[data-page=\"11\"] .thumbnail-meta').textContent"), /코멘트 1/);
  await client.evaluate("document.querySelector('#comment-list .comment-delete').click()");
  await until(() => client.evaluate("$('comment-list').children.length===0"), 'fixture comment deletion');
  assert.equal(await client.evaluate("document.querySelector('.thumbnail-button[data-page=\"11\"] .thumbnail-meta').textContent"), '');
  passed('comment counts update after fixture save and delete');

  assert.match(await client.evaluate("document.querySelector('.thumbnail-button[data-page=\"4\"] .thumbnail-meta').textContent"), /보호/);
  await client.evaluate("$('page-number').value='139';$('page-number').dispatchEvent(new Event('change'))");
  const last = await client.evaluate(`(() => {const a=document.querySelector('.thumbnail-button.active').getBoundingClientRect(),l=$('thumbnail-list').getBoundingClientRect();return {page:$('page-number').value,visible:a.top>=l.top-1&&a.bottom<=l.bottom+1,scroll:$('thumbnail-list').scrollTop};})()`);
  assert.equal(last.page, '139'); assert(last.visible); assert(last.scroll > 0);
  passed('protected-page label and automatic last-page reveal');

  for (const width of [980, 430]) {
    await client.call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
    assert(await client.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), `Horizontal overflow at ${width}px`);
  }
  passed('tablet and narrow layouts avoid horizontal overflow');

  await client.call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await client.evaluate("navigate(2);$('target').value='fixture-anchor';$('target').dispatchEvent(new Event('change'));$('zoom').value='150';$('zoom').dispatchEvent(new Event('change'))");
  await until(() => client.evaluate("$('page-image').complete && $('render-quality').textContent.includes('144dpi')"), 'high-resolution zoom');
  const zoomed = await client.evaluate("({pane:$('document-viewport').scrollWidth>$('document-viewport').clientWidth,body:document.documentElement.scrollWidth<=innerWidth+1,width:parseFloat($('page-container').style.width)})");
  assert(zoomed.pane && zoomed.body); assert.equal(zoomed.width, 1190);
  await client.evaluate("(() => {const r=$('page-image').getBoundingClientRect();$('page-image').dispatchEvent(new MouseEvent('click',{clientX:r.left+r.width*.15,clientY:r.top+r.height*.15,bubbles:true}));})()");
  const annotation = await client.evaluate('point');
  assert(Math.abs(annotation[0] - .15) < .001 && Math.abs(annotation[1] - .15) < .001);
  assert.equal(await client.evaluate("$('overlay').children.length"), 0);
  await client.evaluate("$('zoom').value='fit';$('zoom').dispatchEvent(new Event('change'))");
  passed('zoom stays inside the viewer and preserves annotation coordinates');

  await client.evaluate("$('page-search').value='20';$('page-search').dispatchEvent(new Event('input'))");
  await until(() => client.evaluate("$('result-count').textContent==='1 / 139페이지' && !$('thumbnail-list').querySelector('[data-page=\"20\"]').hidden"), 'physical page search');
  await client.evaluate("$('page-search').value='slow';$('page-search').dispatchEvent(new Event('input'))");
  await until(() => client.evaluate("$('search-status').textContent==='검색 중…'"), 'slow fixture search');
  await client.evaluate("$('page-search').value='unique';$('page-search').dispatchEvent(new Event('input'))");
  await until(() => client.evaluate("$('search-status').textContent==='PDF 문구' && !document.querySelector('.thumbnail-button[data-page=\"2\"]').hidden"), 'latest search result');
  await new Promise(resolve => setTimeout(resolve, 350));
  assert(await client.evaluate("document.querySelector('.thumbnail-button[data-page=\"20\"]').hidden"));
  assert.match(await client.evaluate("document.querySelector('.thumbnail-button[data-page=\"2\"] .thumbnail-snippet').textContent"), /unique/);
  passed('page and PDF-text search ignore stale responses');

  await client.evaluate("$('clear-search').click();$('page-filter').value='protected';$('page-filter').dispatchEvent(new Event('change'))");
  assert.deepEqual(await client.evaluate("[...document.querySelectorAll('.thumbnail-button')].filter(b=>!b.hidden).map(b=>b.dataset.page)"), ['4']);
  await client.evaluate("$('page-filter').value='comments';$('page-filter').dispatchEvent(new Event('change'))");
  assert(await client.evaluate("!$('list-empty').hidden"));
  await client.evaluate("$('page-filter').value='all';$('page-filter').dispatchEvent(new Event('change'));navigate(11)");
  passed('protected and empty-comment filters show correct empty states');

  await client.evaluate("$('comment-text').value='Editable fixture note';$('comment-text').dispatchEvent(new Event('input'));$('save-comment').click()");
  await until(() => client.evaluate("state.comments.comments.length===1 && !busy"), 'editable fixture save');
  const originalId = await client.evaluate('state.comments.comments[0].id');
  await client.evaluate("document.querySelector('.comment-edit').click();$('comment-text').value='Edited fixture note';$('comment-text').dispatchEvent(new Event('input'));document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,bubbles:true}))");
  await until(() => client.evaluate("state.comments.comments[0].comment==='Edited fixture note' && !busy"), 'fixture edit');
  assert.equal(await client.evaluate('state.comments.comments[0].id'), originalId);
  assert.equal(await client.evaluate('state.comments.comments.length'), 1);
  passed('comment editing and Ctrl+Enter update the existing note without duplication');

  saveFailure = true;
  await client.evaluate("$('comment-text').value='Failed fixture draft';$('comment-text').dispatchEvent(new Event('input'));$('save-comment').click()");
  await until(() => client.evaluate("$('status').classList.contains('error') && !busy"), 'save failure');
  assert.equal(await client.evaluate("$('comment-text').value"), 'Failed fixture draft');
  assert.equal(await client.evaluate('state.comments.comments.length'), 1);
  saveFailure = false;
  await client.evaluate("$('save-comment').click()");
  await until(() => client.evaluate('state.comments.comments.length===2 && !busy'), 'explicit fixture retry');
  passed('failed saves retain the draft and retry only on an explicit click');

  revision++;
  comments.comments.push({ id: 'other-window', page: 20, anchor_id: null, region: null, comment: 'Concurrent fixture note' });
  await client.evaluate("$('comment-text').value='Concurrent fixture draft';$('comment-text').dispatchEvent(new Event('input'));$('save-comment').click()");
  await until(() => client.evaluate("$('status').classList.contains('error') && !busy"), 'stale revision conflict');
  assert.equal(await client.evaluate("$('comment-text').value"), 'Concurrent fixture draft');
  assert(comments.comments.some(c => c.id === 'other-window'));
  await client.evaluate("$('reload').click()");
  await until(() => client.evaluate("state.comments.comments.some(c=>c.id==='other-window') && !busy"), 'read latest revision');
  assert.equal(await client.evaluate("$('comment-text').value"), 'Concurrent fixture draft');
  await client.evaluate("$('save-comment').click()");
  await until(() => client.evaluate('state.comments.comments.length===4 && !busy'), 'save merged fixture list');
  passed('stale writes are blocked and refresh preserves the draft plus newer notes');

  await client.evaluate("$('page-filter').value='comments';$('page-filter').dispatchEvent(new Event('change'))");
  assert.deepEqual(await client.evaluate("[...document.querySelectorAll('.thumbnail-button')].filter(b=>!b.hidden).map(b=>b.dataset.page)"), ['11', '20']);
  await client.evaluate("$('page-filter').value='all';$('page-filter').dispatchEvent(new Event('change'))");
  passed('comment-page filter tracks newly saved notes');

  await client.evaluate("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}))");
  assert.equal(await client.evaluate("$('page-number').value"), '12');
  await client.evaluate("$('comment-text').value='Typing arrows';$('comment-text').dispatchEvent(new Event('input'));$('comment-text').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}))");
  assert.equal(await client.evaluate("$('page-number').value"), '12');
  passed('arrow navigation never intercepts typing inside the editor');

  await client.call('Network.enable');
  await client.call('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await client.evaluate("$('comment-text').value='Offline retained draft';$('comment-text').dispatchEvent(new Event('input'));load()");
  await until(() => client.evaluate("$('connection').classList.contains('offline') && !busy"), 'offline state');
  assert.equal(await client.evaluate("$('comment-text').value"), 'Offline retained draft');
  await client.call('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await client.evaluate("$('reconnect').click()");
  await until(() => client.evaluate("$('connection').classList.contains('online') && !busy"), 'reconnect');
  assert.equal(await client.evaluate("$('comment-text').value"), 'Offline retained draft');
  await client.evaluate("$('comment-text').value='';$('comment-text').dispatchEvent(new Event('input'))");
  passed('offline errors and reconnection preserve unsaved text');

  await client.evaluate("navigate(2)");
  await until(() => client.evaluate("$('page-image').complete"), 'note page image');
  const move = "(() => {const r=$('page-image').getBoundingClientRect();$('page-image').dispatchEvent(new PointerEvent('pointermove',{clientX:r.left+r.width*120/595,clientY:r.top+r.height*58/842}));})()";
  await client.evaluate(move);
  assert.equal(await client.evaluate("document.querySelectorAll('.anchor-hover').length"), 1);
  await client.evaluate("$('page-image').dispatchEvent(new PointerEvent('pointerleave'))");
  assert.equal(await client.evaluate("$('overlay').children.length"), 0);
  await client.evaluate(move);
  await client.evaluate("(() => {const r=$('page-image').getBoundingClientRect();$('page-image').dispatchEvent(new MouseEvent('click',{clientX:r.left+r.width*120/595,clientY:r.top+r.height*58/842}));})()");
  assert.equal(await client.evaluate("$('target').value"), 'fixture-anchor');
  await client.evaluate("$('page-image').dispatchEvent(new PointerEvent('pointerleave'))");
  assert.equal(await client.evaluate("document.querySelectorAll('.anchor-selected').length"), 1);
  passed('hover boxes disappear naturally while clicked selections stay pinned');

  await client.evaluate("$('comment-text').value='First location memo';$('comment-text').dispatchEvent(new Event('input'));$('save-comment').click()");
  await until(() => client.evaluate("state.comments.comments.filter(c=>c.page===2).length===1 && !busy"), 'first point memo');
  await client.evaluate("(() => {const r=$('page-image').getBoundingClientRect();$('page-image').dispatchEvent(new MouseEvent('click',{clientX:r.left+r.width*.65,clientY:r.top+r.height*.7}));})()");
  assert.equal(await client.evaluate('editingId'), null);
  await client.evaluate("$('comment-text').value='Second location memo';$('comment-text').dispatchEvent(new Event('input'));$('save-comment').click()");
  await until(() => client.evaluate("state.comments.comments.filter(c=>c.page===2).length===2 && !busy"), 'second point memo');
  const memos = await client.evaluate('state.comments.comments.filter(c=>c.page===2)');
  assert.notEqual(memos[0].id, memos[1].id); assert.notDeepEqual(memos[0].region, memos[1].region);
  assert.equal(await client.evaluate("document.querySelectorAll('.note-pin').length"), 2);
  await client.evaluate("document.querySelector('.note-pin').click()");
  assert.equal(await client.evaluate("document.querySelectorAll('.comment.focused').length"), 1);
  assert.equal(await client.evaluate('editingId'), null, 'Pin focus must not turn a new memo into an overwrite');
  await client.evaluate("$('new-note').click()");
  assert.equal(await client.evaluate("$('comment-text').value"), '');
  assert.equal(await client.evaluate('state.comments.comments.filter(c=>c.page===2).length'), 2);
  passed('multiple positioned notes persist with separate markers and connected cards');

  assert(await client.evaluate("$('mode-split').disabled"), 'Comparison without an applied result must not be fabricated');
  versions = [{ id: '20261008T120000-01234567', status: 'checks_passed_needs_human_visual_review', artifact_integrity:'verified', before_pages: 139, after_pages: 139, changed_pages: [2], page_count_equal: true }];
  await client.evaluate("load()");
  await client.evaluate("$('view-mode').value='split';$('view-mode').dispatchEvent(new Event('change'))");
  await until(() => client.evaluate("document.querySelectorAll('.compare-sheet img').length===2 && [...document.querySelectorAll('.compare-sheet img')].every(i=>i.complete&&i.naturalWidth>0)"), 'side by side fixture render');
  assert(await client.evaluate("$('page-container').hidden && $('comment-text').readOnly && $('new-note').disabled"));
  await client.evaluate("$('view-mode').value='diff';$('view-mode').dispatchEvent(new Event('change'))");
  await until(() => client.evaluate("document.querySelectorAll('.compare-sheet img').length===1 && document.querySelector('.compare-sheet img').complete"), 'pixel diff fixture render');
  assert.match(await client.evaluate("document.querySelector('.compare-sheet figcaption').textContent"), /붉게/);
  versions[0].status = 'blocked'; versions[0].after_pages = 140; versions[0].page_count_equal = false; versions[0].changed_pages = [2, 140];
  await client.evaluate('load()');
  assert.match(await client.evaluate("$('comparison-status').textContent"), /제출 금지/);
  await client.evaluate("$('next-change').click()");
  assert.equal(await client.evaluate("$('page-number').value"), '140');
  await client.evaluate("$('view-mode').value='original';$('view-mode').dispatchEvent(new Event('change'))");
  assert.equal(await client.evaluate("$('page-number').value"), '139');
  assert.equal(await client.evaluate('state.comments.comments.filter(c=>c.page===2).length'), 2);
  passed('applied-version split/diff is read-only and blocked pagination stays visibly blocked');

  assert(await client.evaluate("$('download-hwp').disabled&&$('download-pdf').disabled"));assert.equal(downloadCalls,0);
  versions[0]={...versions[0],status:'checks_passed_needs_human_visual_review',artifact_integrity:'verified',after_pages:139,page_count_equal:true,changed_pages:[2],downloads:downloadInfo};await client.evaluate('load()');
  const approvedBeforeDownload=nativeApprovalCount,commentsBeforeDownload=JSON.stringify(comments);
  await client.evaluate("$('comment-text').value='UNSAVED DOWNLOAD-TIME MEMO';$('comment-text').dispatchEvent(new Event('input'))");
  await mouseClick('#download-hwp');
  const hwpName=`검토용-${versions[0].id}.hwp`;
  await until(async()=>{try{return (await readFile(join(downloadFolder,hwpName))).equals(downloadBytes.hwp)}catch{return false}},'actual hash-verified HWP blob download');
  await until(()=>client.evaluate('!busy'),'HWP download complete');
  assert.equal(nativeApprovalCount,approvedBeforeDownload);assert.equal(JSON.stringify(comments),commentsBeforeDownload);
  assert.equal(await client.evaluate("$('comment-text').value"),'UNSAVED DOWNLOAD-TIME MEMO');assert.match(await client.evaluate("$('result-download-status').textContent"),/최종 제출 승인이 아니/);
  await mouseClick('#download-pdf');const pdfName=`검토용-${versions[0].id}.pdf`;
  await until(async()=>{try{return (await readFile(join(downloadFolder,pdfName))).equals(downloadBytes.pdf)}catch{return false}},'actual reopened PDF blob download');
  await until(()=>client.evaluate('!busy'),'PDF download complete');assert.equal(downloadCalls,2);
  passed('verified HWP/PDF downloads check exact bytes, preserve unsaved notes and never approve/apply');
  await rm(join(downloadFolder,hwpName));downloadMode='corrupt';await mouseClick('#download-hwp');
  await until(async()=>downloadCalls===3&&await client.evaluate('!busy'),'corrupt result blocked');
  assert.match(await client.evaluate("$('result-download-status').textContent"),/해시/);
  await assert.rejects(readFile(join(downloadFolder,hwpName)),{code:'ENOENT'});
  passed('same-size corrupt bytes cannot become a saved HWP even with a matching header');
  downloadMode='error';await mouseClick('#download-hwp');await until(async()=>downloadCalls===4&&await client.evaluate('!busy'),'server download failure');
  assert.match(await client.evaluate("$('result-download-status').textContent"),/자동 재시도하지 않습니다/);
  await new Promise(resolve=>setTimeout(resolve,150));assert.equal(downloadCalls,4);
  passed('server download failure is visible and read-only, never automatically retries');
  downloadMode='slow';await mouseClick('#download-hwp');await until(()=>client.evaluate("busy==='download-result'"),'slow snapshot in flight');
  assert(await client.evaluate("$('result-version').disabled&&$('download-pdf').disabled"));
  await client.evaluate("resultVersions=[];updateDownloads()");await until(()=>client.evaluate('!busy'),'changed selection blocks save');
  await assert.rejects(readFile(join(downloadFolder,hwpName)),{code:'ENOENT'});
  passed('result selection/metadata drift during fetch blocks saving the wrong version');
  downloadMode='ok';versions[0].artifact_integrity='unbound_legacy';await client.evaluate('load()');
  assert(await client.evaluate("$('download-hwp').disabled&&$('download-pdf').disabled"));
  versions[0].artifact_integrity='verified';versions[0].status='blocked';await client.evaluate('load()');
  assert(await client.evaluate("$('download-hwp').disabled&&$('download-pdf').disabled"));
  passed('failed and historical unbound results never offer download controls');
  versions[0].status='checks_passed_needs_human_visual_review';await client.evaluate('load()');await capture('verified-result-download');
  assert.equal(nativeApprovalCount,approvedBeforeDownload);assert.equal(JSON.stringify(comments),commentsBeforeDownload);
  await client.evaluate("$('comment-text').value='';resetComposer()");

  nativeProposal = { schema: 2, workspace_id: manifest.workspace_id, edit_mode: 'native-paragraph-v1',
    native_policy: 'selected-lines-caret-modified-date-preview-v1', edits: [{ anchor_id: 'fixture-anchor',
      old_text: 'Synthetic fixture phrase', new_text: 'Short fixture', region: [50, 50, 180, 65] }] };
  await client.evaluate("$('proposal-kind').value='native';$('proposal-kind').dispatchEvent(new Event('change'))");
  await until(() => client.evaluate("state.proposal?.schema===2 && !busy"), 'native contract review');
  assert(await client.evaluate("!$('native-policy').hidden && $('approve').disabled"));
  await client.evaluate("document.querySelector('.diff-head button').click()");
  assert.equal(await client.evaluate("document.querySelectorAll('.native-region').length"), 1);
  await client.evaluate("$('native-policy-confirm').click()");
  assert(await client.evaluate("$('approve').disabled"), 'Policy alone must not approve the region');
  await client.evaluate("document.querySelector('.native-region-ack').click()");
  assert.equal(await client.evaluate("$('approve').disabled"), false);
  await client.evaluate("document.querySelector('.native-region-ack').click()");
  assert(await client.evaluate("$('approve').disabled"), 'Unreviewed region must block native approval');
  await client.evaluate("document.querySelector('.native-region-ack').click()");
  acceptDialog = true;
  await client.evaluate("$('approve').click()");
  await until(() => client.evaluate("approvedHash===state.proposal_sha256 && !busy"), 'synthetic native approval');
  assert.equal(nativeApprovalCount, 1);assert(await client.evaluate("$('approve').disabled"));
  assert(await client.evaluate("(()=>{const kind=state.proposal_kind,hash=approvedHash;state.proposal_kind='text';approvedHash=null;updateComposer();const blocked=$('approve').disabled;state.proposal_kind=kind;approvedHash=hash;updateComposer();return blocked})()"));
  passed('native orange region and explicit policy/region checks gate approval in synthetic UI only');

  await client.evaluate('load()');
  assert(await client.evaluate("nativeReviewed.size===1 && $('native-policy-confirm').checked && $('native-check-all').checked && !$('native-check-all').disabled"));
  passed('server-verified exact existing approval restores checks without any new approval POST');

  await client.call('Page.navigate',{url:fixtureUrl+'/?proposal=native&panel=summary'});
  await until(()=>client.evaluate(`(${REVIEW_READY}) && $('proposal-review-dialog').open && approvedHash===state.proposal_sha256`),'fresh already-approved review');
  assert(await client.evaluate("nativeReviewed.size===1 && $('summary-check-all').checked && !$('summary-check-all').disabled"));
  await mouseClick('#proposal-review-rows .review-check');
  assert(await client.evaluate("nativeReviewed.size===0 && !$('summary-check-all').checked && $('summary-approve').disabled"));
  await mouseClick('#summary-check-all');
  assert(await client.evaluate("nativeReviewed.size===1 && $('native-check-all').checked"));
  await client.call('Input.dispatchKeyEvent',{type:'keyDown',key:' ',code:'Space',windowsVirtualKeyCode:32});
  await client.call('Input.dispatchKeyEvent',{type:'keyUp',key:' ',code:'Space',windowsVirtualKeyCode:32});
  assert(await client.evaluate('nativeReviewed.size===0'));
  assert.equal(nativeApprovalCount,1);assert.equal(nativeApprovedHash,nativeHash);
  passed('already-approved individual and bulk checks work with real mouse and keyboard without revoking or reposting consent');

  await mouseClick('#summary-policy-confirm');
  assert(await client.evaluate("!$('native-policy-confirm').checked"));
  await client.evaluate('load()');
  assert(await client.evaluate("nativeReviewed.size===0 && !$('summary-policy-confirm').checked && approvedHash===state.proposal_sha256"));
  assert.match(await client.evaluate("$('review-receipt').textContent"),/기존 승인 기록은 유지/);
  assert.equal(nativeApprovalCount,1);
  passed('same-plan refresh preserves local re-review marks separately from the immutable server approval');

  await client.evaluate("setBusy('save')");
  assert(await client.evaluate("$('summary-check-all').disabled && document.querySelector('#proposal-review-rows .native-region-ack').disabled && $('summary-policy-confirm').disabled"));
  await client.evaluate('toggleAllNative(true)');assert.equal(await client.evaluate('nativeReviewed.size'),0);
  await client.evaluate("setBusy('')");
  assert(await client.evaluate("!$('summary-check-all').disabled"));
  await client.evaluate("$('close-review-wide').click()");
  passed('only busy or mismatched contracts lock review controls; prior approval alone never does');

  manifest.anchors.push({anchor_id:'fixture-anchor-2',page_hint:3,old_text:'Second fixture phrase',boxes:[[50,100,180,115]],cell:null,char_style:'1'});
  nativeProposal.edits.push({anchor_id:'fixture-anchor-2',old_text:'Second fixture phrase',new_text:'Second short fixture',region:[50,100,180,115]});
  nativeHash='c'.repeat(64);nativeApprovedHash=null;
  latestAttempt={id:'20261009T144602-cafe1234',status:'blocked',plan_sha256:nativeHash,error:'<img src=x onerror=alert(1)> table height changed',source_unchanged:true};
  await client.evaluate('load()');
  assert(await client.evaluate("nativeReviewed.size===0 && !$('native-policy-confirm').checked && !$('native-check-all').checked && $('approve').disabled"));
  assert.match(await client.evaluate("$('apply-status').textContent"), /검증 차단/);
  assert.equal(await client.evaluate("$('apply-status').querySelectorAll('img').length"),0);
  passed('new proposal hash resets all confirmation and blocked incomplete attempts stay text-only and visible');

  await client.evaluate("$('review-wide').click()");
  assert(await client.evaluate("$('proposal-review-dialog').open && document.querySelectorAll('.review-page-group').length===2 && document.querySelectorAll('.slot-preview').length===2"));
  const pageBefore=await client.evaluate('page');
  await client.evaluate("document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}))");
  assert.equal(await client.evaluate('page'),pageBefore);
  await client.evaluate("$('summary-check-all').click()");
  assert(await client.evaluate("nativeReviewed.size===2 && $('native-check-all').checked && $('summary-approve').disabled"));
  assert.equal(nativeApprovalCount,1,'Bulk check must not approve automatically');
  await client.evaluate("document.querySelector('#proposal-review-rows .native-region-ack').click()");
  assert(await client.evaluate("nativeReviewed.size===1 && $('native-check-all').indeterminate && $('summary-check-all').indeterminate"));
  await client.evaluate("$('summary-check-all').click()");
  assert.equal(await client.evaluate("[...document.querySelectorAll('.native-region-ack')].filter(c=>c.checked).length"),4);
  passed('wide page-grouped before/after table and crop previews support one-click bulk checks and partial-state sync');

  await client.call('Emulation.setDeviceMetricsOverride',{width:375,height:800,deviceScaleFactor:1,mobile:false});
  assert(await client.evaluate("$('proposal-review-dialog').getBoundingClientRect().width<=window.innerWidth && document.documentElement.scrollWidth<=window.innerWidth+1"));
  await client.evaluate("$('summary-policy-confirm').click()");
  assert(await client.evaluate("$('native-policy-confirm').checked && !$('summary-approve').disabled"));
  acceptDialog=false;await client.evaluate("$('summary-approve').click()");
  await until(()=>client.evaluate('!busy'),'cancel summary approval');
  assert.equal(nativeApprovalCount,1);
  await client.evaluate("$('summary-check-all').click()");
  assert(await client.evaluate("nativeReviewed.size===0 && $('summary-approve').disabled"));
  await client.evaluate("$('summary-check-all').click()");
  acceptDialog=true;await client.evaluate("$('summary-approve').click()");
  await until(()=>client.evaluate("approvedHash===state.proposal_sha256 && !busy"),'explicit summary fixture approval');
  assert.equal(nativeApprovalCount,2);
  passed('narrow summary stays scroll-contained and bulk check still requires policy plus separate cancellable final approval');
  await client.evaluate("$('close-review-wide').click()");

  nativeProposal={...nativeProposal,schema:3,edit_mode:'native-original-row-minima-v1',native_policy:'selected-lines-original-row-minima-generated-preview-v1',row_minima:[{part:'Contents/section0.xml',table_id:'fixture-table',row_heights:[1182,2622,2622]}]};
  nativeHash='d'.repeat(64);latestAttempt=null;
  await client.evaluate("$('proposal-kind').value='layout';$('proposal-kind').dispatchEvent(new Event('change'))");
  await until(()=>client.evaluate("!busy && state.proposal?.schema===3"),'fixed-row contract review');
  assert(await client.evaluate("nativeReviewed.size===0 && !$('native-policy-confirm').checked && $('approve').disabled"));
  await client.evaluate("$('review-wide').click()");
  assert.match(await client.evaluate("$('summary-policy-detail').textContent"),/64\.26pt/);
  assert.match(await client.evaluate("$('summary-policy-detail').textContent"),/PNG/);
  await mouseClick('#summary-check-all');assert(await client.evaluate("$('summary-approve').disabled"));
  assert.equal(nativeApprovalCount,2);
  await mouseClick('#summary-policy-confirm');assert(await client.evaluate("!$('summary-approve').disabled"));
  acceptDialog=false;await client.evaluate("$('summary-approve').click()");assert.equal(nativeApprovalCount,2);
  acceptDialog=true;await client.evaluate("$('summary-approve').click()");
  await until(()=>client.evaluate("!busy && approvedHash===state.proposal_sha256"),'fresh fixed-row fixture approval');
  assert.equal(nativeApprovalCount,3);
  await client.evaluate("state.latest_attempt={status:'checks_passed_needs_human_visual_review',artifact_integrity:'verified'};updateComposer()");
  assert.match(await client.evaluate("$('summary-approve').textContent"),/적용·검증 통과/);
  assert.match(await client.evaluate("$('approve').textContent"),/육안 검토 필요/);
  assert.equal(nativeApprovalCount,3,'Showing success must not submit approval again');
  passed('successful apply is distinguished from prior approval and still requires human visual review');
  passed('fixed original rows and generated thumbnail require a separately visible policy plus fresh exact UI consent');
  await client.evaluate("$('close-review-wide').click()");

  const layoutAvailabilitySaved=await client.evaluate('JSON.stringify({job:state.contract_apply??null,scope:state.approval_scope_sha256??null,attempt:state.latest_attempt,confirmed:reviewSourceConfirmed,mode:viewMode})');
  const unavailableCounts=[nativeApprovalCount,applyContractCount,applyTextCount,textSaveCount,revision];
  await client.evaluate("state.latest_attempt=null;state.approval_scope_sha256='a'.repeat(64);state.contract_apply={enabled:true,available:true,supported_kinds:['layout'],active:false,status:'idle',plan_sha256:null};viewMode='original';reviewSourceConfirmed=true;updateComposer()");
  assert(await client.evaluate("contractApplyReady()&&!$('apply-contract').disabled"),'Available positive control must satisfy every other apply condition');
  const unavailableReason='이 양식의 읽기 순서 보존이 검증되지 않아 Native 표 적용을 사용할 수 없습니다. 읽기 전용 검토는 가능합니다. <img src=x onerror=window.syntheticLayoutInjection=true>';
  await client.evaluate(`state.contract_apply.available=false;state.contract_apply.reason=${JSON.stringify(unavailableReason)};updateComposer()`);
  assert(await client.evaluate("!$('contract-apply-panel').hidden&&$('apply-contract').disabled&&!contractApplyReady()"));
  assert.equal(await client.evaluate("$('contract-apply-status').textContent"),unavailableReason);
  assert(await client.evaluate("!$('contract-apply-status').querySelector('img')&&window.syntheticLayoutInjection===undefined&&!$('review-wide').disabled"));
  await client.evaluate("$('review-wide').click()");assert(await client.evaluate("$('proposal-review-dialog').open&&$('proposal-review-rows').querySelectorAll('tr:not(.review-page-group)').length===state.proposal.edits.length"));
  await client.evaluate("$('close-review-wide').click();$('apply-contract').disabled=false;$('apply-contract').click();updateComposer()");
  await until(()=>client.evaluate('!busy'),'unavailable Native handler remains idle');
  assert(await client.evaluate("$('apply-contract').disabled&&!contractApplyReady()"));
  assert.deepEqual([nativeApprovalCount,applyContractCount,applyTextCount,textSaveCount,revision],unavailableCounts);
  assert.equal(applyContractCount,0,'Unavailable profile must never post Native apply, even if the DOM button is enabled');
  await client.evaluate(`{const saved=JSON.parse(${JSON.stringify(layoutAvailabilitySaved)});state.contract_apply=saved.job;state.approval_scope_sha256=saved.scope;state.latest_attempt=saved.attempt;reviewSourceConfirmed=saved.confirmed;viewMode=saved.mode;updateComposer()}`);
  passed('unsupported reading-order profile is inert and unavailable while read-only proposal review stays usable without consent or apply');

  await client.evaluate("state.latest_attempt={status:'checks_passed_needs_human_visual_review',artifact_integrity:'unbound_legacy'};showLatestAttempt();updateComposer();updateVersions([{id:'20261011T000000-a1b2c3d4',status:'checks_passed_needs_human_visual_review',artifact_integrity:'unbound_legacy',before_pages:139,after_pages:139,changed_pages:[2],page_count_equal:true}])");
  assert.match(await client.evaluate("$('apply-status').textContent"),/무결성 미확인/);assert.match(await client.evaluate("$('comparison-status').textContent"),/무결성 미확인/);assert.match(await client.evaluate("$('summary-approve').textContent"),/과거 검증 기록/);
  assert(!/자동 검사 통과/.test(await client.evaluate("$('apply-status').textContent")));assert.equal(nativeApprovalCount,3);
  passed('unbound historical runs remain read-only, explicit and never look like new integrity-confirmed passes');
  assert.match(await client.evaluate("$('review-guide-title').textContent"),/과거/);assert.equal(nativeApprovalCount,3);
  passed('guide distinguishes existing approval, unverified history and result review without reposting approval');
  await client.evaluate("state.latest_attempt={status:'blocked',artifact_integrity:'mismatch',error:'Result artifact integrity is not confirmed; preview blocked'};updateVersions([]);showLatestAttempt();updateComposer();applyZoom()");
  assert.equal(await client.evaluate("$('compare-container').hidden"),true);assert.match(await client.evaluate("$('apply-status').textContent"),/preview blocked/);assert(!/자동 검사 통과/.test(await client.evaluate("$('apply-status').textContent")));assert.equal(nativeApprovalCount,3);
  passed('changed result artifacts remove previews and cannot reuse a cached successful label or consent');
  latestAttempt={id:'20261011T000000-a1b2c3d4',status:'in_progress',plan_sha256:nativeHash,error:'',source_unchanged:false};applyState={locked:true,record_valid:true,run_id:latestAttempt.id};
  await client.evaluate('load()');await until(()=>client.evaluate('!busy'),'in-progress mocked apply');
  assert.match(await client.evaluate("$('apply-status').textContent"),/아직 검증 통과 아님/);assert.match(await client.evaluate("$('summary-apply-status').textContent"),/소유자 확인/);
  assert(!/자동 검사 통과/.test(await client.evaluate("$('apply-status').textContent")));
  passed('in-progress or abruptly stopped apply is visible and never mislabeled as successful verification');
  await client.evaluate("approvedHash=null;updateComposer()");assert(await client.evaluate("$('approve').disabled && $('summary-approve').disabled"));
  await mouseClick('#approve');assert.equal(nativeApprovalCount,3);
  applyState={locked:true,record_valid:false};await client.evaluate('load()');await until(()=>client.evaluate('!busy'),'invalid lock mocked apply');
  assert(await client.evaluate("$('approve').disabled"));assert.match(await client.evaluate("$('apply-status').textContent"),/자동 해제·재시도하지 않습니다/);
  assert.equal(nativeApprovalCount,3);
  passed('active or invalid apply locks prevent new UI consent without clearing locks or retrying');
  applyState={locked:false};latestAttempt=null;versions=[];acceptDialog=true;
  await client.evaluate("viewMode='original';$('proposal-kind').value='text';$('proposal-kind').dispatchEvent(new Event('change'))");
  await until(()=>client.evaluate("!busy&&proposalKind==='text'"),'manual text composer state');
  await client.evaluate("beginTextDraft('fixture-anchor')");
  assert(await client.evaluate("$('text-draft').open&&$('draft-old').textContent==='Synthetic fixture phrase'&&$('draft-new').value===''"));
  assert.equal(textSaveCount,0);assert.equal(nativeApprovalCount,3);
  passed('manual proposal selects source text without copying comment instructions or executing/approving');
  await client.evaluate("$('draft-new').value='SYNTHETIC <img src=x onerror=alert(1)>'; $('draft-new').dispatchEvent(new Event('input'));$('draft-add').click()");
  assert(await client.evaluate("textDraftEdits.length===1&&textDraftDirty()&&$('approve').disabled&&!$('draft-save').disabled&&$('text-draft-list').querySelectorAll('img').length===0"));
  assert(await client.evaluate("document.documentElement.scrollWidth<=innerWidth+1"));
  await client.evaluate("focusReviewControl($('text-draft'))");await capture('manual-text-draft');
  passed('local replacement draft is text-only, narrow-screen safe, and locks old-plan approval');
  acceptDialog=false;await client.evaluate("$('draft-save').click()");assert.equal(textSaveCount,0);
  assert(await client.evaluate('textDraftDirty()'));acceptDialog=true;
  await client.evaluate("$('draft-save').click()");await until(()=>client.evaluate('!busy'),'manual proposal save');
  assert.equal(textSaveCount,1);assert.equal(nativeApprovalCount,3);
  assert(await client.evaluate("!textDraftDirty()&&state.proposal.schema===1&&state.proposal.edits[0].new_text.startsWith('SYNTHETIC')"));
  passed('manual whole-plan publication needs a cancellable explicit click and never approves/applies');
  await client.evaluate("$('draft-new').value='NEW SYNTHETIC PHRASE';$('draft-new').dispatchEvent(new Event('input'));$('draft-add').click()");
  textSaveFailure=true;await client.evaluate("$('draft-save').click()");await until(()=>client.evaluate('!busy'),'failed manual save');
  assert.equal(textSaveCount,1);assert(await client.evaluate("textDraftEdits[0].new_text==='NEW SYNTHETIC PHRASE'&&$('draft-save').disabled"));
  assert.match(await client.evaluate("$('status').textContent"),/초안 유지/);textSaveFailure=false;
  await client.evaluate('load()');assert(await client.evaluate("!$('draft-save').disabled"));
  await client.evaluate("$('draft-save').click()");await until(()=>client.evaluate('!busy'),'explicit manual retry');assert.equal(textSaveCount,2);
  passed('failed strict text proposal retains draft, never escalates policy, and retries only after fresh read plus click');
  await client.evaluate("$('draft-new').value='MY LOCAL CONFLICT TEXT';$('draft-new').dispatchEvent(new Event('input'));$('draft-add').click()");
  textRevision='EXTERNAL-REVISION';textProposal={...textProposal,edits:[{...textProposal.edits[0],new_text:'OTHER WINDOW TEXT'}]};
  await client.evaluate("$('draft-save').click()");await until(()=>client.evaluate('!busy'),'manual conflict');
  assert.equal(textSaveCount,2);await client.evaluate('load()');
  assert(await client.evaluate("textDraftEdits[0].new_text==='MY LOCAL CONFLICT TEXT'&&state.proposal.edits[0].new_text==='OTHER WINDOW TEXT'&&!$('draft-rebase').hidden&&$('draft-save').disabled&&$('approve').disabled"));
  acceptDialog=false;await client.evaluate("$('draft-rebase').click()");assert(await client.evaluate("$('draft-save').disabled"));
  acceptDialog=true;await client.evaluate("$('draft-rebase').click()");assert(await client.evaluate("!$('draft-save').disabled"));
  assert.equal(textSaveCount,2);assert.equal(nativeApprovalCount,3);
  passed('proposal conflict preserves both versions; rebase acknowledgment is not automatic save or approval');
  await client.evaluate("$('draft-save').click()");await until(()=>client.evaluate('!busy'),'manual conflict replacement');assert.equal(textSaveCount,3);
  await client.evaluate("$('draft-new').value='PENDING TEXT';$('draft-new').dispatchEvent(new Event('input'));beginTextDraft('fixture-anchor')");
  assert.equal(await client.evaluate("$('draft-new').value"),'PENDING TEXT');
  await client.evaluate("$('draft-new').value='';$('draft-new').dispatchEvent(new Event('input'));$('draft-discard').click()");
  assert.equal(textSaveCount,3);assert.equal(nativeApprovalCount,3);
  passed('returning to same note anchor never silently drops unadded replacement text');
  manifest.baseline_hwpx_sha256='9'.repeat(64);await client.evaluate('load()');
  assert(await client.evaluate("!textDraftBound()&&$('draft-save').disabled&&$('draft-add').disabled&&!$('draft-discard').disabled&&$('approve').disabled"));
  assert.match(await client.evaluate("$('text-draft-status').textContent"),/자동 연결하지 않습니다/);
  manifest.baseline_hwpx_sha256='f'.repeat(64);await client.evaluate('load()');
  assert(await client.evaluate('textDraftBound()'));assert.equal(textSaveCount,3);
  passed('local proposals bind the original workspace/baseline and never silently rebase to a changed source');
  applyState={locked:true};await client.evaluate('load()');assert(await client.evaluate("$('draft-add').disabled&&$('draft-save').disabled&&$('draft-new').readOnly"));applyState={locked:false};await client.evaluate('load()');
  await client.evaluate("$('proposal-kind').value='native';$('proposal-kind').dispatchEvent(new Event('change'))");await until(()=>client.evaluate('!busy'),'native hides text composer');
  assert(await client.evaluate("$('text-draft').hidden"));assert.equal(textSaveCount,3);assert.equal(nativeApprovalCount,3);
  passed('apply locks and wider Native modes cannot use the strict text composer');
  await client.evaluate("proposalKind='text';$('proposal-kind').value='text';resetComposer();viewMode='original';load()");await until(()=>client.evaluate('!busy'),'text apply source');
  textApply={...textApply,enabled:true,available:true};await client.evaluate('load()');
  assert(await client.evaluate("!$('text-apply-panel').hidden&&$('apply-text').disabled"));assert.equal(applyTextCount,0);
  textApprovedHash=textHash;await client.evaluate('load()');assert(await client.evaluate("!$('apply-text').disabled&&!$('summary-apply-text').disabled"));
  await client.evaluate("if($('proposal-review-dialog').open)$('proposal-review-dialog').close()");
  // Invoke the actual fixture DOM click (not the handler directly). Physical
  // pointer behavior is already covered by the initial guide/Native controls.
  await client.evaluate("$('review-guide-action').click()");
  await until(()=>client.evaluate("document.activeElement.id==='apply-text'"),'guide focuses actual text apply control');
  assert.equal(await client.evaluate('document.activeElement.id'),'apply-text');assert.equal(applyTextCount,0);
  acceptDialog=false;await mouseClick('#apply-text');assert.equal(applyTextCount,0);
  passed('bounded text apply needs existing exact approval and separate cancellable confirmation; guide only focuses');
  chatStatus='ready';chatReadTools=true;chatProposalTools=true;chatArtifacts=[{artifact_id:'a-'+'2'.repeat(32),turn_id:'t-'+'3'.repeat(16),artifact:{kind:'approved_job_request',job:{workspace_id:manifest.workspace_id,baseline_hwpx_sha256:manifest.baseline_hwpx_sha256,proposal_kind:'text',proposal_revision:textRevision,plan_sha256:textHash,approval_scope_sha256:null,requires_separate_apply_confirmation:true}}}];await client.evaluate('refreshChat()');await until(()=>client.evaluate('!chatBusy&&chatState.artifacts.length===1'),'restore own pending job request card');
  const beforeJobCard=[applyTextCount,nativeApprovalCount,textSaveCount];await mouseClick('#inspector-tab-pi');await mouseClick('#pi-chat-artifacts button');await until(()=>client.evaluate("document.activeElement.id==='apply-text'"),'job card focuses original separate apply');assert.deepEqual([applyTextCount,nativeApprovalCount,textSaveCount],beforeJobCard);
  chatArtifacts[0].artifact.job.plan_sha256='0'.repeat(64);await client.evaluate('refreshChat()');await until(()=>client.evaluate('!chatBusy'),'stale job card metadata');await mouseClick('#inspector-tab-pi');await mouseClick('#pi-chat-artifacts button');await until(()=>client.evaluate("$('status').textContent.includes('바뀌었습니다')"),'stale job card cannot focus as exact apply');assert.deepEqual([applyTextCount,nativeApprovalCount,textSaveCount],beforeJobCard);
  chatArtifacts=[];chatStatus='disconnected';chatReadTools=false;chatProposalTools=false;await client.evaluate('refreshChat()');await until(()=>client.evaluate('!chatBusy'),'clear synthetic job-card session');await mouseClick('#inspector-tab-review');
  passed('approved-job request cards recheck identity and only focus existing apply confirmation; stale cards never approve or start HWP');
  await client.evaluate("$('comment-text').value='UNSAVED MEMO';updateComposer()");assert(await client.evaluate("$('apply-text').disabled"));await client.evaluate('resetComposer()');
  await client.evaluate("beginTextDraft('fixture-anchor');$('draft-new').value='UNSAVED REPLACEMENT';$('draft-new').dispatchEvent(new Event('input'))");assert(await client.evaluate("$('apply-text').disabled"));
  acceptDialog=true;await client.evaluate("$('draft-new').value='';$('draft-new').dispatchEvent(new Event('input'));$('draft-discard').click()");
  passed('unsaved memo or exact replacement draft blocks applying an older server plan');
  await mouseClick('#apply-text');await until(async()=>applyTextCount===1&&await client.evaluate('!busy'),'accepted text apply');
  assert.equal(applyTextCount,1);assert.equal(nativeApprovalCount,3);
  assert(await client.evaluate("$('apply-text').disabled&&$('approve').disabled&&$('draft-save').disabled"));
  assert.match(await client.evaluate("$('text-apply-status').textContent"),/진행 중/);await capture('text-apply-in-progress');
  await client.evaluate('load()');assert.equal(applyTextCount,1);
  passed('202 is only a start, locks conflicting actions, preserves consent and never duplicates on refresh');
  const progressSaved=await client.evaluate('JSON.stringify({text_apply:state.text_apply,contract_apply:state.contract_apply,apply_state:state.apply_state,latest_attempt:state.latest_attempt,approved_sha256:state.approved_sha256})');
  const progressCounts=[applyTextCount,nativeApprovalCount,textSaveCount,revision];
  await client.evaluate("state.text_apply={active:true,status:'running',plan_sha256:state.proposal_sha256,elapsed_seconds:4};state.apply_state={locked:true,record_valid:true,run_id:'SYNTHETIC-PROGRESS',plan_sha256:state.proposal_sha256};state.latest_attempt={id:'SYNTHETIC-PROGRESS',plan_sha256:state.proposal_sha256,status:'in_progress',stage:'saved_hwp_reopen'};updateWorkProgress()");
  assert.match(await client.evaluate("$('work-progress-status').textContent"),/저장 HWP 재개방/);
  assert.match(await client.evaluate("$('work-progress-id').textContent"),/SYNTHETIC-PROGRESS/);
  await client.evaluate("state.latest_attempt.id='OTHER-RUN';updateWorkProgress()");
  assert.doesNotMatch(await client.evaluate("$('work-progress-status').textContent"),/HWP 재개방/);
  assert.equal(await client.evaluate("$('work-progress-id').textContent"),'');
  await client.evaluate("state.latest_attempt.id='SYNTHETIC-PROGRESS';state.latest_attempt.stage='UNTRUSTED-STAGE';updateWorkProgress()");
  assert.match(await client.evaluate("$('work-progress-status').textContent"),/단계 확인 필요/);
  await client.evaluate("state.text_apply.active=false;state.contract_apply={active:true,status:'running',proposal_kind:'layout',plan_sha256:state.proposal_sha256};state.latest_attempt.stage='structural_verification';updateWorkProgress()");
  assert.match(await client.evaluate("$('work-progress-status').textContent"),/문서 구조 검사/);
  await client.evaluate("state.contract_apply.active=false;state.text_apply.active=true");
  passed('same-GUI document progress requires exact run/plan/owned lease and never invents an unreported stage');
  await client.evaluate("state.text_apply.active=false;state.latest_attempt.error='<img src=x onerror=alert(1)> LOCK KEEP';updateWorkProgress()");
  assert.match(await client.evaluate("$('work-progress-status').textContent"),/잠금 유지/);
  assert.match(await client.evaluate("$('work-progress-error').textContent"),/LOCK KEEP/);
  assert.equal(await client.evaluate("document.querySelectorAll('#agent-jobs img,#inspector-jobs img').length"),0);
  await client.evaluate("state.apply_state={locked:false};state.latest_attempt.status='checks_passed_needs_human_visual_review';state.latest_attempt.artifact_integrity='unverified';updateWorkProgress()");
  assert.doesNotMatch(await client.evaluate("$('work-progress-status').textContent"),/검사 통과/);
  await client.evaluate("state.latest_attempt.artifact_integrity='verified';updateWorkProgress()");
  assert.match(await client.evaluate("$('work-progress-status').textContent"),/사람의 원본\/결과 시각 검토/);
  passed('same-GUI progress preserves locks, treats errors as text and distinguishes integrity-confirmed checks from human review');
  await client.evaluate("state.latest_attempt=null;state.text_apply={active:false,status:'idle'};state.approved_sha256=state.proposal_sha256;updateWorkProgress()");
  assert.match(await client.evaluate("$('work-progress-status').textContent"),/별도 적용 확인/);
  assert.match(await client.evaluate("$('agent-jobs').textContent"),/Agent 미연결/);
  assert.deepEqual([applyTextCount,nativeApprovalCount,textSaveCount,revision],progressCounts);
  await client.evaluate('Object.assign(state,'+progressSaved+');updateComposer()');
  passed('agent/job panel is explicitly disconnected and read-only; rendering does not approve, apply, save or call a model');
  latestAttempt={id:'20261012T000000-a1b2c3d4',plan_sha256:textHash,status:'checks_passed_needs_human_visual_review',artifact_integrity:'verified',stage:'checks_passed'};
  textApply={...textApply,active:false,status:'finished'};await client.evaluate('load()');assert(await client.evaluate("$('apply-text').disabled"));
  assert.match(await client.evaluate("$('apply-status').textContent"),/육안 검토/);assert.equal(applyTextCount,1);
  passed('verified completion still needs visual review and cannot reapply the same recorded text plan');
  textHash='2'.repeat(64);textApprovedHash=textHash;latestAttempt=null;textApply={...textApply,active:false,status:'idle',plan_sha256:null};applyTextFailure=true;
  await client.evaluate('load()');await mouseClick('#apply-text');await until(async()=>applyTextCount===2&&await client.evaluate('!busy'),'failed text apply');
  assert.equal(applyTextCount,2);assert(await client.evaluate("$('apply-text').disabled&&!document.querySelector('#text-apply-status img')"));
  assert.match(await client.evaluate("$('text-apply-status').textContent"),/자동 재시도하지 않습니다/);await client.evaluate('load()');assert.equal(applyTextCount,2);
  passed('failed/uncertain text apply is literal visible data, retains a same-hash no-retry state after refresh');
  textApprovedHash=null;latestAttempt=null;applyState={locked:false};textApply={...textApply,enabled:false,active:false,status:'idle',plan_sha256:null};await client.evaluate('load()');
  await client.evaluate("resetComposer();showPage(2);$('comment-text').value='UNSAVED RECOVERY MEMO <img src=x>';beginTextDraft('fixture-anchor');$('draft-new').value='PENDING RECOVERY TEXT';$('draft-new').dispatchEvent(new Event('input'));$('recovery-panel').open=true;$('recovery-save').click()");
  await until(async()=>recoverySaveCount===1&&await client.evaluate('!busy'),'explicit recovery save');
  assert.equal(nativeApprovalCount,3);assert.equal(applyTextCount,2);assert.equal(textSaveCount,3);assert.equal(recoverySnapshot.memo.text,'UNSAVED RECOVERY MEMO <img src=x>');
  assert.equal(recoverySnapshot.text_draft.pending_text,'PENDING RECOVERY TEXT');
  passed('explicit recovery checkpoint stores editor/cursor/pending text only, never publishes or approves');
  await client.call('Page.navigate',{url:fixtureUrl});await until(()=>client.evaluate(REVIEW_READY),'new tab reads checkpoint');
  assert.equal(await client.evaluate("$('comment-text').value"),'');assert.equal(await client.evaluate("$('draft-new').value"),'');
  assert(await client.evaluate("!$('recovery-restore').disabled&&$('recovery-panel').open"));acceptDialog=false;await client.evaluate("$('recovery-restore').click()");
  await until(()=>client.evaluate("$('comment-text').value===''"),'cancelled restore');assert.equal(recoverySaveCount,1);
  passed('reload advertises saved recovery but never silently fills fields; cancel changes nothing');
  acceptDialog=true;await client.evaluate("$('recovery-restore').click()");await until(()=>client.evaluate("$('draft-new').value==='PENDING RECOVERY TEXT'"),'explicit checkpoint restore');
  assert.equal(await client.evaluate("$('comment-text').value"),'UNSAVED RECOVERY MEMO <img src=x>');assert.equal(await client.evaluate('page'),2);
  assert(await client.evaluate("$('approve').disabled&&!document.querySelector('#recovery-status img')"));assert.equal(nativeApprovalCount,3);assert.equal(applyTextCount,2);assert.equal(textSaveCount,3);
  passed('restored drafts are inert text and still lock approval/apply until normal explicit save/review');
  revision++;textRevision='changed-in-other-window';await client.evaluate('load()');await client.evaluate("$('recovery-restore').click()");
  assert(await client.evaluate("restoredMemoConflict&&$('save-comment').disabled&&$('approve').disabled&&!$('draft-rebase').hidden"));
  assert.match(await client.evaluate("$('recovery-memo-conflict').textContent"),/서버 메모가 바뀌었습니다/);
  await client.evaluate("$('recovery-memo-ack').click()");assert(await client.evaluate('!restoredMemoConflict'));assert.equal(recoverySaveCount,1);
  passed('changed comments/proposal require fresh comparison acknowledgment, not silent checkpoint rebase');
  recoveryRevision='OTHER-WRITER';await client.evaluate("$('comment-text').value='CURRENT INPUT KEEP';$('recovery-save').click()");await until(()=>client.evaluate('!busy'),'recovery CAS denied');
  assert.match(await client.evaluate("$('recovery-status').textContent"),/실패/);assert.equal(await client.evaluate("$('comment-text').value"),'CURRENT INPUT KEEP');assert.equal(recoverySaveCount,1);
  await client.evaluate('readRecovery(true)');recoveryFailure=true;await client.evaluate("$('recovery-save').click()");await until(()=>client.evaluate('!busy'),'recovery failed save');
  assert.equal(recoverySaveCount,1);assert.equal(await client.evaluate("$('comment-text').value"),'CURRENT INPUT KEEP');recoveryFailure=false;
  passed('checkpoint conflict/network failure preserves both stored and current drafts without retry');
  await client.evaluate('readRecovery(true)');acceptDialog=false;await client.evaluate("$('recovery-clear').click()");assert(recoverySnapshot);
  acceptDialog=true;await client.evaluate("$('recovery-clear').click()");await until(async()=>recoverySaveCount===2&&await client.evaluate('!busy'),'explicit clear marker');
  assert.equal(recoverySnapshot,null);assert.equal(await client.evaluate("$('comment-text').value"),'CURRENT INPUT KEEP');assert.equal(nativeApprovalCount,3);assert.equal(applyTextCount,2);
  passed('explicit checkpoint clear is separate from current input, document consent and actual apply');
  await client.call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await client.call('Page.navigate',{url:fixtureUrl+'/template'});
  await until(()=>client.evaluate("document.querySelectorAll('#jobs>section').length===2"),'template comparison initialization');
  const readTemplateText=(index,side)=>client.evaluate(`[...document.querySelectorAll('#job-panel-${index} .diff-side[data-side="${side}"] .paragraph-text')].map(e=>e.textContent)`);
  assert(await client.evaluate("document.querySelector('#make-draft').disabled&&document.querySelector('#result-panel').hidden"));
  assert(await client.evaluate("document.querySelector('#make-draft').checkVisibility()&&!document.querySelector('#confirm-all').checked"));
  assert.equal(templateImageReads.length,0);
  assert(await client.evaluate("!document.querySelector('#job-panel-0').hidden&&document.querySelector('#job-panel-1').hidden"));
  assert.equal(await client.evaluate("document.querySelectorAll('#job-panel-0 .diff-changed').length"),0);
  assert.equal(await client.evaluate("document.querySelectorAll('#job-panel-1 .diff-changed').length"),1);
  assert.equal(await client.evaluate("document.querySelectorAll('#jobs img,#jobs svg').length"),0);
  assert.equal(await client.evaluate('globalThis.templateInjected'),undefined);
  assert.equal(templatePosts.length,0);await capture('template-comparison-desktop');
  await mouseClick('[data-context="0"]');
  for(const [i,job] of templateState.plan.jobs.entries()){
    assert.deepEqual(await readTemplateText(i,'before'),job.preview.old_text);
    assert.deepEqual(await readTemplateText(i,'after'),job.preview.new_text);
  }
  assert(await client.evaluate("[...document.querySelectorAll('#job-panel-0 .paragraph-text')].every(e=>e.checkVisibility())"));
  passed('comparison preserves complete ordered text and paragraph boundaries; equal text never removes the replacement review or authorizes a write');
  assert(await client.evaluate("document.querySelector('#harness-panel').hidden"));
  Object.assign(templateState,{report_harness:{goal:'SYNTHETIC agreed scope '+templateMalicious,decisions:['Upper left; lower right '+templateMalicious,'Do not ask the same scope again.'],binding_sha256:'8'.repeat(64),phase:'awaiting_user_approval',warnings:['SYNTHETIC review only '+templateMalicious]}});
  await client.evaluate('load()');
  assert(await client.evaluate("!document.querySelector('#harness-panel').hidden"));
  assert.equal(await client.evaluate("document.querySelector('#harness-goal').textContent"),templateState.report_harness.goal);
  assert.deepEqual(await client.evaluate("[...document.querySelectorAll('#harness-decisions li')].map(e=>e.textContent)"),templateState.report_harness.decisions);
  assert.equal(await client.evaluate("document.querySelectorAll('#harness-panel img').length"),0);
  assert.equal(templatePosts.length,0);assert.equal(templateImageReads.length,0);
  delete templateState.report_harness;await client.evaluate('load()');
  assert(await client.evaluate("document.querySelector('#harness-panel').hidden&&!document.querySelector('#harness-goal').textContent&&!document.querySelector('#harness-decisions').children.length"));
  passed('optional pinned feedback stays inert and disappears without stale content when absent');
  await client.evaluate("document.querySelector('#view-tab-0-0').focus()");
  await client.call('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
  await client.call('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowRight',code:'ArrowRight',windowsVirtualKeyCode:39});
  await until(()=>client.evaluate("document.querySelectorAll('#job-panel-0 img').length===2&&[...document.querySelectorAll('#job-panel-0 img')].every(e=>e.complete&&e.naturalWidth===3)"),'keyboard-selected authenticated PNG comparison');
  assert(await client.evaluate("document.activeElement.id==='view-tab-0-1'&&document.querySelector('#view-tab-0-1').getAttribute('aria-selected')==='true'"));
  assert.notEqual(await client.evaluate("getComputedStyle(document.activeElement).outlineStyle"),'none');
  assert.equal(templateImageReads.length,3);assert.equal(templatePosts.length,0);
  assert(await client.evaluate("[...document.querySelectorAll('.image-card img')].every(e=>e.src.startsWith('blob:'))"));
  assert(await client.evaluate("[...document.querySelectorAll('.image-card a')].every(e=>e.href.startsWith('blob:')&&e.rel==='noopener')"));
  assert.equal(await client.evaluate('globalThis.templateInjected'),undefined);
  await capture('template-image-comparison-desktop');
  await mouseClick('#view-tab-0-2');
  for(const value of [templateJob.source_path,templateJob.payload_path,templateJob.source_sha256,templateJob.payload_sha256,templateJob.preview.candidate_sha256,JSON.stringify(templateJob.target_range),JSON.stringify(templateJob.payload_ranges)]){
    assert(await client.evaluate(`document.querySelector('#job-panel-0 .technical').textContent.includes(${JSON.stringify(value)})`));
  }
  for(const width of [390,430,980]){
    await client.call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    for(const view of [0,1,2]){
      await mouseClick('#view-tab-0-'+view);
      assert(await client.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),'Template comparison viewport overflow');
    }
  }
  await client.call('Emulation.setDeviceMetricsOverride',{width:390,height:1000,deviceScaleFactor:1,mobile:false});
  await mouseClick('#view-tab-0-0');await capture('template-comparison-mobile');
  await mouseClick('#document-tab-1');await mouseClick('#view-tab-1-1');
  await until(()=>client.evaluate("document.querySelector('#job-panel-1 .image-review').textContent.includes('SYNTHETIC JPEG 미지원')"),'unsupported image remains explicit');
  assert.equal(await client.evaluate("document.querySelectorAll('#job-panel-1 img').length"),0);
  assert.equal(templatePosts.length,0);
  passed('document and comparison tabs are keyboard-accessible and narrow-screen safe; image reads are lazy and unsupported images never trigger conversion or approval');
  await mouseClick('#document-tab-0');templateImageFailure=true;
  await client.evaluate('load()');await mouseClick('#view-tab-0-1');
  await until(()=>client.evaluate("document.querySelector('#job-panel-0 .image-review').textContent.includes('SYNTHETIC preview conflict')"),'stale image request stays inert');
  assert.equal(await client.evaluate("document.querySelectorAll('#job-panel-0 img').length"),0);assert.equal(templatePosts.length,0);
  await mouseClick('#view-tab-0-0');templateImageFailure=false;
  const compareBefore=['같은 머리말','측정 10ms. '+templateMalicious,'동일 본문','끝'];
  const compareAfter=['같은 머리말','측정 12ms. '+templateMalicious,'동일 본문','새 문단','끝'];
  templateJob.preview.old_text=compareBefore;templateJob.preview.new_text=compareAfter;await client.evaluate('load()');
  assert.deepEqual(await readTemplateText(0,'before'),compareBefore);assert.deepEqual(await readTemplateText(0,'after'),compareAfter);
  assert.deepEqual(await client.evaluate("[...document.querySelectorAll('#job-panel-0 del')].map(e=>e.textContent)"),['10ms.']);
  assert.deepEqual(await client.evaluate("[...document.querySelectorAll('#job-panel-0 ins')].map(e=>e.textContent)"),['12ms.','새 문단']);
  await mouseClick('#job-panel-0 .change-navigation button:last-child');
  assert(await client.evaluate("document.activeElement===document.querySelectorAll('#job-panel-0 .diff-changed')[1]"));
  assert(await client.evaluate("document.querySelector('#job-panel-0 .change-navigation button:last-child').disabled"));
  assert.equal(await client.evaluate('globalThis.templateInjected'),undefined);assert.equal(templatePosts.length,0);
  await client.call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await capture('template-highlighted-changes');
  const comparisonAccessibility=await client.evaluate(`(() => {
    const luminance=rgb=>{const channels=rgb.match(/[0-9.]+/g).slice(0,3).map(value=>{const channel=Number(value)/255;return channel<=.04045?channel/12.92:((channel+.055)/1.055)**2.4});return channels[0]*.2126+channels[1]*.7152+channels[2]*.0722};
    return ['del','ins'].map(tag=>{const style=getComputedStyle(document.querySelector('#job-panel-0 '+tag)),values=[luminance(style.color),luminance(style.backgroundColor)].sort((a,b)=>a-b);return {contrast:(values[1]+.05)/(values[0]+.05),decoration:style.textDecorationLine}});
  })()`);
  assert(comparisonAccessibility.every(value=>value.contrast>=4.5));
  assert(comparisonAccessibility[0].decoration.includes('line-through'));
  assert(comparisonAccessibility[1].decoration.includes('underline'));
  passed('change text meets 4.5:1 contrast and remains distinguishable by strike-through versus underline without relying on hue');
  templateJob.preview.old_text=['same','repeat','repeat','tail'];templateJob.preview.new_text=['same','repeat','tail'];await client.evaluate('load()');
  assert.deepEqual(await readTemplateText(0,'before'),templateJob.preview.old_text);assert.deepEqual(await readTemplateText(0,'after'),templateJob.preview.new_text);
  assert.deepEqual(await client.evaluate("[...document.querySelectorAll('#job-panel-0 del')].map(e=>e.textContent)"),['repeat']);
  templateJob.preview.old_text=['prefix',...Array.from({length:320},(_,i)=>'old '+i),'suffix'];
  templateJob.preview.new_text=['prefix',...Array.from({length:320},(_,i)=>'new '+i),'suffix'];await client.evaluate('load()');
  assert.deepEqual(await readTemplateText(0,'before'),templateJob.preview.old_text);assert.deepEqual(await readTemplateText(0,'after'),templateJob.preview.new_text);
  assert.equal(await client.evaluate("document.querySelectorAll('#job-panel-0 .diff-changed').length"),1);
  assert.equal(templatePosts.length,0);
  passed('word highlights isolate edits without altering Unicode/literal text; repeated paragraphs and bounded large comparisons retain every paragraph in order');
  templateJob.preview.old_text=[];templateJob.preview.new_text=[];await client.evaluate('load()');
  assert.deepEqual(await readTemplateText(0,'before'),[]);assert.deepEqual(await readTemplateText(0,'after'),[]);
  assert(await client.evaluate("document.querySelector('#job-panel-0 .empty-comparison').checkVisibility()&&document.querySelector('#make-draft').disabled"));
  templateJob.preview.old_text=['SYNTHETIC old'];templateJob.preview.new_text=['SYNTHETIC new'];await client.evaluate('load()');
  await mouseClick('#confirm-all');
  assert(await client.evaluate("!document.querySelector('#make-draft').disabled"));
  assert.equal(templatePosts.length,0);
  await client.evaluate("document.querySelector('#confirm-all').focus()");
  await client.call('Input.dispatchKeyEvent',{type:'keyDown',key:' ',code:'Space',windowsVirtualKeyCode:32});
  await client.call('Input.dispatchKeyEvent',{type:'keyUp',key:' ',code:'Space',windowsVirtualKeyCode:32});
  assert(await client.evaluate("!document.querySelector('#confirm-all').checked&&document.querySelector('#make-draft').disabled"));
  await mouseClick('#confirm-all');
  passed('a deliberate draft-only choice is required; toggling it never writes permission or creates a file');
  templatePostError=true;
  await mouseClick('#make-draft');await until(async()=>templatePosts.length===1&&await client.evaluate('!busy'),'draft CAS error');
  assert.equal(templateState.draft_authorized,false);
  assert.match(await client.evaluate("document.querySelector('#error').textContent"),/SYNTHETIC CAS conflict/);
  assert.equal(await client.evaluate("document.querySelectorAll('#error img').length"),0);
  assert.equal(await client.evaluate("document.activeElement.id"),'error');
  templatePostError=false;await mouseClick('#make-draft');
  await until(async()=>templatePosts.length===2&&await client.evaluate('!busy'),'unapproved draft creation');
  assert.equal(templateState.artifacts[0].accepted,false);
  assert(await client.evaluate("!document.querySelector('#result-panel').hidden&&!document.querySelector('#make-draft').checkVisibility()"));
  assert(await client.evaluate("document.activeElement===document.querySelector('#downloads a')"));
  await client.call('Page.navigate',{url:fixtureUrl+'/template'});
  await until(()=>client.evaluate("!document.querySelector('#result-panel').hidden"),'existing draft delivered on reopen');
  assert.equal(templatePosts.length,2);
  await mouseClick('#downloads a');await until(()=>templateDownloads.length===1,'draft HWPX download before acceptance');
  assert.deepEqual(templateDownloads,['/artifact/0']);assert.equal(templatePosts.length,2);
  await mouseClick('#downloads button');
  await until(async()=>templatePosts.length===3&&await client.evaluate('!busy'),'exact artifact acceptance');
  assert.equal(templateState.artifacts[0].accepted,true);
  assert.equal(templatePosts.filter(item=>item.route==='/api/template-draft').length,2);
  assert(await client.evaluate("document.querySelector('#downloads button').disabled"));
  assert.equal(await client.evaluate("document.querySelectorAll('#downloads img').length"),0);
  await capture('template-generated-mobile');
  passed('an actual draft is available before acceptance; reopening never regenerates and selecting the artifact does not create again');
  const createdArtifacts=templateState.artifacts;templateDownloadError=true;
  await mouseClick('#downloads a');await until(()=>client.evaluate("document.querySelector('#result-panel').hidden&&document.activeElement.id==='error'"),'failed artifact download refreshes completion');
  assert.equal(templateDownloads.length,2);assert(await client.evaluate("document.querySelector('#make-draft').disabled"));
  assert.equal(await client.evaluate('current.artifacts.length'),0);
  templateState.artifacts=createdArtifacts;templateState.result_error='';await client.evaluate('load()');templateStateFailure=true;
  await mouseClick('#downloads a');await until(()=>client.evaluate("document.querySelector('#result-panel').hidden&&document.activeElement.id==='error'"),'failed state refresh preserves blocked completion');
  assert.equal(templateDownloads.length,3);assert(await client.evaluate("current.execution_recorded&&document.querySelector('#make-draft').disabled"));
  assert.equal(await client.evaluate("document.querySelectorAll('#error img,#result-error img').length"),0);
  templateDownloadError=false;templateStateFailure=false;templateState.artifacts=createdArtifacts;templateState.result_error='';await client.evaluate('load()');
  passed('rejected downloads immediately clear stale completion; failed state refresh stays blocked without retry, approval or creation');
  const savedArtifacts=templateState.artifacts;templateState.artifacts=[];templateState.locked=false;templateState.result_error='SYNTHETIC artifact changed '+templateMalicious;
  await client.evaluate('load()');
  assert(await client.evaluate("document.querySelector('#result-panel').hidden&&document.querySelector('#make-draft').disabled"));
  assert.match(await client.evaluate("document.querySelector('#result-error').textContent"),/SYNTHETIC artifact changed/);
  assert.equal(await client.evaluate("document.querySelectorAll('#result-error img').length"),0);assert.equal(templatePosts.length,3);
  templateState.artifacts=savedArtifacts;templateState.locked=true;templateState.result_error='';await client.evaluate('load()');
  passed('draft output remains distinct from acceptance, Hancom review and blocked/tampered execution records');
  passed('scope/token/revision, draft-only authority, inert errors, output download and post-generation acceptance remain intact');
  await client.call('Emulation.setDeviceMetricsOverride',{width:900,height:1000,deviceScaleFactor:1,mobile:false});
  await client.call('Page.navigate',{url:fixtureUrl+'/report-document'});
  await until(()=>client.evaluate("document.querySelectorAll('[data-scope]').length===2"),'document workspace initialization');
  await client.call('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  assert.equal(await client.evaluate("getComputedStyle(document.querySelector('.progress'),'::before').animationName"),'none');
  await client.call('Emulation.setEmulatedMedia',{features:[]});
  await client.evaluate("document.querySelector('#instruction').value='SYNTHETIC first request';document.querySelector('#instruction').dispatchEvent(new Event('input',{bubbles:true}))");
  await mouseClick('#draft-consent');await mouseClick('#create-draft');
  await until(()=>reportFailedReads===1,'single injected read failure during active draft');
  finishReportVersion();
  await until(()=>client.evaluate("document.querySelector('#result-title').textContent==='SYNTHETIC 수정본 1'&&!document.querySelector('#accept-version').disabled"),'read-only status recovery observes completed file');
  assert.equal(reportPosts.filter(item=>item.route==='/api/report-start').length,1);
  passed('a transient active-status read failure recovers through GET only, never resending model or document work');
  await client.evaluate("document.querySelector('#instruction').value='SYNTHETIC follow-up request';document.querySelector('#instruction').dispatchEvent(new Event('input',{bubbles:true}))");
  await mouseClick('#create-draft');await until(()=>reportTaskNumber===2,'second explicit document task');
  finishReportVersion();
  await until(()=>client.evaluate("document.querySelector('#result-title').textContent==='SYNTHETIC 수정본 2'"),'newly completed follow-up is selected');
  await mouseClick('#versions li:last-child button');
  await client.evaluate('load()');
  assert.equal(await client.evaluate("document.querySelector('#result-title').textContent"),'SYNTHETIC 수정본 1');
  passed('follow-up completion selects the new artifact once and later history navigation remains deliberate');
  reportState.model.status='connecting';reportState.model.operation_active=true;reportState.model.selection=null;
  await client.call('Page.navigate',{url:fixtureUrl+'/report-document'});
  await until(()=>client.evaluate("document.querySelectorAll('[data-scope]').length===2"),'reload during profile connection');
  assert(await client.evaluate("document.querySelector('#connect-model').disabled"));
  reportState.model.status='ready';reportState.model.operation_active=false;reportState.model.selection=reportModel;
  await until(()=>client.evaluate("document.querySelector('#model-status').textContent.includes('NO-REAL-MODEL')"),'reloaded page observes server connection completion');
  assert.equal(reportPosts.filter(item=>item.route==='/api/report-model/connect').length,0);
  passed('reload resumes observation of server-reported connection activity without restarting it');
  reportState.model.status='failed';reportState.model.selection=null;
  await client.evaluate('load()');await mouseClick('#connect-model');
  await until(()=>reportPosts.some(item=>item.route==='/api/report-model/prepare'),'explicit reconnection passes earlier chat failure');
  await until(()=>client.evaluate("document.querySelector('#model-status').textContent.includes('NO-REAL-MODEL')"),'reconnection complete');
  assert.equal(reportPosts.filter(item=>item.route==='/api/report-model/discover').length,1);
  passed('an earlier chat failure does not abort a new explicit discovery/preparation sequence');
  await mouseClick('#scope-panel>summary');
  await mouseClick('[data-scope][value="scope-a"]');await mouseClick('[data-scope][value="scope-b"]');
  await client.evaluate("document.querySelector('#instruction').value='SYNTHETIC different source scope';document.querySelector('#instruction').dispatchEvent(new Event('input',{bubbles:true}))");
  await mouseClick('details.small-details>summary');await mouseClick('#preview');
  await until(()=>reportPosts.filter(item=>item.route==='/api/report-preview').length===3,'changed scope preview');
  const changedScope=reportPosts.filter(item=>item.route==='/api/report-preview').at(-1).body;
  assert.equal(changedScope.previous_version,null);assert.deepEqual(changedScope.selection_ids,['scope-b']);
  assert.equal(reportPosts.filter(item=>item.route==='/api/report-start').length,2);
  passed('an explicit scope change can start from the original without reusing a different revision scope');
  await capture('document-workspace-recovered');
  assert.deepEqual(errors, []);
  passed('no browser JavaScript errors');
  await mkdir(output, { recursive: true });
  await writeFile(join(output, 'thumbnail-ui-tests.json'), JSON.stringify({ fixture_only: true, passed: true, checks, initial_image_requests: initialImageRequests, total_image_requests: requestedImages.size }, null, 2));
  console.log('Artifacts:',output);
} finally {
  if (client) await client.call('Browser.close').catch(() => {});
  if (browser.exitCode === null) {
    await Promise.race([new Promise(resolve => browser.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 3000))]);
    if (browser.exitCode === null) browser.kill();
  }
  fixture.closeAllConnections();
  await new Promise(resolve => fixture.close(resolve));
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
