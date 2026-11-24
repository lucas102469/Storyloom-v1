const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require('node:path').join(__dirname, '../static/discover.js'), 'utf8');
function method(name, next) {
  return source.slice(source.indexOf('  function ' + name + '('), source.indexOf('  ' + next));
}
function element() {
  return {hidden: false, dataset: {}, textContent: '', style: {setProperty() {}},
    setAttribute() {}, replaceChildren() {}, focus() {}};
}

test('leaving publication management closes its footer and allows the next public work to open', () => {
  const nodes = new Map();
  const getNode = selector => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  const cover = element();
  cover.querySelector = () => getNode('cover-title');
  nodes.set('.discover-text-cover', cover);
  const detail = element();
  detail.querySelector = selector => {
    // The back button currently has publication-cancel, so this selector is absent.
    if (selector === '[data-discover-action="catalog"]') return null;
    return getNode(selector);
  };
  const catalog = element();
  catalog.querySelector = getNode;
  const reader = element();
  reader.querySelector = getNode;
  const classes = new Set(['discover-publishing']);
  const page = {style: {setProperty() {}}, classList: {remove(name) {classes.delete(name);},
    toggle(name, active) {active ? classes.add(name) : classes.delete(name);}}};
  let removed = 0;
  const episodeList = {querySelectorAll() {return [{remove() {removed++;}}];},
    replaceChildren() {}, append() {}};
  const env = {
    catalog, detail, reader, page, episodeList, publicationBar: {}, genrePicker: {open: true},
    publishPage: {hidden: false, prepend() {}},
    stopDialog: {open: true, close() {this.open = false;}},
    catalogBack: {...element(), dataset: {discoverAction: 'publication-cancel'}},
    selectAll: {checked: true, indeterminate: false}, metadataInput: {checked: true},
    metadataChoice: element(), stopButton: element(), errorMessage: element(), catalogError: element(),
    likeButton: element(), manageButton: element(), engagementError: element(), detailComments: element(),
    collection: 'all', readingRestoreId: 0, published: null,
    saveReadingPosition() {}, updateReadingButton() {}, renderComments() {},
    renderBranchControls() {}, selectDetailTab() {}, paintCoverArtwork() {}, window: {scrollTo() {}},
  };
  vm.createContext(env);
  vm.runInContext(method('finishPublication', 'function returnToProject('), env);
  vm.runInContext(method('showView', 'function episodeButton('), env);
  vm.runInContext(method('openWork', 'function openEpisode('), env);
  env.showView(catalog);
  assert.equal(env.publishPage.hidden, true);
  assert.equal(env.errorMessage.hidden, true);
  assert.equal(env.errorMessage.textContent, '');
  assert.equal(env.catalogBack.dataset.discoverAction, 'catalog');
  assert.equal(classes.has('discover-publishing'), false);
  assert.equal(removed, 1);
  env.openWork(null, {id: 'next-work', title: '다음 작품', author: '작가',
    genres: [], episodes: [], likes: 0, views: 0, status: '연재중'});
  assert.equal(getNode('h1').textContent, '다음 작품');
  assert.equal(env.publishPage.hidden, true);
  assert.equal(detail.hidden, false);
});

test('compact genre picker preserves AND matching, chips, reset and existing ordering', () => {
  const genres = [{value: '판타지', checked: true}, {value: '로맨스', checked: true}];
  const allGenres = {checked: false};
  function card(genre, views, likes) {
    const labels = {};
    return {dataset: {genre, views: String(views), likes: String(likes), status: '연재중', liked: 'true'},
      querySelector(selector) {return labels[selector] ||= {};}};
  }
  const both = card('판타지,로맨스', 100, 3);
  const fantasy = card('판타지', 200, 7);
  const romance = card('로맨스', 20, 12);
  const chips = {children: [], replaceChildren() {this.children = [];}, append(item) {this.children.push(item);}};
  const ordered = [];
  const env = {genres, allGenres, selectedGenreChips: chips, cards: [both, fantasy, romance],
    sort: {value: 'recent'}, status: {value: ''}, collection: 'all', likedCount: {}, empty: {},
    grid: {insertBefore(card) {ordered.push(card);}},
    document: {createTextNode(text) {return {text};}, createElement() {
      return {children: [], append(...items) {this.children.push(...items);}, setAttribute() {}};
    }}};
  vm.createContext(env);
  vm.runInContext(method('renderSelectedGenres', 'let catalogRequest'), env);
  vm.runInContext(method('updateCards', 'function updateCollectionNavigation('), env);
  env.updateCards();
  assert.equal(both.hidden, false);
  assert.equal(fantasy.hidden, true);
  assert.equal(romance.hidden, true);
  assert.equal(chips.children.length, 2);
  chips.children[1].onclick();
  assert.equal(genres[1].checked, false);
  assert.equal(fantasy.hidden, false);
  assert.equal(romance.hidden, true);
  env.updateCards({target: allGenres});
  assert.equal(genres.every(input => !input.checked), true);
  assert.equal(allGenres.checked, true);
  assert.equal(chips.children.length, 0);
  assert.equal([both, fantasy, romance].every(card => !card.hidden), true);
  env.sort.value = 'views';
  ordered.length = 0;
  env.updateCards();
  assert.deepEqual(ordered, [fantasy, both, romance]);
  env.sort.value = 'likes';
  ordered.length = 0;
  env.updateCards();
  assert.deepEqual(ordered, [romance, fantasy, both]);
  assert.equal(both.querySelector('.discover-card-views').textContent, '조회 100');
});

test('browse and interest views keep heading controls without exposing genre filters', () => {
  const target = name => ({append(item) {item.parent = name;}});
  const env = {page: {dataset: {}}, collection: 'all', catalogHeading: target('heading'),
    shelfHeading: target('shelf'), toolbar: target('toolbar'), likedCount: {}, catalogControls: {},
    genrePicker: {open: true}, selectedGenreChips: {}};
  vm.createContext(env);
  vm.runInContext(method('placeCatalogTools', 'function renderSelectedGenres('), env);
  env.placeCatalogTools();
  assert.equal(env.likedCount.parent, 'heading');
  assert.equal(env.catalogControls.parent, 'heading');
  assert.equal(env.genrePicker.hidden, false);
  env.collection = 'liked';
  env.placeCatalogTools();
  assert.equal(env.likedCount.parent, 'heading');
  assert.equal(env.catalogControls.parent, 'heading');
  assert.equal(env.genrePicker.hidden, true);
  env.collection = 'all';
  env.placeCatalogTools();
  assert.equal(env.catalogControls.parent, 'heading');
  assert.equal(env.genrePicker.open, false);
});

test('alternative creation stays at the right of chapter tabs and only when allowed', () => {
  function node(tag) {
    return {tag, className: '', children: [], hidden: false, style: {setProperty() {}},
      get childNodes() {return this.children;},
      append(...items) {this.children.push(...items);},
      setAttribute() {}, remove() {}};
  }
  for (const allowed of [true, false]) {
    const detail = node('section');
    const tab = node('button');
    const form = node('form');
    const headerActions = node('div');
    const meta = {after() {assert.fail('The reader metadata must not gain author actions');}};
    detail.querySelector = selector => {
      if (selector === '[data-discover-action="alternatives"]') return tab;
      if (selector === '.discover-work-meta') return meta;
      if (selector === '.discover-comment-form') return form;
      if (selector === '.discover-detail-actions') return headerActions;
      if (selector === '.alternative-list') return detail.children.find(item => item.className === 'alternative-list');
      return null;
    };
    detail.querySelectorAll = () => [];
    let opened = false;
    const env = {detail, document: {createElement: node}, window: {},
      episodeList: node('ol'), detailComments: node('ul'), published: {id: 'original'},
      openBranchDialog() {opened = true;}};
    vm.createContext(env);
    vm.runInContext(method('renderBranchControls', 'function openBranchDialog('), env);
    vm.runInContext(method('selectDetailTab', 'async function openBranchWork('), env);
    env.renderBranchControls({id: 'original', canManage: true, allowBranches: allowed, branches: []});
    const panel = detail.querySelector('.alternative-list');
    assert.equal(panel.hidden, true);
    const create = headerActions.children.find(item => item.className.includes('alternative-create'));
    assert.equal(Boolean(create), allowed);
    assert.equal(panel.children.some(item => item.className === 'alternative-list-actions'), false);
    env.selectDetailTab('alternatives');
    assert.equal(panel.hidden, false);
    assert.equal(env.episodeList.hidden, true);
    if (create) {
      create.onclick();
      assert.equal(opened, true);
    }
    env.selectDetailTab('episodes');
    assert.equal(panel.hidden, true);
  }
  const template = fs.readFileSync(require('node:path').join(__dirname, '../templates/studio.html'), 'utf8');
  assert.equal(template.includes('discover-manage-open'), false);
  assert.equal(template.includes('id="projectHomePublishBtn"'), true);
});
