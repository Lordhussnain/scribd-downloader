const manager = window.docManager;
if (!manager || !manager.pages) {
    return;
}

for (const pageNum of arguments[0]) {
    const page = manager.pages[pageNum];
    if (!page) {
        continue;
    }

    try {
        page.remove();
    } catch (error) {
        const container = document.getElementById(`outer_page_${pageNum}`);
        if (container) {
            const inner = container.querySelector('.newpage');
            if (inner) {
                inner.remove();
            }
        }
    }
}
