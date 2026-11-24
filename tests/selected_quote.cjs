const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');const line=source.split('\n').find(line=>line.startsWith('function syncSelectedQuote()'));
const elements={editor:{value:'앞 문장. 선택한 문장. 뒤 문장.',selectionStart:6,selectionEnd:13},editPanel:{hidden:false},selectedQuotePanel:{},selectedQuote:{},selectedQuoteLabel:{}};
const ctx=vm.createContext({$:id=>elements[id],editKey:'episode:1'});vm.runInContext(line,ctx);vm.runInContext('syncSelectedQuote()',ctx);assert.equal(elements.selectedQuote.textContent,elements.editor.value.slice(6,13));assert.equal(elements.selectedQuotePanel.hidden,false);
elements.editor.selectionStart=elements.editor.selectionEnd;vm.runInContext('syncSelectedQuote()',ctx);assert.equal(elements.selectedQuote.textContent,'');assert.equal(elements.selectedQuotePanel.hidden,true);
elements.editor.selectionStart=6;elements.editPanel.hidden=true;vm.runInContext('syncSelectedQuote()',ctx);assert.equal(elements.selectedQuotePanel.hidden,true);
console.log('PASS: selected text appears automatically and clears when selection collapses or editor closes');
