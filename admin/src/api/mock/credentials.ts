/**
 * Demo credentials for mock mode.
 *
 * Kept in their own module with no side effects so the login screen can show
 * the hint without pulling the whole fixture generator into the bundle.
 */
export const MOCK_STAFF_EMAIL = 'admin@proxycrack.io'
export const MOCK_STAFF_PASSWORD = 'proxycrack'
/** Exists to demonstrate the contract's "non-staff gets 403" rule. */
export const MOCK_NON_STAFF_EMAIL = 'nonstaff@proxycrack.io'
