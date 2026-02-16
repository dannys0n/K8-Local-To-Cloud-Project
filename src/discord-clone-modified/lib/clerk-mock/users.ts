/**
 * Mock users for multi-user testing (similar to Clerk's user management).
 * Add more users here as needed.
 */
export const MOCK_USERS = [
  {
    id: "mock-user-1",
    firstName: "Alice",
    lastName: "Test",
    imageUrl: "/logo.png",
    emailAddresses: [{ emailAddress: "alice@test.com" }],
  },
  {
    id: "mock-user-2",
    firstName: "Bob",
    lastName: "Test",
    imageUrl: "/logo.png",
    emailAddresses: [{ emailAddress: "bob@test.com" }],
  },
  {
    id: "mock-user-3",
    firstName: "Carol",
    lastName: "Test",
    imageUrl: "/logo.png",
    emailAddresses: [{ emailAddress: "carol@test.com" }],
  },
] as const;

export const DEFAULT_MOCK_USER_ID = MOCK_USERS[0].id;

export function getMockUserById(id: string) {
  return MOCK_USERS.find((u) => u.id === id) ?? MOCK_USERS[0];
}
