const targetIndex = arguments[0];

const pages = Array.from(
    document.querySelectorAll(
        '.outer_page'
    )
);

const target = pages[targetIndex];

if (!target) {
    return null;
}

/*
 * Remove previous isolated-print style.
 */
const oldStyle = document.getElementById(
    'isolated-page-print-style'
);

if (oldStyle) {
    oldStyle.remove();
}

/*
 * Restore all pages before measuring.
 */
pages.forEach((page) => {
    page.style.removeProperty('display');
    page.style.removeProperty('visibility');
    page.style.removeProperty('position');
    page.style.removeProperty('top');
    page.style.removeProperty('left');
    page.style.removeProperty('right');
    page.style.removeProperty('bottom');
    page.style.removeProperty('margin');
    page.style.removeProperty('break-after');
    page.style.removeProperty('page-break-after');
    page.style.removeProperty('break-before');
    page.style.removeProperty('page-break-before');
});

const rect = target.getBoundingClientRect();

const width = Math.ceil(rect.width);
const height = Math.ceil(rect.height);

/*
 * Mark the target instead of relying on nth-child.
 */
pages.forEach((page) => {
    page.removeAttribute(
        'data-export-target'
    );
});

target.setAttribute(
    'data-export-target',
    'true'
);

const style = document.createElement(
    'style'
);

style.id = 'isolated-page-print-style';

style.textContent = `
    @page {
        size: ${width}px ${height}px;
        margin: 0;
    }

    @media print {
        html,
        body {
            width: ${width}px !important;
            height: ${height}px !important;
            min-width: ${width}px !important;
            min-height: ${height}px !important;
            max-width: ${width}px !important;
            max-height: ${height}px !important;

            margin: 0 !important;
            padding: 0 !important;

            overflow: hidden !important;

            -webkit-print-color-adjust:
                exact !important;

            print-color-adjust:
                exact !important;
        }

        .outer_page {
            display: none !important;
        }

        .outer_page[
            data-export-target="true"
        ] {
            display: block !important;
            visibility: visible !important;

            position: absolute !important;

            top: 0 !important;
            left: 0 !important;
            right: auto !important;
            bottom: auto !important;

            width: ${width}px !important;
            height: ${height}px !important;

            min-width: 0 !important;
            min-height: 0 !important;

            max-width: none !important;
            max-height: none !important;

            margin: 0 !important;
            padding: 0 !important;

            transform: none !important;

            break-before: auto !important;
            break-after: auto !important;
            break-inside: auto !important;

            page-break-before:
                auto !important;

            page-break-after:
                auto !important;

            page-break-inside:
                auto !important;

            overflow: hidden !important;
        }
    }
`;

document.head.appendChild(style);

return {
    width,
    height
};
