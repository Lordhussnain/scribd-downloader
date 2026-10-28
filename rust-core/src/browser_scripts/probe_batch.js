const pageNumbers = arguments[0];
const manager = window.docManager;

if (!manager || !manager.pages) {
    return {
        supported: false,
        total: pageNumbers.length,
        ready: 0,
        totalImages: 0,
        pendingImages: 0,
        brokenImages: 0,
        pages: []
    };
}

const nowMs = Date.now();
const details = [];
let ready = 0;
let totalImages = 0;
let pendingImages = 0;
let brokenImages = 0;

function lazySource(image) {
    return (
        image.getAttribute('data-src') ||
        image.getAttribute('data-lazy-src') ||
        image.getAttribute('data-original') ||
        ''
    );
}

for (const pageNum of pageNumbers) {
    const page = manager.pages[pageNum];
    const detail = {
        pageNum,
        ready: false,
        reason: '',
        images: 0,
        pendingImages: 0,
        brokenImages: 0,
        unstartedImages: 0
    };

    if (!page) {
        detail.reason = 'page object missing';
        details.push(detail);
        continue;
    }

    try {
        if (
            !page.innerPageElem &&
            !page.loadHasStarted &&
            typeof page.load === 'function'
        ) {
            page.load();
        }
    } catch (error) {
        detail.reason = `load failed: ${error}`;
    }

    if (!page.innerPageElem) {
        if (!detail.reason) {
            detail.reason = 'page content not loaded yet';
        }
        details.push(detail);
        continue;
    }

    try {
        // Call Scribd's render/image hooks once for each
        // inner element. Calling them on every 50 ms tick
        // (the old async implementation) could repeatedly
        // reset image requests on some documents.
        if (
            page.__scribdDownloaderDisplayedElem !==
            page.innerPageElem
        ) {
            if (typeof page.display === 'function') {
                page.display();
            }
            page.__scribdDownloaderDisplayedElem =
                page.innerPageElem;
            page.__scribdDownloaderImagesElem = null;
        }

        if (
            page.__scribdDownloaderImagesElem !==
                page.innerPageElem &&
            !page._imagesTurnedOn &&
            typeof page.turnOnImages === 'function'
        ) {
            page.turnOnImages();
            page.__scribdDownloaderImagesElem =
                page.innerPageElem;
            page.__scribdDownloaderImagesAttemptAt = nowMs;
        }
    } catch (error) {
        detail.reason = `display failed: ${error}`;
        details.push(detail);
        continue;
    }

    let images = Array.from(
        page.innerPageElem.querySelectorAll('img')
    );
    let unstarted = images.filter((image) => (
        !(image.currentSrc || image.src) &&
        lazySource(image)
    ));

    // If Scribd left lazy images unstarted, retry its
    // hook occasionally (not on every poll, which could
    // restart requests that are already in flight).
    if (
        unstarted.length &&
        typeof page.turnOnImages === 'function' &&
        nowMs -
            (page.__scribdDownloaderImagesAttemptAt || 0) >=
            2000
    ) {
        try {
            page.turnOnImages();
            page.__scribdDownloaderImagesElem =
                page.innerPageElem;
            page.__scribdDownloaderImagesAttemptAt = nowMs;
            images = Array.from(
                page.innerPageElem.querySelectorAll('img')
            );
            unstarted = images.filter((image) => (
                !(image.currentSrc || image.src) &&
                lazySource(image)
            ));
        } catch (error) {
            detail.reason = `image load failed: ${error}`;
        }
    }

    detail.images = images.length;
    detail.unstartedImages = unstarted.length;
    for (const image of images) {
        if (!image.complete) {
            detail.pendingImages += 1;
        } else if (
            image.naturalWidth === 0 &&
            (image.currentSrc || image.src)
        ) {
            detail.brokenImages += 1;
        }
    }

    totalImages += detail.images;
    pendingImages += detail.pendingImages;
    brokenImages += detail.brokenImages;

    detail.ready =
        detail.pendingImages === 0 &&
        detail.unstartedImages === 0 &&
        !detail.reason;
    if (detail.ready) {
        ready += 1;
    } else if (!detail.reason) {
        detail.reason = detail.unstartedImages
            ? `${detail.unstartedImages} image(s) have not started loading`
            : `${detail.pendingImages} image(s) still loading`;
    }
    details.push(detail);
}

return {
    supported: true,
    total: pageNumbers.length,
    ready,
    totalImages,
    pendingImages,
    brokenImages,
    pages: details
};
