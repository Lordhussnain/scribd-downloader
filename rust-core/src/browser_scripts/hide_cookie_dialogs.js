const closeButtonSelectors = [
    '[class*="cookie"] [class*="close"]',
    '[class*="cookie"] [class*="dismiss"]',
    '[class*="cookie"] button[aria-label*="close"]',
    '[class*="cookie"] button[aria-label*="Close"]',
    '[class*="consent"] [class*="close"]',
    '[class*="consent"] [class*="dismiss"]',
    '[class*="banner"] [class*="close"]',
    '[class*="banner"] [class*="dismiss"]',
    '[class*="notice"] [class*="close"]',
    '[class*="notice"] [class*="dismiss"]',
    'button[class*="close"]',
    'button[aria-label="Close"]',
    'button[aria-label="close"]',
    'button[aria-label="Dismiss"]',
    '[data-dismiss]',
    '[role="button"][class*="close"]'
];

closeButtonSelectors.forEach((selector) => {
    try {
        document.querySelectorAll(selector).forEach((button) => button.click());
    } catch (error) {}
});

const cookieSelectors = [
    '[class*="cookie"]',
    '[class*="Cookie"]',
    '[class*="consent"]',
    '[class*="Consent"]',
    '[class*="gdpr"]',
    '[class*="GDPR"]',
    '[id*="cookie"]',
    '[id*="Cookie"]',
    '[id*="consent"]',
    '[id*="gdpr"]',
    '[class*="privacy-notice"]',
    '[class*="Privacy"]',
    '[class*="cookie-banner"]',
    '[class*="cookie-notice"]',
    '[class*="cookie-popup"]',
    '[class*="cookie-modal"]',
    '[class*="CookieConsent"]',
    '[class*="notice-banner"]',
    '.cc-window',
    '.cc-banner',
    '#onetrust-consent-sdk',
    '#onetrust-banner-sdk',
    '.evidon-banner',
    '.truste_box_overlay',
    '[class*="osano-cm"]',
    '[id*="osano"]'
];

cookieSelectors.forEach((selector) => {
    try {
        document.querySelectorAll(selector).forEach((element) => element.remove());
    } catch (error) {}
});

// Inspect only plausible overlay containers. The previous version
// called getComputedStyle(), getBoundingClientRect() and innerText
// on every node in the document; on a several-hundred-page Scribd
// DOM that could keep Chrome busy long enough to look like a hang
// before the export had printed even one page.
const overlayCandidates = document.querySelectorAll(
    'body > *, body > * > *, [role="dialog"], [aria-modal="true"]'
);
overlayCandidates.forEach((element) => {
    try {
        const style = getComputedStyle(element);
        if (style.position !== 'fixed' && style.position !== 'sticky') {
            return;
        }

        const rect = element.getBoundingClientRect();
        if (rect.top >= 100 || rect.width === 0 || rect.height === 0) {
            return;
        }

        const text = (element.textContent || '')
            .slice(0, 1000)
            .toLowerCase();
        if (
            text.includes('cookie') ||
            text.includes('privacy') ||
            text.includes('consent') ||
            text.includes('analytics') ||
            text.includes('advertising') ||
            text.includes('personalization')
        ) {
            element.remove();
        }
    } catch (error) {}
});
