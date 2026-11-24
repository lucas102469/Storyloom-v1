const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={window:{},document:{body:{classList:{add(){}}}},Map,URL,URLSearchParams,Response,
 location:{origin:'http://127.0.0.1:5001',search:'?stage=episode'}};
vm.createContext(context);vm.runInContext(fs.readFileSync('static/studio-preview.js','utf8'),context);
const source=fs.readFileSync('static/studio-ui.js','utf8');
const start=source.indexOf('async function refreshUsage(){'),end=source.indexOf("$('usageRefresh').onclick",start);
const elements={};const progress={};let max;
Object.defineProperty(progress,'max',{get:()=>max,set:value=>{assert.ok(Number.isFinite(value),'progress max must be finite');max=value;}});
elements.usageBar=progress;context.busy=false;context.$=id=>elements[id]||(elements[id]={});
context.api=async path=>(await context.window.fetch('/api'+path)).json();
vm.runInContext(source.slice(start,end),context);
(async()=>{
 const usage=await context.api('/usage');
 assert.equal(usage.personal_used,0);assert.equal(usage.personal_limit,60);assert.equal(usage.personal_remaining,60);
 assert.equal(usage.total_used,0);assert.equal(usage.total_limit,400);assert.equal(usage.total_remaining,400);
 assert.equal(usage.limit,400);assert.equal(usage.remaining,60);
 await context.refreshUsage();assert.equal(progress.max,60);assert.equal(progress.value,0);
 assert.equal(elements.usageCount.textContent,'0 / 60회');
 assert.equal((await context.window.fetch('/api/projects/preview/chat',{method:'POST'})).status,403);
 console.log('PASS: public preview feeds finite personal/total usage values to the existing UI and remains read-only');
})().catch(e=>{console.error(e);process.exitCode=1});
