const existing = document.getElementById('scribd-print-styles');
if (existing) {
    existing.remove();
}

const style = document.createElement('style');
style.id = 'scribd-print-styles';
style.textContent = `
    [class*="cookie"],
    [class*="Cookie"],
    [class*="consent"],
    [class*="Consent"],
    [class*="gdpr"],
    [class*="privacy-notice"],
    [class*="notice-banner"],
    [id*="cookie"],
    [id*="consent"],
    [class*="osano-cm"],
    [id*="osano"] {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        height: 0 !important;
        overflow: hidden !important;
    }

    [data-scribd-print-root="true"],
    .document_scroller {
        position: static !important;
        top: auto !important;
        right: auto !important;
        bottom: auto !important;
        left: auto !important;
        overflow: visible !important;
        height: auto !important;
        max-height: none !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    @media print {
        html,
        body {
            margin: 0 !important;
            padding: 0 !important;
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
        }

        .toolbar_top,
        .toolbar_bottom {
            display: none !important;
        }

        [data-scribd-print-root="true"],
        .document_scroller {
            position: static !important;
            top: auto !important;
            right: auto !important;
            bottom: auto !important;
            left: auto !important;
            overflow: visible !important;
            height: auto !important;
            max-height: none !important;
            margin: 0 !important;
            padding: 0 !important;
        }

        mjx-container,
        .MathJax,
        .katex,
        math,
        svg {
            visibility: visible !important;
            overflow: visible !important;
        }
    }
`;

document.head.appendChild(style);
