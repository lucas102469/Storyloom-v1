const {test} = require('node:test');
const assert = require('node:assert/strict');
const {changedSentences} = require('../static/manuscript-diff.js');
const changed = (before, after) => changedSentences(before, after)
  .map(range => after.slice(range.start, range.end));

test('highlights the full revised sentence including shared Korean endings', () => {
  assert.deepEqual(changed(
    '그는 도시 전체를 지배했다. 밤은 조용했다.',
    '그는 작은 회사만 감시했다. 밤은 조용했다.',
  ), ['그는 작은 회사만 감시했다.']);
});
test('insertions do not mark subsequent unchanged sentences and deletion marks nothing', () => {
  assert.deepEqual(changed('문이 열렸다. 그는 들어갔다.',
    '문이 열렸다. 바람이 불었다. 그는 들어갔다.'), ['바람이 불었다.']);
  assert.deepEqual(changed('문이 열렸다. 바람이 불었다. 그는 들어갔다.',
    '문이 열렸다. 그는 들어갔다.'), []);
});
test('repeated sentences are matched in order rather than by character fragments', () => {
  assert.deepEqual(changed('그랬다. 멈췄다. 그랬다.',
    '그랬다. 달렸다. 그랬다.'), ['달렸다.']);
});
test('quotes and multiple changed sentences are included completely', () => {
  assert.deepEqual(changed('"잡았다." 그는 웃었다.\n날이 밝았다.',
    '"놓쳤다." 그는 울었다.\n날이 밝았다.'), ['"놓쳤다."', '그는 울었다.']);
});
test('paragraph spacing and sentence whitespace do not count as content edits', () => {
  assert.deepEqual(changed('문이 열렸다.\n\n그는 들어갔다.',
    '문이 열렸다.  그는 들어갔다.'), []);
});
test('empty and identical manuscripts are safe', () => {
  assert.deepEqual(changed('', ''), []);
  assert.deepEqual(changed('', '새 문장이다.'), ['새 문장이다.']);
  assert.deepEqual(changed('같은 문장이다.', '같은 문장이다.'), []);
});

test('packed comparison preserves every match from the original LCS algorithm', () => {
  function reference(before, after) {
    const segmenter = new Intl.Segmenter('ko', {granularity: 'sentence'});
    function split(text) {
      return text.split('\n').flatMap(line => [...segmenter.segment(line)]
        .map(item => item.segment.trim()).filter(Boolean));
    }
    const a = split(before), b = split(after);
    const rows = Array.from({length: a.length + 1}, () => new Uint32Array(b.length + 1));
    for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--)
      rows[i][j] = a[i] === b[j] ? rows[i+1][j+1]+1 : Math.max(rows[i+1][j],rows[i][j+1]);
    const same = new Set();
    let i=0,j=0;
    while(i<a.length&&j<b.length) {
      if(a[i]===b[j]) {same.add(j);i++;j++;}
      else if(rows[i+1][j]>=rows[i][j+1]) i++;
      else j++;
    }
    return b.filter((_,index)=>!same.has(index));
  }
  let seed=42;
  const random=n=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed%n;};
  const vocabulary=['문이 열렸다.','그랬다.','달렸다.','"잡았다."','멈췄다.'];
  for(let trial=0;trial<400;trial++) {
    const make=()=>Array.from({length:random(25)},()=>vocabulary[random(vocabulary.length)]).join(' ');
    const before=make(),after=make();
    assert.deepEqual(changed(before,after),reference(before,after));
  }
});
