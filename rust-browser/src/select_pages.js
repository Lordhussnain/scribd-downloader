// Executed by WebDriver after the document is prepared. The user selects the
// actual rendered document pages in a visible Chrome window, not placeholders.
const expected = arguments[0];
const pages = Array.from(document.querySelectorAll('.outer_page'));
if (pages.length !== expected) return {error: 'The displayed page count changed; cannot safely select pages.'};
if (window.__scribdPageSelection) return {error: 'Page selection is already active.'};
const selected = new Set(pages.map((_, i) => i + 1));
const root = document.createElement('div');
root.id = 'scribd-downloader-selection-controls';
root.style.cssText = 'position:fixed;z-index:2147483647;bottom:12px;left:50%;transform:translateX(-50%);background:#18202f;color:white;padding:12px 16px;border-radius:12px;box-shadow:0 6px 24px #0008;font:14px sans-serif;display:flex;gap:10px;align-items:center;flex-wrap:wrap;max-width:90vw';
const label = document.createElement('span');
const all = document.createElement('button'); all.textContent = 'Select all';
const none = document.createElement('button'); none.textContent = 'Clear selection';
const done = document.createElement('button'); done.textContent = 'Download selected';
const cancel = document.createElement('button'); cancel.textContent = 'Cancel';
for (const b of [all, none, done, cancel]) {
    b.type = 'button'; b.style.cssText = 'padding:8px 10px;border:0;border-radius:6px;cursor:pointer;background:#e5e7eb;color:#18202f;font-weight:600';
}
done.style.background = '#60a5fa';
root.append(label, all, none, done, cancel);
document.body.appendChild(root);
const style = document.createElement('style');
style.id = 'scribd-downloader-selection-style';
style.textContent = '.outer_page[data-sd-selected="yes"]{outline:4px solid #3b82f6!important;outline-offset:2px!important}.outer_page[data-sd-selected="no"]{outline:3px dashed #f97316!important;outline-offset:2px!important}@media print{#scribd-downloader-selection-controls{display:none!important}.outer_page{outline:none!important}}';
document.head.appendChild(style);
const state = {status: 'pending', pages: [], visible: new Set(), cleanup: null};
// Ask the backend to load only pages near the viewport, so users can inspect
// lazily loaded content without loading the entire document into memory.
let observer = null;
if (typeof IntersectionObserver === 'function') {
    observer = new IntersectionObserver(entries => {
        for (const entry of entries) {
            const index = pages.indexOf(entry.target);
            if (index < 0) continue;
            if (entry.isIntersecting) state.visible.add(index + 1);
            else state.visible.delete(index + 1);
        }
    }, {rootMargin: '300px'});
    pages.forEach(page => observer.observe(page));
}
function refresh() {
    pages.forEach((page, index) => page.setAttribute('data-sd-selected', selected.has(index + 1) ? 'yes' : 'no'));
    label.textContent = `${selected.size} / ${pages.length} selected · Blue: keep · Orange: exclude · Click pages to toggle`;
    done.disabled = selected.size === 0;
    done.style.opacity = done.disabled ? '0.5' : '1';
}
function clickPage(event) {
    const page = event.target instanceof Element && event.target.closest('.outer_page');
    const index = pages.indexOf(page);
    if (index < 0) return;
    event.preventDefault(); event.stopPropagation();
    const number = index + 1;
    if (selected.has(number)) selected.delete(number); else selected.add(number);
    refresh();
}
state.cleanup = () => {
    if (observer) observer.disconnect();
    document.removeEventListener('click', clickPage, true);
    pages.forEach(page => page.removeAttribute('data-sd-selected'));
    root.remove(); style.remove();
};
all.onclick = () => { pages.forEach((_, i) => selected.add(i + 1)); refresh(); };
none.onclick = () => { selected.clear(); refresh(); };
done.onclick = () => { if (selected.size) { state.pages = Array.from(selected).sort((a, b) => a - b); state.status = 'done'; } };
cancel.onclick = () => { state.status = 'cancelled'; };
document.addEventListener('click', clickPage, true);
window.__scribdPageSelection = state;
refresh();
return {ok: true};
