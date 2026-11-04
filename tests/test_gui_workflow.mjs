// Default: continuous synthetic Tk/HTTP/Edge, NO COM. Explicit Native mode uses
// only a NEW owned unsigned TOY and exact candidate; never actual reports/retries.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {createInterface} from 'node:readline';
import {mkdtemp,mkdir,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import { REVIEW_READY } from './review_ready.mjs';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const nativeIndex=process.argv.indexOf('--executable'),nativeExe=nativeIndex>=0?resolve(process.argv[nativeIndex+1]):null;
const layout=process.argv.includes('--layout'),fill=process.argv.includes('--fill'),agentWorkflow=process.argv.includes('--agent-workflow');if(layout||fill)assert(nativeExe,'contract GUI needs an exact candidate and explicit Native flags');
assert(!(layout&&fill),'select one exact Native GUI family');
if(agentWorkflow)assert(nativeExe&&!layout&&!fill,'agent workflow requires an exact Native schema1 TOY, not layout/fill/synthetic mode');
const agentFlow=!nativeExe||agentWorkflow,schema=fill?4:layout?3:1;
const referenceIndex=process.argv.indexOf('--reference-toy-source');if(referenceIndex>=0)assert(nativeExe&&(layout||fill)&&!agentWorkflow&&process.argv[referenceIndex+1]&&!process.argv[referenceIndex+1].startsWith('--'),'reference TOY requires exact Native layout/fill and one source path');
const referenceToy=referenceIndex>=0?resolve(process.argv[referenceIndex+1]):null;
if(nativeExe)assert(process.argv.includes('--run-native-test')&&process.argv.includes('--toy-only'),'explicit Native TOY flags required');
const lab=nativeExe?join(root,'work','toy-gui-native-'+Date.now()):await mkdtemp(join(tmpdir(),'hwp-gui-SYNTHETIC-'));
if(nativeExe)await mkdir(lab);
const hostArgs=['-X','utf8',join(root,'tests/gui_workflow_fixture.py'),lab];if(nativeExe)hostArgs.push(nativeExe,'--run-native-test','--toy-only');if(layout)hostArgs.push('--layout');if(fill)hostArgs.push('--fill');if(agentWorkflow)hostArgs.push('--agent-workflow');if(referenceToy)hostArgs.push('--reference-toy-source',referenceToy);
const host=spawn(join(root,'.venv/Scripts/python.exe'),hostArgs,{cwd:root,stdio:['pipe','pipe','pipe']});
let stderr='';host.stderr.on('data',chunk=>{stderr+=chunk.toString()});
const lines=createInterface({input:host.stdout});
const ready=new Promise((resolve,reject)=>{
 const timer=setTimeout(()=>reject(new Error('Synthetic Tk host not ready: '+stderr)),nativeExe?850000:30000);
 lines.on('line',line=>{try{const data=JSON.parse(line);if(data.origin){clearTimeout(timer);resolve(data)}}catch{}});
 host.once('exit',code=>{clearTimeout(timer);reject(new Error('Synthetic host exited '+code+': '+stderr))});
});
const profile=join(lab,'edge-profile');await mkdir(profile);
let browser,client;const errors=[];const steps=[];
class CDP {
 constructor(socket){this.socket=socket;this.next=1;this.pending=new Map();this.events=new Map();socket.onmessage=({data})=>{const message=JSON.parse(data);if(message.id){const item=this.pending.get(message.id);if(!item)return;this.pending.delete(message.id);clearTimeout(item.timer);if(message.error)item.reject(new Error(JSON.stringify(message.error)));else item.resolve(message.result)}else for(const callback of this.events.get(message.method)||[])callback(message.params)}}
 on(name,callback){if(!this.events.has(name))this.events.set(name,[]);this.events.get(name).push(callback)}
 call(method,params={}){const id=this.next++;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error('CDP timeout '+method))},method==='Page.navigate'?45000:15000);this.pending.set(id,{resolve,reject,timer});this.socket.send(JSON.stringify({id,method,params}))})}
 async evaluate(expression){const result=await this.call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value}
}
async function until(callback,label){const deadline=Date.now()+(nativeExe?600000:20000);while(Date.now()<deadline){if(await callback())return;await new Promise(resolve=>setTimeout(resolve,40))}throw new Error('Timed out: '+label)}
async function click(id){assert(await client.evaluate(`!!document.getElementById(${JSON.stringify(id)})&&!document.getElementById(${JSON.stringify(id)}).disabled`),'actual fixture control disabled '+id);await client.evaluate(`document.getElementById(${JSON.stringify(id)}).click()`)}
async function clickSelector(selector){const point=await client.evaluate(`(()=>{const element=document.querySelector(${JSON.stringify(selector)});element.scrollIntoView({block:'nearest'});const box=element.getBoundingClientRect();return {x:box.x+box.width/2,y:box.y+box.height/2}})()`);await client.call('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});await client.call('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1})}
try {
 const info=await ready;assert.equal(info.native_mocked,!nativeExe);assert.equal(info.agent_model_mocked,agentFlow);assert.equal(info.agent_flow_requested,agentWorkflow);assert.equal(info.source_reference_copy,!!referenceToy);assert.equal(info.schema,schema);assert.equal(info.proposal_kind,fill?'fill':layout?'layout':'text');const oldText=info.old_text||'SYNTHETIC CODE AAAA',newText=info.new_text||'SYNTHETIC CODE BBBB';assert.match(info.origin,/^http:\/\/127\.0\.0\.1:\d+$/);steps.push(...info.events);
 const edge=process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
 browser=spawn(edge,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:'ignore'});
 let port;await until(async()=>{try{port=Number((await readFile(join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]);return port>0}catch{return false}},'owned Edge start');
 const targets=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();const target=targets.find(item=>item.type==='page');assert(target);
 const socket=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true})});client=new CDP(socket);
 client.on('Runtime.exceptionThrown',event=>errors.push(event.exceptionDetails.text));
 client.on('Page.javascriptDialogOpening',()=>{client.call('Page.handleJavaScriptDialog',{accept:true}).catch(error=>errors.push(error.message))});
 await client.call('Page.enable');await client.call('Runtime.enable');
 const downloads=join(lab,'verified-downloads');await mkdir(downloads);await client.call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});
 if(fill){
 const navigation=await client.call('Page.navigate',{url:info.origin+'/fill'});assert.equal(navigation.errorText,undefined);await until(()=>client.evaluate("typeof state!=='undefined'&&!!state&&connected&&!busy&&byId('image').complete&&byId('image').naturalWidth>0&&document.querySelectorAll('#overlay rect').length===3"),'real fill UI, input and original PDF');
 assert.equal(await client.evaluate('location.origin'),info.origin);assert.equal(await client.evaluate('state.proposal'),null);assert.equal(await client.evaluate('placements.size'),0);
 const fields=await client.evaluate('state.fields.filter(f=>f.supported).map(f=>f.field_id)');assert.equal(fields.length,3);assert.equal(await client.evaluate('state.regions.length'),3);
 for(let index=0;index<fields.length;index++){await client.evaluate(`byId('field').value=${JSON.stringify(fields[index])};byId('field').dispatchEvent(new Event('change'))`);await clickSelector('#overlay rect:nth-child('+(index+1)+')')}
 assert.equal(await client.evaluate('placements.size'),3);await click('compile');await until(()=>client.evaluate('!busy&&state.proposal?.schema===4&&!dirty'),'real fill plan compile from actual UI placements');assert.equal(await client.evaluate('!!sameApproval()'),false);
 const job=await client.evaluate('state.contract_apply');assert(job?.available===true&&job.supported_kinds?.includes('fill')===true,'Strict Native fill/source profile unavailable; no approval/apply attempted');steps.push('Real fill input cards and actual template regions → three UI placements → compile, no approval');
 for(const id of ['check-all','unsigned','policy','header-spine'])await client.evaluate(`byId(${JSON.stringify(id)}).checked=true;byId(${JSON.stringify(id)}).dispatchEvent(new Event('change'))`);
 client.events.set('Page.javascriptDialogOpening',[()=>client.call('Page.handleJavaScriptDialog',{accept:false})]);await click('approve');assert.equal(await client.evaluate('!!sameApproval()'),false);
 client.events.set('Page.javascriptDialogOpening',[()=>client.call('Page.handleJavaScriptDialog',{accept:true})]);await click('approve');await until(()=>client.evaluate('!busy&&!!sameApproval()'),'exact fill TEST UI policy/unsigned/header/field consent');steps.push('Fill approval cancellation is no-op; exact policy/unsigned/header/field TEST UI approval');
 client.events.set('Page.javascriptDialogOpening',[()=>client.call('Page.handleJavaScriptDialog',{accept:false})]);await click('apply-fill');assert.equal(await client.evaluate('state.latest_attempt'),null);
 client.events.set('Page.javascriptDialogOpening',[()=>client.call('Page.handleJavaScriptDialog',{accept:true})]);await click('apply-fill');
 await until(async()=>{if(await client.evaluate("!busy&&!state.contract_apply?.active&&!!state.latest_attempt&&state.latest_attempt.status!=='in_progress'"))return true;if(await client.evaluate('!busy'))await click('reload');return false},'one actual fill job; read-only reload never resends');
 assert.equal(await client.evaluate('state.latest_attempt.status'),'checks_passed_needs_human_visual_review');assert.equal(await client.evaluate('state.latest_attempt.artifact_integrity'),'verified');assert.equal(await client.evaluate('state.apply_state.locked'),false);const fillRun=await client.evaluate('state.latest_attempt.id');steps.push('Separate fill apply cancellation no-op → one exact frozen Native fill job → strict saved-HWP gates and closed lease');
 await client.call('Page.navigate',{url:info.origin+'/'});await until(()=>client.evaluate(REVIEW_READY),'main read-only version comparison after fill');assert(await client.evaluate(`!![...$('result-version').options].find(o=>o.value===${JSON.stringify(fillRun)})`));await client.evaluate(`$('result-version').value=${JSON.stringify(fillRun)};$('result-version').dispatchEvent(new Event('change'))`);await until(()=>client.evaluate(`selectedVersion()?.id===${JSON.stringify(fillRun)}`),'exact fill result selected, no new edit');
 }else{
 const reviewUrl=info.origin+(layout?'/?proposal=layout':'');
 const navigation=await client.call('Page.navigate',{url:reviewUrl});assert.equal(navigation.errorText,undefined,'local GUI fixture navigation failed');assert.equal(typeof navigation.frameId,'string');await until(()=>client.evaluate(REVIEW_READY),'real app source and recovery read');
 assert.equal(await client.evaluate('location.origin'),info.origin);
 if(!layout){assert.equal(await client.evaluate('state.proposal'),null);
 const anchor=await client.evaluate(`state.manifest.anchors.find(a=>a.old_text===${JSON.stringify(oldText)}).anchor_id`);
 if(!agentFlow){await client.evaluate(`beginTextDraft(${JSON.stringify(anchor)});$('draft-new').value=${JSON.stringify(newText)}`)}
 else{
  assert.equal(info.agent_model_mocked,true);
  const documentSnapshot=async()=>Promise.all(['baseline.hwpx','baseline.pdf','manifest.json','comments.json','proposal.json','proposal-text.json','approved-plan.json','.apply-lock.json'].map(async name=>{try{return [name,createHash('sha256').update(await readFile(join(info.workspace,name))).digest('hex')]}catch(error){if(error.code==='ENOENT')return [name,null];throw error}}));
  const beforeAgent=await documentSnapshot();
  await client.evaluate(`$('target').value=${JSON.stringify(anchor)};reviewSourceConfirmed=true;viewMode='original';updateComposer();showInspectorTab('pi')`);
  await until(()=>client.evaluate('!agentDiscoveryPending&&piDetected'),'automatic synthetic path discovery only');
  await click('pi-chat-connect');await until(()=>client.evaluate("!chatBusy&&chatState?.status==='ready'"),'separate synthetic profile connection');
  await click('pi-context-preview');await until(()=>client.evaluate('!contextPending&&!!contextPreview'),'actual HWP context preview');
  assert((await client.evaluate("$('pi-context-data').textContent")).includes(oldText));
  await client.evaluate("$('pi-chat-consent').checked=true;$('pi-chat-consent').dispatchEvent(new Event('change'));$('pi-context-confirm').checked=true;$('pi-context-confirm').dispatchEvent(new Event('change'));$('pi-context-actions').checked=true;$('pi-context-actions').dispatchEvent(new Event('change'));$('pi-chat-input').value='SYNTHETIC propose one replacement draft';updateChatControls()");
  await click('pi-chat-send');await until(()=>client.evaluate("!chatBusy&&chatState?.status==='ready'&&chatState.artifacts.length===1"),'fixed synthetic agent draft card');
  assert.equal(await client.evaluate('textDraftEdits.length'),0);assert.equal(await client.evaluate('approvedHash'),null);assert.equal(await client.evaluate('state.proposal'),null);
  await client.evaluate("document.querySelector('#pi-chat-artifacts button').click()");await until(()=>client.evaluate(`$('draft-new').value===${JSON.stringify(newText)}&&$('target').value===${JSON.stringify(anchor)}`),'explicit agent artifact review/import');
  assert.deepEqual(await documentSnapshot(),beforeAgent,'Fake model and artifact import must not save notes/proposals/approvals/leases or change baselines');
  steps.push('Synthetic Pi: automatic path discovery only, separate profile/context/send consent → fixed fake model draft → real artifact review/import; document/consent/hash state unchanged');
 }
 await client.evaluate("$('draft-new').dispatchEvent(new Event('input'));$('recovery-panel').open=true");
 await click('recovery-save');await until(()=>client.evaluate('!busy&&!!recoverySnapshot'),'real checkpoint saved');
 assert.equal(await client.evaluate('state.proposal'),null);assert.equal(await client.evaluate('approvedHash'),null);steps.push('Real Edge saves recovery only, no actual proposal/consent');
 const reopened=await client.call('Page.navigate',{url:info.origin});assert.equal(reopened.errorText,undefined,'local GUI fixture reopen failed');assert.equal(typeof reopened.frameId,'string');await until(()=>client.evaluate(REVIEW_READY),'reopen reads recovery');
 assert.equal(await client.evaluate("$('draft-new').value"),'');await click('recovery-restore');
 await until(()=>client.evaluate(`$('draft-new').value===${JSON.stringify(newText)}`),'explicit recovery restore');
 if(agentFlow)await until(()=>client.evaluate('!agentDiscoveryPending&&piDetected'),'reopened page metadata scan without a new model turn');
 steps.push('New page restores pending draft only after separate choice; metadata refresh cannot resend the agent turn');
 await click('draft-add');await click('draft-save');await until(()=>client.evaluate('!busy&&!!state.proposal'),'real strict proposal saved');
 assert.equal(await client.evaluate('approvedHash'),null);steps.push('Explicit proposal save, real schema/cache/CAS gate');
 }else{assert.equal(await client.evaluate('state.proposal.schema'),3);assert.equal(await client.evaluate('approvedHash'),null);const job=await client.evaluate('state.contract_apply');assert.equal(job?.available,true,'Layout Native/source profile unavailable; no TEST consent/apply attempted: '+String(job?.reason??'Missing availability').slice(0,500));steps.push('Table proposal compiled without consent; available profile checked, original-row-minima policy remains unchanged')}
 await click('review-wide');await until(()=>client.evaluate(`$('proposal-review-dialog').open&&state.proposal.edits.length===1&&$('proposal-review-rows').textContent.includes(${JSON.stringify(oldText)})`),'actual before/after review');
 assert((await client.evaluate("$('proposal-review-rows').textContent")).includes(oldText));assert((await client.evaluate("$('proposal-review-rows').textContent")).includes(newText));
 if(layout){await client.evaluate("$('summary-policy-confirm').checked=true;$('summary-policy-confirm').dispatchEvent(new Event('change'));$('summary-check-all').checked=true;$('summary-check-all').dispatchEvent(new Event('change'))");assert.match(await client.evaluate("$('summary-policy-detail').textContent"),/행|높이/);steps.push('Actual table GUI policy and each exact original-row region checked through UI handlers')}
 await click('summary-approve');await until(()=>client.evaluate('!busy&&approvedHash===state.proposal_sha256'),'separate actual synthetic UI consent');steps.push('Before/after dialog → exact local UI TEST consent, no direct approval file write');
 if(layout)await click('close-review-wide');await click(layout?'apply-contract':'summary-apply-text');await until(()=>client.evaluate('!busy&&state.latest_attempt?.artifact_integrity===\'verified\''),'real apply lifecycle with MOCK Native exporter');
 assert.equal(await client.evaluate('state.latest_attempt.status'),'checks_passed_needs_human_visual_review');assert.equal(await client.evaluate('state.apply_state.locked'),false);steps.push('Separate APPLY confirmation → real progress/patch/structure/PDF/hash gates with '+(nativeExe?'actual exact EXE Native':'MOCK Native'));
 if(!layout)await click('close-review-wide');
 }
 await client.evaluate("$('view-mode').value='split';$('view-mode').dispatchEvent(new Event('change'))");
 await until(()=>client.evaluate("document.querySelectorAll('.compare-sheet img').length===2&&[...document.querySelectorAll('.compare-sheet img')].every(i=>i.complete&&i.naturalWidth>0)"),'actual original/result PDF comparison');steps.push('Actual comparison image routes → browser read-only preview');
 const run=await client.evaluate('selectedVersion().id');
 for(const kind of ['hwp','pdf']){
  await click('download-'+kind);const file=join(downloads,`검토용-${run}.${kind}`);
  await until(async()=>{try{return (await readFile(file)).length>0}catch{return false}},'actual '+kind+' file');await until(()=>client.evaluate('!busy'),'download completed');
  const stored=await readFile(join(info.workspace,'runs',run,kind==='hwp'?'candidate.hwp':'candidate.pdf'));assert((await readFile(file)).equals(stored));
 }
 steps.push('HWP/reopened PDF → actual token-protected snapshots → client SHA verified → exact disk bytes');
 await client.evaluate(`$('compare-reference').value=${JSON.stringify(run)};$('compare-reference').dispatchEvent(new Event('change'));$('view-mode').value='diff';$('view-mode').dispatchEvent(new Event('change'))`);
 await until(()=>client.evaluate("document.querySelector('.compare-sheet img')?.complete&&document.querySelector('.compare-sheet img')?.naturalWidth>0"),'bound version-reference comparison');
 steps.push('Bound result as comparison reference → real pair image API, read-only');
 if(!layout&&!fill){await client.evaluate("$('view-mode').value='original';$('view-mode').dispatchEvent(new Event('change'));resetComposer();$('comment-text').value='SYNTHETIC BATCH FOLLOWUP';updateComposer()");
 await click('save-comment');await until(()=>client.evaluate('!busy&&workflowRead&&state.comments.comments.length===1'),'batch memo saved');
 const evidenceFile=join(lab,'batch-source.txt');await writeFile(evidenceFile,'DATA ONLY <script>never execute</script>');
 const dom=await client.call('DOM.getDocument');const input=await client.call('DOM.querySelector',{nodeId:dom.root.nodeId,selector:'#evidence-file'});
 await client.call('DOM.setFileInputFiles',{nodeId:input.nodeId,files:[evidenceFile]});await click('evidence-upload');
 await until(()=>client.evaluate('!busy&&Object.keys(workflowData.evidence).length===1'),'actual local evidence stored');
 await client.evaluate("(() => {const card=$('comment-list').firstElementChild;card.querySelector('select[aria-label=\"메모 처리 상태\"]').value='resolved';card.querySelector('select[aria-label=\"메모 근거 자료\"]').value=Object.keys(workflowData.evidence)[0];[...card.querySelectorAll('button')].find(b=>b.textContent==='처리·근거 기록').click()})()");
 try{await until(()=>client.evaluate("!busy&&Object.values(workflowData.notes)[0]?.status==='resolved'"),'hash-bound note processing recorded')}
 catch(error){console.log('WORKFLOW DEBUG',await client.evaluate("({message:$('workflow-status').textContent,read:workflowRead,hashes:workflowHashes,data:workflowData,comments:state.comments})"));throw error}
 await client.evaluate("for(const input of $('visual-review-checks').querySelectorAll('input')){input.checked=true;input.dispatchEvent(new Event('change'))}");await click('visual-review-save');
 await until(()=>client.evaluate('!busy&&!!workflowData.visual_reviews[selectedVersion().id]'),'exact local visual record');
 assert.equal(await client.evaluate('workflowData.visual_reviews[selectedVersion().id].submission_approved'),false);
 await client.evaluate("$('recovery-autosave').checked=true;$('recovery-autosave').dispatchEvent(new Event('change'));$('comment-text').value='SYNTHETIC OPT-IN AUTOSAVE';updateComposer()");
 await until(()=>client.evaluate("!busy&&recoverySnapshot.memo.text==='SYNTHETIC OPT-IN AUTOSAVE'"),'explicit opt-in automatic checkpoint');
 await client.evaluate("$('recovery-autosave').checked=false;$('recovery-autosave').dispatchEvent(new Event('change'))");
 steps.push('Batch: current-note hash status + local evidence upload + exact-result visual log (NOT submission consent) + opt-in autosave');
 }assert.deepEqual(errors,[]);host.stdin.write('STOP\n');
 await new Promise((resolve,reject)=>{if(host.exitCode!==null){host.exitCode===0?resolve():reject(new Error(stderr));return}const timer=setTimeout(()=>reject(new Error('Owned synthetic Tk host did not stop')),15000);host.once('exit',code=>{clearTimeout(timer);code===0?resolve():reject(new Error(stderr))})});
 const tk=JSON.parse(await readFile(join(lab,'SYNTHETIC-TK-WORKFLOW.json'),'utf8'));assert.equal(tk.source_unchanged,true);assert.equal(tk.native_mocked,!nativeExe);assert.equal(tk.schema,schema);
 assert.equal(tk.actual_SDK_or_model_called,false);assert.equal(tk.agent_model_mocked,agentFlow);assert.equal(tk.agent_flow_requested,agentWorkflow);assert.equal(tk.source_reference_copy,!!referenceToy);assert.equal(tk.agent_flow_exercised,agentFlow);
 if(agentFlow)assert.deepEqual(tk.agent_calls,{discovery:2,metadata:1,profile:1,chat:1});else assert.equal(tk.agent_calls,null);
 const output=nativeExe?lab:join(root,'work','gui-check-'+Date.now()+'-'+randomUUID().slice(0,8));await mkdir(output,{recursive:true});await writeFile(join(output,nativeExe?'TOY-ONLY-NATIVE-GUI-REPORT.json':'continuous-gui-workflow.json'),JSON.stringify({passed:true,fixture_only:true,schema,source_reference_copy:!!referenceToy,source_fixture_kind:referenceToy?'exclusive_SHA_pinned_synthetic_reference_copy':nativeExe?'new_native_authored_synthetic':'synthetic_package',native_mocked:!nativeExe,agent_model_mocked:agentFlow,agent_flow_requested:agentWorkflow,agent_flow_exercised:agentFlow,actual_SDK_or_model_called:false,agent_calls:tk.agent_calls,executable:nativeExe,chooser_confirm_mocked:true,real_tk_callbacks:true,real_http_and_edge:true,steps,source_unchanged:true,human_visual_review_still_required:true},null,2));
 if(!nativeExe)console.log('Artifacts:',output);
 console.log(nativeExe?'PASS: actual candidate Native GUI on NEW unsigned TOY; '+(agentWorkflow?'fixed MOCK agent draft through real review/apply; NO SDK/model; ':'')+'chooser/confirm supplied by fixture; '+(referenceToy?'SHA-pinned reference SOURCE COPY, not newly authored; ':'')+lab:'PASS: continuous synthetic Tk→prepare→fake agent/context/draft import→real Edge recovery→proposal→consent→apply→compare→verified downloads; SDK/model/Native MOCKED');
} finally {
 if(client)await client.call('Browser.close').catch(()=>{});
 if(browser&&browser.exitCode===null){await Promise.race([new Promise(resolve=>browser.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,3000))]);if(browser.exitCode===null)browser.kill()}
 if(host.exitCode===null){host.stdin.write('STOP\n');await Promise.race([new Promise(resolve=>host.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,15000))]);if(host.exitCode===null&&!nativeExe)host.kill()}
 lines.close();if(!nativeExe)await rm(lab,{recursive:true,force:true,maxRetries:5,retryDelay:200});else console.log('Native TOY evidence retained:',lab);
}
