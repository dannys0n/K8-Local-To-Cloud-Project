/**
 * Mock getAuth for @clerk/nextjs/server - returns mock userId
 */
const MOCK_USER_ID = "mock-user-id";

export function getAuth(_req?: { headers: Headers } | unknown) {
  return { userId: MOCK_USER_ID };
}
