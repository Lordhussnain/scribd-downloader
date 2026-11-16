const managerPages = window.docManager && window.docManager.pages
    ? Object.values(window.docManager.pages).filter(Boolean).length
    : 0;
return {
    outerPages: document.querySelectorAll('.outer_page').length,
    managerPages,
    readyState: document.readyState,
    title: document.title || ''
};
