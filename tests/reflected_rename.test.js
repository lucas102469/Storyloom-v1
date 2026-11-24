const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {parseSettingRecords}=require('../static/plot-view.js');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');
const context={parseSettingRecords};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function reflectedFactsForDocument('),source.indexOf('function documentContent(')),context);
const old='## 강진혁\n역할: 주인공\n성격: 냉철하다.\n목표: 정상에 선다.\n\n## 박 씨\n역할: 채무 업자\n설명: 정보를 팔았다.';
const state={official:{'characters:0':{text:old}}};
const facts=[{name:'강진혁',fact:'강진혁은 신분을 바꿨다.',role:'주인공',field:'목표'}];
test('renamed preview merges old chapter facts into the new character without mutating records',()=>{
 const before=JSON.stringify({state,facts});
 const result=context.reflectedFactsForDocument(state,'characters',old.replace('## 강진혁','## 지진라'),facts);
 assert.equal(result[0].name,'지진라');
 assert.equal(result[0].fact,'지진라는 신분을 바꿨다.');
 assert.equal(JSON.stringify({state,facts}),before);
 assert.equal(context.reflectedFactsForDocument(state,'characters',old,facts),facts);
});
test('adding a separate character leaves the original facts alone',()=>{
 const result=context.reflectedFactsForDocument(state,'characters',old+'\n\n## 지진라\n역할: 친구',facts);
 assert.equal(result,facts);
});
test('ambiguous identical cards are never guessed to be a rename',()=>{
 const text='## 가람\n역할: 친구\n\n## 나람\n역할: 친구';
 const result=context.reflectedFactsForDocument({official:{'characters:0':{text}}},'characters',
  text.replace('## 가람','## 다람').replace('## 나람','## 라람'),[{name:'가람',fact:'가람이 도왔다.'}]);
 assert.equal(result[0].name,'가람');
});
