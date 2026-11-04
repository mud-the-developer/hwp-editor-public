// Page.navigate can return before the review script defines its globals.
export const REVIEW_READY = "typeof state !== 'undefined' && !!state && typeof recoveryRead !== 'undefined' && !!recoveryRead && typeof busy !== 'undefined' && !busy";
