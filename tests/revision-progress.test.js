const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('progress separates phase, volume and chapter count without breaking a sentence across lines',()=>{
 const source=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');
 const start=source.indexOf('function revisionProgressView('),end=source.indexOf('function showRevisionImpact(',start);
 const ctx={};vm.createContext(ctx);vm.runInContext(source.slice(start,end),ctx);
 const view=ctx.revisionProgressView({phase:'1화 분량 조정 중 · 현재 3,699자 / 목표 4,500~5,000자',checked:0,total:2});
 assert.equal(view.phase,'1화 분량 조정 중');assert.equal(view.current,'3,699자');
 assert.equal(view.target,'4,500~5,000자');assert.equal(view.count,'회차 진행 0 / 2화');
 const other=ctx.revisionProgressView({phase:'일관성 재점검 중'});
 assert.equal(other.current,null);assert.equal(other.phase,'일관성 재점검 중');
 assert.ok(!source.includes("const rules=button('작가 지침'"));
});
