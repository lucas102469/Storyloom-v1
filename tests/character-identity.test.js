const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');
const context={};vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function reflectedSettingFacts('),source.indexOf('function normalizeManuscriptBreaks(')),context);
const before={kind:'characters',name:'문지기',fact:'문지기는 정원을 지킨다.'};
const reveal={kind:'characters',name:'박문수',previous_name:'문지기',fact:'실명이 밝혀졌다.',quote:'문지기의 이름은 박문수였다.'};
test('revealed name attaches older facts to the same card while historical state stays unchanged',()=>{
 const historical={records:{1:{findings:[before]}}};
 const current={official:{'characters:0':{text:'## 박문수'}},records:{1:{findings:[before]},2:{findings:[reveal]}}};
 assert.equal(context.reflectedSettingFacts(current,'characters')[0].name,'박문수');
 assert.equal(context.reflectedSettingFacts(historical,'characters')[0].name,'문지기');
 assert.equal(before.name,'문지기');
});
test('co-occurrence and conflicting names never merge characters',()=>{
 const state={records:{1:{findings:[before]},2:{findings:[{...reveal,quote:'문지기와 박문수가 같이 왔다.'}]}}};
 assert.equal(context.reflectedSettingFacts(state,'characters')[0].name,'문지기');
 state.records[2].findings=[reveal,{...reveal,name:'김철수',quote:'문지기의 이름은 김철수였다.'}];
 assert.equal(context.reflectedSettingFacts(state,'characters')[0].name,'문지기');
});
