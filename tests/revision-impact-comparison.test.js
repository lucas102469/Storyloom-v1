const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
test('impact manuscripts use paragraph formatting and the same side comparison without old tabs',()=>{
 const source=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');
 const start=source.indexOf('function revisionManuscript('),end=source.indexOf('\nfunction hasCleanRevisionImpact',start);
 const node=(tag,text='')=>({tag,textContent:text,children:[],hidden:false,
  append(...children){this.children.push(...children);},prepend(...children){this.children.unshift(...children);},querySelectorAll(){return [];},setAttribute(k,v){this[k]=v;},
  classList:{values:new Set(),contains(k){return this.values.has(k);},toggle(k,on){on?this.values.add(k):this.values.delete(k);}}});
 let formatted=0,highlighted=0,jumps=0;
 const ctx={node,styledButton:(text,callback)=>Object.assign(node('button',text),{onclick:callback}),
  formatManuscriptParagraphs:text=>{formatted++;return text;},richText:text=>Object.assign(node('div',text),{querySelectorAll:()=>text==='after'?[{scrollIntoView(){jumps++;}}]:[]}),
  window:{manuscriptDiff:{highlight(original,revised){highlighted++;assert.equal(original.textContent,'before');assert.equal(revised.textContent,'after');}}}};
 vm.createContext(ctx);vm.runInContext(source.slice(start,end),ctx);
 const body=node('main'),navigation=node('header');ctx.revisionManuscript(body,'before','after',[],navigation);
 const area=body.children[0],button=navigation.children.find(item=>item.textContent==='기존 원고랑 비교'),original=area.children[1].children[0];
 assert.equal(formatted,2);assert.equal(highlighted,1);assert.equal(original.hidden,true);
 assert.equal(button.textContent,'기존 원고랑 비교');button.onclick();
 assert.equal(original.hidden,false);assert.equal(button.textContent,'비교 닫기');
 button.onclick();assert.equal(original.hidden,true);
 assert.ok(!source.slice(start,end).includes('draft-switch'));
 const next=navigation.children.find(item=>item.textContent==='다음 변경 ↓');
 assert.equal(navigation.children[0],next);
 assert.ok(next);next.onclick();next.onclick();assert.equal(jumps,2);
 assert.ok(!area.children[0].children.includes(next),'navigation must stay outside the scrolling body');
});

test('selecting a sidebar document dismisses the review panel before opening that document',async()=>{
 const source=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');
 const helperStart=source.indexOf('function dismissRevisionImpactForNavigation(){');
 const helperEnd=source.indexOf('\nconst impactDecisionLabels=',helperStart);
 const selection=source.match(/select=async function\(name\)\{[^\n]+/)[0];
 const events=[],stored=[];
 let panel={hidden:false,remove(){events.push('dismiss');panel=null;}};
 const input={};
 const ctx={dirty:false,saving:false,project:{state:{revision_impact:{id:'review'}}},pid:'work',impactView:{episode:2},
  $:id=>id==='revisionImpactPanel'?panel:input,
  sessionStorage:{setItem(...args){stored.push(args);}},
  document:{querySelector(){return {classList:{remove(value){events.push(value);}}};}},
  coreSelect:async name=>{events.push(name);},documentView:'preview'};
 vm.createContext(ctx);vm.runInContext(source.slice(helperStart,helperEnd)+'\n'+selection,ctx);
 await ctx.select('characters');
 assert.deepEqual(events,['dismiss','show-revision-impact','characters']);
 assert.equal(ctx.impactView,null);assert.equal(stored[0][0],'storyloom.impact.work:review');
 ctx.dirty=true;await assert.rejects(ctx.select('world'));assert.equal(events.length,3);
});
