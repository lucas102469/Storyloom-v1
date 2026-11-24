// Offline browser-state tests. No browser profile, localhost or API connection.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element{
 constructor(){this.value='';this.children=[];this.hidden=false;this.checked=true;this.textContent='';this.dataset={};}
 append(...nodes){this.children.push(...nodes);} prepend(...nodes){this.children.unshift(...nodes);}
 replaceChildren(...nodes){this.children=[...nodes];} get firstChild(){return this.children[0];}
 addEventListener(){} showModal(){this.open=true;} close(){this.open=false;}
}
const elements=new Map(),saved=new Map();let quotaFailure=false,timers=0;
const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const localStorage={getItem:key=>saved.get(key)??null,setItem:(key,value)=>{if(quotaFailure)throw Error('quota');saved.set(key,value);},removeItem:key=>saved.delete(key)};
const context=vm.createContext({console,document:{getElementById:element,createElement:()=>new Element(),querySelectorAll:()=>[]},window:{localStorage,addEventListener(){}},
 localStorage,setTimeout:()=>++timers,clearTimeout(){},crypto:{randomUUID:()=> 'test-request-001'},
 fetch:async path=>({ok:true,status:200,json:async()=>path.includes('/auth/session')?{required:true,logged_in:false}:[]})});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../static/workbench.js'),'utf8'),context);
const run=code=>vm.runInContext(code,context);
async function main(){
 await new Promise(resolve=>setImmediate(resolve));
 run("pid='project-a';project={title:'A',jobs:[],state:{selected:'plan',episode:1,messages:[],official:{},drafts:{'plan:0':{text:'original',version:1,status:'draft'}},settings:{target_chars:4000,total_episodes:30}}};");
 run('beginEdit();');element('editor').value='unsaved';run('rememberEdit();');
 assert.throws(()=>run('beginEdit();'),/저장 전 편집/);assert.equal(element('editor').value,'unsaved');
 console.log('PASS: reopening editor preserves unsaved input');
 saved.set('broken','{invalid');assert.equal(run("readLocalJSON('broken')"),null);
 console.log('PASS: corrupt local metadata does not crash');
 quotaFailure=true;const before=timers;run('rememberEdit();');assert.ok(timers>before);assert.match(element('editStatus').textContent,/실패/);assert.equal(run('dirty'),true);
 console.log('PASS: local quota failure retains input and schedules server autosave');
 quotaFailure=false;run("dirty=false;editKey=null;project.state.selected='world';");
 assert.throws(()=>run("beginEdit({key:'plan:0',text:'recovery',base_version:1})"),/먼저 선택/);assert.equal(run('editKey'),null);
 console.log('PASS: mismatched recovery does not corrupt editor selection');
 element('chatInput').value='message from another project';run("pid='project-b';render();");
 assert.equal(element('chatInput').value,'');console.log('PASS: switching projects does not carry old chat input');
 // Simulate a second UI event clearing global pending while POST is in flight.
 let resolvePost;
 context.fetch=path=>path.endsWith('/chat')?new Promise(resolve=>{resolvePost=()=>resolve({ok:true,status:202,json:async()=>({ok:true})});}):Promise.resolve({ok:true,status:200,json:async()=>({})});
 run("pid='project-race';dirty=false;pending={request_id:'stable-request-001',text:'request'};load=async()=>{};");
 const sending=run('send()');
 run('pending=null;');resolvePost();await sending;
 assert.equal(run('activeJob'),'stable-request-001');console.log('PASS: in-flight send keeps its request ID despite later UI events');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
