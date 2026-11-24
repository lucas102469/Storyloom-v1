const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/studio-ui.js','utf8');
const start=source.indexOf('function openLengthDialog('),end=source.indexOf('async function performLengthAction(',start);
assert.ok(start>=0&&end>start);
function setup(draft=null){
 const elements={};let updates=0;
 const context={project:{state:{settings:{target_chars:4000,total_episodes:null},drafts:draft?{'settings:0':draft}:{},length_setup_pending:!draft}},busy:false,resumeFirstEpisode:null,
  $:id=>elements[id]||(elements[id]={value:'',replaceChildren(){},removeAttribute(){},showModal(){this.open=true;}}),
  safeMove(){},formatEpisodeTotal:n=>n==null?'총 회차 자유':'총 '+n+'화',updateLengthDifference(){updates++;}};
 vm.createContext(context);vm.runInContext(source.slice(start,end),context);
 return {context,elements,updates:()=>updates};
}
const initial=setup();assert.doesNotThrow(()=>initial.context.openLengthDialog());
assert.equal(initial.elements.lengthDialog.open,true);assert.equal(initial.elements.targetChars.value,4000);
assert.equal(initial.elements.totalEpisodes.value,'');assert.equal(initial.elements.lengthRecommendation.hidden,true);
assert.equal(initial.updates(),1);
const edited=setup({text:JSON.stringify({target_chars:3000,total_episodes:10})});
assert.doesNotThrow(()=>edited.context.openLengthDialog());assert.equal(edited.elements.targetChars.value,3000);
assert.equal(edited.elements.totalEpisodes.value,10);
const busy=setup();busy.context.busy=true;assert.throws(()=>busy.context.openLengthDialog(),/현재 작업이 끝난 뒤/);
console.log('PASS: length dialog opens without removed recommendation variables and preserves existing values');
