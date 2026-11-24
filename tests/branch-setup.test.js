const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
function setup(origin){
 const calls={length:0,guide:0,write:0,send:0,notices:[]};
 const guide={hidden:true,children:[],replaceChildren(){this.children=[];},append(...values){this.children.push(...values);}};
 const context={project:{state:{branch_origin:origin,drafts:{},settings:{}}},pending:null,
  $:id=>id==='approvalGuide'?guide:id==='chatInput'?{value:''}:null,
  openLengthDialog:()=>++calls.length,showApprovalGuide:()=>++calls.guide,
  writeNextEpisode:()=>++calls.write,send:()=>++calls.send,
  node:(tag,text)=>({tag,text}),styledButton:(text,click)=>({text,click}),notice:value=>calls.notices.push(value)};
 vm.createContext(context);
 vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../static/branch-setup.js'),'utf8'),context);
 return {context,calls,guide};
}
test('ordinary stories delegate to every original action',async()=>{
 const {context,calls}=setup(undefined);
 context.openLengthDialog();context.showApprovalGuide();await context.writeNextEpisode();await context.send('3화를 써줘');
 assert.equal(calls.length,1);assert.equal(calls.guide,1);assert.equal(calls.write,1);assert.equal(calls.send,1);
});
test('branch preparation blocks next writing but permits editing the imported chapter',async()=>{
 const {context,calls}=setup({setup_flow:1,setup_complete:false,setup_stage:'review',episode:2});
 await context.writeNextEpisode();await context.send('3화를 써줘');await context.send('2화를 수정해줘');
 assert.equal(calls.write,0);assert.equal(calls.send,1);assert.equal(calls.notices.length,2);
});
test('branch-only guide offers length after review and plan approval before continuation',()=>{
 const {context,guide}=setup({setup_flow:1,setup_complete:false,setup_stage:'length',episode:2});
 context.showApprovalGuide();assert.match(guide.children[1].text,/분량.*회차 구성/);
 context.project.state.branch_origin.setup_stage='plan';context.showApprovalGuide();
 assert.equal(guide.children[1].text,'회차 구성 검토');
});
test('completed branch preparation restores the original actions',async()=>{
 const {context,calls}=setup({setup_flow:1,setup_complete:true,setup_stage:'complete',episode:2});
 context.openLengthDialog();await context.writeNextEpisode();await context.send('3화를 써줘');
 assert.equal(calls.length,1);assert.equal(calls.write,1);assert.equal(calls.send,1);
});
