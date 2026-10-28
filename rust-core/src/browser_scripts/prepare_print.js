const removed = { toolbarTop: false, toolbarBottom: false, containers: 0 };

const toolbarTop = document.querySelector('.toolbar_top');
if (toolbarTop) {
    toolbarTop.remove();
    removed.toolbarTop = true;
}

const toolbarBottom = document.querySelector('.toolbar_bottom');
if (toolbarBottom) {
    toolbarBottom.remove();
    removed.toolbarBottom = true;
}

document.querySelectorAll('.document_scroller').forEach((element) => {
    element.setAttribute('data-scribd-print-root', 'true');
    element.style.position = 'static';
    element.style.top = 'auto';
    element.style.bottom = 'auto';
    element.style.left = 'auto';
    element.style.right = 'auto';
    element.style.overflow = 'visible';
    element.style.maxHeight = 'none';
    element.style.height = 'auto';
    element.style.margin = '0';
    element.style.padding = '0';
    removed.containers += 1;
});

return removed;
