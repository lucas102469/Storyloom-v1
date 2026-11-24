const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/studio-ui.js'),'utf8');

test('an accepted chapter is ready for settings review while the next batch chapter remains a draft',()=>{
 const state={official:{'episode:3':{version:33}},drafts:{'episode:4':{version:44}},
  records:{3:{version:33,setting_review:'organize'}},episode_batch:{draft_versions:{3:33,4:44},status:'done'}};
 const start=source.indexOf('const pendingReview=Object.entries(state.records||{})');
 const end=source.indexOf('const batch=state.episode_batch;',start);
 const result=vm.runInNewContext(source.slice(start,end)+'pendingReview[0]',{state});
 assert.equal(result,'3');
});

test('settings review processes only the requested chapter even when several chapters await review',async()=>{
 const calls=[],state={official:{'episode:3':{version:33},'episode:4':{version:44}},drafts:{},
  records:{3:{version:33,setting_review:'organize'},4:{version:44,setting_review:'organize'}}};
 const element=()=>({open:false,append(){},setAttribute(){},addEventListener(){},showModal(){this.open=true;},close(){this.open=false;},remove(){}});
 const ctx={pid:'p',project:{state},busy:false,documentView:'preview',key:()=> 'episode:99',safeMove(){},
  reviewingEpisodeSettings:new Map(),episodeSettingsErrors:new Map(),deferredEpisodeSettings:new Set(),
  openedEpisodeResults:new Set(),node:element,document:{body:element()},load:async()=>{},
  api:async(url,payload)=>{calls.push(payload);state.records[payload.episode].setting_review='done';}};
 vm.createContext(ctx);
 const start=source.indexOf('async function runEpisodeSettingsReview('),end=source.indexOf('function appendEpisodeSettingsReview(',start);
 vm.runInContext(source.slice(start,end),ctx);
 await ctx.runEpisodeSettingsReview(3,'organize');
 assert.deepEqual(calls.map(call=>call.episode),[3]);
 assert.equal(state.records[3].setting_review,'done');
 assert.equal(state.records[4].setting_review,'organize');
});

test('the studio loads the individual review flow without the grouped override',()=>{
 const template=fs.readFileSync(path.join(__dirname,'../templates/studio.html'),'utf8');
 assert.doesNotMatch(template,/episode-review-group\.js/);
 assert.doesNotMatch(source,/episodeReviewGroups|설정 함께 검토하기/);
});
