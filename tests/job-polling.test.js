const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const core=fs.readFileSync(path.join(__dirname,'../static/workbench.js'),'utf8');
const ui=fs.readFileSync(path.join(__dirname,'../static/studio-ui.js'),'utf8');
test('running polls skip usage requests and completion keeps project reload',async()=>{
 const elements=new Map(),calls=[];let status='running',loads=0,polls=0;
 const ctx={pid:'p',activeJob:'j',pending:{},project:{state:{}},
  $:id=>{if(!elements.has(id))elements.set(id,{hidden:false,textContent:''});return elements.get(id);},
  api:async url=>{calls.push(url);return {status,progress:'작업 중',elapsed:1};},
  schedulePoll:()=>polls++,notice(){},lock(){},storage:k=>k,local:{removeItem(){}},
  load:async()=>{loads++;},refreshUsage:async()=>{throw Error('poll must not fetch usage separately');},
  account:{logged_in:true}};
 vm.createContext(ctx);
 vm.runInContext(core.slice(core.indexOf('async function poll(){'),core.indexOf('async function send(')),ctx);
 vm.runInContext(ui.slice(ui.indexOf('const corePoll=poll;'),ui.indexOf('const renderAtScale=render;')),ctx);
 await ctx.poll();await ctx.poll();
 assert.equal(polls,2);assert.equal(loads,0);
 status='done';await ctx.poll();
 assert.equal(loads,1);assert.equal(ctx.activeJob,null);
 assert.deepEqual(calls,['/projects/p/jobs/j','/projects/p/jobs/j','/projects/p/jobs/j']);
});
