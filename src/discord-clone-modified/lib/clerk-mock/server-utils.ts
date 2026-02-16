import { redirect } from "next/navigation";

const MOCK_USER_ID = "mock-user-id";

const MOCK_USER = {
  id: MOCK_USER_ID,
  firstName: "Mock",
  lastName: "User",
  imageUrl: "/logo.png",
  emailAddresses: [{ emailAddress: "mock@example.com" }],
};

/** Server-only: used by initialProfile, etc. */
export function auth() {
  return { userId: MOCK_USER_ID };
}

/** Server-only: used by initialProfile */
export async function currentUser() {
  return MOCK_USER;
}

/** Server-only: redirect to sign-in */
export function redirectToSignIn() {
  redirect("/sign-in");
}
