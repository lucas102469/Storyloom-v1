const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/studio-ui.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../templates/studio.html'),'utf8');
const handlers={};
const input={value:'',selectionStart:0,selectionEnd:0,
 addEventListener(name,fn){handlers[name]=fn;},
 setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;}};
const context={$:()=>input};vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function episodeDigits('),source.indexOf("$('lengthForm').oninput=")),context);
function prevented(value,data,start=value.length,end=start){
 input.value=value;input.selectionStart=start;input.selectionEnd=end;
 let prevented=false;handlers.beforeinput({target:input,data,preventDefault(){prevented=true;}});
 return prevented;
}
assert.equal(prevented('','0'),true);
assert.equal(prevented('1','0'),false);
assert.equal(prevented('10','0',0,1),true);
for(const character of ['-','+','.','e','E','가'])assert.equal(prevented('1',character),true);
for(const [value,expected] of [['0',''],['000',''],['010','10'],['10','10'],['1-0','10'],['','']]){
 input.value=value;input.selectionStart=value.length;
 handlers.input({target:input});assert.equal(input.value,expected);
}
const pattern=html.match(/id="totalEpisodes"[^>]*pattern="([^"]+)"/)[1];
const valid=new RegExp('^(?:'+pattern+')$');
for(const value of ['1','10','999','1000'])assert.ok(valid.test(value));
for(const value of ['0','01','-1','1.5','1e2','1001','10000'])assert.ok(!valid.test(value));
const valuesContext={$:id=>id==='totalEpisodes'?input:{valueAsNumber:4000}};vm.createContext(valuesContext);
vm.runInContext(source.slice(source.indexOf('function lengthValues('),source.indexOf('function updateLengthDifference(')),valuesContext);
input.value='';assert.equal(valuesContext.lengthValues().total_episodes,null);
input.value='10';assert.equal(valuesContext.lengthValues().total_episodes,10);
console.log('Passed positive integer input, no leading zero, paste cleanup, range and optional total checks.');
