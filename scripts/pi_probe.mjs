// One-shot, no-model SDK/JSONL probe. Never imports the CLI startup entry.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {syncBuiltinESMExports} from 'node:module';
import net from 'node:net';
import tls from 'node:tls';
import http from 'node:http';
import https from 'node:https';
import http2 from 'node:http2';
import dns from 'node:dns';
import dgram from 'node:dgram';
import childProcess from 'node:child_process';

const attempts={network:0,process:0,model:0};
const denyNetwork=()=>{attempts.network++;throw new Error('Probe network denied')};
const denyProcess=()=>{attempts.process++;throw new Error('Probe subprocess denied')};
const denyModel=()=>{attempts.model++;throw new Error('Probe model denied')};
net.Socket.prototype.connect=denyNetwork;net.Server.prototype.listen=denyNetwork;
net.connect=denyNetwork;net.createConnection=denyNetwork;tls.connect=denyNetwork;
http.request=denyNetwork;http.get=denyNetwork;https.request=denyNetwork;https.get=denyNetwork;
http2.connect=denyNetwork;dgram.createSocket=denyNetwork;globalThis.fetch=denyNetwork;
for(const key of Object.keys(dns)){if(/^(lookup|resolve|reverse)/.test(key)&&typeof dns[key]==='function')dns[key]=denyNetwork}
for(const key of Object.keys(dns.promises)){if(/^(lookup|resolve|reverse)/.test(key)&&typeof dns.promises[key]==='function')dns.promises[key]=denyNetwork}
for(const key of ['spawn','spawnSync','exec','execSync','execFile','execFileSync','fork'])childProcess[key]=denyProcess;
syncBuiltinESMExports();
// Reserve stdout for the single fixed response, not library diagnostics.
console.log=()=>{};console.info=()=>{};console.warn=()=>{};console.error=()=>{};
let command,session,stage='request';
let success=false,sessionFile=null,sdkVersion=null;
try{
 let input=Buffer.alloc(0);
 for await(const chunk of process.stdin){input=Buffer.concat([input,chunk]);if(input.length>4096)throw new Error('Probe input limit')}
 const frames=input.toString('utf8').split('\n');
 if(frames.length!==2||frames[1]!==''||!frames[0])throw new Error('Single LF frame required');
 command=JSON.parse(frames[0].endsWith('\r')?frames[0].slice(0,-1):frames[0]);
 if(!command||Array.isArray(command)||Object.keys(command).sort().join(',')!=='id,nonce,type'||command.type!=='sdk_probe'||!/^p-[a-f0-9]{16}$/.test(command.id)||!/^[a-f0-9]{32}$/.test(command.nonce))throw new Error('Closed probe schema');
 stage='sdk_import';
 const entry=process.argv[2];
 if(process.argv.length!==3||!path.isAbsolute(entry))throw new Error('Fixed absolute SDK entry required');
 const sdk=await import(pathToFileURL(entry).href);
 if(!['1.2.0','1.3.0','1.4.0'].includes(sdk.VERSION))throw new Error('Unsupported SDK version');
 sdkVersion=sdk.VERSION;
 stage='sdk_session';
 const cwd=process.cwd(),agentDir=path.join(cwd,'profile');
 fs.mkdirSync(agentDir,{recursive:true});
 const modelRuntime=await sdk.ModelRuntime.create({authPath:path.join(agentDir,'auth.json'),modelsPath:null,modelsStorePath:path.join(agentDir,'models-cache.json'),allowModelNetwork:false,refreshOnCreate:false});
 for(const key of ['stream','complete','streamSimple','completeSimple','streamDeferred','fetchDeferred','cancelDeferred','generateImages','classify'])modelRuntime[key]=denyModel;
 const manager=sdk.SessionManager.inMemory(cwd);
 const loader={getExtensions:()=>({extensions:[],errors:[],runtime:sdk.createExtensionRuntime()}),getSkills:()=>({skills:[],diagnostics:[]}),getPrompts:()=>({prompts:[],diagnostics:[]}),getThemes:()=>({themes:[],diagnostics:[]}),getAgentsFiles:()=>({agentsFiles:[]}),getSystemPrompt:()=>('Transport probe only; no prompts or tools.'),getSystemPromptSource:()=>undefined,getAppendSystemPrompt:()=>[],getAppendSystemPromptSources:()=>[],extendResources:()=>{},reload:async()=>{}};
 ({session}=await sdk.createAgentSession({cwd,agentDir,modelRuntime,resourceLoader:loader,settingsManager:sdk.SettingsManager.inMemory({retry:{enabled:false},compaction:{enabled:false}}),sessionManager:manager,noTools:'all',tools:[],customTools:[]}));
 if(session.getActiveToolNames().length!==0||session.messages.length!==0||session.isStreaming)throw new Error('Unexpected session activity');
 sessionFile=manager.getSessionFile()??null;
 if(sessionFile!==null)throw new Error('Persistent session not allowed');
 stage='sdk_dispose';session.dispose();session=undefined;
 if(Object.values(attempts).some(value=>value!==0))throw new Error('Forbidden attempt');
 success=true;stage='passed';
}catch{
 success=false;
}finally{
 if(session){try{session.dispose()}catch{success=false;stage='sdk_dispose'}}
}
// Include delayed initialization work before attesting zero attempts/clean exit.
process.once('beforeExit',()=>{
 if(Object.values(attempts).some(value=>value!==0)){success=false;stage='forbidden_attempt'}
 const response={type:'sdk_probe_result',id:command?.id??null,nonce:command?.nonce??null,success,stage,sdk_version:sdkVersion,node_version:process.versions.node,tools:[],message_count:0,streaming:false,session_file:sessionFile,attempts,authenticated:false,connected:false};
 fs.writeSync(1,JSON.stringify(response)+'\n');process.exitCode=success?0:1;
});
