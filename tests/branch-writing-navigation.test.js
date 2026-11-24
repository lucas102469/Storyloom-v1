const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('next chapter action routes an unmodified branch to its starting chapter without sending a writing request',async()=>{
 const source=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');
 const start=source.indexOf('async function writeNextEpisode(){'),end=source.indexOf('const functionalSidebar=',start);
 const input={},calls=[];
 const ctx={safeMove(){},project:{state:{branch_origin:{episode:2,revision_required:true}}},
  $:()=>input,select:async name=>calls.push(name),notice:message=>calls.push(message),send:()=>{throw Error('must not generate a later chapter');}};
 vm.createContext(ctx);vm.runInContext(source.slice(start,end),ctx);await ctx.writeNextEpisode();
 assert.equal(input.value,2);assert.equal(calls[0],'episode');assert.ok(calls[1].includes('먼저 수정하고 확정'));
});
