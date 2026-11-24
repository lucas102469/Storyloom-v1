const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('first chapter starts the story while later chapters continue confirmed events',()=>{
 const source=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');
 const start=source.indexOf('function episodeWritingRequest('),end=source.indexOf('async function writeNextEpisode',start);
 const ctx={};vm.createContext(ctx);vm.runInContext(source.slice(start,end),ctx);
 assert.ok(ctx.episodeWritingRequest(1).includes('첫 장면부터 1화를 작성'));
 assert.ok(!ctx.episodeWritingRequest(1).includes('직전 확정 회차'));
 assert.ok(ctx.episodeWritingRequest(2).startsWith('2화를 이어 써줘.'));
 assert.ok(ctx.episodeWritingRequest(2).includes('직전 확정 회차'));
 assert.ok(!ctx.episodeWritingRequest(2).includes('작가 지침'));
});
